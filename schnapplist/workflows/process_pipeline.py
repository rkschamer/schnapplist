"""Deterministic processing workflow implementation."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol, TypeVar

from ..core.item_analyzer import analyze_item, build_item, is_low_confidence
from ..core.llm import LLMClient
from ..core.models import Item
from ..core.photo_processor import (
    enhance_photo,
    filter_redundant_photos,
    group_photos_by_item,
    load_photos,
)
from ..core.price_researcher import research_price
from ..core.report_generator import generate_report

_T = TypeVar("_T")

# Stages executed per item — used to size the per-item progress bar.
_ITEM_STAGES = ("filter", "enhance", "analyze", "price")


class ProgressCallback(Protocol):
    """Receives progress events from ProcessWorkflow.

    Emitted events and their kwargs:
      scan_done      count: int
      group_done     count: int
      item_start     idx: int, total: int
      item_stage     idx: int, stage: str
      item_done      idx: int, name: str, price: str
      report_done    path: Path
      warning        message: str
    """

    def __call__(self, event: str, **kwargs: Any) -> None: ...


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
    """Deterministic processing workflow with explicit run state."""

    def __init__(self, client: LLMClient, on_progress: ProgressCallback | None = None) -> None:
        self._client = client
        self._on_progress = on_progress

    def _emit(self, event: str, **kwargs: Any) -> None:
        if self._on_progress is not None:
            self._on_progress(event, **kwargs)

    def run(self, *, photos_dir: Path, output_dir: Path, single_item: bool) -> ProcessRunResult:
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
                lambda fe=filtered_for_enhance: [
                    enhance_photo(photo, enhanced_root, self._client) for photo in fe
                ],
                details=lambda v: {"count": len(v)},
            )
            item_state.enhanced_photos = enhanced

            self._emit("item_stage", idx=idx, stage="analyze")
            filtered_for_analysis = list(filtered)
            analysis = self._run_stage(
                item_state.stage_records,
                "analyze_item",
                lambda fa=filtered_for_analysis: analyze_item(fa, self._client),
            )
            item_state.item_name = str(analysis.get("name", f"Item {idx}"))
            item_state.condition = str(analysis.get("condition", "good"))

            # Google Lens: only when the LLM could not identify the item at all.
            if is_low_confidence(analysis) and filtered_for_analysis:
                from .image_search_agent import identify_via_google_lens

                self._emit("item_stage", idx=idx, stage="image_search")
                try:
                    lens_enriched = identify_via_google_lens(filtered_for_analysis[0])
                    for key, val in lens_enriched.items():
                        if val and not analysis.get(key):
                            analysis[key] = val
                    if lens_enriched.get("name"):
                        item_state.item_name = str(lens_enriched["name"])
                except Exception as exc:
                    self._emit("warning", message=f"Google Lens fallback failed: {exc}")

            # Text search: always — verifies and corrects the current identification.
            from .image_search_agent import identify_via_text_search

            self._emit("item_stage", idx=idx, stage="web_search")
            try:
                text_enriched = identify_via_text_search(analysis, self._client)
                # Override existing values — purpose is correction, not just gap-fill.
                for key, val in text_enriched.items():
                    if val:
                        analysis[key] = val
                if text_enriched.get("name"):
                    item_state.item_name = str(text_enriched["name"])
            except Exception as exc:
                self._emit("warning", message=f"Text search failed: {exc}")

            self._emit("item_stage", idx=idx, stage="price")
            keywords_for_price = list(analysis.get("keywords") or [item_state.item_name])
            condition_for_price = item_state.condition
            price_info = self._run_stage(
                item_state.stage_records,
                "research_price",
                lambda kw=keywords_for_price, cond=condition_for_price: research_price(
                    kw, cond, self._client
                ),
                details=lambda v: {
                    "suggested_price": v.suggested_price,
                    "currency": v.currency,
                },
            )

            item = build_item(analysis, filtered, enhanced)
            item.price_info = price_info
            items.append(item)

            price_str = (
                f"{price_info.suggested_price:.2f} {price_info.currency}"
                if price_info
                else "—"
            )
            self._emit("item_done", idx=idx, name=item_state.item_name, price=price_str)

        report_path = self._run_stage(
            state.stage_records,
            "generate_report",
            lambda: generate_report(items, run_dir),
            details=lambda v: {"path": str(v)},
        )
        self._emit("report_done", path=report_path)

        return ProcessRunResult(
            state=state,
            items=items,
            report_path=report_path,
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
