"""Search the web for comparable listings and ask Claude for a price recommendation."""

from __future__ import annotations

import importlib
import json
from typing import Any, TypedDict, cast

from .llm import LLMClient
from .models import PriceInfo

_SYSTEM_PROMPT = """\
You are a pricing expert for second-hand goods sold on German marketplaces. \
Given web search results, suggest a competitive yet fair selling price in EUR.\
"""


class SearchResult(TypedDict):
    title: str
    body: str
    href: str


def _web_search(query: str, max_results: int = 12) -> list[SearchResult]:
    """Search via DuckDuckGo. Returns list of {title, body, href} dicts."""
    try:
        ddgs_module = importlib.import_module("ddgs")
        ddgs_class = getattr(ddgs_module, "DDGS")

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
    except (ImportError, AttributeError, TypeError, ValueError, OSError):
        return []


def research_price(keywords: list[str], condition: str, client: LLMClient) -> PriceInfo:
    """Return a PriceInfo by searching for comparable sold/active listings."""
    base_query = " ".join(keywords[:4])
    results: list[SearchResult] = []

    # Search German marketplaces first
    de_results = _web_search(f"{base_query} site:kleinanzeigen.de OR site:ebay.de", max_results=8)
    results.extend(de_results)

    # Fallback to general search in German
    if len(results) < 4:
        de_general = _web_search(f"{base_query} gebraucht Preis kaufen", max_results=6)
        results.extend(de_general)

    # Deduplicate by href
    seen: set[str] = set()
    unique: list[SearchResult] = []
    for r in results:
        href = r.get("href", "")
        if href not in seen:
            seen.add(href)
            unique.append(r)

    snippets = "\n".join(
        f"- {r.get('title','')}: {r.get('body','')[:180]}"
        for r in unique[:10]
    )

    prompt = (
        f'Item: "{base_query}"\n'
        f"Condition: {condition}\n\n"
        f"Search results:\n{snippets or 'No results found.'}\n\n"
        "Based on these results, suggest a fair selling price for a private German seller.\n"
        "Return ONLY a JSON object:\n"
        "{\n"
        '  "suggested_price": 25.00,\n'
        '  "min_price": 15.00,\n'
        '  "max_price": 35.00,\n'
        '  "currency": "EUR",\n'
        '  "reasoning": "one-sentence rationale"\n'
        "}"
    )

    response = client.messages_create(
        max_tokens=384,
        system=[
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text
    start, end = text.find("{"), text.rfind("}") + 1
    data = cast(dict[str, Any], json.loads(text[start:end]))
    return PriceInfo(**data)
