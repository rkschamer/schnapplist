# Agent Confidence & Configurable Iterations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `confidence` / `confidence_notes` to `ItemResearchOutput`, expose `[agent]` config in `schnapplist.toml`, and surface low-confidence items with a `⚠` icon in the CLI display and a badge in the Markdown report.

**Architecture:** `confidence` and `confidence_notes` are added to the pydantic output schema; the agent system prompt gains a stop-early rule and a confidence rubric. `AGENT_TARGET_CONFIDENCE` and `AGENT_MAX_ITERATIONS` are read from `schnapplist.toml` via `config.py` and passed into the agent call. The pipeline computes `low_confidence` and adds it to the `item_done` event; the CLI display and report generator consume it.

**Tech Stack:** Python 3.11+, pydantic v2, pydantic-ai 1.66, Rich, existing config/toml loading pattern.

---

## File Map

| Action | Path | Change |
|--------|------|--------|
| Modify | `schnapplist/workflows/item_research_agent.py` | Add `confidence`/`confidence_notes` to schema; update system prompt with stop-early + rubric; add `max_iterations`/`target_confidence` params to `run_item_research_agent` |
| Modify | `schnapplist/config.py` | Add `AGENT_TARGET_CONFIDENCE`, `AGENT_MAX_ITERATIONS` |
| Modify | `schnapplist.toml` | Add `[agent]` section |
| Modify | `schnapplist/workflows/process_pipeline.py` | Compute `low_confidence`; add to `item_done` event; pass config params to agent |
| Modify | `schnapplist/cli/display.py` | Add `low_confidence`/`confidence` to `ItemRow`; update `item_done` handler and `_render_items` |
| Modify | `schnapplist/core/report_generator.py` | Add confidence row + Recherche note when below target |
| Modify | `tests/test_item_research_agent.py` | Add `confidence`/`confidence_notes` to existing test fixtures; add new tests |
| Modify | `tests/test_process_pipeline_agent.py` | Add `low_confidence` kwarg assertions |

---

## Task 1: Add `confidence` fields to `ItemResearchOutput`

**Files:**
- Modify: `schnapplist/workflows/item_research_agent.py`
- Test: `tests/test_item_research_agent.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_item_research_agent.py`:

```python
def test_item_research_output_has_confidence_fields():
    from schnapplist.workflows.item_research_agent import ItemResearchOutput
    from schnapplist.core.models import ItemCondition, PriceInfo

    output = ItemResearchOutput(
        name="Test",
        brand=None,
        model=None,
        condition=ItemCondition.GOOD,
        condition_notes="",
        title_de="Test",
        description_de="Test.",
        specs={},
        keywords=[],
        category="Other",
        price_info=PriceInfo(suggested_price=1.0, min_price=1.0, max_price=1.0, reasoning=""),
        ka_options=None,
        ebay_options=None,
        confidence=0.6,
        confidence_notes="Model uncertain",
    )
    assert output.confidence == 0.6
    assert output.confidence_notes == "Model uncertain"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_item_research_agent.py::test_item_research_output_has_confidence_fields -v
```
Expected: `TypeError: ... unexpected keyword argument 'confidence'`

- [ ] **Step 3: Add fields to `ItemResearchOutput`**

In `schnapplist/workflows/item_research_agent.py`, find `ItemResearchOutput` and add after `ebay_options`:

```python
confidence: float = 0.5           # 0.0–1.0, agent self-rated
confidence_notes: str = ""        # one sentence explaining the rating
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_item_research_agent.py::test_item_research_output_has_confidence_fields -v
```
Expected: PASSED

- [ ] **Step 5: Update the two existing `ItemResearchOutput` constructors in the test file** to include the new fields (they will fail without defaults otherwise — but since the fields have defaults, existing tests still pass; verify with a full run)

```bash
uv run pytest -v
```
Expected: all PASSED

- [ ] **Step 6: Commit**

```bash
git add schnapplist/workflows/item_research_agent.py tests/test_item_research_agent.py
git commit -m "feat: add confidence and confidence_notes to ItemResearchOutput"
```

---

## Task 2: Config — `AGENT_TARGET_CONFIDENCE` and `AGENT_MAX_ITERATIONS`

**Files:**
- Modify: `schnapplist/config.py`
- Modify: `schnapplist.toml`

No automated test needed — config loading is verified by the pipeline tests in Tasks 4+.

- [ ] **Step 1: Add `[agent]` section to `schnapplist.toml`**

Add after the existing `[listing]` section:

```toml
[agent]
# Confidence threshold (0.0–1.0). Items below this are flagged in the CLI and report.
target_confidence = 0.8
# Maximum LLM turns per item (safety cap).
max_iterations = 10
```

