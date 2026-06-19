"""Post page — post approved items to a marketplace."""

from __future__ import annotations

import asyncio

from nicegui import ui

from ...config import DEFAULT_MARKETPLACE
from ...services.posting_service import PostResult, post_item
from ..state import SessionState


def create(state: SessionState) -> None:
    with ui.column().classes("w-full max-w-3xl mx-auto gap-6 p-6"):
        with ui.row().classes("items-center justify-between w-full"):
            ui.label("Post Items").classes("text-3xl font-bold text-primary")
            ui.button(
                "Back to Review", icon="arrow_back", on_click=lambda: ui.navigate.to("/review")
            ).props("flat")

        approved = [it for it in state.items if it.approved]

        if not approved:
            ui.label(
                "No approved items. Go back to Review and toggle Approved on the items you want to post."
            ).classes("text-gray-500 italic")
            return

        ui.label(f"{len(approved)} item(s) ready to post").classes("text-gray-500")

        # Per-item marketplace override
        overrides: dict[str, str] = {}
        results_container = ui.column().classes("w-full gap-3 mt-2")

        with ui.card().classes("w-full"):
            with ui.card_section():
                ui.label("Items").classes("text-lg font-semibold")
            with ui.card_section().classes("gap-3 flex flex-col"):
                for item in approved:
                    default_mkt = item.marketplace or DEFAULT_MARKETPLACE
                    overrides[item.id] = default_mkt
                    with ui.row().classes("items-center gap-4 w-full"):
                        ui.label(item.title_de or item.name).classes("flex-1 text-sm font-medium")
                        price = (
                            f"{item.price_info.suggested_price:.2f} EUR" if item.price_info else "—"
                        )
                        ui.label(price).classes("text-sm text-gray-500 w-24 text-right")
                        ui.select(
                            options=["kleinanzeigen", "ebay"],
                            value=default_mkt,
                            on_change=lambda e, iid=item.id: overrides.update({iid: e.value}),
                        ).classes("w-36")

        dry_run_toggle = ui.switch("Dry run (no actual posting)", value=False)

        post_btn = (
            ui.button(
                "Post All",
                icon="send",
                on_click=lambda: asyncio.ensure_future(
                    _post_all(
                        approved, overrides, dry_run_toggle.value, results_container, post_btn
                    )
                ),
            )
            .props("color=primary size=lg")
            .classes("w-full")
        )


async def _post_all(
    items: list,
    overrides: dict[str, str],
    dry_run: bool,
    container: ui.column,
    btn: ui.button,
) -> None:
    btn.props("loading=true disabled=true")
    container.clear()

    loop = asyncio.get_event_loop()

    for item in items:
        marketplace = overrides.get(item.id, DEFAULT_MARKETPLACE)
        result: PostResult = await loop.run_in_executor(
            None,
            lambda it=item, mkt=marketplace: post_item(it, mkt, dry_run=dry_run),
        )
        with container:
            _result_card(result)

    btn.props(remove="loading disabled")
    ui.notify("Done!", type="positive")


def _result_card(result: PostResult) -> None:
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center gap-3"):
                if result.dry_run:
                    ui.icon("info", color="blue")
                    ui.label(f"[DRY RUN] {result.item_name}").classes("font-medium")
                elif result.success:
                    ui.icon("check_circle", color="green")
                    ui.label(result.item_name).classes("font-medium text-green-700")
                else:
                    ui.icon("error", color="red")
                    ui.label(result.item_name).classes("font-medium text-red-700")

        if result.dry_run and result.dry_run_summary:
            with ui.card_section().classes("text-sm text-gray-600 gap-1 flex flex-col"):
                for key, val in result.dry_run_summary.items():
                    with ui.row().classes("gap-2"):
                        ui.label(key.replace("_", " ").title() + ":").classes("font-medium w-32")
                        ui.label(str(val))
        elif result.success and result.url:
            with ui.card_section():
                ui.link(result.url, result.url).classes("text-sm text-blue-600 break-all")
        elif result.error:
            with ui.card_section():
                ui.label(result.error).classes("text-sm text-red-500")
