"""CLI entry point — schnapplist."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Item, Photo

import click
from rich.console import Console
from rich.rule import Rule
from rich.table import Table

console = Console()


def _require_anthropic_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        console.print(
            "[red]Error:[/red] ANTHROPIC_API_KEY is not set.\n"
            "Add it to a [bold].env[/bold] file or export it in your shell."
        )
        sys.exit(1)
    return key


@click.group()
def main() -> None:
    """Snaplist — AI-powered listing creator."""


# ---------------------------------------------------------------------------
# process
# ---------------------------------------------------------------------------

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
    from .config import CLAUDE_MODEL, LLM_PROVIDER, OLLAMA_HOST, OLLAMA_MODEL
    from .llm import LLMClient
    from .workflows.process_pipeline import ProcessWorkflow

    provider = llm_provider or LLM_PROVIDER

    if provider == "anthropic":
        api_key = _require_anthropic_key()
        model = llm_model or CLAUDE_MODEL
        client = LLMClient("anthropic", model, api_key=api_key)
    else:
        model = llm_model or OLLAMA_MODEL
        host = ollama_host or OLLAMA_HOST
        client = LLMClient("ollama", model, ollama_host=host)
        console.print(f"Using Ollama [bold]{model}[/bold] at [cyan]{host}[/cyan]")
    workflow = ProcessWorkflow(client, console=console)
    result = workflow.run(
        photos_dir=photos_dir,
        output_dir=output_dir,
        single_item=single_item,
    )

    if result.state.total_photos == 0:
        console.print("[yellow]No supported photos found in directory.[/yellow]")
        return

    report_path = result.report_path
    if report_path is None:
        console.print("[red]Processing did not produce a report.[/red]")
        sys.exit(1)

    console.print(f"\n[bold green]Report:[/bold green] {report_path}")

    # Offer inline review
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
    from .workflows.review_pipeline import find_latest_report

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
    from .report_parser import parse_report
    from .workflows.review_pipeline import find_latest_report

    report_path = find_latest_report(output_dir)
    if not report_path:
        console.print("[yellow]No items found. Run 'process' first.[/yellow]")
        return

    items_data = parse_report(report_path)

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

    Items are read directly from the latest Markdown report.
    """
    from .config import DEFAULT_MARKETPLACE
    from .models import Item, ItemCondition
    from .providers import MARKETPLACES
    from .report_parser import parse_report
    from .workflows.review_pipeline import find_latest_report

    latest_report = find_latest_report(output_dir)
    if not latest_report:
        console.print("[red]No report found. Run 'process' first.[/red]")
        sys.exit(1)

    parsed_items = parse_report(latest_report)
    if not parsed_items:
        console.print("[red]No items found in report.[/red]")
        sys.exit(1)

    # Build Item objects from parsed report data
    items: list[Item] = []
    for data in parsed_items:
        photos = _resolve_photos(data.pop("photo_paths", []), latest_report)
        price_info = None
        if "suggested_price" in data:
            from .models import PriceInfo

            price_info = PriceInfo(
                suggested_price=data.pop("suggested_price"),
                min_price=0,
                max_price=0,
                reasoning="",
            )
        ebay_options = None
        if "ebay_options" in data:
            from .models import EbayListingOptions

            ebay_options = EbayListingOptions(**data.pop("ebay_options"))

        ka_options = None
        if "ka_options" in data:
            from .models import KleinanzeigenListingOptions

            ka_options = KleinanzeigenListingOptions(**data.pop("ka_options"))

        condition = data.pop("condition", "good")
        items.append(
            Item(
                id=data.get("id", ""),
                name=data.get("name", ""),
                title_de=data.get("title_de", ""),
                description=data.get("description", ""),
                condition=ItemCondition(condition),
                photos=photos,
                price_info=price_info,
                approved=data.get("approved", False),
                tags=data.get("tags", []),
                category=data.get("category"),
                brand=data.get("brand"),
                model=data.get("model"),
                marketplace=data.get("marketplace"),
                ebay_options=ebay_options,
                ka_options=ka_options,
            )
        )

    # Determine which items to post
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

    posted = 0
    failed = 0
    total = len(targets)

    if total > 1:
        console.print(f"\nPosting [bold]{total}[/bold] items…\n")

    for idx, item in enumerate(targets, 1):
        effective_marketplace = (
            marketplace or item.marketplace or DEFAULT_MARKETPLACE
        )

        if schedule and item.ebay_options:
            item.ebay_options.scheduled_start = datetime.fromisoformat(schedule)

        if dry_run:
            _print_dry_run(item, effective_marketplace)
            posted += 1
            continue

        mkt = MARKETPLACES.get(effective_marketplace)
        if mkt is None:
            console.print(
                f"[red]Unknown marketplace '{effective_marketplace}' "
                f"for item '{item.name}'.[/red]"
            )
            failed += 1
            continue

        if not mkt.is_available():
            console.print(
                f"[red]Marketplace '{effective_marketplace}' "
                "is not configured.[/red]"
            )
            failed += 1
            continue

        prefix = f"[dim][{idx}/{total}][/dim] " if total > 1 else ""
        console.print(Rule(
            f"{prefix}[bold]{item.name}[/bold]  →  {effective_marketplace}",
            style="dim",
        ))
        _print_item_details(item, effective_marketplace)

        try:
            url = mkt.post_listing(
                item,
                item.ebay_options if effective_marketplace == "ebay" else None,
            )
            console.print(f"\n[bold green]✓ Posted:[/bold green] {url}\n")
            posted += 1
        except (RuntimeError, NotImplementedError) as exc:
            console.print(f"\n[red]Error posting '{item.name}':[/red] {exc}\n")
            failed += 1

    if total > 1:
        console.print(Rule(style="dim"))
        console.print(f"[bold]Done:[/bold] {posted} posted, {failed} failed.")


def _resolve_photos(paths: list[str], output_dir: Path) -> list[Photo]:
    """Convert relative photo paths from the report to Photo objects."""
    from .models import Photo as _Photo

    photos: list[_Photo] = []
    for p in paths:
        resolved = (output_dir / p).resolve()
        if "enhanced" in p:
            photos.append(_Photo(original_path=resolved, enhanced_path=resolved))
        else:
            photos.append(_Photo(original_path=resolved))
    return photos


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


def _print_dry_run(item: Item, marketplace: str) -> None:
    ebay_opts = item.ebay_options
    console.print(f"[yellow][DRY RUN][/yellow] Would post to [bold]{marketplace}[/bold]:")
    console.print(f"  Title:     {item.title_de or item.name}")
    console.print(f"  Condition: {item.condition.to_german()}")
    if item.price_info:
        console.print(f"  Price:     {item.price_info.suggested_price:.2f} EUR")
    console.print(f"  Photos:    {len(item.photos)}")
    if marketplace == "ebay" and ebay_opts:
        console.print(f"  Listing type: {ebay_opts.listing_type.value}")
        if ebay_opts.reserve_price:
            console.print(f"  Reserve:      {ebay_opts.reserve_price:.2f} EUR")
        console.print(f"  Duration:     {ebay_opts.duration_days} days")
        if ebay_opts.scheduled_start:
            console.print(f"  Scheduled:    {ebay_opts.scheduled_start.isoformat()}")


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