- [ ] **Step 2: Add config constants to `schnapplist/config.py`**

After the `_llm` and `_listing` lines (around line 38), add:

```python
_agent = _toml.get("agent", {})
```

Then after the `LISTING_DISCLAIMER` / `DEFAULT_MARKETPLACE` block, add:

```python
AGENT_TARGET_CONFIDENCE: float = float(_agent.get("target_confidence", 0.8))
AGENT_MAX_ITERATIONS: int = int(_agent.get("max_iterations", 10))
```

- [ ] **Step 3: Verify the module imports cleanly**

```bash
uv run python -c "from schnapplist.config import AGENT_TARGET_CONFIDENCE, AGENT_MAX_ITERATIONS; print(AGENT_TARGET_CONFIDENCE, AGENT_MAX_ITERATIONS)"
```
Expected: `0.8 10`

- [ ] **Step 4: Commit**

```bash
git add schnapplist/config.py schnapplist.toml
git commit -m "feat: add [agent] config section with target_confidence and max_iterations"
```

---

## Task 3: Agent system prompt — stop-early rule and confidence rubric

**Files:**
- Modify: `schnapplist/workflows/item_research_agent.py`

- [ ] **Step 1: Update `_AGENT_SYSTEM_PROMPT` to accept `target_confidence` as a parameter**

In `schnapplist/workflows/item_research_agent.py`, replace the module-level `_AGENT_SYSTEM_PROMPT` string with a function that formats it:

```python
def _build_system_prompt(target_confidence: float) -> str:
    return f"""\
You are an expert reseller assistant for German marketplaces (Kleinanzeigen, eBay.de).

Your job:
1. Call analyze_photos to identify the item (name, brand, model, condition).
2. Call web_search to look up the exact manufacturer specifications for the identified model.
3. Call web_search to find current prices on kleinanzeigen.de and ebay.de.
4. After each web_search, ask yourself: do I have brand, model, at least one verified \
spec, and a price signal? If yes and your confidence would be >= {target_confidence:.2f}, \
call finish immediately — do not search further.
5. If any spec or price is still unclear, call web_search again with a refined query.

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

Confidence rating — set when producing your final output:
- 1.0: brand, model, full spec sheet, and multiple price sources confirmed
- 0.8: brand + model confirmed, key specs verified, one price source
- 0.6: brand or model uncertain, specs partially verified
- 0.4: identification is a best guess, little verification possible
Set confidence_notes to one sentence describing what was uncertain \
(or "Fully verified" if confidence = 1.0).
"""
```

- [ ] **Step 2: Update `_build_agent` to call `_build_system_prompt`**

`_build_agent` currently accepts `on_stage` only. Add `target_confidence`:

```python
def _build_agent(
    on_stage: Callable[[str], None] | None = None,
    target_confidence: float = 0.8,
) -> Agent[_AgentDeps, ItemResearchOutput]:
    model_name = _resolve_model_name()
    agent: Agent[_AgentDeps, ItemResearchOutput] = Agent(
        model_name,
        output_type=ItemResearchOutput,
        deps_type=_AgentDeps,
        system_prompt=_build_system_prompt(target_confidence),
        retries=2,
    )
    # ... rest of tools unchanged
```

- [ ] **Step 3: Update `run_item_research_agent` signature**

Add `max_iterations` and `target_confidence` parameters, replace the hardcoded `_MAX_AGENT_ITERATIONS`:

```python
def run_item_research_agent(
    photos: list[Path],
    client: LLMClient,
    on_stage: Callable[[str], None] | None = None,
    on_usage: Callable[[RunUsage, float], None] | None = None,
    *,
    max_iterations: int = 10,
    target_confidence: float = 0.8,
) -> AgentResult:
    """Run the ReAct agent and return verified item research output with usage stats."""
    agent = _build_agent(on_stage=on_stage, target_confidence=target_confidence)
    deps = _AgentDeps(photos=photos, client=client)

    async def _run() -> AgentResult:
        # ... same as current, but replace _MAX_AGENT_ITERATIONS with max_iterations:
        async with agent.iter(
            "Research this item and produce a verified listing.",
            deps=deps,
            usage_limits=UsageLimits(request_limit=max_iterations),
        ) as run:
```

Also remove the now-unused module-level `_MAX_AGENT_ITERATIONS = 10` constant.

- [ ] **Step 4: Run the full test suite**

