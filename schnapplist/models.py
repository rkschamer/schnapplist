"""Core data models."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from pathlib import Path

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
            "new": "1000",       # New
            "like_new": "1500",  # New other
            "good": "3000",      # Used
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
    VERSAND  = "versand"
    ABHOLUNG = "abholung"


class KaPriceType(StrEnum):
    FESTPREIS      = "festpreis"
    VB             = "vb"             # Verhandlungsbasis
    ZU_VERSCHENKEN = "zu_verschenken"


class KleinanzeigenListingOptions(BaseModel):
    ka_category: str | None = None        # e.g. "Elektronik > PC-Zubehör & Software"
    shipping: KaShipping = KaShipping.VERSAND
    shipping_methods: list[str] = []      # e.g. ["Hermes Päckchen", "DHL Paket 2 kg"]
    price_type: KaPriceType = KaPriceType.FESTPREIS


class EbayListingType(StrEnum):
    AUCTION = "auction"    # Chinese auction (starting bid)
    FIXED   = "fixed"      # Buy It Now
    BOTH    = "both"       # Fixed price + Best Offer enabled


class EbayListingOptions(BaseModel):
    listing_type: EbayListingType = EbayListingType.FIXED
    reserve_price: float | None = None      # auction only
    duration_days: int = 7                     # 1, 3, 5, 7, or 10
    scheduled_start: datetime | None = None


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
    marketplace: str | None = None          # "ebay" | "kleinanzeigen"
    ebay_options: EbayListingOptions | None = None
    ka_options: KleinanzeigenListingOptions | None = None
