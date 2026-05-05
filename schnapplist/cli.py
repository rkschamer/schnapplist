"""CLI entry point — schnapplist."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
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
    help="Model name (default: claude-sonnet-4-6 for Anthropic, $OLLAMA_MODEL or qwen3:14b for Ollama).",
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
    from .orchestration import ProcessOrchestrator

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
    orchestrator = ProcessOrchestrator(client, console=console)
    result = orchestrator.run(
        photos_dir=photos_dir,
        output_dir=output_dir,
        single_item=single_item,
    )

    if result.state.total_photos == 0:
        console.print("[yellow]No supported photos found in directory.[/yellow]")
        return

    report_path = result.report_path
    state_file = result.state_file
    if report_path is None or state_file is None:
        console.print("[red]Processing did not produce output files.[/red]")
        sys.exit(1)

    console.print(f"\n[bold green]Report:[/bold green] {report_path}")
    console.print(f"[bold green]State:[/bold green]  {state_file}")

    # Offer inline review
    if click.confirm("\nReview and edit the report now?", default=True):
        _run_review(output_dir, report_path)

    console.print(
        "\nWhen ready, run [bold]schnapplist post[/bold] to create listings."
    )


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------

@main.command()
@click.option("--output-dir", "-o", default="./output", type=click.Path(path_type=Path), show_default=True)
@click.option("--report", default=None, type=click.Path(path_type=Path), help="Explicit report path (default: most recent).")
def review(output_dir: Path, report: Path | None) -> None:
    """Open the Markdown report in $EDITOR and sync edits back to items.json."""
    report_path = Path(report) if report else _find_latest_report(output_dir)
    if report_path is None:
        console.print("[yellow]No report found. Run 'process' first.[/yellow]")
        sys.exit(1)
    _run_review(output_dir, report_path)


def _find_latest_report(output_dir: Path) -> Path | None:
    candidates = sorted(output_dir.glob("schnapplist_report_*.md"), reverse=True)
    return candidates[0] if candidates else None


def _run_review(output_dir: Path, report_path: Path) -> None:
    """Open report in $EDITOR, then parse edits back to items.json."""
    from .report_parser import parse_report

    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or _find_fallback_editor()
    console.print(f"Opening [bold]{report_path}[/bold] in [cyan]{editor}[/cyan] …")
    subprocess.run([editor, str(report_path)], check=False)

    state_file = output_dir / "items.json"
    if not state_file.exists():
        console.print("[yellow]items.json not found — cannot sync edits.[/yellow]")
        return

    diffs = parse_report(report_path)
    if not diffs:
        console.print("[yellow]No parseable item sections found in report.[/yellow]")
        return

    items_data: list[dict] = json.loads(state_file.read_text(encoding="utf-8"))
    index = {d["id"]: d for d in items_data}
    changed = 0

    for diff in diffs:
        item_id = diff.get("id")
        if not item_id or item_id not in index:
            continue
        existing = index[item_id]
        for key, new_val in diff.items():
            if key == "id":
                continue
            if key == "suggested_price":
                if (
                    "price_info" in existing
                    and existing["price_info"]
                    and existing["price_info"].get("suggested_price") != new_val
                ):
                    existing["price_info"]["suggested_price"] = new_val
                    changed += 1
            elif key == "ebay_options":
                # Merge into existing ebay_options sub-dict
                if "ebay_options" not in existing or not existing["ebay_options"]:
                    existing["ebay_options"] = {}
                for opt_key, opt_val in new_val.items():
                    if existing["ebay_options"].get(opt_key) != opt_val:
                        existing["ebay_options"][opt_key] = opt_val
                        changed += 1
            elif existing.get(key) != new_val:
                existing[key] = new_val
                changed += 1

    state_file.write_text(
        json.dumps(items_data, indent=2, default=str),
        encoding="utf-8",
    )
    console.print(f"[green]✓[/green] Synced [bold]{changed}[/bold] field change(s) to {state_file}")


def _find_fallback_editor() -> str:
    for candidate in ("nano", "vi", "notepad"):
        result = subprocess.run(["which", candidate], capture_output=True, text=True)
        if result.returncode == 0:
            return candidate
    return "vi"


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@main.command("list")
@click.option("--output-dir", "-o", default="./output", type=click.Path(path_type=Path), show_default=True)
def list_items(output_dir: Path) -> None:
    """Show all processed items and their status."""
    from .models import Item

    state_file = output_dir / "items.json"
    if not state_file.exists():
        console.print("[yellow]No items found. Run 'process' first.[/yellow]")
        return

    items_data: list[dict] = json.loads(state_file.read_text(encoding="utf-8"))

    table = Table(title="Processed Items", show_lines=True)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("Condition")
    table.add_column("Price (EUR)", justify="right")
    table.add_column("Photos", justify="center")
    table.add_column("Approved", justify="center")

    for data in items_data:
        item = Item.model_validate(data)
        price = f"{item.price_info.suggested_price:.2f}" if item.price_info else "—"
        approved = "[green]✓[/green]" if item.approved else "[dim]—[/dim]"
        table.add_row(
            item.id,
            item.name,
            item.condition.value.replace("_", " "),
            price,
            str(len(item.photos)),
            approved,
        )

    console.print(table)


# ---------------------------------------------------------------------------
# post
# ---------------------------------------------------------------------------

@main.command()
@click.option("--output-dir", "-o", default="./output", type=click.Path(path_type=Path), show_default=True)
@click.option("--item-id", "-i", required=True, help="Item ID to post (see 'list' command).")
@click.option(
    "--marketplace", "-m",
    type=click.Choice(["kleinanzeigen", "ebay"]),
    default=None,
    help="Override the marketplace set in the report (default: use report value).",
)
@click.option("--schedule", default=None, help="eBay scheduled start (ISO 8601, e.g. 2026-05-10T18:00:00).")
@click.option("--dry-run", is_flag=True, default=False, help="Print what would be posted without actually posting.")
def post(
    output_dir: Path,
    item_id: str,
    marketplace: str | None,
    schedule: str | None,
    dry_run: bool,
) -> None:
    """Post an item to a marketplace.

    Marketplace and eBay options (listing type, duration, reserve price) are
    read from items.json (set by the LLM and editable in the Markdown report).
    Use --marketplace to override the marketplace from the report.
    """
    from .models import Item
    from .providers import MARKETPLACES

    state_file = output_dir / "items.json"
    if not state_file.exists():
        console.print("[red]No items.json found. Run 'process' first.[/red]")
        sys.exit(1)

    items_data: list[dict] = json.loads(state_file.read_text(encoding="utf-8"))
    item_data = next((d for d in items_data if d["id"] == item_id), None)
    if not item_data:
        console.print(f"[red]Item '{item_id}' not found.[/red]")
        sys.exit(1)

    from .config import DEFAULT_MARKETPLACE
    item = Item.model_validate(item_data)

    # Resolve marketplace: CLI flag overrides report value
    effective_marketplace = marketplace or item.marketplace or DEFAULT_MARKETPLACE

    # Apply schedule override to eBay options if provided
    if schedule and item.ebay_options:
        item.ebay_options.scheduled_start = datetime.fromisoformat(schedule)

    if dry_run:
        _print_dry_run(item, effective_marketplace)
        return

    mkt = MARKETPLACES.get(effective_marketplace)
    if mkt is None:
        console.print(f"[red]Unknown marketplace '{effective_marketplace}'.[/red]")
        sys.exit(1)

    if not mkt.is_available():
        console.print(
            f"[red]Marketplace '{effective_marketplace}' is not configured.[/red]\n"
            "Check your .env credentials."
        )
        sys.exit(1)

    try:
        with _spinner(f"Posting to {effective_marketplace}…"):
            url = mkt.post_listing(item, item.ebay_options if effective_marketplace == "ebay" else None)
        console.print(f"[bold green]Posted![/bold green] {url}")

        for d in items_data:
            if d["id"] == item_id:
                d["approved"] = True
        state_file.write_text(json.dumps(items_data, indent=2, default=str), encoding="utf-8")

    except (RuntimeError, NotImplementedError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)


def _print_dry_run(item, marketplace: str) -> None:
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
# Helpers
# ---------------------------------------------------------------------------

def _spinner(msg: str):
    return Progress(SpinnerColumn(), TextColumn(msg), console=console, transient=True)


def _item_to_dict(item) -> dict:
    """Pydantic model → JSON-serialisable dict."""
    return json.loads(item.model_dump_json())
