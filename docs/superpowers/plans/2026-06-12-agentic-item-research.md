# Agentic Item Research Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three pipeline stages `analyze_item()` → `identify_via_text_search()` → `research_price()` with a single pydantic-ai ReAct agent that identifies items from photos, verifies specs via web search, and researches prices — never inventing specs it cannot confirm.

**Architecture:** A pydantic-ai `Agent` with three tools (`analyze_photos`, `web_search`, `finish`) runs a multi-turn ReAct loop. The pipeline calls it through a thin `run_item_research_agent()` function. On failure the pipeline invokes a new `DecisionCallback` to ask the user whether to retry or skip the item.

**Tech Stack:** Python 3.11+, pydantic-ai 1.66, pydantic v2, DuckDuckGo (ddgs), existing `LLMClient` (litellm), Rich (CLI prompts).

---

## File Map

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `schnapplist/workflows/item_research_agent.py` | Agent definition, tools, `ItemResearchOutput`, `run_item_research_agent()` |
| Modify | `schnapplist/workflows/process_pipeline.py` | Accept `DecisionCallback`, replace three stages with agent call |
| Modify | `schnapplist/services/process_service.py` | Thread `DecisionCallback` from CLI through to workflow |
| Modify | `schnapplist/cli/__init__.py` | Implement CLI `DecisionCallback` (Rich prompt) |
| Create | `tests/test_item_research_agent.py` | Unit tests for agent tools and output schema |
| Create | `tests/test_process_pipeline_agent.py` | Pipeline integration tests for retry/skip behaviour |

---

## Task 1: `ItemResearchOutput` schema

**Files:**
- Create: `schnapplist/workflows/item_research_agent.py`
- Test: `tests/test_item_research_agent.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_item_research_agent.py
from __future__ import annotations

from schnapplist.core.models import ItemCondition, KleinanzeigenListingOptions, PriceInfo
from schnapplist.workflows.item_research_agent import ItemResearchOutput


def test_item_research_output_round_trips():
    output = ItemResearchOutput(
        name="Sony WH-1000XM5",
        brand="Sony",
        model="WH-1000XM5",
        condition=ItemCondition.GOOD,
        condition_notes="Minor scratches on headband",
        title_de="Sony WH-1000XM5 Kopfhörer",
        description_de="Hochwertige Kopfhörer mit aktiver Geräuschunterdrückung.",
        specs={"Typ": "Over-Ear", "Konnektivität": "Bluetooth 5.2"},
        keywords=["Sony", "Kopfhörer", "ANC"],
        category="Electronics",
        price_info=PriceInfo(
            suggested_price=180.0,
            min_price=150.0,
            max_price=220.0,
            reasoning="Current market",
        ),
        ka_options=KleinanzeigenListingOptions(),
        ebay_options=None,
    )
    assert output.name == "Sony WH-1000XM5"
    assert output.specs["Typ"] == "Over-Ear"
    assert output.ka_options is not None


def test_item_research_output_minimal():
    """ka_options and ebay_options can both be None."""
    output = ItemResearchOutput(
        name="Unknown",
        brand=None,
        model=None,
        condition=ItemCondition.ACCEPTABLE,
        condition_notes="",
        title_de="Unbekanntes Gerät",
        description_de="Beschreibung fehlt.",
        specs={},
        keywords=[],
        category="Other",
        price_info=PriceInfo(
            suggested_price=5.0,
            min_price=1.0,
            max_price=10.0,
            reasoning="No data",
        ),
        ka_options=None,
        ebay_options=None,
    )
    assert output.brand is None
    assert output.specs == {}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_item_research_agent.py -v
```
Expected: `ImportError: cannot import name 'ItemResearchOutput'`

- [ ] **Step 3: Create `item_research_agent.py` with the schema only**

