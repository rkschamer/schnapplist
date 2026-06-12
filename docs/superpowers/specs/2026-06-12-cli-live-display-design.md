# CLI Live Display Redesign

**Date:** 2026-06-12  
**Status:** Approved

## Problem

The current CLI progress display uses a flat list of Rich `Progress` tasks. Overall pipeline stages (scan, group, overall) and per-item tasks appear in the same undifferentiated column, making it hard to identify overall progress at a glance. There is also no visibility into the LLM's activity: which tool calls have fired, how many tokens have been consumed, or how fast the model is generating.

## Goals

1. Clear visual separation between overall pipeline progress and per-item progress.
2. Live LLM activity panel showing: active item, current stage, running token totals (input/output/cache across the whole run), tokens/sec, requests used, tool calls fired, and a compact log of the last few tool calls.
3. Maintainable code — rendering logic isolated from Click command wiring.

## Layout

```
┌─ Schnapplist ────────────────────────────────────────────────────────────────┐
│  Scan ✓  10 photos    Group ✓  5 items    Overall  ██████░░░░  3/5  0:02:14 │
└──────────────────────────────────────────────────────────────────────────────┘

┌─ Items ──────────────────────────────────────────┐┌─ LLM Activity ──────────┐
│ ✓  1/5  DDR2 Desktop RAM (Elixir)    2.00 EUR    ││ Item 4/5                │
│ ✓  2/5  Toshiba SDRAM SO-DIMM        5.00 EUR    ││ Stage: web_search       │
│ ✓  3/5  SIMM Speichermodul          16.90 EUR    ││ Requests:   7 / 10      │
│ ⠸  4/5  analyzing…                              ││ Tool calls: 4           │
│    5/5  queued                                   ││                         │
│                                                  ││ ↑ in    2,341 tokens    │
│                                                  ││ ↓ out     187 tokens    │
│                                                  ││ ◈ cache    890 tokens   │
│                                                  ││ ⚡ 23.4 tok/s           │
│                                                  ││                         │
│                                                  ││ Recent tool calls:      │
│                                                  ││  analyze_photos  +87    │
│                                                  ││  web_search      +43    │
│                                                  ││  web_search      +61    │
└──────────────────────────────────────────────────┘└─────────────────────────┘
```

Rich `Layout("root")` split:
- `Layout("header")` — `min_size=3`, `ratio` not set (fixed height)
- `Layout("body")` split horizontally:
  - `Layout("items")` — `ratio=2`
  - `Layout("llm")` — `ratio=1`

`Live(layout, refresh_per_second=4)` drives the whole display.

## Architecture

### New file: `schnapplist/cli/display.py`

**`RunState` dataclass** — single source of truth for all mutable display state. No Rich imports. Fields:

```python
@dataclass
class RunState:
    # scan/group
    photo_count: int = 0
    group_count: int = 0
    scan_done: bool = False
    group_done: bool = False

    # overall
    total_items: int = 0
    completed_items: int = 0
    start_time: float = field(default_factory=time.monotonic)

    # per-item rows: idx → ItemRow
    items: dict[int, ItemRow] = field(default_factory=dict)
    active_idx: int | None = None

    # LLM usage — accumulated across whole run
    input_tokens: int = 0
    output_tokens: int = 0
    cache_tokens: int = 0
    requests: int = 0
    tool_calls: int = 0
    first_output_time: float | None = None

    # tool call log (last N entries)
    tool_log: list[ToolLogEntry] = field(default_factory=list)
```

**`ItemRow` dataclass** — per-item display state:
```python
@dataclass
class ItemRow:
    idx: int
    total: int
    status: Literal["queued", "active", "done", "failed", "skipped"]
    stage: str = ""
    name: str = ""
    price: str = ""
```

**`ToolLogEntry` dataclass:**
```python
@dataclass
class ToolLogEntry:
    tool: str
    output_tokens: int  # delta from this call
```

**Three render functions** (private, each returns a Rich `Renderable`):

- `_render_header(state: RunState) -> Panel` — scan/group summary + overall progress bar
- `_render_items(state: RunState) -> Panel` — scrolling item table (Rich `Table`, no header, fixed cols)
- `_render_llm(state: RunState) -> Panel` — token totals + tok/s + last 5 tool calls

**`RichLiveCallback`** — implements `ProgressCallback` protocol:
- Owns a `RunState`, a `Layout`, and a `Live`
- `__call__(event, **kwargs)` mutates `RunState` then calls `self._live.refresh()`
- Context manager: `__enter__` starts `Live`, `__exit__` stops it
- `stop()` / `start()` for the decision callback pause

**`RichDecisionCallback`** — unchanged in role, takes a `RichLiveCallback` reference, calls `.stop()` before the prompt and `.start()` after.

### Modified: `schnapplist/workflows/process_pipeline.py`

Add one new event emitted after `run_item_research_agent` returns:

```python
result = run_item_research_agent(photos, client)
usage = result.usage()   # pydantic-ai AgentRunResult.usage()
self._emit(
    "item_usage",
    idx=idx,
    input_tokens=usage.input_tokens,
    output_tokens=usage.output_tokens,
    cache_read_tokens=usage.cache_read_tokens,
    requests=usage.requests,
    tool_calls=usage.tool_calls,
)
```

The `ProgressCallback` protocol docstring gets `item_usage` added to its event list.

Also emit `item_stage` events for `analyze_photos` and `web_search` tool calls from within the agent — these drive both the Items panel stage label and a `ToolLogEntry` in the LLM panel. This requires passing an `on_stage: Callable[[str], None] | None = None` parameter into `run_item_research_agent`. Each `@agent.tool` function calls `on_stage(tool_name)` if the callback is set. The pipeline wires it up to `lambda stage: self._emit("item_stage", idx=idx, stage=stage)`.

### Modified: `schnapplist/cli/__init__.py`

- Remove `_RichProgressCallback` and `_RichDecisionCallback` class definitions
- Replace with `from .display import RichLiveCallback, RichDecisionCallback`
- The `process` command body is otherwise unchanged

## Token/sec calculation

`output_tokens / elapsed_seconds` where elapsed starts at the first `item_usage` event received. Input tokens are largely served from cache and don't reflect generation throughput.

## Tool call log

Keep the last **5** entries. Each entry shows the tool name and a "fired" marker — no per-call token delta, since pydantic-ai only reports cumulative usage after the full agent run. True per-call token breakdowns require streaming (deferred to a future Option C enhancement).

Entries are appended by the stage-emit hook inside each `@agent.tool` function (see pipeline change below).

## What is NOT in scope

- Async / streaming agent (Option C) — deferred
- The web UI (`schnapplist/ui/`) — unchanged
- The `list`, `review`, `post` CLI commands — unchanged
- Any change to output data or the report format
