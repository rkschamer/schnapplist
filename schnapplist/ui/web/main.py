"""NiceGUI entry point — schnapplist web UI."""

from __future__ import annotations

from nicegui import app, ui
from nicegui.client import Client

from .state import SessionState

# One session state per browser tab (stored in app.storage.tab).
# NiceGUI's tab storage is dict-like; we keep a single key.
_STATE_KEY = "session"


def _get_state() -> SessionState:
    storage = app.storage.tab
    if _STATE_KEY not in storage:
        storage[_STATE_KEY] = SessionState()
    state = storage[_STATE_KEY]
    if not isinstance(state, SessionState):
        state = SessionState()
        storage[_STATE_KEY] = state
    return state


def _nav_sidebar() -> None:
    with (
        ui.left_drawer(top_corner=True, bottom_corner=True).classes("bg-gray-50 border-r"),
        ui.column().classes("p-4 gap-1"),
    ):
        ui.label("Schnapplist").classes("text-lg font-bold text-primary mb-4")
        for label, path, icon in [
            ("Upload", "/", "upload"),
            ("Process", "/process", "settings"),
            ("Review", "/review", "rate_review"),
            ("Post", "/post", "send"),
        ]:
            ui.button(
                label,
                icon=icon,
                on_click=lambda p=path: ui.navigate.to(p),
            ).props("flat align=left").classes("w-full justify-start")


@ui.page("/")
async def page_home(client: Client) -> None:
    await client.connected()
    _nav_sidebar()
    from .pages.home import create

    create(_get_state())


@ui.page("/process")
async def page_process(client: Client) -> None:
    await client.connected()
    _nav_sidebar()
    from .pages.process import create

    create(_get_state())


@ui.page("/review")
async def page_review(client: Client) -> None:
    await client.connected()
    _nav_sidebar()
    from .pages.review import create

    create(_get_state())


@ui.page("/post")
async def page_post(client: Client) -> None:
    await client.connected()
    _nav_sidebar()
    from .pages.post import create

    create(_get_state())


def run() -> None:
    ui.run(
        title="Schnapplist",
        port=8080,
        reload=False,
        storage_secret="schnapplist-secret-change-me",
    )
