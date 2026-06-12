"""Agentic item research: identify → verify specs → price, all in one ReAct loop."""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import BaseModel

from ..core.models import (
    EbayListingOptions,
    ItemCondition,
    KleinanzeigenListingOptions,
    PriceInfo,
)
from ..config import API_IMAGE_MAX_PX
from ..core.llm import LLMClient

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
    price_info: PriceInfo
    ka_options: KleinanzeigenListingOptions | None
    ebay_options: EbayListingOptions | None


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
    import json
    from typing import cast

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
