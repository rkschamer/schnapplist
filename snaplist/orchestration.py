"""Lightweight orchestration layer for the processing pipeline.

This module keeps stage execution deterministic while centralizing state,
metrics, and stage outcomes in typed structures.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

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

    def __init__(self, client: LLMClient) -> None:
        self._client = client

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
            details=lambda value: {"count": len(value)},
        )

        state.total_photos = len(photos)
        if not photos:
            return ProcessRunResult(state=state, items=[], report_path=None, state_file=None)

        groups = [photos] if single_item else self._run_stage(
            state.stage_records,
            "group_photos",
            lambda: group_photos_by_item(photos, self._client),
            details=lambda value: {"count": len(value)},
        )
        state.total_groups = len(groups)

        items: list[Item] = []
        enhanced_root = output_dir / "enhanced"

        for idx, group in enumerate(groups, start=1):
            item_state = ItemRunState(index=idx, original_photos=list(group))
            state.item_states.append(item_state)

            filtered = list(group)
            if len(filtered) > 1:
                group_for_filter = list(filtered)
                filtered = self._run_stage(
                    item_state.stage_records,
                    "filter_redundant_photos",
                    lambda group_for_filter=group_for_filter: filter_redundant_photos(group_for_filter, self._client),
                    details=lambda value: {"count": len(value)},
                )
            item_state.filtered_photos = filtered

            filtered_for_enhance = list(filtered)

            enhanced = self._run_stage(
                item_state.stage_records,
                "enhance_photos",
                lambda filtered_for_enhance=filtered_for_enhance: [
                    enhance_photo(photo, enhanced_root, self._client) for photo in filtered_for_enhance
                ],
                details=lambda value: {"count": len(value)},
            )
            item_state.enhanced_photos = enhanced

            filtered_for_analysis = list(filtered)

            analysis = self._run_stage(
                item_state.stage_records,
                "analyze_item",
                lambda filtered_for_analysis=filtered_for_analysis: analyze_item(filtered_for_analysis, self._client),
            )
            item_state.item_name = str(analysis.get("name", f"Item {idx}"))
            item_state.condition = str(analysis.get("condition", "good"))
            keywords = analysis.get("keywords") or [item_state.item_name]
            keywords_for_price = list(keywords)
            condition_for_price = item_state.condition

            price_info = self._run_stage(
                item_state.stage_records,
                "research_price",
                lambda keywords_for_price=keywords_for_price, condition_for_price=condition_for_price: research_price(
                    keywords_for_price,
                    condition_for_price,
                    self._client,
                ),
                details=lambda value: {
                    "suggested_price": value.suggested_price,
                    "currency": value.currency,
                },
            )

            item = build_item(analysis, filtered, enhanced)
            item.price_info = price_info
            items.append(item)

        report_path = self._run_stage(
            state.stage_records,
            "generate_report",
            lambda: generate_report(items, output_dir),
            details=lambda value: {"path": str(value)},
        )

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

        return ProcessRunResult(
            state=state,
            items=items,
            report_path=report_path,
            state_file=state_file,
        )

    @staticmethod
    def _run_stage(
        bucket: list[StageRecord],
        stage: str,
        fn,
        details=None,
    ):
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
