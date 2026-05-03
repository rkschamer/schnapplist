"""Core data models."""

from __future__ import annotations

import uuid
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class ItemCondition(str, Enum):
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
    enhanced_path: Optional[Path] = None

    @property
    def display_path(self) -> Path:
        return self.enhanced_path or self.original_path


class PriceInfo(BaseModel):
    suggested_price: float
    min_price: float
    max_price: float
    currency: str = "EUR"
    reasoning: str


class Item(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    title_de: str = ""
    description: str
    condition: ItemCondition
    photos: list[Photo]
    price_info: Optional[PriceInfo] = None
    approved: bool = False
    tags: list[str] = []
    category: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
