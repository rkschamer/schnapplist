"""Abstract provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Item


class BaseProvider(ABC):
    name: str

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the provider is properly configured."""

    @abstractmethod
    def post_listing(self, item: Item) -> str:
        """Create a listing and return its public URL."""
