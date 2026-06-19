"""Process service — orchestrates the full photo-to-report pipeline."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol, TypeVar

from pydantic_ai.usage import RunUsage

from ..agents.item_research_agent import AgentResult, ItemResearchOutput, run_item_research_agent
from ..config import (
    AGENT_MAX_ITERATIONS,
    AGENT_TARGET_CONFIDENCE,
    CLAUDE_MODEL,
    LLM_PROVIDER,
    OLLAMA_HOST,
    OLLAMA_MODEL,
)
from ..core.llm import LLMClient
from ..core.models import Item
from ..core.photo_processor import (
    enhance_photo,
    filter_redundant_photos,
    group_photos_by_item,
    load_photos,
)
from ..core.report_generator import write_item_report

_T = TypeVar("_T")

_ITEM_STAGES = ("enhance", "analyze", "report")


class ProgressCallback(Protocol):
    """Receives progress events from ProcessWorkflow.

    Emitted events and their kwargs:
      scan_done      count: int
      group_done     count: int
      item_start     idx: int, total: int
      item_stage     idx: int, stage: str
      item_done      idx: int, name: str, price: str, confidence: float, low_confidence: bool
      item_usage     idx: int, input_tokens: int, output_tokens: int,
                     cache_read_tokens: int, requests: int, tool_calls: int
      report_done    path: Path
      warning        idx: int, message: str
    """

    def __call__(self, event: str, **kwargs: Any) -> None: ...


class DecisionCallback(Protocol):
    """Called when an item fails processing. Returns 'retry' or 'skip'."""

    def __call__(self, event: str, **kwargs: Any) -> str: ...


def _default_decision_callback(event: str, **kwargs: Any) -> str:
    return "skip"


def _details_factory() -> dict[str, Any]:
    return {}


def _path_list_factory() -> list[Path]:
    return []


def _stage_record_list_factory() -> list[StageRecord]:
    return []


def _item_state_list_factory() -> list[ItemRunState]:
    return []


@dataclass
class StageRecord:
    stage: str
    status: str
    duration_ms: float
    details: dict[str, Any] = field(default_factory=_details_factory)


@dataclass
class ItemRunState:
    index: int
    original_photos: list[Path]
    filtered_photos: list[Path] = field(default_factory=_path_list_factory)
    enhanced_photos: list[Path] = field(default_factory=_path_list_factory)
    item_name: str = ""
    condition: str = ""
    stage_records: list[StageRecord] = field(default_factory=_stage_record_list_factory)


@dataclass
class ProcessRunState:
    run_id: str
    photos_dir: Path
    output_dir: Path
    single_item: bool
    total_photos: int = 0
    total_groups: int = 0
    stage_records: list[StageRecord] = field(default_factory=_stage_record_list_factory)
    item_states: list[ItemRunState] = field(default_factory=_item_state_list_factory)


@dataclass
class ProcessRunResult:
    state: ProcessRunState
    items: list[Item]
    report_path: Path | None


class ProcessWorkflow:
    """Orchestrates the full photo → item identification → report pipeline."""

    def __init__(
        self,
        client: LLMClient,
        on_progress: ProgressCallback | None = None,
        on_decision: DecisionCallback | None = None,
    ) -> None:
        self._client = client
        self._on_progress = on_progress
        self._on_decision = on_decision or _default_decision_callback

    def _emit(self, event: str, **kwargs: Any) -> None:
        if self._on_progress is not None:
            self._on_progress(event, **kwargs)

    def run(
        self,
        *,
        photos_dir: Path,
        output_dir: Path,
        single_item: bool,
        marketplace: str | None = None,
    ) -> ProcessRunResult:
        state = ProcessRunState(
            run_id=str(uuid.uuid4())[:8],
            photos_dir=photos_dir,
            output_dir=output_dir,
            single_item=single_item,
        )

        photos = self._run_stage(
            state.stage_records,
            "scan_photos",
            lambda: load_photos(photos_dir),
            details=lambda v: {"count": len(v)},
        )
        self._emit("scan_done", count=len(photos))

        state.total_photos = len(photos)
        if not photos:
            return ProcessRunResult(state=state, items=[], report_path=None)

        if single_item:
            groups = [photos]
            self._emit("group_done", count=1)
        else:
            groups = self._run_stage(
                state.stage_records,
                "group_photos",
                lambda: group_photos_by_item(photos, self._client),
                details=lambda v: {"count": len(v)},
            )
            self._emit("group_done", count=len(groups))

        state.total_groups = len(groups)

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = output_dir / f"schnapplist-report-{timestamp}"

        items: list[Item] = []
        enhanced_root = run_dir / "pictures"

        for idx, group in enumerate(groups, start=1):
            self._emit("item_start", idx=idx, total=len(groups))

            item_state = ItemRunState(index=idx, original_photos=list(group))
            state.item_states.append(item_state)

            filtered = list(group)
            if len(filtered) > 1:
                self._emit("item_stage", idx=idx, stage="filter")
                group_for_filter = list(filtered)
                filtered = self._run_stage(
                    item_state.stage_records,
                    "filter_redundant_photos",
                    lambda g=group_for_filter: filter_redundant_photos(g, self._client),
                    details=lambda v: {"count": len(v)},
                )
            item_state.filtered_photos = filtered

            self._emit("item_stage", idx=idx, stage="enhance")
            filtered_for_enhance = list(filtered)
            enhanced = self._run_stage(
                item_state.stage_records,
                "enhance_photos",
                lambda fe=filtered_for_enhance, i=idx: [
                    enhance_photo(photo, enhanced_root, self._client, output_stem=f"item-{i}-{n}")
                    for n, photo in enumerate(fe, start=1)
                ],
                details=lambda v: {"count": len(v)},
            )
            item_state.enhanced_photos = enhanced

            self._emit("item_stage", idx=idx, stage="analyze")
            _MAX_RETRIES = 2
            attempts = 0
            agent_output: ItemResearchOutput | None = None
            while agent_output is None:
                try:
                    filtered_for_agent = list(filtered)
                    _prev: list[RunUsage] = [RunUsage()]

                    def _on_usage(
                        u: RunUsage,
                        _idx: int = idx,
                        _prev: list[RunUsage] = _prev,
                    ) -> None:
                        prev = _prev[0]
                        self._emit(
                            "item_usage",
                            idx=_idx,
                            input_tokens=u.input_tokens - prev.input_tokens,
                            output_tokens=u.output_tokens - prev.output_tokens,
                            cache_read_tokens=u.cache_read_tokens - prev.cache_read_tokens,
                            requests=u.requests - prev.requests,
                            tool_calls=u.tool_calls - prev.tool_calls,
                        )
                        _prev[0] = u

                    agent_result: AgentResult = self._run_stage(
                        item_state.stage_records,
                        "item_research_agent",
                        lambda fa=filtered_for_agent, _idx=idx: run_item_research_agent(
                            fa,
                            self._client,
                            on_stage=lambda stage: self._emit("item_stage", idx=_idx, stage=stage),
                            on_usage=_on_usage,
                            max_iterations=AGENT_MAX_ITERATIONS,
                            target_confidence=AGENT_TARGET_CONFIDENCE,
                        ),
                    )
                    agent_output = agent_result.output
                    low_confidence = agent_output.confidence < AGENT_TARGET_CONFIDENCE
                    item_state.item_name = agent_output.name
                    item_state.condition = agent_output.condition.value
                except Exception as exc:
                    decision = self._on_decision(
                        "item_failed",
                        idx=idx,
                        name=item_state.item_name or f"Item {idx}",
                        error=str(exc),
                    )
                    if decision == "retry" and attempts < _MAX_RETRIES:
                        attempts += 1
                        continue
                    self._emit("warning", idx=idx, message=f"Skipping item {idx}: {exc}")
                    break

            if agent_output is None:
                continue

            _analysis_dict: dict[str, Any] = {
                "name": agent_output.name,
                "brand": agent_output.brand,
                "model": agent_output.model,
                "condition": agent_output.condition.value,
                "condition_notes": agent_output.condition_notes,
                "title_de": agent_output.title_de,
                "description_de": agent_output.description_de,
                "keywords": agent_output.keywords,
                "category": agent_output.category,
                "confidence": agent_output.confidence,
                "confidence_notes": agent_output.confidence_notes,
            }
            if agent_output.ka_options is not None:
                o = agent_output.ka_options
                _analysis_dict["ka_category"] = o.ka_category
                _analysis_dict["ka_shipping"] = o.shipping.value
                _analysis_dict["ka_shipping_methods"] = o.shipping_methods
                _analysis_dict["ka_price_type"] = o.price_type.value
            if agent_output.ebay_options is not None:
                o2 = agent_output.ebay_options
                _analysis_dict["ebay_listing_type"] = o2.listing_type.value
                _analysis_dict["ebay_duration_days"] = o2.duration_days
                _analysis_dict["ebay_reserve_price"] = o2.reserve_price
                _analysis_dict["ebay_category_id"] = agent_output.ebay_category_id

            item = Item.from_analysis(_analysis_dict, filtered, enhanced, marketplace=marketplace)
            item.price_info = agent_output.price_info
            items.append(item)

            self._emit("item_stage", idx=idx, stage="report")
            write_item_report(item, idx, run_dir)

            price_str = (
                f"{agent_output.price_info.suggested_price:.2f} {agent_output.price_info.currency}"
                if agent_output.price_info
                else "—"
            )
            self._emit(
                "item_done",
                idx=idx,
                name=item_state.item_name,
                price=price_str,
                confidence=agent_output.confidence,
                low_confidence=low_confidence,
            )

        self._emit("report_done", path=run_dir)

        return ProcessRunResult(
            state=state,
            items=items,
            report_path=run_dir,
        )

    @staticmethod
    def _run_stage(
        bucket: list[StageRecord],
        stage: str,
        fn: Callable[[], _T],
        details: Callable[[_T], dict[str, Any]] | None = None,
    ) -> _T:
        start = perf_counter()
        try:
            value = fn()
            record = StageRecord(
                stage=stage,
                status="success",
                duration_ms=(perf_counter() - start) * 1000,
                details=details(value) if details else {},
            )
            bucket.append(record)
            return value
        except Exception as exc:
            bucket.append(
                StageRecord(
                    stage=stage,
                    status="failed",
                    duration_ms=(perf_counter() - start) * 1000,
                    details={"error": str(exc)},
                )
            )
            raise


def run_process(
    photos_dir: Path,
    output_dir: Path,
    *,
    single_item: bool = False,
    marketplace: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    ollama_host: str | None = None,
    on_progress: ProgressCallback | None = None,
    on_decision: DecisionCallback | None = None,
) -> ProcessRunResult:
    """Build an LLMClient from config/args and run the processing workflow.

    Raises:
        ValueError: if provider is "anthropic" and ANTHROPIC_API_KEY is not set.
        Any exception propagated from the workflow stages.
    """
    from ..config import ANTHROPIC_API_KEY

    provider = llm_provider or LLM_PROVIDER
    if provider == "anthropic":
        api_key = ANTHROPIC_API_KEY
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set. Add it to a .env file or export it in your shell."
            )
        model = llm_model or CLAUDE_MODEL
        client = LLMClient("anthropic", model, api_key=api_key)
    else:
        model = llm_model or OLLAMA_MODEL
        host = ollama_host or OLLAMA_HOST
        client = LLMClient("ollama", model, ollama_host=host)

    workflow = ProcessWorkflow(client, on_progress=on_progress, on_decision=on_decision)
    return workflow.run(
        photos_dir=photos_dir,
        output_dir=output_dir,
        single_item=single_item,
        marketplace=marketplace,
    )
