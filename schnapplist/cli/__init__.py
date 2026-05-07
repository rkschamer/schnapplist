"""CLI entry point — schnapplist."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core.models import Item

import click
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.rule import Rule
from rich.table import Table

console = Console()


# ---------------------------------------------------------------------------
# RichProgressCallback — translates workflow events to Rich progress display
# ---------------------------------------------------------------------------

class _RichProgressCallback:
    """Drives a Rich Progress context from ProcessWorkflow events."""

    def __init__(self) -> None:
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        )
        self._scan_task_id: Any = None
        self._group_task_id: Any = None
        self._items_task_id: Any = None
        self._item_task_ids: dict[int, Any] = {}
        self._report_task_id: Any = None
        self._total_items: int = 0

        # Add placeholder tasks immediately so the progress bar renders cleanly
        self._scan_task_id = self._progress.add_task("Scanning photos…", total=None)

    def __enter__(self) -> "_RichProgressCallback":
        self._progress.__enter__()
        return self

    def __exit__(self, *args: Any) -> None:
        self._progress.__exit__(*args)

    def __call__(self, event: str, **kwargs: Any) -> None:
        p = self._progress
        if event == "scan_done":
            count = kwargs["count"]
            p.update(
                self._scan_task_id,
                description=f"Found [bold]{count}[/bold] photo(s)",
                total=1,
                completed=1,
            )
            self._group_task_id = p.add_task("Grouping photos by item…", total=None)

        elif event == "group_done":
            count = kwargs["count"]
            self._total_items = count
            if self._group_task_id is not None:
                p.update(
                    self._group_task_id,
                    description=f"Identified [bold]{count}[/bold] item group(s)",
                    total=1,
                    completed=1,
                )
            self._items_task_id = p.add_task("Processing items…", total=count)

        elif event == "item_start":
            idx, total = kwargs["idx"], kwargs["total"]
            task_id = p.add_task(
                f"[cyan]Item {idx}/{total}[/cyan] — starting…",
                total=len(("filter", "enhance", "analyze", "price")),
            )
            self._item_task_ids[idx] = task_id

        elif event == "item_stage":
            idx, stage = kwargs["idx"], kwargs["stage"]
            task_id = self._item_task_ids.get(idx)
            total = self._total_items
            label = f"[cyan]Item {idx}/{total}[/cyan]"
            stage_labels = {
                "filter": "filtering photos…",
                "enhance": "enhancing photos…",
                "analyze": "analyzing item…",
                "image_search": "image search…",
                "web_search": "web search…",
                "price": "researching price…",
            }
            desc = stage_labels.get(stage, f"{stage}…")
            if task_id is not None:
                p.update(task_id, description=f"{label} — {desc}")
                if stage in ("enhance", "analyze", "price"):
                    p.advance(task_id)

        elif event == "item_done":
            idx, name, price = kwargs["idx"], kwargs["name"], kwargs["price"]
            task_id = self._item_task_ids.get(idx)
            total = self._total_items
            if task_id is not None:
                p.update(
                    task_id,
                    description=(
                        f"[cyan]Item {idx}/{total}[/cyan] [green]✓[/green] "
                        f"[bold]{name}[/bold]  {price}"
                    ),
                )
            if self._items_task_id is not None:
                p.advance(self._items_task_id)

        elif event == "report_done":
            if self._report_task_id is None:
                self._report_task_id = p.add_task("Generating report…", total=1)
            p.update(self._report_task_id, description="Report written", completed=1)

        elif event == "warning":
            console.print(f"  [yellow]⚠[/yellow] {kwargs.get('message', '')}")


# ---------------------------------------------------------------------------
# process
# ---------------------------------------------------------------------------

@click.group()
def main() -> None:
    """Snaplist — AI-powered listing creator."""


@main.command()
@click.option(
    "--photos-dir", "-p",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Folder containing your item photos.",
)
@click.option(
    "--output-dir", "-o",
    default="./output",
    type=click.Path(path_type=Path),
    show_default=True,
    help="Where to save enhanced photos and the report.",
)
@click.option(
    "--single-item", "-s",
    is_flag=True,
    default=False,
    help="Treat all photos as one item — skips AI grouping.",
)
@click.option(
    "--llm-provider",
    default=None,
    type=click.Choice(["anthropic", "ollama"]),
    help="LLM backend (default: from schnapplist.toml [llm] provider).",
)
@click.option(
    "--llm-model",
    default=None,
    help="Model name (default: claude-sonnet-4-6 for Anthropic, qwen3:14b for Ollama).",
)
@click.option(
    "--ollama-host",
    default=None,
    help="Ollama API base URL (default: $OLLAMA_HOST or http://localhost:11434).",
)
def process(
    photos_dir: Path,
    output_dir: Path,
    single_item: bool,
    llm_provider: str,
    llm_model: str | None,
    ollama_host: str | None,
) -> None:
    """Analyse photos, identify items, look up prices, and write a Markdown report."""
    from ..services.process_service import run_process

    rich_cb = _RichProgressCallback()
    try:
        with rich_cb:
            result = run_process(
                photos_dir=photos_dir,
                output_dir=output_dir,
                single_item=single_item,
                llm_provider=llm_provider,
                llm_model=llm_model,
                ollama_host=ollama_host,
                on_progress=rich_cb,
            )
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    if result.state.total_photos == 0:
        console.print("[yellow]No supported photos found in directory.[/yellow]")
        return

    report_path = result.report_path
    if report_path is None:
        console.print("[red]Processing did not produce a report.[/red]")
        sys.exit(1)

    console.print(f"\n[bold green]Report:[/bold green] {report_path}")

    if click.confirm("\nReview and edit the report now?", default=True):
        _run_review(report_path)

    console.print(
        "\nWhen ready, run [bold]schnapplist post[/bold] to create listings."
    )


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------

@main.command()
@click.option(
    "--output-dir", "-o",
    default="./output",
    type=click.Path(path_type=Path),
    show_default=True,
)
@click.option(
    "--report",
    default=None,
    type=click.Path(path_type=Path),
    help="Explicit run folder or item file (default: most recent run).",
)
def review(output_dir: Path, report: Path | None) -> None:
    """Open the Markdown report in $EDITOR."""
    from ..workflows.review_pipeline import find_latest_report

    report_path = Path(report) if report else find_latest_report(output_dir)
    if report_path is None:
        console.print("[yellow]No report found. Run 'process' first.[/yellow]")
        sys.exit(1)
    _run_review(report_path)


def _run_review(report_path: Path) -> None:
    """Open report in $EDITOR — accepts a run folder or a single item file."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or _find_fallback_editor()
    if report_path.is_dir():
        files = sorted(
            report_path.glob("item-*.md"),
            key=lambda p: int(p.stem.split("-")[1]),
        )
        paths = [str(f) for f in files] if files else [str(report_path)]
    else:
        paths = [str(report_path)]
    console.print(f"Opening [bold]{report_path}[/bold] in [cyan]{editor}[/cyan] …")
    subprocess.run([editor, *paths], check=False)


