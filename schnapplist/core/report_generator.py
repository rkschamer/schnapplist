"""Generate per-item Markdown inspection files into a run folder."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from ..config import AGENT_TARGET_CONFIDENCE, DEFAULT_MARKETPLACE, LISTING_DISCLAIMER
from .models import EbayListingType, Item, KleinanzeigenListingOptions


def _next_evening_start(hour: int = 19) -> datetime:
    """Return the next upcoming datetime at *hour*:00 local time."""
    now = datetime.now()
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def generate_report(items: list[Item], run_dir: Path) -> Path:
    """Write one Markdown file per item into run_dir and return run_dir."""
    run_dir.mkdir(parents=True, exist_ok=True)

    for n, item in enumerate(items, start=1):
        write_item_report(item, n, run_dir)

    return run_dir


def write_item_report(item: Item, index: int, run_dir: Path) -> Path:
    """Write a single item's Markdown file and return its path."""
    run_dir.mkdir(parents=True, exist_ok=True)
    item_path = run_dir / f"item-{index}.md"
    _write_item_file(item, item_path, run_dir)
    return item_path


def _write_item_file(item: Item, item_path: Path, run_dir: Path) -> None:
    price = item.price_info
    price_str = f"**{price.suggested_price:.2f} {price.currency}**" if price else "_not determined_"
    range_str = (
        f"(range {price.min_price:.2f}–{price.max_price:.2f} {price.currency})"
        if price else ""
    )

    marketplace = item.marketplace or DEFAULT_MARKETPLACE

    lines: list[str] = [
        f"## {item.name}",
        "",
        "### Inserat",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| **ID** | `{item.id}` |",
        f"| **Title (DE)** | {item.title_de or '—'} |",
        f"| **Condition** | {item.condition.value.replace('_', ' ').title()}"
        f" ({item.condition.to_german()}) |",
        f"| **Category** | {item.category or '—'} |",
        f"| **Brand / Model** | {item.brand or '—'} / {item.model or '—'} |",
        f"| **Suggested price** | {price_str} |",
        f"| **Marketplace** | {marketplace} |",
        f"| **Approved** | {str(item.approved).lower()} |",
    ]

    if item.confidence < AGENT_TARGET_CONFIDENCE:
        lines.append(
            f"| **Confidence** | {item.confidence:.2f} ⚠ — {item.confidence_notes} |"
        )

    if marketplace == "ebay":
        opts = item.ebay_options
        lt = opts.listing_type.value if opts else EbayListingType.FIXED.value
        dur = opts.duration_days if opts else 7
        reserve = f"{opts.reserve_price:.2f}" if (opts and opts.reserve_price) else "—"
        scheduled = (opts.scheduled_start if (opts and opts.scheduled_start)
                     else _next_evening_start())
        sched_str = scheduled.strftime("%Y-%m-%dT%H:%M:%S")
        category_id = opts.ebay_category_id if opts else None
        lines += [
            f"| **eBay listing type** | {lt} |",
            f"| **eBay duration (days)** | {dur} |",
            f"| **eBay reserve price (EUR)** | {reserve} |",
            f"| **eBay scheduled start** | {sched_str} |",
            f"| **eBay Category ID** | {category_id or '—'} |",
        ]

    if marketplace == "kleinanzeigen":
        ka: KleinanzeigenListingOptions = item.ka_options or KleinanzeigenListingOptions()
        methods = ", ".join(ka.shipping_methods) if ka.shipping_methods else "—"
        lines += [
            f"| **KA Category** | {ka.ka_category or '—'} |",
            f"| **Shipping** | {ka.shipping.value} |",
            f"| **Shipping methods** | {methods} |",
            f"| **Price type** | {ka.price_type.value} |",
        ]

    lines.append("")

    if item.description:
        lines += [
            "#### Beschreibung",
            "",
            item.description,
            "",
        ]

    if item.tags:
        lines += [
            "#### Tags",
            "",
            ", ".join(f"`{t}`" for t in item.tags),
            "",
        ]

    lines += ["#### Fotos", ""]
    for photo in item.photos:
        display = photo.enhanced_path or photo.original_path
        try:
            rel = display.relative_to(run_dir)
        except ValueError:
            rel = display
        lines.append(f"![{photo.original_path.name}]({rel})")
    lines.append("")

    if LISTING_DISCLAIMER:
        lines += [
            "> **Disclaimer** _(wird beim Posten automatisch angehängt)_: "
            f"{LISTING_DISCLAIMER.strip()}",
            "",
        ]

    has_research = price and (price.reasoning or price.sources or range_str)
    if has_research:
        lines += [
            "### Recherche _(wird nicht veröffentlicht)_",
            "",
        ]
        if price and range_str:
            lines.append(f"Preisspanne: {range_str}  ")
        if price and price.reasoning:
            lines.append(f"Begründung: {price.reasoning}  ")
        lines.append("")
        if price and price.sources:
            lines.append("**Quellen:**")
            lines.append("")
            for src in price.sources:
                title = src.get("title", src.get("href", ""))
                href = src.get("href", "")
                if href:
                    lines.append(f"- [{title}]({href})")
                else:
                    lines.append(f"- {title}")
            lines.append("")
        if item.confidence < AGENT_TARGET_CONFIDENCE:
            lines += [
                f"**⚠ Niedrige Konfidenz:** {item.confidence_notes}",
                "",
            ]

    lines += ["---", ""]

    item_path.write_text("\n".join(lines), encoding="utf-8")
