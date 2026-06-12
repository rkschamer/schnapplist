"""Rich Live/Layout display for the schnapplist CLI."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

import click
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
    first_output_time: float | None = None
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
            state.tool_log.append(ToolLogEntry(tool=stage))
            if len(state.tool_log) > _MAX_TOOL_LOG:
                state.tool_log = state.tool_log[-_MAX_TOOL_LOG:]

    elif event == "item_done":
        idx = kwargs["idx"]
        if idx in state.items:
            state.items[idx].status = "done"
            state.items[idx].name = kwargs["name"]
            state.items[idx].price = kwargs["price"]
        state.completed_items += 1
        if state.active_idx == idx:
            state.active_idx = None

    elif event == "item_usage":
        state.input_tokens += kwargs.get("input_tokens", 0)
        out = kwargs.get("output_tokens", 0)
        state.output_tokens += out
        state.cache_tokens += kwargs.get("cache_read_tokens", 0)
        state.requests += kwargs.get("requests", 0)
        state.tool_calls += kwargs.get("tool_calls", 0)
        if out > 0 and state.first_output_time is None:
            state.first_output_time = time.monotonic()

    elif event == "warning":
        idx = kwargs.get("idx")
        if idx is not None and idx in state.items:
            state.items[idx].status = "skipped"


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
            icon = "[green]✓[/green]"
            name_cell = f"[bold]{row.name}[/bold]"
            price_cell = f"[green]{row.price}[/green]"
        elif row.status in ("skipped", "failed"):
            icon = "[red]✗[/red]"
            name_cell = f"[dim]{row.name or 'skipped'}[/dim]"
            price_cell = ""
        elif row.status == "active":
            icon = "[cyan]⠸[/cyan]"
            stage_label = row.stage.replace("_", " ") + "…" if row.stage else "starting…"
            name_cell = f"[cyan]{stage_label}[/cyan]"
            price_cell = ""
        else:
            icon = " "
            name_cell = "[dim]queued[/dim]"
            price_cell = ""

        table.add_row(icon, label, name_cell, price_cell)

    if not state.items:
        table.add_row("", "", "[dim]Waiting for items…[/dim]", "")

    return Panel(table, title="[bold]Items[/bold]")


def _render_llm(state: RunState) -> Panel:
    elapsed = time.monotonic() - state.start_time

    lines: list[str] = []

    if state.active_idx is not None:
        row = state.items.get(state.active_idx)
        stage = row.stage.replace("_", " ") if row and row.stage else "—"
        lines.append(f"[bold]Item {state.active_idx}/{state.total_items}[/bold]")
        lines.append(f"Stage: [cyan]{stage}[/cyan]")
        lines.append("")

    lines.append(f"Requests:   [bold]{state.requests}[/bold] / 10")
    lines.append(f"Tool calls: [bold]{state.tool_calls}[/bold]")
    lines.append("")
    lines.append(f"↑ in    [bold]{state.input_tokens:,}[/bold] tokens")
    lines.append(f"↓ out   [bold]{state.output_tokens:,}[/bold] tokens")
    lines.append(f"◈ cache [bold]{state.cache_tokens:,}[/bold] tokens")

    if state.output_tokens > 0 and elapsed > 0:
        tps = state.output_tokens / elapsed
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
            refresh_per_second=4,
            transient=False,
        )

    def __enter__(self) -> "RichLiveCallback":
        self._live.__enter__()
        return self

    def __exit__(self, *args: Any) -> None:
        self._live.__exit__(*args)

    def stop(self) -> None:
        self._live.stop()

    def start(self) -> None:
        self._live.start()

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
            self._progress_cb.stop()
            console.print(
                f"\n[yellow]⚠[/yellow] Agent failed for item {idx} "
                f"([bold]{name}[/bold]): {error}"
            )
            choice = click.prompt(
                "  What would you like to do?",
                type=click.Choice(["r", "s"], case_sensitive=False),
                default="s",
                show_choices=False,
                prompt_suffix=" ([r]etry / [s]kip) ",
            )
            self._progress_cb.start()
            return "retry" if choice == "r" else "skip"
        return "skip"