```bash
uv run pytest -v
```
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add schnapplist/workflows/item_research_agent.py
git commit -m "feat: add stop-early rule and confidence rubric to agent system prompt"
```

---

## Task 4: Pipeline — pass config, compute `low_confidence`, emit in `item_done`

**Files:**
- Modify: `schnapplist/workflows/process_pipeline.py`
- Test: `tests/test_process_pipeline_agent.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_process_pipeline_agent.py`:

```python
def test_pipeline_item_done_includes_low_confidence_false(tmp_path):
    from PIL import Image
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir()
    img = Image.new("RGB", (10, 10))
    img.save(photos_dir / "item.jpg", "JPEG")

    mock_output = _make_mock_output("Test Item")  # confidence=0.9 by default in helper

    events = []

    def _cb(event: str, **kwargs):
        events.append((event, kwargs))

    with (
        patch("schnapplist.workflows.process_pipeline.group_photos_by_item", return_value=[[photos_dir / "item.jpg"]]),
        patch("schnapplist.workflows.process_pipeline.filter_redundant_photos", return_value=[photos_dir / "item.jpg"]),
        patch("schnapplist.workflows.process_pipeline.enhance_photo", return_value=photos_dir / "item.jpg"),
        patch("schnapplist.workflows.process_pipeline.run_item_research_agent", return_value=MagicMock(output=mock_output, usage=MagicMock())),
        patch("schnapplist.workflows.process_pipeline.write_item_report"),
    ):
        ProcessWorkflow(_make_mock_client(), on_progress=_cb).run(
            photos_dir=photos_dir,
            output_dir=tmp_path / "output",
            single_item=False,
        )

    done_events = [(e, k) for e, k in events if e == "item_done"]
    assert len(done_events) == 1
    assert done_events[0][1]["low_confidence"] is False
    assert done_events[0][1]["confidence"] == 0.9


def test_pipeline_item_done_includes_low_confidence_true(tmp_path):
    from PIL import Image
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir()
    img = Image.new("RGB", (10, 10))
    img.save(photos_dir / "item.jpg", "JPEG")

    mock_output = _make_mock_output("Test Item", confidence=0.4)

    events = []

    def _cb(event: str, **kwargs):
        events.append((event, kwargs))

    with (
        patch("schnapplist.workflows.process_pipeline.group_photos_by_item", return_value=[[photos_dir / "item.jpg"]]),
        patch("schnapplist.workflows.process_pipeline.filter_redundant_photos", return_value=[photos_dir / "item.jpg"]),
        patch("schnapplist.workflows.process_pipeline.enhance_photo", return_value=photos_dir / "item.jpg"),
        patch("schnapplist.workflows.process_pipeline.run_item_research_agent", return_value=MagicMock(output=mock_output, usage=MagicMock())),
        patch("schnapplist.workflows.process_pipeline.write_item_report"),
    ):
        ProcessWorkflow(_make_mock_client(), on_progress=_cb).run(
            photos_dir=photos_dir,
            output_dir=tmp_path / "output",
            single_item=False,
        )

    done_events = [(e, k) for e, k in events if e == "item_done"]
    assert done_events[0][1]["low_confidence"] is True
    assert done_events[0][1]["confidence"] == 0.4
```

Also update `_make_mock_output` in the same file to accept `confidence`:

```python
def _make_mock_output(name: str = "Test Item", confidence: float = 0.9):
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
        confidence=confidence,
        confidence_notes="Fully verified" if confidence >= 0.8 else "Model uncertain",
    )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_process_pipeline_agent.py::test_pipeline_item_done_includes_low_confidence_false tests/test_process_pipeline_agent.py::test_pipeline_item_done_includes_low_confidence_true -v
