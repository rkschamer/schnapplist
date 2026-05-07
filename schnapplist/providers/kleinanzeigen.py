"""Kleinanzeigen.de provider via Playwright MCP browser agent."""

from __future__ import annotations

from schnapplist.core.models import Item
from schnapplist.providers.base import BaseMarketplace
from schnapplist.workflows.kleinanzeigen_posting import run_mcp_posting


class KleinanzeigenMarketplace(BaseMarketplace):
    name = "kleinanzeigen"

    def is_available(self) -> bool:
        return True

    def post_listing(self, item: Item, options: None = None) -> str:
        """Run MCP-driven posting for this item."""
        return run_mcp_posting(item, max_steps=80)
