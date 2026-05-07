"""Fallback identification: Google Lens (visual, MCP) and DuckDuckGo (text, direct)."""

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
from ..core.llm import LLMClient
from ..core.web_search import web_search

JsonDict = dict[str, Any]

_IDENTIFICATION_RULES = (
    "Respond with ONLY this line:\n"
    'IDENTIFICATION: {"name": "...", "brand": "...", "model": "..."}\n\n'
    "Rules:\n"
    "- name: full product name, e.g. 'Sony WH-1000XM5 Wireless Headphones'\n"
    "- brand: manufacturer only, e.g. 'Sony', or null if unknown\n"
    "- model: model number/name, e.g. 'WH-1000XM5', or null if unknown\n"
    "- If the item cannot be identified: IDENTIFICATION: {}"
)

_SYSTEM_PROMPT = """\
You are a product identification expert. Given web search snippets, identify the exact \
product name, brand, and model. Prefer the precise manufacturer name over generic descriptions.\
"""


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

    output = _run_lens_agent(
        prompt=prompt,
        system_prompt=(
            "You are an autonomous browser agent identifying a product using Google Lens. "
            "Use Playwright MCP tools only. Extract the product name, brand, and model "
            "from the search results."
        ),
    )
    return _parse_identification(output)


def identify_via_text_search(analysis: JsonDict, client: LLMClient) -> JsonDict:
    """Search DuckDuckGo and use the LLM to verify/correct the current identification.

    Uses the ddgs package directly — no browser automation needed.
    Returns a (possibly empty) dict with any of: name, brand, model.
    """
    query = _build_search_query(analysis)
    if not query:
        return {}

    results = web_search(query, max_results=8)
    if not results:
        return {}

    snippets = "\n".join(
        f"- {r['title']}: {r['body'][:200]}"
        for r in results[:8]
    )

    current_name  = str(analysis.get("name",  "") or "").strip()
    current_brand = str(analysis.get("brand", "") or "").strip()
    current_model = str(analysis.get("model", "") or "").strip()

    if current_name and "unknown" not in current_name.lower():
        context = (
            f"Current identification to verify:\n"
            f"  Name:  {current_name}\n"
            f"  Brand: {current_brand or '—'}\n"
            f"  Model: {current_model or '—'}\n\n"
            "Correct it if the search results show a different or more precise name. "
            "If it is already correct, return the same values."
        )
    else:
        context = "The item has not been identified yet. Identify it from the search results."

    prompt = (
        f"Search query used: {query}\n\n"
        f"Search results:\n{snippets}\n\n"
        f"{context}\n\n"
        "Return ONLY a JSON object:\n"
        '{"name": "...", "brand": "...", "model": "..."}\n\n'
        "Rules:\n"
        "- name: full product name, e.g. 'Sony WH-1000XM5 Wireless Headphones'\n"
        "- brand: manufacturer only, or null\n"
        "- model: model number/name, or null\n"
        "- If you cannot determine the product: return {}"
    )

    response = client.messages_create(
        max_tokens=256,
        system=[{"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text
    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1 or end == 0:
        return {}
    try:
        data: JsonDict = json.loads(text[start:end])
    except json.JSONDecodeError:
        return {}
    return {k: v for k, v in data.items() if v and v != "null"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_search_query(analysis: JsonDict) -> str:
    """Compose a focused search query from the current analysis.

    When a name is already identified: use brand + name + first keyword for context.
    When unidentified: use keywords + category.
    """
    name  = str(analysis.get("name",  "") or "").strip()
    brand = str(analysis.get("brand", "") or "").strip()

    if name and "unknown" not in name.lower():
        parts: list[str] = []
        if brand and brand.lower() not in name.lower():
            parts.append(brand)
        parts.append(name)
        keywords = analysis.get("keywords", [])
        if isinstance(keywords, list) and keywords:
            parts.append(str(keywords[0]))
        return " ".join(parts[:4])

    # Unidentified — fall back to keywords + category.
    kw_parts: list[str] = []
    keywords = analysis.get("keywords", [])
    if isinstance(keywords, list):
        kw_parts.extend(str(k) for k in keywords[:5])
    category = str(analysis.get("category", "")).strip()
    if category and category.lower() not in ("", "other"):
        kw_parts.append(category.lower())

    seen: set[str] = set()
    unique: list[str] = []
    for p in kw_parts:
        key = p.lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(p)
    return " ".join(unique[:6])


def _run_lens_agent(*, prompt: str, system_prompt: str) -> str:
    """Spin up a Playwright MCP agent for Google Lens and return the raw output."""
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