def _find_fallback_editor() -> str:
    for candidate in ("nano", "vi", "notepad"):
        result = subprocess.run(
            ["which", candidate],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return candidate
    return "vi"


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@main.command("list")
@click.option(
    "--output-dir", "-o",
    default="./output",
    type=click.Path(path_type=Path),
    show_default=True,
)
def list_items(output_dir: Path) -> None:
    """Show all processed items and their status."""
    from ..services.item_service import list_items as svc_list_items

    items_data = svc_list_items(output_dir)
    if not items_data:
        console.print("[yellow]No items found. Run 'process' first.[/yellow]")
        return

    table = Table(title="Processed Items", show_lines=True)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("Condition")
    table.add_column("Price (EUR)", justify="right")
    table.add_column("Photos", justify="center")
    table.add_column("Approved", justify="center")

    for data in items_data:
        price_raw = data.get("suggested_price")
        price = f"{price_raw:.2f}" if price_raw is not None else "—"
        condition_raw = data.get("condition", "")
        condition = condition_raw.replace("_", " ") if condition_raw else "—"
        approved = "[green]✓[/green]" if data.get("approved") else "[dim]—[/dim]"
        table.add_row(
            data.get("id", ""),
            data.get("name", ""),
            condition,
            price,
            str(len(data.get("photo_paths", []))),
            approved,
        )

    console.print(table)


# ---------------------------------------------------------------------------
# post
# ---------------------------------------------------------------------------

@main.command()
@click.option(
    "--output-dir", "-o",
    default="./output",
    type=click.Path(path_type=Path),
    show_default=True,
)
@click.option(
    "--item-id", "-i",
    required=False,
    default=None,
    help="Item ID to post. If omitted, posts all approved items.",
)
@click.option(
    "--marketplace", "-m",
    type=click.Choice(["kleinanzeigen", "ebay"]),
    default=None,
    help="Override the marketplace set in the report (default: use report value).",
)
@click.option(
    "--schedule",
    default=None,
    help="eBay scheduled start (ISO 8601, e.g. 2026-05-10T18:00:00).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print what would be posted without actually posting.",
)
def post(
    output_dir: Path,
    item_id: str | None,
    marketplace: str | None,
    schedule: str | None,
    dry_run: bool,
) -> None:
    """Post items to a marketplace.

    If --item-id is given, posts that single item.
    Otherwise, posts all approved items (set Approved to true in the report).
    """
    from ..services.posting_service import load_items_from_report, post_item

    try:
        _, items = load_items_from_report(output_dir)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    if item_id:
        targets = [it for it in items if it.id == item_id]
        if not targets:
            console.print(f"[red]Item '{item_id}' not found in report.[/red]")
            sys.exit(1)
    else:
        targets = [it for it in items if it.approved]
        if not targets:
            console.print(
                "[yellow]No approved items to post.[/yellow] "
                "Set [bold]Approved[/bold] to [bold]true[/bold] in the report."
            )
            return

    total = len(targets)
    posted = 0
    failed = 0

    if total > 1:
        console.print(f"\nPosting [bold]{total}[/bold] items…\n")

    for idx, item in enumerate(targets, 1):
        from .config import DEFAULT_MARKETPLACE
        effective_marketplace = marketplace or item.marketplace or DEFAULT_MARKETPLACE

        prefix = f"[dim][{idx}/{total}][/dim] " if total > 1 else ""
        console.print(Rule(
            f"{prefix}[bold]{item.name}[/bold]  →  {effective_marketplace}",
            style="dim",
        ))
        _print_item_details(item, effective_marketplace)

        result = post_item(item, effective_marketplace, schedule=schedule, dry_run=dry_run)

        if result.dry_run:
            _print_dry_run_summary(result.dry_run_summary or {})
            posted += 1
        elif result.success:
            console.print(f"\n[bold green]✓ Posted:[/bold green] {result.url}\n")
            posted += 1
        else:
            console.print(f"\n[red]Error posting '{item.name}':[/red] {result.error}\n")
            failed += 1

    if total > 1:
        console.print(Rule(style="dim"))
        console.print(f"[bold]Done:[/bold] {posted} posted, {failed} failed.")


def _print_item_details(item: Item, marketplace: str) -> None:
    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_column(style="dim", no_wrap=True)
    t.add_column()
    t.add_row("Title", item.title_de or item.name)
    if item.price_info:
        t.add_row("Price", f"{item.price_info.suggested_price:.2f} EUR")
    t.add_row("Condition", item.condition.value.replace("_", " "))
    t.add_row("Photos", str(len(item.photos)))
    if marketplace == "ebay" and item.ebay_options:
        opts = item.ebay_options
        t.add_row("Listing", f"{opts.listing_type.value}  ·  {opts.duration_days} days")
    console.print(t)
    console.print()


def _print_dry_run_summary(summary: dict) -> None:
    console.print("[yellow][DRY RUN][/yellow] Would post:")
    for key, val in summary.items():
        console.print(f"  {key.replace('_', ' ').title()}: {val}")


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

@main.group()
def config() -> None:
    """Manage schnapplist configuration."""


@config.command("init")
@click.option("--force", is_flag=True, default=False, help="Overwrite existing config file.")
def config_init(force: bool) -> None:
    """Write a starter config.toml to the user config directory."""
    from .config import TOML_USER_PATH

    if TOML_USER_PATH.exists() and not force:
        console.print(
            f"[yellow]Config already exists:[/yellow] {TOML_USER_PATH}\n"
            "Run with [bold]--force[/bold] to overwrite."
        )
        return

    template = Path(__file__).parent.parent / "schnapplist.toml"
    if not template.exists():
        console.print("[red]Error:[/red] schnapplist.toml template not found in package root.")
        return

    TOML_USER_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOML_USER_PATH.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    console.print(f"[green]Config created:[/green] {TOML_USER_PATH}")


@config.command("show")
def config_show() -> None:
    """Show which config file is active and its resolved settings."""
    from .config import (
        CLAUDE_MODEL,
        DEFAULT_MARKETPLACE,
        LISTING_DISCLAIMER,
        LLM_PROVIDER,
        OLLAMA_HOST,
        OLLAMA_MODEL,
        TOML_USER_PATH,
        _find_toml,
    )

    active = _find_toml()
    if active:
        console.print(f"[bold]Active config:[/bold] {active}")
    else:
        console.print(
            "[yellow]No config file found.[/yellow] "
            "Run [bold]schnapplist config init[/bold] to create one at:"
        )
        console.print(f"  {TOML_USER_PATH}")
        console.print("Using built-in defaults.")

    console.print()
    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_column(style="bold")
    t.add_column()
    t.add_row("[llm] provider", LLM_PROVIDER)
    t.add_row("[llm] model", CLAUDE_MODEL if LLM_PROVIDER == "anthropic" else OLLAMA_MODEL)
    if LLM_PROVIDER == "ollama":
        t.add_row("[llm] ollama_host", OLLAMA_HOST)
    t.add_row("[listing] default_marketplace", DEFAULT_MARKETPLACE)
    disclaimer_preview = (
        (LISTING_DISCLAIMER[:60] + "…")
        if len(LISTING_DISCLAIMER) > 60
        else (LISTING_DISCLAIMER or "—")
    )
    t.add_row("[listing] disclaimer", disclaimer_preview)
    console.print(t)
