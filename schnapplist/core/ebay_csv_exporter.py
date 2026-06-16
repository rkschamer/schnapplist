"""Generate an eBay draft listing CSV for bulk upload via Seller Hub Reports."""

from __future__ import annotations

from pathlib import Path

from ..config import EBAY_CSV_ACTION_HEADER
from .models import EbayListingType, Item

_INFO_LINES = [
    "#INFO;Version=0.0.2;Template= eBay-draft-listings-template_DE",
    "#INFO Action und Category ID sind erforderliche Felder. "
    "1) Stellen Sie Action auf Draft ein. "
    "2) Die Kategorie-ID finden Sie hier: "
    "https://pages.ebay.com/sellerinformation/news/categorychanges.html",
    "#INFO Nachdem Sie Ihren Entwurf erfolgreich hochgeladen haben; "
    "können Sie die Entwürfe hier vervollständigen: "
    "https://www.ebay.de/sh/lst/drafts",
    "#INFO",
]

_COLUMNS = [
    EBAY_CSV_ACTION_HEADER,
    "Custom label (SKU)",
    "Category ID",
    "Title",
    "UPC",
    "Price",
    "Quantity",
    "Item photo URL",
    "Condition ID",
    "Description",
    "Format",
]

_FORMAT_MAP = {
    EbayListingType.FIXED: "FixedPrice",
    EbayListingType.AUCTION: "Chinese",
    EbayListingType.BOTH: "FixedPrice",
}


def export_to_csv(items: list[Item], output_path: Path) -> int:
    """Write approved eBay items to a semicolon-delimited CSV draft file.

    Returns the number of items written (0 if no approved eBay items).
    """
    rows = [_item_to_row(item) for item in items
            if item.approved and item.marketplace == "ebay"]

    lines = _INFO_LINES + [";".join(_COLUMNS)]
    for row in rows:
        lines.append(";".join(row))

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(rows)


def _item_to_row(item: Item) -> list[str]:
    opts = item.ebay_options
    price = str(item.price_info.suggested_price) if item.price_info else ""
    category_id = (opts.ebay_category_id or "") if opts else ""
    listing_type = opts.listing_type if opts else EbayListingType.FIXED
    fmt = _FORMAT_MAP[listing_type]
    description = f"<p>{item.description}</p>" if item.description else ""

    return [
        "Draft",
        item.id,
        category_id,
        item.title_de or item.name,
        "",                              # UPC
        price,
        "1",
        "",                              # Item photo URL
        item.condition.to_ebay_condition(),
        description,
        fmt,
    ]
