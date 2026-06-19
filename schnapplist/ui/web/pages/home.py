"""Home page — upload photos and start processing."""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from ..state import SessionState


def create(state: SessionState) -> None:
    with ui.column().classes("w-full max-w-3xl mx-auto gap-6 p-6"):
        ui.label("Schnapplist").classes("text-3xl font-bold text-primary")
        ui.label("AI-powered listing creator").classes("text-gray-500 -mt-4")

        # --- Photo upload ---
        with ui.card().classes("w-full"):
            with ui.card_section():
                ui.label("Photos").classes("text-lg font-semibold")
                ui.label(
                    "Upload the photos of the items you want to list. "
                    "You can upload multiple items at once."
                ).classes("text-sm text-gray-500")

            with ui.card_section():

                @ui.refreshable
                def file_list() -> None:
                    files = sorted(state.upload_dir.glob("*"))
                    if files:
                        for f in files:
                            with ui.row().classes("items-center gap-2"):
                                ui.icon("image", size="sm").classes("text-gray-400")
                                ui.label(f.name).classes("text-sm")
                    else:
                        ui.label("No photos yet").classes("text-sm text-gray-400 italic")

                async def _handle_upload(e: object) -> None:
                    f = e.file
                    dest = state.upload_dir / f.name
                    await f.save(dest)
                    file_list.refresh()

                ui.upload(
                    label="Drop photos here or click to select",
                    multiple=True,
                    auto_upload=True,
                    on_upload=_handle_upload,
                ).classes("w-full").props("accept=image/*")

                ui.label("Uploaded files:").classes("text-sm font-medium mt-2")
                file_list()

        # --- Options ---
        with ui.card().classes("w-full"):
            with ui.card_section():
                ui.label("Options").classes("text-lg font-semibold")

            with ui.card_section().classes("gap-4 flex flex-col"):
                with ui.row().classes("items-center gap-4 w-full"):
                    ui.label("LLM Provider").classes("w-32 text-sm font-medium")
                    ui.select(
                        options=["anthropic", "ollama"],
                        value=state.llm_provider,
                        on_change=lambda e: setattr(state, "llm_provider", e.value),
                    ).classes("flex-1")

                with ui.row().classes("items-center gap-4 w-full"):
                    ui.label("Model").classes("w-32 text-sm font-medium")
                    ui.input(
                        placeholder="Leave empty for default",
                        value=state.llm_model,
                        on_change=lambda e: setattr(state, "llm_model", e.value),
                    ).classes("flex-1")

                with ui.row().classes("items-center gap-4"):
                    ui.label("Single item").classes("w-32 text-sm font-medium")
                    ui.switch(
                        "Treat all photos as one item",
                        value=state.single_item,
                        on_change=lambda e: setattr(state, "single_item", e.value),
                    )

                with ui.row().classes("items-center gap-4 w-full"):
                    ui.label("Output dir").classes("w-32 text-sm font-medium")
                    ui.input(
                        value=str(state.output_dir),
                        on_change=lambda e: setattr(state, "output_dir", Path(e.value)),
                    ).classes("flex-1")

        # --- Start button ---
        ui.button(
            "Start Processing",
            icon="play_arrow",
            on_click=lambda: _start(state),
        ).classes("w-full").props("color=primary size=lg")


def _start(s: SessionState) -> None:
    files = list(s.upload_dir.glob("*"))
    if not files:
        ui.notify("Please upload at least one photo first.", type="warning")
        return
    s.reset_processing()
    ui.navigate.to("/process")