```python
# schnapplist/workflows/item_research_agent.py
"""Agentic item research: identify → verify specs → price, all in one ReAct loop."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..core.models import (
    EbayListingOptions,
    ItemCondition,
    KleinanzeigenListingOptions,
    PriceInfo,
)

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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_item_research_agent.py -v
```
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add schnapplist/workflows/item_research_agent.py tests/test_item_research_agent.py
git commit -m "feat: add ItemResearchOutput schema"
```

---

## Task 2: `analyze_photos` tool (vision call)

**Files:**
- Modify: `schnapplist/workflows/item_research_agent.py`
- Test: `tests/test_item_research_agent.py`

The `analyze_photos` tool replicates the vision logic from `item_analyzer.analyze_item()` but returns only identification fields (name, brand, model, condition, condition_notes, category, keywords) — **no specs, no description**.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_item_research_agent.py`:

```python
from unittest.mock import MagicMock, patch
from pathlib import Path
from schnapplist.workflows.item_research_agent import _analyze_photos_impl


def test_analyze_photos_returns_identification(tmp_path):
    # Create a minimal 1x1 JPEG
    from PIL import Image
    img = Image.new("RGB", (1, 1), color=(128, 128, 128))
    photo = tmp_path / "test.jpg"
    img.save(photo, "JPEG")

    mock_client = MagicMock()
    mock_client.messages_create.return_value = MagicMock(
        content=[MagicMock(text='{"name": "Sony WH-1000XM5", "brand": "Sony", '
                                '"model": "WH-1000XM5", "condition": "good", '
                                '"condition_notes": "light wear", '
                                '"category": "Electronics", '
                                '"keywords": ["Sony", "Headphones"]}')]
    )

    result = _analyze_photos_impl([photo], mock_client)

    assert result["name"] == "Sony WH-1000XM5"
    assert result["brand"] == "Sony"
    assert "specs" not in result
    assert "description_de" not in result
    mock_client.messages_create.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_item_research_agent.py::test_analyze_photos_returns_identification -v
```
Expected: `ImportError: cannot import name '_analyze_photos_impl'`

- [ ] **Step 3: Implement `_analyze_photos_impl`**

Add to `schnapplist/workflows/item_research_agent.py` after the imports:

```python
import base64
import io

from PIL import Image

from ..config import API_IMAGE_MAX_PX
from ..core.llm import LLMClient

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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_item_research_agent.py::test_analyze_photos_returns_identification -v
```
Expected: PASSED

- [ ] **Step 5: Commit**

```bash
git add schnapplist/workflows/item_research_agent.py tests/test_item_research_agent.py
git commit -m "feat: add _analyze_photos_impl vision tool"
```

---

## Task 3: Build the pydantic-ai agent

**Files:**
- Modify: `schnapplist/workflows/item_research_agent.py`
- Test: `tests/test_item_research_agent.py`

The agent uses `pydantic-ai`'s `Agent` with `result_type=ItemResearchOutput`. Tools are defined as `@agent.tool` functions. The agent is instantiated once per call inside `run_item_research_agent()` so that the photo list can be captured in the closure.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_item_research_agent.py`:

```python
from unittest.mock import MagicMock, patch
from pathlib import Path
from schnapplist.workflows.item_research_agent import run_item_research_agent
from schnapplist.core.models import ItemCondition


def test_run_item_research_agent_returns_output(tmp_path):
    from PIL import Image
    img = Image.new("RGB", (1, 1))
    photo = tmp_path / "item.jpg"
    img.save(photo, "JPEG")

    mock_output = MagicMock()
    mock_output.output = MagicMock(spec=["name", "brand", "model", "condition",
        "condition_notes", "title_de", "description_de", "specs", "keywords",
        "category", "price_info", "ka_options", "ebay_options"])
    mock_output.output.name = "Canon EOS 400D"

    mock_client = MagicMock()

    with patch(
        "schnapplist.workflows.item_research_agent._build_agent"
    ) as mock_build:
        mock_agent = MagicMock()
        mock_agent.run_sync.return_value = mock_output
        mock_build.return_value = mock_agent

        result = run_item_research_agent([photo], mock_client)

    assert result.name == "Canon EOS 400D"
    mock_agent.run_sync.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_item_research_agent.py::test_run_item_research_agent_returns_output -v
```
Expected: `ImportError: cannot import name 'run_item_research_agent'`

- [ ] **Step 3: Implement the agent and `run_item_research_agent`**

Add to `schnapplist/workflows/item_research_agent.py`:

