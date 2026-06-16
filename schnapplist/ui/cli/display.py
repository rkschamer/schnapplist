"""Rich Live/Layout display for the schnapplist CLI."""

from __future__ import annotations

import sys
import termios
import time
import tty
from dataclasses import dataclass, field
from typing import Any, Literal

from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

_TOOL_STAGES = {"analyze_photos", "web_search"}
_MAX_TOOL_LOG = 5


# ---------------------------------------------------------------------------
# State dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ToolLogEntry:
    tool: str


@dataclass
class ItemRow:
    idx: int
    total: int
    status: Literal["queued", "active", "done", "failed", "skipped"] = "queued"
    stage: str = ""
    name: str = ""
    price: str = ""
    confidence: float = 1.0
    low_confidence: bool = False


@dataclass
class RunState:
    photo_count: int = 0
    group_count: int = 0
    scan_done: bool = False
    group_done: bool = False
    total_items: int = 0
    completed_items: int = 0
    start_time: float = field(default_factory=time.monotonic)
    items: dict[int, ItemRow] = field(default_factory=dict)
    active_idx: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_tokens: int = 0
    requests: int = 0
    tool_calls: int = 0
    gen_secs: float = 0.0
    tool_log: list[ToolLogEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# State mutation
# ---------------------------------------------------------------------------

def apply_event(state: RunState, event: str, **kwargs: Any) -> None:
    if event == "scan_done":
        state.photo_count = kwargs["count"]
        state.scan_done = True

    elif event == "group_done":
        state.group_count = kwargs["count"]
        state.total_items = kwargs["count"]
        state.group_done = True

    elif event == "item_start":
        idx, total = kwargs["idx"], kwargs["total"]
        state.items[idx] = ItemRow(idx=idx, total=total, status="active")
        state.active_idx = idx

    elif event == "item_stage":
        idx, stage = kwargs["idx"], kwargs["stage"]
        if idx in state.items:
            state.items[idx].stage = stage
        if stage in _TOOL_STAGES:
            state.tool_calls += 1
            state.tool_log.append(ToolLogEntry(tool=stage))
            if len(state.tool_log) > _MAX_TOOL_LOG:
                state.tool_log = state.tool_log[-_MAX_TOOL_LOG:]

    elif event == "item_done":
        idx = kwargs["idx"]
        if idx in state.items:
            state.items[idx].status = "done"
            state.items[idx].name = kwargs["name"]
            state.items[idx].price = kwargs["price"]
            state.items[idx].confidence = kwargs.get("confidence", 1.0)
            state.items[idx].low_confidence = kwargs.get("low_confidence", False)
            state.completed_items += 1
            if state.active_idx == idx:
                state.active_idx = None

    elif event == "item_usage":
        state.input_tokens += kwargs.get("input_tokens", 0)
        out = kwargs.get("output_tokens", 0)
        state.output_tokens += out
        state.cache_tokens += kwargs.get("cache_read_tokens", 0)
        state.requests += kwargs.get("requests", 0)
        state.gen_secs += kwargs.get("gen_secs", 0.0)

    elif event == "warning":
        idx = kwargs.get("idx")
        if idx is not None and idx in state.items:
            state.items[idx].status = "skipped"
            if state.active_idx == idx:
                state.active_idx = None


# ---------------------------------------------------------------------------
# Render functions
# ---------------------------------------------------------------------------

def _render_header(state: RunState) -> Panel:
    elapsed = time.monotonic() - state.start_time
    elapsed_str = _fmt_elapsed(elapsed)

    scan_text = (
        f"[green]✓[/green] Scan  [bold]{state.photo_count}[/bold] photos"
        if state.scan_done
        else "[dim]Scanning…[/dim]"
    )
    group_text = (
        f"[green]✓[/green] Group  [bold]{state.group_count}[/bold] items"
        if state.group_done
        else ("[dim]Grouping…[/dim]" if state.scan_done else "[dim]—[/dim]")
    )

    if state.total_items > 0:
        pct = state.completed_items / state.total_items
        bar_width = 20
        filled = int(bar_width * pct)
        bar = "[green]" + "█" * filled + "[/green]" + "[dim]" + "░" * (bar_width - filled) + "[/dim]"
        overall_text = (
            f"Overall  {bar}  "
            f"[bold]{state.completed_items}/{state.total_items}[/bold]  {elapsed_str}"
        )
    else:
        overall_text = f"[dim]Overall  —  {elapsed_str}[/dim]"

    content = Text.assemble(
        Text.from_markup(scan_text),
        "    ",
        Text.from_markup(group_text),
        "    ",
        Text.from_markup(overall_text),
    )
    return Panel(content, title="[bold blue]Schnapplist[/bold blue]", height=3)


def _render_items(state: RunState) -> Panel:
    table = Table(show_header=False, box=None, padding=(0, 1), expand=True)
    table.add_column(width=2)
    table.add_column(width=6)
    table.add_column()
    table.add_column(width=12, justify="right")

    for idx in sorted(state.items):
        row = state.items[idx]
        label = f"{row.idx}/{row.total}"

        if row.status == "done":
            if row.low_confidence:
                icon = "[yellow]⚠[/yellow]"
                name_cell = Text(row.name, style="bold")
                price_cell = Text.from_markup(f"[yellow]{row.price}  [dim](conf: {row.confidence:.2f})[/dim]")
            else:
                icon = "[green]✓[/green]"
                name_cell = Text(row.name, style="bold")
                price_cell = Text(row.price, style="green")
        elif row.status in ("skipped", "failed"):
            icon = "[red]✗[/red]"
            name_cell = Text(row.name or "skipped", style="dim")
            price_cell = Text("")
        elif row.status == "active":
            icon = "[cyan]⠸[/cyan]"
            stage_label = row.stage.replace("_", " ") + "…" if row.stage else "starting…"
            name_cell = Text(stage_label, style="cyan")
            price_cell = Text("")
        else:
            icon = " "
            name_cell = Text("queued", style="dim")
            price_cell = Text("")

        table.add_row(icon, label, name_cell, price_cell)

    if not state.items:
        table.add_row("", "", Text("Waiting for items…", style="dim"), Text(""))

    return Panel(table, title="[bold]Items[/bold]")


def _render_llm(state: RunState) -> Panel:
    lines: list[str] = []

    if state.active_idx is not None:
        row = state.items.get(state.active_idx)
        stage = row.stage.replace("_", " ") if row and row.stage else "—"
        lines.append(f"[bold]Item {state.active_idx}/{state.total_items}[/bold]")
        lines.append(f"Stage: [cyan]{stage}[/cyan]")
        lines.append("")

    lines.append(f"Requests:   [bold]{state.requests}[/bold]")
    lines.append(f"Tool calls: [bold]{state.tool_calls}[/bold]")
    lines.append("")
    lines.append(f"↑ in    [bold]{state.input_tokens:,}[/bold] tokens")
    lines.append(f"↓ out   [bold]{state.output_tokens:,}[/bold] tokens")
    lines.append(f"◈ cache [bold]{state.cache_tokens:,}[/bold] tokens")

    if state.output_tokens > 0 and state.gen_secs > 0:
        tps = state.output_tokens / state.gen_secs
        lines.append(f"⚡ [bold]{tps:.1f}[/bold] tok/s")

    if state.tool_log:
        lines.append("")
        lines.append("[dim]Recent tool calls:[/dim]")
        for entry in state.tool_log:
            lines.append(f"  [dim]{entry.tool}[/dim]")

    content = Text.from_markup("\n".join(lines))
    return Panel(content, title="[bold]LLM Activity[/bold]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_elapsed(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _make_layout() -> Layout:
    layout = Layout(name="root")
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
    )
    layout["body"].split_row(
        Layout(name="items", ratio=2),
        Layout(name="llm", ratio=1),
    )
    return layout


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

class RichLiveCallback:
    """Drives a Rich Live/Layout display from ProcessWorkflow events."""

    def __init__(self) -> None:
        self._state = RunState()
        self._layout = _make_layout()
        self._live = Live(
            self._layout,
            console=console,
            auto_refresh=False,
            transient=False,
        )

    def __enter__(self) -> RichLiveCallback:
        self._live.__enter__()
        return self

    def __exit__(self, *args: Any) -> None:
        self._live.__exit__(*args)

    def stop(self) -> None:
        self._live.stop()

    def start(self) -> None:
        self._live.start()

    def show_modal(self, renderable: Any) -> None:
        """Replace the body with a centered modal and refresh once."""
        self._layout["body"].update(Align.center(renderable, vertical="middle"))
        self._live.refresh()

    def restore_body(self) -> None:
        """Restore the normal two-column body after a modal."""
        self._layout["body"].update(self._layout["body"])
        # Re-split body into items/llm columns
        self._layout["body"].split_row(
            Layout(name="items", ratio=2),
            Layout(name="llm", ratio=1),
        )
        self._layout["items"].update(_render_items(self._state))
        self._layout["llm"].update(_render_llm(self._state))
        self._live.refresh()

    def __call__(self, event: str, **kwargs: Any) -> None:
        apply_event(self._state, event, **kwargs)
        self._layout["header"].update(_render_header(self._state))
        self._layout["items"].update(_render_items(self._state))
        self._layout["llm"].update(_render_llm(self._state))
        self._live.refresh()


class RichDecisionCallback:
    """Prompts the user via Rich when an item fails."""

    def __init__(self, progress_cb: RichLiveCallback) -> None:
        self._progress_cb = progress_cb

    def __call__(self, event: str, **kwargs: Any) -> str:
        if event == "item_failed":
            idx = kwargs.get("idx", "?")
            name = kwargs.get("name", "unknown")
            error = kwargs.get("error", "")
            modal = Panel(
                Text.assemble(
                    (str(error), "dim"),
                    "\n\n",
                    ("  ", ""),
                    ("[ r ]", "bold black on yellow"),
                    ("  retry      ", ""),
                    ("[ s ]", "bold black on white"),
                    ("  skip", ""),
                    "\n\n",
                    ("  press a key…", "dim italic"),
                ),
                title=Text.assemble(
                    ("⚠  Item ", "bold yellow"),
                    (str(idx), "bold yellow"),
                    (" — ", "yellow"),
                    (str(name), "bold"),
                ),
                border_style="yellow",
                width=60,
                padding=(1, 2),
            )
            self._progress_cb.show_modal(modal)
            try:
                choice = _read_single_key({"r", "s"}, default="s")
            finally:
                self._progress_cb.restore_body()
            return "retry" if choice == "r" else "skip"
        return "skip"


def _read_single_key(allowed: set[str], default: str) -> str:
    """Read a single keypress from stdin in raw mode without echoing.

    Returns the lowercased key if it's in `allowed`, otherwise `default`.
    Falls back to the default if stdin isn't a TTY.
    """
    if not sys.stdin.isatty():
        return default
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1).lower()
            if ch in ("\x03", "\x04"):  # Ctrl-C / Ctrl-D
                raise KeyboardInterrupt
            if ch in ("\r", "\n"):
                return default
            if ch in allowed:
                return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
