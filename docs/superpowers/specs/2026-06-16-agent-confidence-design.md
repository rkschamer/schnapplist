# Agent Confidence & Configurable Iterations — Design Spec

**Date:** 2026-06-16
**Status:** Approved

---

## Problem

The `ItemResearchAgent` runs up to a fixed 10 LLM turns per item regardless of how quickly it reaches a reliable result. There is no way for users to tune this cap, no signal in the CLI or report when a result is uncertain, and no mechanism for the agent to stop early when it already has high-confidence data.

---

## Solution

Three coordinated changes:

1. **`ItemResearchOutput` gains `confidence` and `confidence_notes` fields** — the agent self-rates when calling `finish`.
2. **`schnapplist.toml` gains an `[agent]` section** — `target_confidence` and `max_iterations` are user-configurable; both are read into `config.py`.
3. **Pipeline, CLI, and Markdown report surface low-confidence items** — icon changes in the progress display; a confidence row appears in the report table when below target.

---

## Data Model

Add two fields to `ItemResearchOutput` in `schnapplist/workflows/item_research_agent.py`:

```python
confidence: float          # 0.0–1.0, agent self-rated at finish time
confidence_notes: str      # one sentence explaining the rating
```

---

## Configuration

Add to `schnapplist.toml` (and the template shipped with the package):

```toml
[agent]
# Confidence threshold (0.0–1.0). Items below this are flagged in the CLI and report.
target_confidence = 0.8
# Maximum LLM turns per item (safety cap).
max_iterations = 10
```

Add to `schnapplist/config.py`:

```python
_agent = _toml.get("agent", {})
AGENT_TARGET_CONFIDENCE: float = float(_agent.get("target_confidence", 0.8))
AGENT_MAX_ITERATIONS: int = int(_agent.get("max_iterations", 10))
```

The hardcoded `_MAX_AGENT_ITERATIONS = 10` in `item_research_agent.py` is replaced by `AGENT_MAX_ITERATIONS` from config.

---

## Agent System Prompt Changes

Two additions to `_AGENT_SYSTEM_PROMPT`:

**Stop-early rule:**
```
After each web_search, ask yourself: do I have brand, model, at least one verified
spec, and a price signal? If yes and your confidence would be >= {target_confidence},
call finish immediately — do not search further.
```

The `target_confidence` value is injected into the prompt string at agent construction time (not hardcoded).

**Confidence rating rubric:**
```
When calling finish, set confidence according to this rubric:
- 1.0: brand, model, full spec sheet, and multiple price sources confirmed
- 0.8: brand + model confirmed, key specs verified, one price source
- 0.6: brand or model uncertain, specs partially verified
- 0.4: identification is a best guess, little verification possible
Set confidence_notes to one sentence describing what was uncertain (or "Fully verified"
if confidence = 1.0).
```

---

## Pipeline Changes

`run_item_research_agent()` signature gains two parameters:

```python
def run_item_research_agent(
    photos: list[Path],
    client: LLMClient,
    *,
    max_iterations: int = 10,
    target_confidence: float = 0.8,
) -> ItemResearchOutput
```

Both are passed from `ProcessWorkflow.run()` using `AGENT_MAX_ITERATIONS` and `AGENT_TARGET_CONFIDENCE` from config.

After the agent returns, the pipeline computes:

```python
low_confidence = agent_output.confidence < AGENT_TARGET_CONFIDENCE
```

Items with `confidence == target` are treated as passing (strict less-than) — no flag shown.

The `item_done` progress event gains a new kwarg:

```python
self._emit("item_done", idx=idx, name=..., price=..., low_confidence=low_confidence)
```

---

## CLI Progress Display

The `item_done` handler in `_RichProgressCallback` changes the icon based on `low_confidence`:

- `[green]✓[/green]` — confidence ≥ target (current behaviour)
- `[yellow]⚠[/yellow]` — confidence below target, confidence score appended:

```
⚠  Item 2/5  Toshiba SRAM Speicherchip  6.00 EUR  (confidence: 0.55)
```

---

## Markdown Report Changes

In `report_generator._write_item_file()`, add a confidence row to the report table **only when confidence is below target**:

```markdown
| **Confidence** | 0.55 ⚠ — Could not find exact model number |
```

When confidence is below target, also add a note in the `### Recherche` section:

```markdown
**⚠ Niedrige Konfidenz:** Could not find exact model number
```

When confidence ≥ target, nothing is added — clean reports stay clean.

---

## File Map

| Action | File | Change |
|--------|------|--------|
| Modify | `schnapplist/workflows/item_research_agent.py` | Add `confidence`/`confidence_notes` to schema; update system prompt; add params to `run_item_research_agent`; use `AGENT_MAX_ITERATIONS` |
| Modify | `schnapplist/config.py` | Add `AGENT_TARGET_CONFIDENCE`, `AGENT_MAX_ITERATIONS` |
| Modify | `schnapplist.toml` | Add `[agent]` section |
| Modify | `schnapplist/workflows/process_pipeline.py` | Pass `max_iterations`/`target_confidence`; compute `low_confidence`; add to `item_done` event |
| Modify | `schnapplist/cli/__init__.py` | Handle `low_confidence` in `item_done` handler |
| Modify | `schnapplist/core/report_generator.py` | Add confidence row and Recherche note when below target |

---

## Out of Scope

- Changing the retry/skip prompt on low confidence (Option A — warn and proceed).
- Any changes to photo grouping, enhancement, or posting logic.
