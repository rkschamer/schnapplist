"""Kleinanzeigen.de provider via Playwright browser automation.

Kleinanzeigen has no public posting API, so we automate the web UI.
Requires: uv sync --extra playwright && uv run playwright install chromium
Credentials: KLEINANZEIGEN_EMAIL and KLEINANZEIGEN_PASSWORD in .env
"""

from __future__ import annotations

from ..config import KLEINANZEIGEN_EMAIL, KLEINANZEIGEN_PASSWORD
from ..models import Item
from .base import BaseProvider

_BASE_URL = "https://www.kleinanzeigen.de"


class KleinanzeigenProvider(BaseProvider):
    name = "kleinanzeigen"

    def is_available(self) -> bool:
        return bool(KLEINANZEIGEN_EMAIL and KLEINANZEIGEN_PASSWORD)

    def post_listing(self, item: Item) -> str:
        """Post item to Kleinanzeigen and return the listing URL."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Run:\n"
                "  pip install 'auction-buddy[playwright]'\n"
                "  playwright install chromium"
            ) from exc

        if not self.is_available():
            raise RuntimeError(
                "Kleinanzeigen credentials missing. "
                "Set KLEINANZEIGEN_EMAIL and KLEINANZEIGEN_PASSWORD in .env"
            )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)  # visible for manual CAPTCHA solving
            ctx = browser.new_context()
            page = ctx.new_page()

            # Login
            page.goto(f"{_BASE_URL}/m-einloggen.html")
            page.fill("#login-email", KLEINANZEIGEN_EMAIL)
            page.fill("#login-password", KLEINANZEIGEN_PASSWORD)
            page.click("#login-submit")
            page.wait_for_url("**/m-meine-anzeigen.html**", timeout=30_000)

            # Navigate to new ad form
            page.goto(f"{_BASE_URL}/p-anzeige-aufgeben.html")
            page.wait_for_load_state("networkidle")

            # Fill category (best-effort: click first matching category tile)
            if item.category:
                page.get_by_text(item.category, exact=False).first.click()
                page.wait_for_load_state("networkidle")

            # Title
            title = item.title_de or item.name
            page.fill("#postad-title", title[:60])

            # Description
            page.fill("#postad-description", item.description)

            # Price
            if item.price_info:
                page.fill("#postad-price", str(int(item.price_info.suggested_price)))

            # Condition (if the field exists — depends on category)
            condition_map = {
                "new": "Neu",
                "like_new": "Wie neu",
                "good": "Gut",
                "acceptable": "Akzeptabel",
                "poor": "Beschädigt",
            }
            condition_label = condition_map.get(item.condition.value)
            if condition_label:
                try:
                    page.get_by_label(condition_label).click()
                except Exception:
                    pass  # Condition field not present for this category

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