```python
import json
import os
from typing import cast

from pydantic_ai import Agent, RunContext

from ..config import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    DEFAULT_MARKETPLACE,
    LLM_PROVIDER,
    OLLAMA_HOST,
    OLLAMA_MODEL,
)
from ..core.web_search import web_search as _ddg_search

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
"""


class _AgentDeps:
    """Dependencies injected into agent tools."""

    def __init__(self, photos: list[Path], client: LLMClient) -> None:
        self.photos = photos
        self.client = client


def _build_agent() -> Agent[_AgentDeps, ItemResearchOutput]:
    model_name = _resolve_model_name()
    agent: Agent[_AgentDeps, ItemResearchOutput] = Agent(
        model_name,
        result_type=ItemResearchOutput,
        system_prompt=_AGENT_SYSTEM_PROMPT,
        retries=_MAX_AGENT_ITERATIONS,
    )

    @agent.tool
    def analyze_photos(ctx: RunContext[_AgentDeps]) -> JsonDict:
        """Identify the item from photos. Returns name, brand, model, condition, category, keywords."""
        return _analyze_photos_impl(ctx.deps.photos, ctx.deps.client)

    @agent.tool
    def web_search(ctx: RunContext[_AgentDeps], query: str, max_results: int = 8) -> str:
        """Search the web. Use for spec lookup and price research. Returns newline-separated snippets."""
        results = _ddg_search(query, max_results=max_results)
        if not results:
            return "No results found."
        return "\n".join(
            f"- {r['title']}: {r['body'][:200]}"
            for r in results
        )

    return agent


def run_item_research_agent(photos: list[Path], client: LLMClient) -> ItemResearchOutput:
    """Run the ReAct agent and return verified item research output."""
    agent = _build_agent()
    deps = _AgentDeps(photos=photos, client=client)
    result = agent.run_sync("Research this item and produce a verified listing.", deps=deps)
    return result.output


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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_item_research_agent.py -v
```
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add schnapplist/workflows/item_research_agent.py tests/test_item_research_agent.py
git commit -m "feat: implement ItemResearchAgent with analyze_photos and web_search tools"
```

---

## Task 4: `DecisionCallback` and pipeline wiring

**Files:**
- Modify: `schnapplist/workflows/process_pipeline.py`
- Create: `tests/test_process_pipeline_agent.py`

The `DecisionCallback` Protocol is added to `process_pipeline.py`. `ProcessWorkflow.__init__` gains an optional `on_decision` parameter. A default implementation always returns `"skip"` (used in tests and headless mode).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_process_pipeline_agent.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from schnapplist.workflows.process_pipeline import DecisionCallback, ProcessWorkflow


def _make_mock_client():
    return MagicMock()


def _make_mock_output(name: str = "Test Item"):
    from schnapplist.core.models import (
        ItemCondition, KleinanzeigenListingOptions, PriceInfo,
        KaShipping, KaPriceType,
    )
    from schnapplist.workflows.item_research_agent import ItemResearchOutput
    return ItemResearchOutput(
        name=name,
        brand="TestBrand",
        model="X1",
        condition=ItemCondition.GOOD,
        condition_notes="",
        title_de=f"{name} zu verkaufen",
        description_de="Ein gutes Gerät.",
        specs={"RAM": "4 GB"},
        keywords=["test"],
        category="Electronics",
        price_info=PriceInfo(
            suggested_price=50.0, min_price=40.0, max_price=60.0, reasoning="market"
        ),
        ka_options=KleinanzeigenListingOptions(
            shipping=KaShipping.VERSAND, price_type=KaPriceType.FESTPREIS
        ),
        ebay_options=None,
    )


def test_pipeline_uses_agent_on_success(tmp_path):
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir()
    from PIL import Image
    img = Image.new("RGB", (10, 10))
    img.save(photos_dir / "item.jpg", "JPEG")

    mock_output = _make_mock_output("Canon EOS")

    with (
        patch("schnapplist.workflows.process_pipeline.group_photos_by_item", return_value=[[photos_dir / "item.jpg"]]),
        patch("schnapplist.workflows.process_pipeline.filter_redundant_photos", return_value=[photos_dir / "item.jpg"]),
        patch("schnapplist.workflows.process_pipeline.enhance_photo", return_value=photos_dir / "item.jpg"),
        patch("schnapplist.workflows.process_pipeline.run_item_research_agent", return_value=mock_output),
        patch("schnapplist.workflows.process_pipeline.write_item_report"),
    ):
        result = ProcessWorkflow(_make_mock_client()).run(
            photos_dir=photos_dir,
            output_dir=tmp_path / "output",
            single_item=False,
        )

    assert len(result.items) == 1
    assert result.items[0].name == "Canon EOS"


def test_pipeline_calls_decision_callback_on_agent_failure(tmp_path):
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir()
    from PIL import Image
    img = Image.new("RGB", (10, 10))
    img.save(photos_dir / "item.jpg", "JPEG")

    decision_cb = MagicMock(return_value="skip")

    with (
        patch("schnapplist.workflows.process_pipeline.group_photos_by_item", return_value=[[photos_dir / "item.jpg"]]),
        patch("schnapplist.workflows.process_pipeline.filter_redundant_photos", return_value=[photos_dir / "item.jpg"]),
        patch("schnapplist.workflows.process_pipeline.enhance_photo", return_value=photos_dir / "item.jpg"),
        patch("schnapplist.workflows.process_pipeline.run_item_research_agent", side_effect=RuntimeError("LLM error")),
        patch("schnapplist.workflows.process_pipeline.write_item_report"),
    ):
        result = ProcessWorkflow(_make_mock_client(), on_decision=decision_cb).run(
            photos_dir=photos_dir,
            output_dir=tmp_path / "output",
            single_item=False,
        )

    decision_cb.assert_called_once()
    assert result.items == []


def test_pipeline_retries_then_skips(tmp_path):
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir()
    from PIL import Image
    img = Image.new("RGB", (10, 10))
    img.save(photos_dir / "item.jpg", "JPEG")

    # always retry, exhausts retries, item is skipped
    decision_cb = MagicMock(return_value="retry")

    with (
        patch("schnapplist.workflows.process_pipeline.group_photos_by_item", return_value=[[photos_dir / "item.jpg"]]),
        patch("schnapplist.workflows.process_pipeline.filter_redundant_photos", return_value=[photos_dir / "item.jpg"]),
        patch("schnapplist.workflows.process_pipeline.enhance_photo", return_value=photos_dir / "item.jpg"),
        patch("schnapplist.workflows.process_pipeline.run_item_research_agent", side_effect=RuntimeError("LLM error")),
        patch("schnapplist.workflows.process_pipeline.write_item_report"),
    ):
        result = ProcessWorkflow(_make_mock_client(), on_decision=decision_cb).run(
            photos_dir=photos_dir,
            output_dir=tmp_path / "output",
            single_item=False,
        )

    # 1 initial attempt + 2 retries = 3 total calls to decision_cb
    assert decision_cb.call_count == 3
    assert result.items == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_process_pipeline_agent.py -v
```
Expected: `ImportError: cannot import name 'DecisionCallback'`

