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
    default="anthropic",
    show_default=True,
    type=click.Choice(["anthropic", "ollama"]),
    help="LLM backend to use for analysis.",
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
    from .config import CLAUDE_MODEL, OLLAMA_HOST, OLLAMA_MODEL
    from .llm import LLMClient
    from .orchestration import ProcessOrchestrator

    if llm_provider == "anthropic":
        api_key = _require_anthropic_key()
        model = llm_model or CLAUDE_MODEL
        client = LLMClient("anthropic", model, api_key=api_key)
    else:
        model = llm_model or OLLAMA_MODEL
        host = ollama_host or OLLAMA_HOST
        client = LLMClient("ollama", model, ollama_host=host)
        console.print(f"Using Ollama [bold]{model}[/bold] at [cyan]{host}[/cyan]")

    orchestrator = ProcessOrchestrator(client)
    result = orchestrator.run(
        photos_dir=photos_dir,
        output_dir=output_dir,
        single_item=single_item,
    )

    if result.state.total_photos == 0:
        console.print("[yellow]No supported photos found in directory.[/yellow]")
        return

    console.print(f"Found [bold]{result.state.total_photos}[/bold] photo(s)")

    if single_item:
        console.print(
            f"Using all [bold]{result.state.total_photos}[/bold] photo(s) as one item (--single-item)"
        )
    else:
        console.print(f"Identified [bold]{result.state.total_groups}[/bold] item(s)")

    for idx, item in enumerate(result.items, 1):
        console.rule(f"Item {idx}/{len(result.items)}")
        console.print(
            f"  [bold]{item.name}[/bold] — condition: [italic]{item.condition.value}[/italic]"
        )
        if item.price_info:
            console.print(
                f"  Price: [green]{item.price_info.suggested_price:.2f} EUR[/green] "
                f"({item.price_info.min_price:.2f}–{item.price_info.max_price:.2f})"
            )

    report_path = result.report_path
    state_file = result.state_file
    if report_path is None or state_file is None:
        console.print("[red]Processing did not produce output files.[/red]")
        sys.exit(1)

    console.print(f"\n[bold green]Report:[/bold green] {report_path}")
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
    return json.loads(item.model_dump_json())
