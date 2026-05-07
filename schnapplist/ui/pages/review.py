"""Review page — edit and approve items before posting."""

from __future__ import annotations

from nicegui import ui

from ...models import Item, ItemCondition
from ...report_generator import generate_report
from ..state import SessionState

_CONDITIONS = [c.value for c in ItemCondition]


def create(state: SessionState) -> None:
    with ui.column().classes("w-full max-w-4xl mx-auto gap-6 p-6"):
        with ui.row().classes("items-center justify-between w-full"):
            ui.label("Review Items").classes("text-3xl font-bold text-primary")
            with ui.row().classes("gap-2"):
                ui.button("Save all", icon="save", on_click=lambda: _save_all(state)).props(
                    "color=primary outline"
                )
                ui.button(
                    "Go to Post",
                    icon="send",
                    on_click=lambda: ui.navigate.to("/post"),
                ).props("color=primary")

        if not state.items:
            ui.label("No items to review. Run processing first.").classes(
                "text-gray-500 italic"
            )
            ui.button("Back to Home", on_click=lambda: ui.navigate.to("/")).props("flat")
            return

        for item in state.items:
            _item_card(item)


def _item_card(item: Item) -> None:
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between"):
                ui.label(f"Item #{item.id}").classes("text-xs text-gray-400 font-mono")
                approved_switch = ui.switch(
                    "Approved",
                    value=item.approved,
                    on_change=lambda e, it=item: setattr(it, "approved", e.value),
                ).props("color=positive")

        with ui.card_section().classes("gap-4 flex flex-col"):
            # Photos preview
            if item.photos:
                with ui.row().classes("gap-2 flex-wrap"):
                    for photo in item.photos[:6]:
                        path = photo.display_path
                        if path.exists():
                            ui.image(str(path)).classes(
                                "w-24 h-24 object-cover rounded shadow"
                            )

            # Editable fields
            with ui.grid(columns=2).classes("w-full gap-4"):
                with ui.column():
                    ui.label("Name").classes("text-xs font-semibold text-gray-500 uppercase")
                    ui.input(
                        value=item.name,
                        on_change=lambda e, it=item: setattr(it, "name", e.value),
                    ).classes("w-full")

                with ui.column():
                    ui.label("Title (DE)").classes(
                        "text-xs font-semibold text-gray-500 uppercase"
                    )
                    ui.input(
                        value=item.title_de,
                        on_change=lambda e, it=item: setattr(it, "title_de", e.value),
                    ).classes("w-full")

                with ui.column():
                    ui.label("Condition").classes(
                        "text-xs font-semibold text-gray-500 uppercase"
                    )
                    ui.select(
                        options=_CONDITIONS,
                        value=item.condition.value,
                        on_change=lambda e, it=item: setattr(
                            it, "condition", ItemCondition(e.value)
                        ),
                    ).classes("w-full")

                with ui.column():
                    ui.label("Price (EUR)").classes(
                        "text-xs font-semibold text-gray-500 uppercase"
                    )
                    price_val = (
                        item.price_info.suggested_price if item.price_info else 0.0
                    )

                    def _update_price(e: object, it: Item = item) -> None:
                        try:
                            val = float(getattr(e, "value", 0))
                        except ValueError:
                            return
                        if it.price_info:
                            it.price_info.suggested_price = val

                    ui.number(value=price_val, format="%.2f", on_change=_update_price).classes(
                        "w-full"
                    )

            ui.label("Description").classes("text-xs font-semibold text-gray-500 uppercase mt-2")
            ui.textarea(
                value=item.description,
                on_change=lambda e, it=item: setattr(it, "description", e.value),
            ).classes("w-full").props("rows=4 autogrow")

            # Marketplace
            with ui.row().classes("items-center gap-4"):
                ui.label("Marketplace").classes("text-xs font-semibold text-gray-500 uppercase")
                ui.select(
                    options=["kleinanzeigen", "ebay"],
                    value=item.marketplace or "kleinanzeigen",
                    on_change=lambda e, it=item: setattr(it, "marketplace", e.value),
                )


def _save_all(state: SessionState) -> None:
    if state.result and state.result.report_path:
        try:
            generate_report(state.items, state.result.report_path)
            ui.notify("Saved — report files updated.", type="positive")
        except Exception as exc:
            ui.notify(f"Save failed: {exc}", type="negative")
    else:
        ui.notify("No report path available.", type="warning")