- [ ] **Step 3: Add `DecisionCallback` Protocol to `process_pipeline.py`**

Add after the existing `ProgressCallback` Protocol in `schnapplist/workflows/process_pipeline.py`:

```python
class DecisionCallback(Protocol):
    """Called when an item fails processing. Returns 'retry' or 'skip'."""

    def __call__(self, event: str, **kwargs: Any) -> str: ...


def _default_decision_callback(event: str, **kwargs: Any) -> str:
    return "skip"
```

- [ ] **Step 4: Update `ProcessWorkflow.__init__` to accept `on_decision`**

In `schnapplist/workflows/process_pipeline.py`, update `ProcessWorkflow.__init__`:

```python
def __init__(
    self,
    client: LLMClient,
    on_progress: ProgressCallback | None = None,
    on_decision: DecisionCallback | None = None,
) -> None:
    self._client = client
    self._on_progress = on_progress
    self._on_decision = on_decision or _default_decision_callback
```

- [ ] **Step 5: Run tests to verify they fail with a new error**

```bash
uv run pytest tests/test_process_pipeline_agent.py -v
```
Expected: errors about `run_item_research_agent` not being imported in pipeline.

- [ ] **Step 6: Commit Protocol and constructor change**

```bash
git add schnapplist/workflows/process_pipeline.py tests/test_process_pipeline_agent.py
git commit -m "feat: add DecisionCallback protocol to ProcessWorkflow"
```

