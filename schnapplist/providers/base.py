"""Abstract marketplace interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import Item


class BaseMarketplace(ABC):
    name: str

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the marketplace is properly configured."""

    @abstractmethod
    def post_listing(self, item: Item, options: Any = None) -> str:
        """Create a listing and return its public URL."""
