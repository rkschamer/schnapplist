"""Kleinanzeigen.de provider via Playwright browser automation."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

from platformdirs import user_cache_dir

from schnapplist.models import Item
from schnapplist.providers.base import BaseMarketplace
from schnapplist.workflows.kleinanzeigen_posting import (
    dismiss_cookie_banner,
    goto_resilient,
    run_agentic_posting,
    wait_for_login_completion,
)

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext

    from ..llm import LLMClient

_BASE_URL = "https://www.kleinanzeigen.de"
_SESSION_FILE = Path(user_cache_dir("schnapplist")) / "kleinanzeigen_session.json"


class KleinanzeigenMarketplace(BaseMarketplace):
    name = "kleinanzeigen"

    def is_available(self) -> bool:
        return importlib.util.find_spec("playwright") is not None

    def post_listing(self, item: Item, options: None = None) -> str:
        """Open a browser, restore or create a session, then post the item."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Run:\n"
                "  uv sync --extra playwright\n"
                "  uv run playwright install chromium"
            ) from exc

        client = _make_llm_client()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            ctx = browser.new_context(
                storage_state=str(_SESSION_FILE) if _SESSION_FILE.exists() else None
            )
            page = ctx.new_page()

            goto_resilient(page, _BASE_URL)
            dismiss_cookie_banner(page)

            goto_resilient(page, f"{_BASE_URL}/p-anzeige-aufgeben.html")
            dismiss_cookie_banner(page)

            if "einloggen" in page.url or "login" in page.url:
                print(
                    "\n>>> Please log in to Kleinanzeigen in the browser window. "
                    "Posting will continue automatically once you're in. <<<\n"
                )
                if not wait_for_login_completion(page, timeout_ms=300_000):
                    raise RuntimeError(
                        "Login did not complete in time. If a CAPTCHA or 2FA step is open, "
                        "finish it in the browser and rerun the command."
                    )
                goto_resilient(page, f"{_BASE_URL}/p-anzeige-aufgeben.html")
                dismiss_cookie_banner(page)

            _save_session(ctx)

            start_url = page.url
            if not run_agentic_posting(
                page,
                item,
                client,
                start_url=start_url,
                max_steps=60,
            ):
                raise RuntimeError(
                    "Agentic posting did not reach a completion state. "
                    "Please rerun and complete any blocked step in the browser."
                )

            listing_url = page.url
            _save_session(ctx)
            browser.close()

        return listing_url


def _make_llm_client() -> LLMClient:
    from ..config import (
        ANTHROPIC_API_KEY,
        CLAUDE_MODEL,
        LLM_PROVIDER,
        OLLAMA_HOST,
        OLLAMA_MODEL,
    )
    from ..llm import LLMClient

    if LLM_PROVIDER == "ollama":
        return LLMClient("ollama", OLLAMA_MODEL, ollama_host=OLLAMA_HOST)
    return LLMClient("anthropic", CLAUDE_MODEL, api_key=ANTHROPIC_API_KEY)


def _save_session(ctx: BrowserContext) -> None:
    _SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    ctx.storage_state(path=str(_SESSION_FILE))