---

## Task 5: Replace pipeline stages with agent call

**Files:**
- Modify: `schnapplist/workflows/process_pipeline.py`

Replace the `analyze_item` / `identify_via_text_search` / `research_price` block in `ProcessWorkflow.run()` with a call to `run_item_research_agent`, wrapped in retry/skip logic.

- [ ] **Step 1: Add import at the top of `process_pipeline.py`**

Add to the imports block:

```python
from .item_research_agent import ItemResearchOutput, run_item_research_agent
```

- [ ] **Step 2: Replace the three-stage block in `ProcessWorkflow.run()`**

Find the section in `run()` that starts with `self._emit("item_stage", idx=idx, stage="analyze")` and ends after the `research_price` stage (before `item = build_item(...)`). Replace it entirely with:

```python
self._emit("item_stage", idx=idx, stage="analyze")
_MAX_RETRIES = 2
attempts = 0
agent_output: ItemResearchOutput | None = None
while agent_output is None:
    try:
        filtered_for_agent = list(filtered)
        agent_output = self._run_stage(
            item_state.stage_records,
            "item_research_agent",
            lambda fa=filtered_for_agent: run_item_research_agent(fa, self._client),
        )
        item_state.item_name = agent_output.name
        item_state.condition = agent_output.condition.value
    except Exception as exc:
        decision = self._on_decision(
            "item_failed",
            idx=idx,
            name=item_state.item_name or f"Item {idx}",
            error=str(exc),
        )
        if decision == "retry" and attempts < _MAX_RETRIES:
            attempts += 1
            continue
        # skip
        self._emit("warning", message=f"Skipping item {idx}: {exc}")
        break

if agent_output is None:
    continue
```

- [ ] **Step 3: Run all pipeline tests**

```bash
uv run pytest tests/test_process_pipeline_agent.py -v
```
Expected: all 3 PASSED

- [ ] **Step 4: Update `build_item` call to use agent output**

After the retry loop, replace the old `build_item(analysis, ...)` call with one that maps `ItemResearchOutput` fields. In `process_pipeline.py`, find `item = build_item(analysis, filtered, enhanced)` and replace with:

```python
from ..core.item_analyzer import build_item as _build_item_from_analysis

_analysis_dict: dict[str, Any] = {
    "name": agent_output.name,
    "brand": agent_output.brand,
    "model": agent_output.model,
    "condition": agent_output.condition.value,
    "condition_notes": agent_output.condition_notes,
    "title_de": agent_output.title_de,
    "description_de": agent_output.description_de,
    "keywords": agent_output.keywords,
    "category": agent_output.category,
}
# Pass marketplace options through to build_item via the analysis dict
if agent_output.ka_options is not None:
    o = agent_output.ka_options
    _analysis_dict["ka_category"] = o.ka_category
    _analysis_dict["ka_shipping"] = o.shipping.value
    _analysis_dict["ka_shipping_methods"] = o.shipping_methods
    _analysis_dict["ka_price_type"] = o.price_type.value
if agent_output.ebay_options is not None:
    o2 = agent_output.ebay_options
    _analysis_dict["ebay_listing_type"] = o2.listing_type.value
    _analysis_dict["ebay_duration_days"] = o2.duration_days
    _analysis_dict["ebay_reserve_price"] = o2.reserve_price

item = _build_item_from_analysis(_analysis_dict, filtered, enhanced)
item.price_info = agent_output.price_info
if marketplace:
    item.marketplace = marketplace
```

- [ ] **Step 5: Remove old stage imports that are no longer called by the pipeline**

In `process_pipeline.py`, remove the imports:

```python
from ..core.item_analyzer import analyze_item, build_item, is_low_confidence
from ..core.price_researcher import research_price
```

Replace with:

```python
from ..core.item_analyzer import build_item
```

