"""Marketplace registry."""

from .ebay import EbayMarketplace
from .kleinanzeigen import KleinanzeigenMarketplace

MARKETPLACES: dict = {
    "kleinanzeigen": KleinanzeigenMarketplace(),
    "ebay": EbayMarketplace(),
}
