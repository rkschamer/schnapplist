"""Kleinanzeigen.de provider via Playwright browser automation.

Kleinanzeigen has no public posting API, so we automate the web UI.
Requires: uv sync --extra playwright && uv run playwright install chromium

Session cookies are persisted to the user cache dir so you only need to log
in once. The browser opens in headed mode so you can solve CAPTCHAs manually.
"""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

from platformdirs import user_cache_dir

from ..models import Item
from .base import BaseMarketplace

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page

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

            # Warm up the home page so the cookie banner fires before any redirect
            _goto_resilient(page, _BASE_URL)
            _dismiss_cookie_banner(page)

            # Navigate to posting form — redirected to login means session expired.
            # Use "commit" because Kleinanzeigen does SPA-style redirects that abort
            # the underlying network request before "load" fires.
            _goto_resilient(page, f"{_BASE_URL}/p-anzeige-aufgeben.html")
            _dismiss_cookie_banner(page)

            if "einloggen" in page.url or "login" in page.url:
                print(
                    "\n>>> Please log in to Kleinanzeigen in the browser window. "
                    "Posting will continue automatically once you're in. <<<\n"
                )
                if not _wait_for_login_completion(page, timeout_ms=300_000):
                    raise RuntimeError(
                        "Login did not complete in time. If a CAPTCHA or 2FA step is open, "
                        "finish it in the browser and rerun the command."
                    )
                _goto_resilient(page, f"{_BASE_URL}/p-anzeige-aufgeben.html")
                _dismiss_cookie_banner(page)

            # Persist session so next run skips login
            _save_session(ctx)

            payload = _build_listing_payload(item)
            start_url = page.url
            if not _agentic_complete_listing(
                page,
                item,
                client,
                payload=payload,
                start_url=start_url,
                max_steps=60,
            ):
                raise RuntimeError(
                    "Agentic posting did not reach a completion state. "
                    "Please rerun and complete any blocked step in the browser."
                )

            listing_url = page.url

            # Refresh persisted session (keeps auth cookies current)
            _save_session(ctx)
            browser.close()

        return listing_url


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _wait_for_login_completion(page: Page, *, timeout_ms: int) -> bool:
    """Poll for successful login across URL changes and SPA transitions."""
    deadline = time.monotonic() + (timeout_ms / 1000)
    login_tokens = ("einloggen", "login", "m-einloggen")
    account_hint = re.compile(r"mein\s*konto|profil|abmelden|logout", re.I)

    while time.monotonic() < deadline:
        _dismiss_cookie_banner(page)
        url = page.url.lower()

        # If we visibly reached posting form, we're done.
        with contextlib.suppress(Exception):
            if page.locator("#postad-title").count() > 0:
                return True

        # If URL no longer looks like the login route, login likely completed.
        if not any(token in url for token in login_tokens):
            return True

        # Some account UI hints can appear before URL updates settle.
        with contextlib.suppress(Exception):
            if page.get_by_role("link", name=account_hint).count() > 0:
                return True

        page.wait_for_timeout(1_000)

    return False


def _dismiss_cookie_banner(page: Page) -> None:
    """Click the accept-all cookie button if present; silent on failure."""
    with contextlib.suppress(Exception):
        page.get_by_role(
            "button",
            name=re.compile(r"alle.*akzeptieren|akzeptieren|accept\s*all|accept", re.I),
        ).first.click(timeout=3_000)
        page.wait_for_timeout(500)


def _goto_resilient(page: Page, url: str) -> None:
    """Navigate with relaxed waiting to avoid brittle SPA timing behavior."""
    page.goto(url, wait_until="commit")
    with contextlib.suppress(Exception):
        page.wait_for_load_state("domcontentloaded", timeout=8_000)
    page.wait_for_timeout(1_000)


