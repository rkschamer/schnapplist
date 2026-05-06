"""Fallback identification agents: Google Lens (visual) and DuckDuckGo (text)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from platformdirs import user_cache_dir
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio

from ..config import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    LLM_PROVIDER,
    OLLAMA_HOST,
    OLLAMA_MODEL,
)

JsonDict = dict[str, Any]

_IDENTIFICATION_SENTINEL = "IDENTIFICATION:"
_IDENTIFICATION_RULES = (
    "Respond with ONLY this line:\n"
    'IDENTIFICATION: {"name": "...", "brand": "...", "model": "..."}\n\n'
    "Rules:\n"
    "- name: full product name, e.g. 'Sony WH-1000XM5 Wireless Headphones'\n"
    "- brand: manufacturer only, e.g. 'Sony', or null if unknown\n"
    "- model: model number/name, e.g. 'WH-1000XM5', or null if unknown\n"
    "- If the item cannot be identified: IDENTIFICATION: {}"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def identify_via_google_lens(photo: Path) -> JsonDict:
    """Upload *photo* to Google Lens and return identification enrichments.

    Returns a (possibly empty) dict with any of: name, brand, model.
    """
    abs_path = str(photo.expanduser().resolve())

    prompt = (
        "Identify the product in the photo using Google Lens reverse image search.\n\n"
        f"Photo: {abs_path}\n\n"
        "Steps:\n"
        "1. Navigate to https://lens.google.com\n"
        "2. Find and click the camera / upload-image icon\n"
        "3. When the upload option appears, click it to open the file chooser\n"
        f"4. Call browser_file_upload with path: {json.dumps(abs_path)}\n"
        "5. Wait for the results page to load\n"
        "6. Take a snapshot and read the identified product information:\n"
        "   - Check the product title highlighted at the top of results\n"
        "   - Check 'Visual matches' and 'Shopping results' sections\n"
        "   - Note any brand names or model numbers\n\n"
        + _IDENTIFICATION_RULES
    )

    output = _run_agent(
        prompt=prompt,
        system_prompt=(
            "You are an autonomous browser agent identifying a product using Google Lens. "
            "Use Playwright MCP tools only. Extract the product name, brand, and model "
            "from the search results."
        ),
    )
    return _parse_identification(output)


def identify_via_text_search(analysis: JsonDict) -> JsonDict:
    """Build a query from partial LLM analysis and search DuckDuckGo to identify the item.

    Returns a (possibly empty) dict with any of: name, brand, model.
    """
    query = _build_search_query(analysis)
    if not query:
        return {}

    prompt = (
        f"Identify the product by searching DuckDuckGo with this query: {query}\n\n"
        "Steps:\n"
        "1. Navigate to https://duckduckgo.com\n"
        "2. Click the search box, type the query, and press Enter\n"
        "3. Read the search result titles and snippets\n"
        "4. Identify the most likely product being described\n\n"
        + _IDENTIFICATION_RULES
    )

    output = _run_agent(
        prompt=prompt,
        system_prompt=(
            "You are an autonomous browser agent identifying a product via web search. "
            "Use Playwright MCP tools only. Read search result snippets carefully and "
            "extract the most likely product name, brand, and model."
        ),
    )
    return _parse_identification(output)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_search_query(analysis: JsonDict) -> str:
    """Compose a short search query from the LLM's partial analysis."""
    parts: list[str] = []

    keywords = analysis.get("keywords", [])
    if isinstance(keywords, list):
        parts.extend(str(k) for k in keywords[:5])

    category = str(analysis.get("category", "")).strip()
    if category and category.lower() not in ("", "other"):
        parts.append(category.lower())

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for p in parts:
        key = p.lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(p)

    return " ".join(unique[:6])


def _run_agent(*, prompt: str, system_prompt: str) -> str:
    """Spin up a Playwright MCP agent, run *prompt*, and return the raw output."""
    model_name = _resolve_model_name()

    output_dir = Path(user_cache_dir("schnapplist")) / "playwright-mcp"
    output_dir.mkdir(parents=True, exist_ok=True)

    server = MCPServerStdio(
        "npx",
        args=[
            "-y",
            "@playwright/mcp@latest",
            "--output-dir",
            str(output_dir),
            "--save-session",
            "--allow-unrestricted-file-access",
            "--viewport-size",
            "1280x800",
        ],
        timeout=60,
        read_timeout=180,
        max_retries=3,
    )

    agent = Agent(model_name, toolsets=[server], retries=2, system_prompt=system_prompt)
    result = agent.run_sync(prompt)
    return str(result.output).strip()


def _parse_identification(output: str) -> JsonDict:
    """Extract the IDENTIFICATION sentinel dict from agent output."""
    m = re.search(r"IDENTIFICATION:\s*(\{.*?\})", output, re.DOTALL)
    if not m:
        return {}
    try:
        data: JsonDict = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}
    return {k: v for k, v in data.items() if v and v != "null"}


def _resolve_model_name() -> str:
    if LLM_PROVIDER == "anthropic":
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is required for anthropic MCP mode.")
        return f"anthropic:{CLAUDE_MODEL}"

    if LLM_PROVIDER == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "").strip() or OLLAMA_HOST.strip()
        base_url = base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        os.environ["OLLAMA_BASE_URL"] = base_url
        return f"ollama:{OLLAMA_MODEL}"

    raise RuntimeError(
        "Unsupported llm.provider for MCP mode. Use 'anthropic' or 'ollama'."
    )
