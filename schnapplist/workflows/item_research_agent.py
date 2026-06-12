"""Agentic item research: identify → verify specs → price, all in one ReAct loop."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..core.models import (
    EbayListingOptions,
    ItemCondition,
    KleinanzeigenListingOptions,
    PriceInfo,
)

JsonDict = dict[str, Any]


class ItemResearchOutput(BaseModel):
    name: str
    brand: str | None
    model: str | None
    condition: ItemCondition
    condition_notes: str
    title_de: str
    description_de: str
    specs: dict[str, str]
    keywords: list[str]
    category: str
    price_info: PriceInfo
    ka_options: KleinanzeigenListingOptions | None
    ebay_options: EbayListingOptions | None