def _build_listing_payload(item: Item) -> dict[str, str | list[str]]:
    from ..config import LISTING_DISCLAIMER

    description = item.description
    if LISTING_DISCLAIMER:
        description = f"{description}\n\n{LISTING_DISCLAIMER}"

    condition_map = {
        "new": "Neu",
        "like_new": "Wie neu",
        "good": "Gut",
        "acceptable": "Akzeptabel",
        "poor": "Beschädigt",
    }

    photo_paths = [str(p.enhanced_path or p.original_path) for p in item.photos]

    payload: dict[str, str | list[str]] = {
        "title": (item.title_de or item.name)[:60],
        "description": description,
        "condition": condition_map.get(item.condition.value, ""),
        "photo_paths": photo_paths,
    }
    if item.price_info:
        payload["price"] = str(int(item.price_info.suggested_price))
    return payload


def _agentic_complete_listing(
    page: Page,
    item: Item,
    client: LLMClient,
    *,
    payload: dict[str, str | list[str]],
    start_url: str,
    max_steps: int,
) -> bool:
    """Drive listing creation end-to-end via screenshot-planned actions."""
    uploaded = 0
    submitted = False
    photo_paths = [str(p) for p in payload.get("photo_paths", [])]

    item_desc = f"Item: {item.name}"
    if item.category:
        item_desc += f"\nCategory hint: {item.category}"

    for _ in range(max_steps):
        _dismiss_cookie_banner(page)
        if _is_posting_complete(page, start_url=start_url, submitted=submitted):
            return True

        has_form = False
        with contextlib.suppress(Exception):
            has_form = page.locator("#postad-title").count() > 0
        category_options = _visible_category_options(page)

        screenshot_b64 = base64.b64encode(page.screenshot()).decode()
        response = client.messages_create(
            max_tokens=260,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": screenshot_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "You are controlling Playwright for kleinanzeigen.de posting.\n"
                                f"Goal: Create the listing fully and submit it.\n"
                                f"Current URL: {page.url}\n"
                                f"{item_desc}\n"
                                f"Known listing values: "
                                f"title='{payload.get('title', '')}', "
                                f"price='{payload.get('price', '')}', "
                                f"condition='{payload.get('condition', '')}'.\n"
                                f"Description to use exactly:\n{payload.get('description', '')}\n\n"
                                f"Photo upload progress: {uploaded}/{len(photo_paths)}.\n"
                                f"Listing form visible: {has_form}.\n"
                                "Visible category options with index "
                                "(choose these when form is not visible):\n"
                                f"{_format_indexed_options(category_options)}\n"
                                "Return ONLY one JSON object with one action:\n"
                                '{"action":"click_text","text":"visible text"}\n'
                                '{"action":"click_role","role":"button|link",'
                                '"name":"visible label"}\n'
                                '{"action":"click_category_index","index":1}\n'
                                '{"action":"click_category_text","text":"exact option text"}\n'
                                '{"action":"fill","field":"title|description|price"}\n'
                                '{"action":"set_condition"}\n'
                                '{"action":"upload_next_photo"}\n'
                                '{"action":"submit"}\n'
                                '{"action":"press","key":"Escape|Enter|Tab"}\n'
                                '{"action":"wait","ms":1200}\n'
                                '{"action":"manual","prompt":"what user should do"}\n'
                                '{"action":"done"}\n\n'
                                "Rules: use provided values only; do not invent listing text. "
                                "Prefer text/role actions over css selectors."
                            ),
                        },
                    ],
                }
            ],
        )

        data = _parse_json_action(response.content[0].text)
        if not data:
            page.wait_for_timeout(800)
            continue

        action = str(data.get("action", "")).strip().lower()
        if action == "done":
            return _is_posting_complete(page, start_url=start_url, submitted=submitted)
        if action == "wait":
            ms = _coerce_int(data.get("ms"), default=1200)
            page.wait_for_timeout(max(300, min(ms, 10_000)))
            continue
        if action == "manual":
            prompt = str(data.get("prompt", "Complete the visible manual step.")).strip()
            print(f"\n>>> Manual step required: {prompt} <<<\n")
            page.wait_for_timeout(20_000)
            continue

        acted = _execute_agent_action(
            page,
            data,
            payload=payload,
            photo_paths=photo_paths,
            uploaded=uploaded,
            category_options=category_options,
        )

        if action == "upload_next_photo" and acted and uploaded < len(photo_paths):
            uploaded += 1
        if action == "submit" and acted:
            submitted = True

        if not acted:
            if not has_form and category_options:
                preview = ", ".join(category_options[:6])
                print(f"Agent retry: visible category options -> {preview}")
            page.wait_for_timeout(900)
            continue

        page.wait_for_timeout(1_000)

    return _is_posting_complete(page, start_url=start_url, submitted=submitted)


