"""Lightweight orchestration layer for the processing pipeline.

This module keeps stage execution deterministic while centralizing state,
metrics, and stage outcomes in typed structures.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, TypeVar

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from .item_analyzer import analyze_item, build_item
from .llm import LLMClient
from .models import Item
from .photo_processor import (
    enhance_photo,
    filter_redundant_photos,
    group_photos_by_item,
    load_photos,
)
from .price_researcher import research_price
from .report_generator import generate_report

_T = TypeVar("_T")

# Stages executed per item — used to size the per-item progress bar.
_ITEM_STAGES = ("filter", "enhance", "analyze", "price")


@dataclass
class StageRecord:
    stage: str
    status: str
    duration_ms: float
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ItemRunState:
    index: int
    original_photos: list[Path]
    filtered_photos: list[Path] = field(default_factory=list)
    enhanced_photos: list[Path] = field(default_factory=list)
    item_name: str = ""
    condition: str = ""
    stage_records: list[StageRecord] = field(default_factory=list)


@dataclass
class ProcessRunState:
    run_id: str
    photos_dir: Path
    output_dir: Path
    single_item: bool
    total_photos: int = 0
    total_groups: int = 0
    stage_records: list[StageRecord] = field(default_factory=list)
    item_states: list[ItemRunState] = field(default_factory=list)


@dataclass
class ProcessRunResult:
    state: ProcessRunState
    items: list[Item]
    report_path: Path | None
    state_file: Path | None


class ProcessOrchestrator:
    """Deterministic pipeline orchestrator with explicit run state."""

    def __init__(self, client: LLMClient, console: Console | None = None) -> None:
        self._client = client
        self._console = console or Console()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, *, photos_dir: Path, output_dir: Path, single_item: bool) -> ProcessRunResult:
        state = ProcessRunState(
            run_id=str(uuid.uuid4())[:8],
            photos_dir=photos_dir,
            output_dir=output_dir,
            single_item=single_item,
        )

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=self._console,
            transient=False,
        )

        with progress:
            # ── scan ──────────────────────────────────────────────────
            scan_task = progress.add_task("Scanning photos…", total=None)
            photos = self._run_stage(
                state.stage_records,
                "scan_photos",
                lambda: load_photos(photos_dir),
                details=lambda v: {"count": len(v)},
            )
            progress.update(scan_task, description=f"Found [bold]{len(photos)}[/bold] photo(s)", total=1, completed=1)

            state.total_photos = len(photos)
            if not photos:
                return ProcessRunResult(state=state, items=[], report_path=None, state_file=None)

            # ── group ─────────────────────────────────────────────────
            if single_item:
                groups = [photos]
                progress.add_task("Grouping skipped (--single-item)", total=1, completed=1)
            else:
                group_task = progress.add_task("Grouping photos by item…", total=None)
                groups = self._run_stage(
                    state.stage_records,
                    "group_photos",
                    lambda: group_photos_by_item(photos, self._client),
                    details=lambda v: {"count": len(v)},
                )
                progress.update(
                    group_task,
                    description=f"Identified [bold]{len(groups)}[/bold] item group(s)",
                    total=1,
                    completed=1,
                )

            state.total_groups = len(groups)

            # ── per-item loop ─────────────────────────────────────────
            items_task = progress.add_task("Processing items…", total=len(groups))
            items: list[Item] = []
            enhanced_root = output_dir / "enhanced"

            for idx, group in enumerate(groups, start=1):
                item_label = f"Item {idx}/{len(groups)}"
                item_task = progress.add_task(
                    f"[cyan]{item_label}[/cyan] — starting…",
                    total=len(_ITEM_STAGES),
                )

                item_state = ItemRunState(index=idx, original_photos=list(group))
                state.item_states.append(item_state)

                # filter
                filtered = list(group)
                if len(filtered) > 1:
                    progress.update(item_task, description=f"[cyan]{item_label}[/cyan] — filtering photos…")
                    group_for_filter = list(filtered)
                    filtered = self._run_stage(
                        item_state.stage_records,
                        "filter_redundant_photos",
                        lambda g=group_for_filter: filter_redundant_photos(g, self._client),
                        details=lambda v: {"count": len(v)},
                    )
                item_state.filtered_photos = filtered
                progress.advance(item_task)

                # enhance
                progress.update(item_task, description=f"[cyan]{item_label}[/cyan] — enhancing {len(filtered)} photo(s)…")
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
                progress.advance(item_task)

                # analyze
                progress.update(item_task, description=f"[cyan]{item_label}[/cyan] — analyzing item…")
                filtered_for_analysis = list(filtered)
                analysis = self._run_stage(
                    item_state.stage_records,
                    "analyze_item",
                    lambda fa=filtered_for_analysis: analyze_item(fa, self._client),
                )
                item_state.item_name = str(analysis.get("name", f"Item {idx}"))
                item_state.condition = str(analysis.get("condition", "good"))
                progress.advance(item_task)

                # price
                progress.update(item_task, description=f"[cyan]{item_label}[/cyan] — researching price…")
                keywords_for_price = list(analysis.get("keywords") or [item_state.item_name])
                condition_for_price = item_state.condition
                price_info = self._run_stage(
                    item_state.stage_records,
                    "research_price",
                    lambda kw=keywords_for_price, cond=condition_for_price: research_price(kw, cond, self._client),
                    details=lambda v: {"suggested_price": v.suggested_price, "currency": v.currency},
                )
                progress.advance(item_task)

                item = build_item(analysis, filtered, enhanced)
                item.price_info = price_info
                items.append(item)

                # summarize this item inline
                price_str = f"{price_info.suggested_price:.2f} {price_info.currency}" if price_info else "—"
                progress.update(
                    item_task,
                    description=(
                        f"[cyan]{item_label}[/cyan] [green]✓[/green] "
                        f"[bold]{item_state.item_name}[/bold]  {price_str}"
                    ),
                )
                progress.advance(items_task)

            # ── report + persist ──────────────────────────────────────
            report_task = progress.add_task("Generating report…", total=None)
            report_path = self._run_stage(
                state.stage_records,
                "generate_report",
                lambda: generate_report(items, output_dir),
                details=lambda v: {"path": str(v)},
            )
            progress.update(report_task, description="Report written", total=1, completed=1)

            persist_task = progress.add_task("Saving state…", total=None)
            state_file = output_dir / "items.json"
            self._run_stage(
                state.stage_records,
                "persist_state",
                lambda: state_file.write_text(
                    json.dumps([json.loads(item.model_dump_json()) for item in items], indent=2, default=str),
                    encoding="utf-8",
                ),
                details=lambda _: {"path": str(state_file)},
            )
            progress.update(persist_task, description="State saved", total=1, completed=1)

        return ProcessRunResult(
            state=state,
            items=items,
            report_path=report_path,
            state_file=state_file,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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
