"""Marketplace registry."""

from .base import BaseMarketplace
from .ebay import EbayMarketplace
from .kleinanzeigen import KleinanzeigenMarketplace

MARKETPLACES: dict[str, BaseMarketplace] = {
    "kleinanzeigen": KleinanzeigenMarketplace(),
    "ebay": EbayMarketplace(),
}