def _parse_json_action(raw: str) -> dict[str, object] | None:
    m = re.search(r"\{[\s\S]*\}", raw.strip())
    if not m:
        return None
    with contextlib.suppress(json.JSONDecodeError):
        data = json.loads(m.group(0))
        if isinstance(data, dict):
            out: dict[str, object] = {}
            json_obj = cast(dict[object, object], data)
            for k, v in json_obj.items():
                out[str(k)] = v
            return out
    return None


def _coerce_int(value: object, *, default: int) -> int:
    if isinstance(value, (int, float, str)):
        with contextlib.suppress(ValueError):
            return int(value)
    return default


def _execute_agent_action(
    page: Page,
    data: dict[str, object],
    *,
    payload: dict[str, str | list[str]],
    photo_paths: list[str],
    uploaded: int,
    category_options: list[str],
) -> bool:
    action = str(data.get("action", "")).strip().lower()
    acted = False

    if action == "click_text" and data.get("text"):
        with contextlib.suppress(Exception):
            page.get_by_text(str(data["text"]), exact=False).first.click(timeout=3_500)
            acted = True

    elif action == "click_role" and data.get("role") and data.get("name"):
        role = str(data["role"]).strip().lower()
        name = re.compile(re.escape(str(data["name"])), re.I)
        if role == "button":
            with contextlib.suppress(Exception):
                page.get_by_role("button", name=name).first.click(timeout=3_500)
                acted = True
        elif role == "link":
            with contextlib.suppress(Exception):
                page.get_by_role("link", name=name).first.click(timeout=3_500)
                acted = True

    elif action == "click_category_index":
        idx = _coerce_int(data.get("index"), default=0)
        acted = _click_category_option(page, category_options, idx)

    elif action == "click_category_text" and data.get("text"):
        target = str(data["text"]).strip()
        if target:
            with contextlib.suppress(Exception):
                page.get_by_text(target, exact=True).first.click(timeout=3_500)
                acted = True
            if not acted:
                with contextlib.suppress(Exception):
                    page.get_by_text(target, exact=False).first.click(timeout=3_500)
                    acted = True

    elif action == "fill" and data.get("field"):
        field = str(data["field"]).strip().lower()
        value = str(payload.get(field, "")).strip()
        if value:
            acted = _fill_field_by_semantic_name(page, field, value)

    elif action == "set_condition":
        condition = str(payload.get("condition", "")).strip()
        if condition:
            with contextlib.suppress(Exception):
                page.get_by_label(condition).first.click(timeout=3_500)
                acted = True
            if not acted:
                with contextlib.suppress(Exception):
                    page.get_by_text(condition, exact=False).first.click(timeout=3_500)
                    acted = True

    elif action == "upload_next_photo":
        if uploaded < len(photo_paths):
            with contextlib.suppress(Exception):
                page.locator("input[type=file]").first.set_input_files(photo_paths[uploaded])
                acted = True

    elif action == "submit":
        with contextlib.suppress(Exception):
            page.locator("#postad-submit").first.click(timeout=3_500)
            acted = True
        if not acted:
            with contextlib.suppress(Exception):
                page.get_by_role(
                    "button",
                    name=re.compile(r"weiter|veroeffentlichen|anzeigen", re.I),
                ).first.click(timeout=3_500)
                acted = True

    elif action == "press" and data.get("key"):
        with contextlib.suppress(Exception):
            page.keyboard.press(str(data["key"]))
            acted = True

    return acted


