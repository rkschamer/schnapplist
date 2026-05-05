"""Kleinanzeigen.de provider via Playwright browser automation.

Kleinanzeigen has no public posting API, so we automate the web UI.
Requires: uv sync --extra playwright && uv run playwright install chromium

No credentials are stored — the browser opens the login page and you log in manually.
"""

from __future__ import annotations

import contextlib

from ..models import Item
from .base import BaseMarketplace

_BASE_URL = "https://www.kleinanzeigen.de"


class KleinanzeigenMarketplace(BaseMarketplace):
    name = "kleinanzeigen"

    def is_available(self) -> bool:
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            return False

    def post_listing(self, item: Item, options: None = None) -> str:
        """Open a browser, let the user log in, then post the item."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Run:\n"
                "  uv sync --extra playwright\n"
                "  uv run playwright install chromium"
            ) from exc

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            ctx = browser.new_context()
            page = ctx.new_page()

            # Let the user log in manually
            page.goto(f"{_BASE_URL}/m-einloggen.html")
            print(
                "\n>>> Please log in to Kleinanzeigen in the browser window. "
                "Posting will continue automatically once you're in. <<<\n"
            )
            page.wait_for_url(
                lambda url: "einloggen" not in url and "login" not in url,
                timeout=300_000,  # 5-minute window
            )

            # Navigate to new listing form
            page.goto(f"{_BASE_URL}/p-anzeige-aufgeben.html")
            page.wait_for_load_state("networkidle")

            # Fill category (best-effort)
            if item.category:
                with contextlib.suppress(Exception):
                    page.get_by_text(item.category, exact=False).first.click()
                    page.wait_for_load_state("networkidle")

            # Title
            page.fill("#postad-title", (item.title_de or item.name)[:60])

            # Description with disclaimer appended
            from ..config import LISTING_DISCLAIMER
            description = item.description
            if LISTING_DISCLAIMER:
                description = f"{description}\n\n{LISTING_DISCLAIMER}"
            page.fill("#postad-description", description)

            # Price
            if item.price_info:
                page.fill("#postad-price", str(int(item.price_info.suggested_price)))

            # Condition (field may not exist for every category)
            condition_map = {
                "new": "Neu",
                "like_new": "Wie neu",
                "good": "Gut",
                "acceptable": "Akzeptabel",
                "poor": "Beschädigt",
            }
            condition_label = condition_map.get(item.condition.value)
            if condition_label:
                with contextlib.suppress(Exception):
                    page.get_by_label(condition_label).click()

            # Upload photos
            for photo in item.photos:
                display = photo.enhanced_path or photo.original_path
                upload_input = page.locator("input[type=file]").first
                upload_input.set_input_files(str(display))
                page.wait_for_timeout(1500)

            # Submit
            page.click("#postad-submit")
            page.wait_for_load_state("networkidle")

            listing_url = page.url
            browser.close()

        return listing_url
