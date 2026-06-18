"""CLI entry point — Click commands and Rich terminal display.

No business logic lives here. Commands delegate to services/:
  process  →  services.process_service.run_process
  post     →  services.posting_service.post_item
  list     →  services.item_service.list_items
  review   →  opens $EDITOR on the run folder
  export   →  providers.ebay_csv_exporter.export_to_csv
  config   →  reads config.py constants
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...core.models import Item

import click
from rich.console import Console
from rich.rule import Rule
from rich.table import Table

from .display import RichDecisionCallback, RichLiveCallback

console = Console()


def _configure_debug_logging() -> None:
    """Write agent DEBUG traces to schnapplist-debug.log when SCHNAPPLIST_DEBUG=1."""
    if os.getenv("SCHNAPPLIST_DEBUG") != "1":
        return
    handler = logging.FileHandler("schnapplist-debug.log", mode="w")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger = logging.getLogger("schnapplist.agents")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.propagate = False  # don't bubble up to root logger


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
    "--marketplace", "-m",
    default=None,
    type=click.Choice(["kleinanzeigen", "ebay"]),
    help="Override the default marketplace for all items in this report.",
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
    marketplace: str | None,
    llm_provider: str,
    llm_model: str | None,
    ollama_host: str | None,
) -> None:
    """Analyse photos, identify items, look up prices, and write a Markdown report."""
    _configure_debug_logging()
    from ...services.process_service import run_process

    rich_cb = RichLiveCallback()
    decision_cb = RichDecisionCallback(rich_cb)
    try:
        with rich_cb:
            result = run_process(
                photos_dir=photos_dir,
                output_dir=output_dir,
                single_item=single_item,
                marketplace=marketplace,
                llm_provider=llm_provider,
                llm_model=llm_model,
                ollama_host=ollama_host,
                on_progress=rich_cb,
                on_decision=decision_cb,
            )

            if result.state.total_photos == 0:
                rich_cb.print("[yellow]No supported photos found in directory.[/yellow]")
                return

            report_path = result.report_path
            if report_path is None:
                rich_cb.print("[red]Processing did not produce a report.[/red]")
                sys.exit(1)

            rich_cb.print(f"\n[bold green]Report:[/bold green] {report_path}")

            item_paths = sorted(
                report_path.glob("item-*.md"),
                key=lambda p: int(p.stem.split("-")[1]),
            )
            decision_cb("report_ready", report_path=report_path, item_paths=item_paths)

            from ...providers.ebay_csv_exporter import export_to_csv
            from ...services.posting_service import items_from_report_path

            items = items_from_report_path(report_path)
            ebay_items = [it for it in items if it.marketplace == "ebay"]
            approved_ebay = [it for it in ebay_items if it.approved]

            if approved_ebay:
                choice = decision_cb(
                    "ebay_export_prompt",
                    approved_count=len(approved_ebay),
                    total_ebay_count=len(ebay_items),
                )
                if choice == "yes":
                    csv_path = report_path / "ebay-export.csv"
                    count = export_to_csv(items, csv_path)
                    if count == 0:
                        rich_cb.print("[yellow]No approved eBay items — CSV not written.[/yellow]")
                    else:
                        rich_cb.print(
                            f"\n[bold green]eBay CSV written:[/bold green] {csv_path}  "
                            f"([bold]{count}[/bold] approved item(s))"
                        )

    except (ValueError, RuntimeError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

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
    from ...services.item_service import find_latest_report

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
    from ...services.item_service import list_items as svc_list_items

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
    from ...services.posting_service import load_items_from_report, post_item

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
        from ...config import DEFAULT_MARKETPLACE
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
# export
# ---------------------------------------------------------------------------

@main.group()
def export() -> None:
    """Export items to external formats."""


@export.command("ebay")
@click.option(
    "--output-dir", "-o",
    default="./output",
    type=click.Path(path_type=Path),
    show_default=True,
    help="Root output directory to search for the latest run.",
)
@click.option(
    "--run-dir",
    default=None,
    type=click.Path(path_type=Path),
    help="Explicit run folder (default: most recent in --output-dir).",
)
@click.option(
    "--output",
    default=None,
    type=click.Path(path_type=Path),
    help="Output CSV path (default: <run-dir>/ebay-export.csv).",
)
def export_ebay(output_dir: Path, run_dir: Path | None, output: Path | None) -> None:
    """Generate an eBay draft listing CSV for bulk upload."""
    from ...providers.ebay_csv_exporter import export_to_csv
    from ...services.posting_service import items_from_report_path
    from ...services.item_service import find_latest_report

    report_path = Path(run_dir) if run_dir else find_latest_report(output_dir)

    if report_path is None:
        console.print("[yellow]No report found. Run 'process' first.[/yellow]")
        sys.exit(1)

    try:
        items = items_from_report_path(report_path)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    csv_path = Path(output) if output else report_path / "ebay-export.csv"
    count = export_to_csv(items, csv_path)
    if count == 0:
        console.print("[yellow]No approved eBay items found — nothing to export.[/yellow]")
    else:
        console.print(
            f"[bold green]eBay CSV written:[/bold green] {csv_path}  "
            f"([bold]{count}[/bold] approved item(s))"
        )


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
    from ...config import TOML_USER_PATH

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
    from ...config import (
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
