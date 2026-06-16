"""Agentic item research: identify → verify specs → price, all in one ReAct loop."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from PIL import Image
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import RunUsage, UsageLimits

from ..config import (
    ANTHROPIC_API_KEY,
    API_IMAGE_MAX_PX,
    CLAUDE_MODEL,
    LLM_PROVIDER,
    OLLAMA_HOST,
    OLLAMA_MODEL,
)
from ..core.llm import LLMClient
from ..core.models import (
    EbayListingOptions,
    ItemCondition,
    KleinanzeigenListingOptions,
    PriceInfo,
)
from ..core.web_search import web_search as _ddg_search

JsonDict = dict[str, Any]


class ItemResearchOutput(BaseModel):
    name: str
    brand: str | None
    model: str | None
    condition: ItemCondition
    condition_notes: str
    title_de: str
    description_de: str
    specs: dict[str, str]
    keywords: list[str]
    category: str
    ebay_category_id: str | None = None
    price_info: PriceInfo
    ka_options: KleinanzeigenListingOptions | None
    ebay_options: EbayListingOptions | None


@dataclass
class AgentResult:
    output: ItemResearchOutput
    usage: RunUsage


_SYSTEM_PROMPT = """\
You are an expert at identifying second-hand items for resale on German marketplaces. \
Identify the item from photos. Do not invent specifications — only report what you can \
directly observe or confidently know from the item's visible identity.\
"""

_ANALYZE_PROMPT = """\
Analyze these photos and identify the item.

Return ONLY a JSON object:
{
  "name": "Brand Model (English, concise)",
  "brand": "brand name or null",
  "model": "model name or null",
  "condition": "new|like_new|good|acceptable|poor",
  "condition_notes": "visible wear or damage details",
  "category": "Electronics|Clothing|Books|Toys|Furniture|Sports|Kitchen|Garden|Other",
  "keywords": ["keyword1", "keyword2"]
}

Condition guide: new=unused/sealed, like_new=barely used/no visible wear, \
good=light wear/fully functional, acceptable=noticeable wear/functional, \
poor=heavy wear/defects.

Do NOT include specs, prices, or descriptions — those come from web research.
"""


def _encode_photo(path: Path) -> tuple[str, str]:
    img = Image.open(path).convert("RGB")
    img.thumbnail((API_IMAGE_MAX_PX, API_IMAGE_MAX_PX), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return base64.standard_b64encode(buf.getvalue()).decode(), "image/jpeg"


def _analyze_photos_impl(photos: list[Path], client: LLMClient) -> JsonDict:
    """Vision call: identify item, return identification fields only."""
    content: list[JsonDict] = []
    for photo in photos:
        data, media_type = _encode_photo(photo)
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        })
    content.append({"type": "text", "text": _ANALYZE_PROMPT})

    response = client.messages_create(
        max_tokens=512,
        system=[{"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
    )
    text = response.content[0].text
    start, end = text.find("{"), text.rfind("}") + 1
    return cast(JsonDict, json.loads(text[start:end]))


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

_MAX_AGENT_ITERATIONS = 10

_AGENT_SYSTEM_PROMPT = """\
You are an expert reseller assistant for German marketplaces (Kleinanzeigen, eBay.de).

Your job:
1. Call analyze_photos to identify the item (name, brand, model, condition).
2. Call web_search to look up the exact manufacturer specifications for the identified model.
3. Call web_search to find current prices on kleinanzeigen.de and ebay.de.
4. If any spec or price is unclear, call web_search again with a refined query.
5. When you have enough verified information, produce your final structured output.

Rules:
- NEVER invent specifications. Only include specs confirmed by web search results.
- If a spec cannot be verified, omit it from the specs dict.
- The description_de must only mention specs that appear in your specs dict.
- title_de must be max 60 characters.
- description_de should be 80-150 words, written for a private German seller.
- Suggest a fair price based on condition and current market data.
- For ebay_category_id: provide the numeric eBay Germany category ID that best fits \
the item (e.g. "293" for Bücher, "9355" for Kleidung, "58058" for Kopfhörer). \
If unsure, set to null.
"""


class _AgentDeps:
    """Dependencies injected into agent tools."""

    def __init__(self, photos: list[Path], client: LLMClient) -> None:
        self.photos = photos
        self.client = client


def _build_agent(on_stage: Callable[[str], None] | None = None) -> Agent[_AgentDeps, ItemResearchOutput]:
    model_name = _resolve_model_name()
    agent: Agent[_AgentDeps, ItemResearchOutput] = Agent(
        model_name,
        output_type=ItemResearchOutput,
        deps_type=_AgentDeps,
        system_prompt=_AGENT_SYSTEM_PROMPT,
        retries=2,
    )

    @agent.tool
    def analyze_photos(ctx: RunContext[_AgentDeps]) -> JsonDict:
        """Identify the item from photos. Returns name, brand, model, condition, category, keywords."""
        if on_stage is not None:
            on_stage("analyze_photos")
        return _analyze_photos_impl(ctx.deps.photos, ctx.deps.client)

    @agent.tool
    def web_search(ctx: RunContext[_AgentDeps], query: str, max_results: int = 8) -> str:
        """Search the web. Use for spec lookup and price research. Returns newline-separated snippets."""
        if on_stage is not None:
            on_stage("web_search")
        results = _ddg_search(query, max_results=max_results)
        if not results:
            return "No results found."
        return "\n".join(
            f"- {r['title']}: {r['body'][:200]}"
            for r in results
        )

    return agent


def run_item_research_agent(
    photos: list[Path],
    client: LLMClient,
    on_stage: Callable[[str], None] | None = None,
    on_usage: Callable[[RunUsage, float], None] | None = None,
) -> AgentResult:
    """Run the ReAct agent and return verified item research output with usage stats."""
    agent = _build_agent(on_stage=on_stage)
    deps = _AgentDeps(photos=photos, client=client)

    async def _run() -> AgentResult:
        from pydantic_ai._agent_graph import CallToolsNode, ModelRequestNode  # noqa: PLC0415
        request_start: float | None = None
        async with agent.iter(
            "Research this item and produce a verified listing.",
            deps=deps,
            usage_limits=UsageLimits(request_limit=_MAX_AGENT_ITERATIONS),
        ) as run:
            async for node in run:
                if isinstance(node, ModelRequestNode):
                    request_start = asyncio.get_event_loop().time()
                elif isinstance(node, CallToolsNode):
                    gen_secs = (asyncio.get_event_loop().time() - request_start) if request_start is not None else 0.0
                    request_start = None
                    if on_usage is not None:
                        on_usage(run.usage(), gen_secs)
        return AgentResult(output=run.result.output, usage=run.result.usage())

    return asyncio.run(_run())


def _resolve_model_name() -> str:
    if LLM_PROVIDER == "anthropic":
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is required.")
        return f"anthropic:{CLAUDE_MODEL}"
    if LLM_PROVIDER == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "").strip() or OLLAMA_HOST.strip()
        base_url = base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        os.environ["OLLAMA_BASE_URL"] = base_url
        return f"ollama:{OLLAMA_MODEL}"
    raise RuntimeError(f"Unsupported LLM provider: {LLM_PROVIDER!r}")
