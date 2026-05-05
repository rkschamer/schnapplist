"""Generate a Markdown inspection report before listings go live."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import DEFAULT_MARKETPLACE, LISTING_DISCLAIMER
from .models import EbayListingType, Item


def generate_report(items: list[Item], output_dir: Path) -> Path:
    """Write a Markdown report to output_dir and return its path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"schnapplist_report_{timestamp}.md"

    lines: list[str] = [
        "# Schnapplist — Inspection Report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
        f"Items: {len(items)}",
        "",
        "> Fields and sections marked **_(Inserat)_** go into the marketplace listing and can be edited here.",
        "> Sections marked **_(Recherche)_** are for your review only and will not be posted.",
        "",
        "---",
        "",
    ]

    for item in items:
        price = item.price_info
        price_str = f"**{price.suggested_price:.2f} {price.currency}**" if price else "_not determined_"
        range_str = f"(range {price.min_price:.2f}–{price.max_price:.2f} {price.currency})" if price else ""

        marketplace = item.marketplace or DEFAULT_MARKETPLACE

        lines += [
            f"## {item.name}",
            "",
            "### Inserat",
            "",
            "| Field | Value |",
            "|---|---|",
            f"| **ID** | `{item.id}` |",
            f"| **Title (DE)** | {item.title_de or '—'} |",
            f"| **Condition** | {item.condition.value.replace('_', ' ').title()} ({item.condition.to_german()}) |",
            f"| **Category** | {item.category or '—'} |",
            f"| **Brand / Model** | {item.brand or '—'} / {item.model or '—'} |",
            f"| **Suggested price** | {price_str} |",
            f"| **Marketplace** | {marketplace} |",
            f"| **Approved** | {str(item.approved).lower()} |",
        ]

        # eBay-specific rows
        if marketplace == "ebay":
            opts = item.ebay_options
            lt = opts.listing_type.value if opts else EbayListingType.FIXED.value
            dur = opts.duration_days if opts else 7
            reserve = f"{opts.reserve_price:.2f}" if (opts and opts.reserve_price) else "—"
            lines += [
                f"| **eBay listing type** | {lt} |",
                f"| **eBay duration (days)** | {dur} |",
                f"| **eBay reserve price (EUR)** | {reserve} |",
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
                rel = display.relative_to(output_dir)
            except ValueError:
                rel = display
            lines.append(f"![{photo.original_path.name}]({rel})")
        lines.append("")

        if LISTING_DISCLAIMER:
            lines += [
                f"> **Disclaimer** _(wird beim Posten automatisch angehängt)_: {LISTING_DISCLAIMER.strip()}",
                "",
            ]

        # --- Research section (not posted) ---
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

        lines += ["---", ""]

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