```
Expected: `KeyError: 'low_confidence'`

- [ ] **Step 3: Update pipeline to import config and pass params**

In `schnapplist/workflows/process_pipeline.py`, add to imports:

```python
from ..config import AGENT_MAX_ITERATIONS, AGENT_TARGET_CONFIDENCE
```

- [ ] **Step 4: Pass `max_iterations` and `target_confidence` to the agent call**

In `ProcessWorkflow.run()`, find the `run_item_research_agent(...)` lambda and add the two new keyword arguments:

```python
lambda fa=filtered_for_agent, _idx=idx: run_item_research_agent(
    fa,
    self._client,
    on_stage=lambda stage: self._emit("item_stage", idx=_idx, stage=stage),
    on_usage=_on_usage,
    max_iterations=AGENT_MAX_ITERATIONS,
    target_confidence=AGENT_TARGET_CONFIDENCE,
),
```

- [ ] **Step 5: Compute `low_confidence` and add to `item_done` event**

Directly after `agent_output = agent_result.output`, add:

```python
low_confidence = agent_output.confidence < AGENT_TARGET_CONFIDENCE
```

Then find the `self._emit("item_done", ...)` call and add the two new kwargs:

```python
self._emit(
    "item_done",
    idx=idx,
    name=item_state.item_name,
    price=price_str,
    confidence=agent_output.confidence,
    low_confidence=low_confidence,
)
```

- [ ] **Step 6: Run the new tests**

```bash
uv run pytest tests/test_process_pipeline_agent.py -v
```
Expected: all PASSED

- [ ] **Step 7: Run full suite**

```bash
uv run pytest -v
```
Expected: all PASSED

- [ ] **Step 8: Commit**

```bash
git add schnapplist/workflows/process_pipeline.py tests/test_process_pipeline_agent.py
git commit -m "feat: pass agent config params and emit low_confidence in item_done"
```

---

## Task 5: CLI display — `⚠` icon and confidence score for low-confidence items

**Files:**
- Modify: `schnapplist/cli/display.py`
- Test: `tests/test_cli_display.py`

- [ ] **Step 1: Check what tests already exist**

```bash
uv run pytest tests/test_cli_display.py -v
```
Note any existing tests for `item_done` or `_render_items`.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_cli_display.py`:

```python
from schnapplist.cli.display import ItemRow, RunState, apply_event, _render_items


def test_item_done_low_confidence_sets_flag():
    state = RunState()
    apply_event(state, "item_start", idx=1, total=1)
    apply_event(state, "item_done", idx=1, name="Toshiba SRAM", price="6.00 EUR",
                confidence=0.55, low_confidence=True)
    assert state.items[1].low_confidence is True
    assert state.items[1].confidence == 0.55


def test_item_done_high_confidence_no_flag():
    state = RunState()
    apply_event(state, "item_start", idx=1, total=1)
    apply_event(state, "item_done", idx=1, name="Sony WH-1000XM5", price="180.00 EUR",
                confidence=0.9, low_confidence=False)
    assert state.items[1].low_confidence is False


def test_render_items_low_confidence_shows_warning_icon():
    state = RunState()
    apply_event(state, "item_start", idx=1, total=1)
    apply_event(state, "item_done", idx=1, name="Toshiba SRAM", price="6.00 EUR",
                confidence=0.55, low_confidence=True)
    panel = _render_items(state)
    rendered = str(panel.renderable)
    assert "⚠" in rendered or "0.55" in rendered
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/test_cli_display.py::test_item_done_low_confidence_sets_flag tests/test_cli_display.py::test_item_done_high_confidence_no_flag tests/test_cli_display.py::test_render_items_low_confidence_shows_warning_icon -v
```
Expected: `TypeError` or `AssertionError`

- [ ] **Step 4: Add `low_confidence` and `confidence` fields to `ItemRow`**

In `schnapplist/cli/display.py`, update `ItemRow`:

```python
@dataclass
class ItemRow:
    idx: int
    total: int
    status: Literal["queued", "active", "done", "failed", "skipped"] = "queued"
    stage: str = ""
    name: str = ""
    price: str = ""
    confidence: float = 1.0
    low_confidence: bool = False
```

- [ ] **Step 5: Update `apply_event` for `item_done`**

In `apply_event`, find the `"item_done"` branch and add:

```python
elif event == "item_done":
    idx = kwargs["idx"]
    if idx in state.items:
        state.items[idx].status = "done"
        state.items[idx].name = kwargs["name"]
        state.items[idx].price = kwargs["price"]
        state.items[idx].confidence = kwargs.get("confidence", 1.0)
        state.items[idx].low_confidence = kwargs.get("low_confidence", False)
        state.completed_items += 1
        if state.active_idx == idx:
            state.active_idx = None
```

- [ ] **Step 6: Update `_render_items` for the `"done"` status branch**

Find the `if row.status == "done":` block and replace:

```python
if row.status == "done":
    if row.low_confidence:
        icon = "[yellow]⚠[/yellow]"
        name_cell = Text(row.name, style="bold")
        price_cell = Text(f"{row.price}  [dim](conf: {row.confidence:.2f})[/dim]", style="yellow")
    else:
        icon = "[green]✓[/green]"
        name_cell = Text(row.name, style="bold")
        price_cell = Text(row.price, style="green")
```

- [ ] **Step 7: Run tests**

```bash
uv run pytest tests/test_cli_display.py -v
```
Expected: all PASSED

- [ ] **Step 8: Run full suite**

```bash
uv run pytest -v
```
Expected: all PASSED

- [ ] **Step 9: Commit**

