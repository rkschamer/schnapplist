"""DuckDuckGo search helper used by the item research agent."""

from __future__ import annotations

import importlib
import time
from typing import Any, TypedDict, cast

_SEARCH_RETRIES = 3
_RETRY_DELAY_S = 2.0


class SearchResult(TypedDict):
    title: str
    body: str
    href: str


def web_search(query: str, max_results: int = 12) -> list[SearchResult]:
    """Search via DuckDuckGo with retries. Returns list of {title, body, href} dicts."""
    try:
        ddgs_module = importlib.import_module("ddgs")
        ddgs_class = ddgs_module.DDGS
    except (ImportError, AttributeError):
        return []

    last_exc: Exception | None = None
    for attempt in range(_SEARCH_RETRIES):
        try:
            with ddgs_class() as ddgs:
                raw_results = ddgs.text(query, max_results=max_results)

            normalized: list[SearchResult] = []
            for raw_any in raw_results:
                if not isinstance(raw_any, dict):
                    continue
                raw = cast(dict[str, Any], raw_any)
                normalized.append(
                    {
                        "title": str(raw.get("title", "")),
                        "body": str(raw.get("body", "")),
                        "href": str(raw.get("href", "")),
                    }
                )
            return normalized
        except Exception as exc:  # noqa: BLE001 — DDGS raises various internal exceptions
            last_exc = exc
            if attempt < _SEARCH_RETRIES - 1:
                time.sleep(_RETRY_DELAY_S)

    _ = last_exc  # available for debugging; not re-raised
    return []
