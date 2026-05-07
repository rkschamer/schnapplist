"""Posting service — loads items from a report and executes marketplace posts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..models import (
    EbayListingOptions,
    Item,
    ItemCondition,
    KleinanzeigenListingOptions,
    Photo,
    PriceInfo,
)
from ..report_parser import parse_report
from ..workflows.review_pipeline import find_latest_report


@dataclass
class PostResult:
    item_id: str
    item_name: str
    marketplace: str
    url: str | None = None
    error: str | None = None
    dry_run: bool = False
    dry_run_summary: dict | None = None

    @property
    def success(self) -> bool:
        return self.error is None


def load_items_from_report(output_dir: Path) -> tuple[Path, list[Item]]:
    """Return (report_path, items) for the latest run.

    Raises:
        FileNotFoundError: if no report exists in output_dir.
    """
    report_path = find_latest_report(output_dir)
    if not report_path:
        raise FileNotFoundError(f"No report found in {output_dir}. Run 'process' first.")

    parsed = parse_report(report_path)
    if not parsed:
        raise FileNotFoundError("No items found in report.")

    items: list[Item] = []
    for data in parsed:
        photos = _resolve_photos(data.pop("photo_paths", []), report_path)

        price_info: PriceInfo | None = None
        if "suggested_price" in data:
            price_info = PriceInfo(
                suggested_price=data.pop("suggested_price"),
                min_price=0,
                max_price=0,
                reasoning="",
            )

        ebay_options: EbayListingOptions | None = None
        if "ebay_options" in data:
            ebay_options = EbayListingOptions(**data.pop("ebay_options"))

        ka_options: KleinanzeigenListingOptions | None = None
        if "ka_options" in data:
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
    return report_path, items


def post_item(
    item: Item,
    marketplace: str,
    schedule: str | None = None,
    dry_run: bool = False,
) -> PostResult:
    """Post a single item to a marketplace.

    Returns a PostResult with url on success or error on failure.
    When dry_run=True, returns immediately with a summary dict and no network call.
    """
    from ..config import DEFAULT_MARKETPLACE
    from ..providers import MARKETPLACES

    effective_marketplace = marketplace or item.marketplace or DEFAULT_MARKETPLACE

    if schedule and item.ebay_options:
        item.ebay_options.scheduled_start = datetime.fromisoformat(schedule)

    if dry_run:
        summary = _dry_run_summary(item, effective_marketplace)
        return PostResult(
            item_id=item.id,
            item_name=item.name,
            marketplace=effective_marketplace,
            dry_run=True,
            dry_run_summary=summary,
        )

    mkt = MARKETPLACES.get(effective_marketplace)
    if mkt is None:
        return PostResult(
            item_id=item.id,
            item_name=item.name,
            marketplace=effective_marketplace,
            error=f"Unknown marketplace '{effective_marketplace}'",
        )

    if not mkt.is_available():
        return PostResult(
            item_id=item.id,
            item_name=item.name,
            marketplace=effective_marketplace,
            error=f"Marketplace '{effective_marketplace}' is not configured",
        )

    try:
        url = mkt.post_listing(
            item,
            item.ebay_options if effective_marketplace == "ebay" else None,
        )
        return PostResult(
            item_id=item.id,
            item_name=item.name,
            marketplace=effective_marketplace,
            url=url,
        )
    except (RuntimeError, NotImplementedError) as exc:
        return PostResult(
            item_id=item.id,
            item_name=item.name,
            marketplace=effective_marketplace,
            error=str(exc),
        )


def _resolve_photos(paths: list[str], report_path: Path) -> list[Photo]:
    photos: list[Photo] = []
    for p in paths:
        resolved = (report_path / p).resolve()
        if "enhanced" in p:
            photos.append(Photo(original_path=resolved, enhanced_path=resolved))
        else:
            photos.append(Photo(original_path=resolved))
    return photos


def _dry_run_summary(item: Item, marketplace: str) -> dict:
    summary: dict = {
        "title": item.title_de or item.name,
        "condition": item.condition.to_german(),
        "photos": len(item.photos),
        "marketplace": marketplace,
    }
    if item.price_info:
        summary["price"] = f"{item.price_info.suggested_price:.2f} EUR"
    if marketplace == "ebay" and item.ebay_options:
        opts = item.ebay_options
        summary["listing_type"] = opts.listing_type.value
        summary["duration_days"] = opts.duration_days
        if opts.reserve_price:
            summary["reserve_price"] = f"{opts.reserve_price:.2f} EUR"
        if opts.scheduled_start:
            summary["scheduled_start"] = opts.scheduled_start.isoformat()
    return summary
