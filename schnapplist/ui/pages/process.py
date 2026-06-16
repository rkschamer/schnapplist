"""Processing page — shows live progress while the workflow runs."""

from __future__ import annotations

import asyncio
from typing import Any

from nicegui import ui

from ...services.process_service import run_process
from ...agents.process_pipeline import ProgressCallback
from ..state import ProcessingEvent, SessionState

_STAGE_LABELS: dict[str, str] = {
    "filter": "Filtering redundant photos",
    "enhance": "Enhancing photos",
    "analyze": "Analysing item",
    "image_search": "Reverse image search",
    "web_search": "Web search refinement",
    "price": "Researching price",
}


def create(state: SessionState) -> None:
    with ui.column().classes("w-full max-w-3xl mx-auto gap-6 p-6"):
        ui.label("Processing…").classes("text-3xl font-bold text-primary")

        overall_progress = ui.linear_progress(value=0).classes("w-full")
        status_label = ui.label("Starting…").classes("text-sm text-gray-500")
        events_container = ui.column().classes("w-full gap-2 mt-2")

        error_label = ui.label("").classes("text-red-500 hidden")

        nav_row = ui.row().classes("w-full gap-4 mt-4 hidden")
        with nav_row:
            ui.button("Review Items", icon="rate_review",
                      on_click=lambda: ui.navigate.to("/review")).props("color=primary")
            ui.button("Back", icon="arrow_back",
                      on_click=lambda: ui.navigate.to("/")).props("flat")

        rendered_events: list[str] = []

        def _render_events() -> None:
            new_events = state.progress_events[len(rendered_events):]
            for ev in new_events:
                rendered_events.append(ev.event)
                _render_event(ev, events_container)

        def _render_event(ev: ProcessingEvent, container: ui.column) -> None:
            with container:
                if ev.event == "scan_done":
                    count = ev.kwargs.get("count", 0)
                    _event_row(f"Found {count} photo(s)", "photo_library", "text-blue-500")
                elif ev.event == "group_done":
                    count = ev.kwargs.get("count", 0)
                    _event_row(f"Identified {count} item group(s)", "category", "text-blue-500")
                elif ev.event == "item_start":
                    idx, total = ev.kwargs.get("idx", 0), ev.kwargs.get("total", 0)
                    _event_row(f"Item {idx} of {total}", "inventory_2", "text-primary font-semibold")
                elif ev.event == "item_stage":
                    stage = ev.kwargs.get("stage", "")
                    label = _STAGE_LABELS.get(stage, stage)
                    _event_row(f"  · {label}", "arrow_right", "text-gray-500 ml-4")
                elif ev.event == "item_done":
                    name = ev.kwargs.get("name", "")
                    price = ev.kwargs.get("price", "")
                    _event_row(f"✓ {name}  {price}", "check_circle", "text-green-600 font-medium")
                elif ev.event == "report_done":
                    _event_row("Report written", "description", "text-green-600")
                elif ev.event == "warning":
                    msg = ev.kwargs.get("message", "")
                    _event_row(f"⚠ {msg}", "warning", "text-yellow-600")

        def _event_row(text: str, icon: str, classes: str) -> None:
            with ui.row().classes(f"items-center gap-2 {classes}"):
                ui.icon(icon, size="xs")
                ui.label(text).classes("text-sm")

        def _update_progress() -> None:
            total = state.result.state.total_groups if state.result else 0
            done = len([e for e in state.progress_events if e.event == "item_done"])
            if total > 0:
                overall_progress.set_value(done / total)

            if state.progress_events:
                last = state.progress_events[-1]
                if last.event == "item_stage":
                    stage = last.kwargs.get("stage", "")
                    status_label.set_text(_STAGE_LABELS.get(stage, stage) + "…")
                elif last.event == "item_done":
                    name = last.kwargs.get("name", "")
                    status_label.set_text(f"Done: {name}")

        timer = ui.timer(0.4, lambda: (_render_events(), _update_progress()))

        async def _run() -> None:
            state.processing = True

            def _cb(event: str, **kwargs: Any) -> None:
                state.progress_events.append(ProcessingEvent(event=event, kwargs=kwargs))

            loop = asyncio.get_event_loop()
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda: run_process(
                        photos_dir=state.upload_dir,
                        output_dir=state.output_dir,
                        single_item=state.single_item,
                        llm_provider=state.llm_provider,
                        llm_model=state.llm_model or None,
                        on_progress=_cb,
                    ),
                )
                state.result = result
                state.items = result.items
                overall_progress.set_value(1.0)
                status_label.set_text("Processing complete!")
                state.processing_done = True
            except Exception as exc:
                state.processing_error = str(exc)
                error_label.set_text(f"Error: {exc}")
                error_label.classes(remove="hidden")
                status_label.set_text("Processing failed.")
            finally:
                state.processing = False
                timer.cancel()
                _render_events()
                nav_row.classes(remove="hidden")

        ui.timer(0.1, _run, once=True)
