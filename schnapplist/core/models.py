"""Core data models."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field


class ItemCondition(StrEnum):
    NEW = "new"
    LIKE_NEW = "like_new"
    GOOD = "good"
    ACCEPTABLE = "acceptable"
    POOR = "poor"

    def to_german(self) -> str:
        return {
            "new": "Neu",
            "like_new": "Wie neu",
            "good": "Gut",
            "acceptable": "Akzeptabel",
            "poor": "Schlecht",
        }[self.value]

    def to_ebay_condition(self) -> str:
        return {
            "new": "1000",  # New
            "like_new": "1500",  # New other
            "good": "3000",  # Used
            "acceptable": "5000",
            "poor": "7000",
        }[self.value]


class Photo(BaseModel):
    original_path: Path
    enhanced_path: Path | None = None

    @property
    def display_path(self) -> Path:
        return self.enhanced_path or self.original_path


class PriceInfo(BaseModel):
    suggested_price: float
    min_price: float
    max_price: float
    currency: str = "EUR"
    reasoning: str
    sources: list[dict[str, str]] = []  # [{"title": ..., "href": ...}, ...]


# eBay listing options must be defined before Item references them.


class KaShipping(StrEnum):
    VERSAND = "versand"
    ABHOLUNG = "abholung"


class KaPriceType(StrEnum):
    FESTPREIS = "festpreis"
    VB = "vb"  # Verhandlungsbasis
    ZU_VERSCHENKEN = "zu_verschenken"


class KleinanzeigenListingOptions(BaseModel):
    ka_category: str | None = None  # e.g. "Elektronik > PC-Zubehör & Software"
    shipping: KaShipping = KaShipping.VERSAND
    shipping_methods: list[str] = []  # e.g. ["Hermes Päckchen", "DHL Paket 2 kg"]
    price_type: KaPriceType = KaPriceType.FESTPREIS


class EbayListingType(StrEnum):
    AUCTION = "auction"  # Chinese auction (starting bid)
    FIXED = "fixed"  # Buy It Now
    BOTH = "both"  # Fixed price + Best Offer enabled


class EbayListingOptions(BaseModel):
    listing_type: EbayListingType = EbayListingType.FIXED
    reserve_price: float | None = None  # auction only
    duration_days: int = 7  # 1, 3, 5, 7, or 10
    scheduled_start: datetime | None = None
    ebay_category_id: str | None = None  # numeric eBay DE category ID


class Item(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    title_de: str = ""
    description: str
    condition: ItemCondition
    photos: list[Photo]
    price_info: PriceInfo | None = None
    approved: bool = False
    tags: list[str] = []
    category: str | None = None
    brand: str | None = None
    model: str | None = None
    marketplace: str | None = None  # "ebay" | "kleinanzeigen"
    ebay_options: EbayListingOptions | None = None
    ka_options: KleinanzeigenListingOptions | None = None
    confidence: float = 1.0
    confidence_notes: str = ""

    @classmethod
    def from_analysis(
        cls,
        analysis: dict[str, Any],
        photos: list[Path],
        enhanced_paths: list[Path],
        marketplace: str | None = None,
    ) -> Item:
        """Construct an Item from a raw agent analysis dict and photo paths."""
        photo_models = [
            Photo(original_path=orig, enhanced_path=enh)
            for orig, enh in zip(photos, enhanced_paths, strict=False)
        ]

        condition_raw = analysis.get("condition", "good")
        try:
            condition = ItemCondition(condition_raw)
        except ValueError:
            condition = ItemCondition.GOOD

        effective_marketplace = marketplace or analysis.get("marketplace", "kleinanzeigen")

        ebay_opts: EbayListingOptions | None = None
        ka_opts: KleinanzeigenListingOptions | None = None

        if effective_marketplace == "ebay":
            lt_raw = analysis.get("ebay_listing_type", "fixed")
            try:
                listing_type = EbayListingType(lt_raw)
            except ValueError:
                listing_type = EbayListingType.FIXED
            duration = int(analysis.get("ebay_duration_days") or 7)
            if duration not in (1, 3, 5, 7, 10):
                duration = 7
            reserve_raw = analysis.get("ebay_reserve_price")
            reserve = float(reserve_raw) if reserve_raw else None
            ebay_opts = EbayListingOptions(
                listing_type=listing_type,
                duration_days=duration,
                reserve_price=reserve,
                ebay_category_id=analysis.get("ebay_category_id"),
            )
        else:
            try:
                shipping = KaShipping(analysis.get("ka_shipping", "versand"))
            except ValueError:
                shipping = KaShipping.VERSAND
            try:
                price_type = KaPriceType(analysis.get("ka_price_type", "festpreis"))
            except ValueError:
                price_type = KaPriceType.FESTPREIS
            methods_raw = analysis.get("ka_shipping_methods", [])
            methods: list[str] = (
                [str(m) for m in cast(list[Any], methods_raw)]
                if isinstance(methods_raw, list)
                else []
            )
            ka_opts = KleinanzeigenListingOptions(
                ka_category=analysis.get("ka_category") or None,
                shipping=shipping,
                shipping_methods=methods,
                price_type=price_type,
            )

        return cls(
            name=analysis.get("name", "Unknown Item"),
            title_de=analysis.get("title_de", ""),
            description=str(analysis.get("description_de", analysis.get("description", "")) or ""),
            condition=condition,
            photos=photo_models,
            tags=analysis.get("keywords", []),
            category=analysis.get("category"),
            brand=analysis.get("brand"),
            model=analysis.get("model"),
            marketplace=effective_marketplace,
            ebay_options=ebay_opts,
            ka_options=ka_opts,
            confidence=float(analysis.get("confidence", 1.0)),
            confidence_notes=str(analysis.get("confidence_notes", "")),
        )