def _fill_field_by_semantic_name(page: Page, field: str, value: str) -> bool:
    label_patterns = {
        "title": re.compile(r"titel", re.I),
        "description": re.compile(r"beschreibung", re.I),
        "price": re.compile(r"preis", re.I),
    }
    selector_map = {
        "title": "#postad-title",
        "description": "#postad-description",
        "price": "#postad-price",
    }

    selector = selector_map.get(field)
    if selector:
        with contextlib.suppress(Exception):
            page.locator(selector).first.fill(value, timeout=3_500)
            return True

    pattern = label_patterns.get(field)
    if pattern:
        with contextlib.suppress(Exception):
            page.get_by_label(pattern).first.fill(value, timeout=3_500)
            return True

    if field == "description":
        with contextlib.suppress(Exception):
            page.locator("textarea").first.fill(value, timeout=3_500)
            return True

    if field in {"title", "price"}:
        with contextlib.suppress(Exception):
            page.locator("input").first.fill(value, timeout=3_500)
            return True

    return False


def _visible_category_options(page: Page) -> list[str]:
    """Collect likely clickable category labels currently visible on screen."""
    with contextlib.suppress(Exception):
        options = page.evaluate(
            """
            () => {
              const sel = [
                'a',
                'button',
                '[role="button"]',
                'label',
                'li',
                'div'
              ].join(',');

              const isVisible = (el) => {
                const st = window.getComputedStyle(el);
                if (st.display === 'none' || st.visibility === 'hidden') return false;
                const r = el.getBoundingClientRect();
                if (r.width < 20 || r.height < 14) return false;
                if (r.bottom < 0 || r.top > window.innerHeight) return false;
                return true;
              };

              const out = [];
              const seen = new Set();
              for (const el of Array.from(document.querySelectorAll(sel))) {
                if (!isVisible(el)) continue;
                const txt = (el.textContent || '').replace(/\\s+/g, ' ').trim();
                if (!txt) continue;
                if (txt.length > 80) continue;
                if (!(/[A-Za-zÄÖÜäöüß]/.test(txt))) continue;
                if (seen.has(txt)) continue;
                seen.add(txt);
                out.push(txt);
                if (out.length >= 14) break;
              }
              return out;
            }
            """
        )
        if isinstance(options, list):
            cleaned: list[str] = []
            for opt in cast(list[object], options):
                if isinstance(opt, str) and opt.strip():
                    cleaned.append(opt.strip())
            return cleaned
    return []


def _format_indexed_options(options: list[str]) -> str:
    if not options:
        return "none"
    return "\n".join(f"{i + 1}. {text}" for i, text in enumerate(options))


def _click_category_option(page: Page, options: list[str], index: int) -> bool:
    if index < 1 or index > len(options):
        return False
    target = options[index - 1]

    with contextlib.suppress(Exception):
        page.get_by_text(target, exact=True).first.click(timeout=3_500)
        return True
    with contextlib.suppress(Exception):
        page.get_by_text(target, exact=False).first.click(timeout=3_500)
        return True
    return False


def _is_posting_complete(page: Page, *, start_url: str, submitted: bool) -> bool:
    url = page.url
    if not submitted and "p-anzeige-aufgeben" in url:
        return False

    success_hints = re.compile(
        r"anzeige.*(veroeffentlicht|online|aktiv)|deine anzeige|geschafft",
        re.I,
    )
    with contextlib.suppress(Exception):
        if page.get_by_text(success_hints, exact=False).count() > 0:
            return True

    return submitted and url != start_url and "p-anzeige-aufgeben" not in url