```bash
git add schnapplist/cli/display.py tests/test_cli_display.py
git commit -m "feat: show warning icon and confidence score for low-confidence items in CLI"
```

---

## Task 6: Markdown report — confidence row and Recherche note

**Files:**
- Modify: `schnapplist/core/report_generator.py`
- Test: `tests/test_item_service.py` (or a new test if none covers report content)

The report changes are gated on `item.confidence < AGENT_TARGET_CONFIDENCE`. The `Item` model needs `confidence` and `confidence_notes` fields so the report generator can read them.

- [ ] **Step 1: Add `confidence` and `confidence_notes` to `Item` in `models.py`**

In `schnapplist/core/models.py`, add to the `Item` class:

```python
confidence: float = 1.0
confidence_notes: str = ""
```

- [ ] **Step 2: Update `build_item` to populate the new fields**

In `schnapplist/core/item_analyzer.py`, find `build_item` and add after `model=analysis.get("model")`:

```python
confidence=float(analysis.get("confidence", 1.0)),
confidence_notes=str(analysis.get("confidence_notes", "")),
```

- [ ] **Step 3: Update the pipeline to pass confidence into `_analysis_dict`**

Task 4 Step 3 already added `AGENT_MAX_ITERATIONS` and `AGENT_TARGET_CONFIDENCE` imports and wired the agent call. Now also add `confidence` and `confidence_notes` to `_analysis_dict` in `schnapplist/workflows/process_pipeline.py`:

```python
"confidence": agent_output.confidence,
"confidence_notes": agent_output.confidence_notes,
```

- [ ] **Step 4: Write the failing report test**

Add to the most relevant test file (create `tests/test_report_generator.py` if none exists):

```python
from pathlib import Path
from schnapplist.core.models import Item, ItemCondition, Photo, PriceInfo
from schnapplist.core.report_generator import write_item_report
from schnapplist.config import AGENT_TARGET_CONFIDENCE


def _make_item(tmp_path: Path, confidence: float) -> Item:
    photo = Photo(original_path=tmp_path / "photo.jpg")
    return Item(
        name="Test Item",
        title_de="Test",
        description="Test description.",
        condition=ItemCondition.GOOD,
        photos=[photo],
        price_info=PriceInfo(suggested_price=10.0, min_price=8.0, max_price=12.0, reasoning="test"),
        confidence=confidence,
        confidence_notes="Model uncertain" if confidence < AGENT_TARGET_CONFIDENCE else "Fully verified",
    )


def test_report_includes_confidence_row_when_low(tmp_path):
    item = _make_item(tmp_path, confidence=0.5)
    path = write_item_report(item, 1, tmp_path / "run")
    content = path.read_text()
    assert "Confidence" in content
    assert "0.50" in content
    assert "Model uncertain" in content


def test_report_no_confidence_row_when_high(tmp_path):
    item = _make_item(tmp_path, confidence=0.9)
    path = write_item_report(item, 1, tmp_path / "run")
    content = path.read_text()
    assert "Confidence" not in content
```

- [ ] **Step 5: Run tests to verify they fail**

```bash
uv run pytest tests/test_report_generator.py -v
```
Expected: `AssertionError` (Confidence not in content)

- [ ] **Step 6: Update `_write_item_file` in `report_generator.py`**

After the `| **Approved** |` row and before `lines.append("")`, add:

```python
if item.confidence < AGENT_TARGET_CONFIDENCE:
    lines.append(
        f"| **Confidence** | {item.confidence:.2f} ⚠ — {item.confidence_notes} |"
    )
```

Import `AGENT_TARGET_CONFIDENCE` at the top of `report_generator.py`:

```python
from ..config import AGENT_TARGET_CONFIDENCE, DEFAULT_MARKETPLACE, LISTING_DISCLAIMER
```

Also add the Recherche note. In `_write_item_file`, find the `has_research` block and add before `lines.append("")` at the end of that section:

```python
if item.confidence < AGENT_TARGET_CONFIDENCE:
    lines += [
        f"**⚠ Niedrige Konfidenz:** {item.confidence_notes}",
        "",
    ]
```

- [ ] **Step 7: Run report tests**

```bash
uv run pytest tests/test_report_generator.py -v
```
Expected: both PASSED

- [ ] **Step 8: Run full suite**

```bash
uv run pytest -v
```
Expected: all PASSED

- [ ] **Step 9: Commit**

```bash
git add schnapplist/core/models.py schnapplist/core/item_analyzer.py schnapplist/workflows/process_pipeline.py schnapplist/core/report_generator.py tests/test_report_generator.py
git commit -m "feat: add confidence row and low-confidence note to Markdown report"
```
