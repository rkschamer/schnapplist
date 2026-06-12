# Agentic Item Research Agent — Design Spec

**Date:** 2026-06-12
**Status:** Approved

---

## Problem

The current pipeline runs item identification and spec extraction in a single one-shot vision call (`analyze_item()`). The model correctly identifies brand and model from photos but invents plausible-sounding specs it cannot actually read (e.g. "2 GB RAM" instead of "1 GB RAM"). This is the dominant source of inaccurate listings.

---

## Solution

Replace the three pipeline stages `analyze_item()` → `identify_via_text_search()` → `research_price()` with a single **`ItemResearchAgent`** — a pydantic-ai ReAct agent that owns the full identify → verify specs → research price loop.

---

## Pipeline Integration

Everything before and after the agent is unchanged:

```
scan_photos → group_photos → filter_redundant → enhance_photos
    → ItemResearchAgent.run(photos)   ← replaces three stages
    → build_item() → write_item_report()
```

The agent is invoked via a thin function:

```python
def run_item_research_agent(photos: list[Path], client: LLMClient) -> ItemResearchOutput
```

---

## Agent Design

### Framework

`pydantic-ai` Agent with structured output — consistent with the existing `image_search_agent.py`. The ReAct loop runs natively: tool call results are injected as conversation turns until the agent produces the final structured output.

### Tools

| Tool | Purpose |
|------|---------|
| `analyze_photos` | One-shot vision call. Returns name, brand, model, condition, condition_notes, category, keywords. No specs, no description. |
| `web_search(query, max_results)` | DuckDuckGo search via existing `web_search.py` helper. Used for spec lookup, price research, and any verification follow-up. |
| `finish(output)` | Structured output tool. The agent writes `title_de`, `description_de`, and all other output fields in its reasoning turn, then calls `finish` to commit the result. No further generation happens after `finish` is called. |

### Loop behaviour

```
analyze_photos(photos)
  → identify: brand, model, condition
web_search("{brand} {model} specs")
  → extract verified specs
web_search("{brand} {model} site:kleinanzeigen.de OR site:ebay.de")
  → gather price signals
[optional further web_search calls for uncertain specs or prices]
finish(verified_output)
```

### System prompt constraints

- Never invent specs — only include values confirmed by search results.
- If a spec cannot be verified, omit it from the output.
- If the item cannot be identified from photos, broaden the search using keywords and category before giving up.
- Max iterations: 10 (guard against runaway loops).

---

## Output Schema

```python
class ItemResearchOutput(BaseModel):
    name: str
    brand: str | None
    model: str | None
    condition: ItemCondition
    condition_notes: str
    title_de: str                      # max 60 chars
    description_de: str                # 80-150 words, uses only verified specs
    specs: dict[str, str]              # verified only, e.g. {"RAM": "1 GB"}
    keywords: list[str]
    category: str
    price_info: PriceInfo
    ka_options: KleinanzeigenListingOptions | None
    ebay_options: EbayListingOptions | None
```

---

## Error Handling

The automatic silent fallback to the old pipeline is removed. Instead, failures are surfaced to the user interactively via a new `DecisionCallback`.

### DecisionCallback

```python
class DecisionCallback(Protocol):
    def __call__(self, event: str, **kwargs: Any) -> str: ...
```

On agent failure, the pipeline calls:

```python
decision_callback("item_failed", idx=idx, name=..., error=...)
# returns "retry" | "skip"
```

The pipeline loops on `"retry"` (max 2 retries) and skips the item on `"skip"`.

`ProcessWorkflow` accepts `DecisionCallback` as an optional constructor argument alongside the existing `ProgressCallback`. If not provided (e.g. in tests), a default implementation always returns `"skip"`.

### CLI implementation

```
⚠ Agent failed for item 2 (Sony Vaio): <error message>
  [r] Retry   [s] Skip
```

Rendered via Rich. The NiceGUI UI shows an equivalent modal dialog.

---

## File Location

```
schnapplist/workflows/item_research_agent.py   ← new agent
schnapplist/workflows/process_pipeline.py      ← updated to call agent + DecisionCallback
schnapplist/cli/__init__.py                    ← DecisionCallback implementation (CLI)
schnapplist/ui/                                ← DecisionCallback implementation (UI)
```

The existing `item_analyzer.py`, `price_researcher.py`, and `image_search_agent.py` remain in place — they are no longer called by the pipeline but are kept for reference and potential direct use.

---

## Out of Scope

- Changes to photo grouping, enhancement, or report generation.
- Changes to posting/provider logic.
- Replacing the Google Lens fallback path (stays for direct calls if needed).
