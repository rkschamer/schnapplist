"""Marketplace provider adapters.

Each provider implements BaseMarketplace (base.py):
  - is_available() → bool   check credentials/config
  - post_listing(item, options) → str   create listing, return URL

Registered providers:
  "kleinanzeigen"  →  KleinanzeigenMarketplace  (Playwright MCP browser agent)
  "ebay"           →  EbayMarketplace            (eBay Trading API)

eBay bulk-upload CSV export lives here too (ebay_csv_exporter.py) — it is
provider-specific and not part of the core domain.

To add a new marketplace:
  1. Create schnapplist/providers/<name>.py
  2. Implement BaseMarketplace
  3. Add an entry to MARKETPLACES below
"""

from .base import BaseMarketplace
from .ebay import EbayMarketplace
from .kleinanzeigen import KleinanzeigenMarketplace

MARKETPLACES: dict[str, BaseMarketplace] = {
    "kleinanzeigen": KleinanzeigenMarketplace(),
    "ebay": EbayMarketplace(),
}