Also update the alias in Step 4 from `_build_item_from_analysis` to `build_item` — the import above already names it `build_item`, so remove the `as _build_item_from_analysis` alias and call `build_item(...)` directly in the Step 4 code block.

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest -v
```
Expected: all PASSED

- [ ] **Step 7: Commit**

```bash
git add schnapplist/workflows/process_pipeline.py
git commit -m "feat: replace analyze/search/price stages with ItemResearchAgent"
```

---

## Task 6: CLI `DecisionCallback` (Rich prompt)

**Files:**
- Modify: `schnapplist/cli/__init__.py`
- Modify: `schnapplist/services/process_service.py`

- [ ] **Step 1: Add `_RichDecisionCallback` to `cli/__init__.py`**

Add after `_RichProgressCallback` class:

```python
class _RichDecisionCallback:
    """Prompts the user via Rich when an item fails."""

    def __init__(self, console: Console) -> None:
        self._console = console

    def __call__(self, event: str, **kwargs: Any) -> str:
        if event == "item_failed":
            idx = kwargs.get("idx", "?")
            name = kwargs.get("name", "unknown")
            error = kwargs.get("error", "")
            self._console.print(
                f"\n[yellow]⚠[/yellow] Agent failed for item {idx} "
                f"([bold]{name}[/bold]): {error}"
            )
            choice = click.prompt(
                "  What would you like to do?",
                type=click.Choice(["r", "s"], case_sensitive=False),
                default="s",
                show_choices=True,
                prompt_suffix=" ([r]etry / [s]kip) ",
            )
            return "retry" if choice == "r" else "skip"
        return "skip"
```

- [ ] **Step 2: Update `process_service.run_process` signature**

In `schnapplist/services/process_service.py`, add `on_decision` parameter:

```python
from ..workflows.process_pipeline import (
    DecisionCallback,
    ProcessRunResult,
    ProcessWorkflow,
    ProgressCallback,
)


def run_process(
    photos_dir: Path,
    output_dir: Path,
    *,
    single_item: bool = False,
    marketplace: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    ollama_host: str | None = None,
    on_progress: ProgressCallback | None = None,
    on_decision: DecisionCallback | None = None,
) -> ProcessRunResult:
    # ... existing LLMClient construction unchanged ...
    workflow = ProcessWorkflow(client, on_progress=on_progress, on_decision=on_decision)
    return workflow.run(
        photos_dir=photos_dir,
        output_dir=output_dir,
        single_item=single_item,
        marketplace=marketplace,
    )
```

- [ ] **Step 3: Wire `_RichDecisionCallback` into the `process` CLI command**

In `cli/__init__.py`, find the `process` command body and update the `run_process` call:

```python
rich_cb = _RichProgressCallback()
decision_cb = _RichDecisionCallback(console)
try:
    with rich_cb:
        result = run_process(
            photos_dir=photos_dir,
            output_dir=output_dir,
            single_item=single_item,
            marketplace=marketplace,
            llm_provider=llm_provider,
            llm_model=llm_model,
            ollama_host=ollama_host,
            on_progress=rich_cb,
            on_decision=decision_cb,
        )
```

- [ ] **Step 4: Run full test suite**

```bash
uv run pytest -v
```
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add schnapplist/cli/__init__.py schnapplist/services/process_service.py
git commit -m "feat: add CLI DecisionCallback with Rich retry/skip prompt"
```

---

## Task 7: Smoke test with real photos

This is a manual integration test — no automated test required.

- [ ] **Step 1: Run with a real photo folder**

```bash
uv run schnapplist process --photos-dir ./photos --single-item
```

- [ ] **Step 2: Verify the report**

Open the generated report and check:
- Specs in `description_de` are accurate (cross-check with a quick web search)
- No invented specs like wrong RAM/storage values
- Price range is plausible

- [ ] **Step 3: Trigger the error path**

Temporarily set an invalid `ANTHROPIC_API_KEY` in `.env`, run process, confirm the Rich prompt appears with retry/skip options, choose skip, confirm the item is omitted from the report.

- [ ] **Step 4: Restore `.env` and commit if any adjustments were needed**

```bash
git add -p
git commit -m "fix: <any adjustments from smoke test>"
```
