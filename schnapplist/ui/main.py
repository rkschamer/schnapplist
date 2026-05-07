"""NiceGUI entry point — schnapplist web UI."""

from __future__ import annotations

from nicegui import app, ui

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
    with ui.left_drawer(top_corner=True, bottom_corner=True).classes(
        "bg-gray-50 border-r"
    ):
        with ui.column().classes("p-4 gap-1"):
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
def page_home() -> None:
    _nav_sidebar()
    from .pages.home import create
    create(_get_state())


@ui.page("/process")
def page_process() -> None:
    _nav_sidebar()
    from .pages.process import create
    create(_get_state())


@ui.page("/review")
def page_review() -> None:
    _nav_sidebar()
    from .pages.review import create
    create(_get_state())


@ui.page("/post")
def page_post() -> None:
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
