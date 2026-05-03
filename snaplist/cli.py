"""CLI entry point — snaplist."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

console = Console()


def _require_api_key() -> str:
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
def process(photos_dir: Path, output_dir: Path) -> None:
    """Analyse photos, identify items, look up prices, and write a Markdown report."""
    import anthropic

    from .item_analyzer import analyze_item, build_item
    from .photo_processor import enhance_photo, group_photos_by_item, load_photos
    from .price_researcher import research_price
    from .report_generator import generate_report

    api_key = _require_api_key()
    client = anthropic.Anthropic(api_key=api_key)

    # 1. Scan folder
    with _spinner("Scanning photos…") as _:
        photos = load_photos(photos_dir)

    if not photos:
        console.print("[yellow]No supported photos found in directory.[/yellow]")
        return

    console.print(f"Found [bold]{len(photos)}[/bold] photo(s)")

    # 2. Group by item
    with _spinner("Grouping photos by item (Claude vision)…"):
        groups = group_photos_by_item(photos, client)

    console.print(f"Identified [bold]{len(groups)}[/bold] item(s)")

    items = []
    enhanced_root = output_dir / "enhanced"

    for idx, group_photos in enumerate(groups, 1):
        console.rule(f"Item {idx}/{len(groups)}")

        # 3. Enhance photos
        enhanced_paths = []
        for photo in group_photos:
            with _spinner(f"  Enhancing [cyan]{photo.name}[/cyan]…"):
                enhanced_paths.append(enhance_photo(photo, enhanced_root))

        # 4. Analyse item
        with _spinner("  Analysing item with Claude…"):
            analysis = analyze_item(group_photos, client)

        name = analysis.get("name", f"Item {idx}")
        condition = analysis.get("condition", "good")
        keywords = analysis.get("keywords") or [name]

        console.print(f"  [bold]{name}[/bold] — condition: [italic]{condition}[/italic]")

        # 5. Price research
        with _spinner("  Researching prices…"):
            price_info = research_price(keywords, condition, client)

        console.print(
            f"  Price: [green]{price_info.suggested_price:.2f} EUR[/green] "
            f"({price_info.min_price:.2f}–{price_info.max_price:.2f})"
        )

        item = build_item(analysis, group_photos, enhanced_paths)
        item.price_info = price_info
        items.append(item)

    # 6. Write report
    report_path = generate_report(items, output_dir)
    console.print(f"\n[bold green]Report:[/bold green] {report_path}")

    # Persist state for the post command
    state_file = output_dir / "items.json"
    state_file.write_text(
        json.dumps([_item_to_dict(i) for i in items], indent=2, default=str),
        encoding="utf-8",
    )
    console.print(f"[bold green]State:[/bold green]  {state_file}")
    console.print(
        "\nReview the report, then run [bold]auction-buddy post[/bold] to create listings."
    )


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
    "--provider", "-p",
    type=click.Choice(["kleinanzeigen", "ebay"]),
    required=True,
    help="Marketplace to post on.",
)
@click.option("--dry-run", is_flag=True, default=False, help="Print what would be posted without actually posting.")
def post(output_dir: Path, item_id: str, provider: str, dry_run: bool) -> None:
    """Post an item to a marketplace."""
    from .models import Item
    from .providers import PROVIDERS

    state_file = output_dir / "items.json"
    if not state_file.exists():
        console.print("[red]No items.json found. Run 'process' first.[/red]")
        sys.exit(1)

    items_data: list[dict] = json.loads(state_file.read_text(encoding="utf-8"))
    item_data = next((d for d in items_data if d["id"] == item_id), None)
    if not item_data:
        console.print(f"[red]Item '{item_id}' not found.[/red]")
        sys.exit(1)

    item = Item.model_validate(item_data)

    if dry_run:
        console.print(f"[yellow][DRY RUN][/yellow] Would post to [bold]{provider}[/bold]:")
        console.print(f"  Title:     {item.title_de or item.name}")
        console.print(f"  Condition: {item.condition.to_german()}")
        if item.price_info:
            console.print(f"  Price:     {item.price_info.suggested_price:.2f} EUR")
        console.print(f"  Photos:    {len(item.photos)}")
        return

    prov = PROVIDERS.get(provider)
    if prov is None:
        console.print(f"[red]Unknown provider '{provider}'.[/red]")
        sys.exit(1)

    if not prov.is_available():
        console.print(
            f"[red]Provider '{provider}' is not configured.[/red]\n"
            "Check your .env credentials."
        )
        sys.exit(1)

    try:
        with _spinner(f"Posting to {provider}…"):
            url = prov.post_listing(item)
        console.print(f"[bold green]Posted![/bold green] {url}")

        # Mark as approved in state file
        for d in items_data:
            if d["id"] == item_id:
                d["approved"] = True
        state_file.write_text(json.dumps(items_data, indent=2, default=str), encoding="utf-8")

    except (RuntimeError, NotImplementedError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _spinner(msg: str):
    return Progress(SpinnerColumn(), TextColumn(msg), console=console, transient=True)


def _item_to_dict(item) -> dict:
    """Pydantic model → JSON-serialisable dict."""
    from .models import Item
    return json.loads(item.model_dump_json())
