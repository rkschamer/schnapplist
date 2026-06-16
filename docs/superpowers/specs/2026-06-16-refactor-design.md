# Schnapplist Refactor Design
Date: 2026-06-16

## Goal

Clean up structural degradation that accumulated during feature development. Establish a clear,
navigable package layout, remove dead code, and add documentation so new readers can orient
quickly.

---

## Package Structure

### After refactor

```
schnapplist/
  __init__.py
  config.py
  agents/               ← renamed from workflows/
    __init__.py
    item_research_agent.py
    kleinanzeigen_posting.py
  core/                 ← domain primitives only
    __init__.py
    llm.py
    models.py           ← gains Item.from_analysis() classmethod
    photo_processor.py
    report_generator.py
    report_parser.py
    web_search.py
  providers/            ← gains ebay_csv_exporter.py
    __init__.py
    base.py
    ebay.py
    ebay_csv_exporter.py
    kleinanzeigen.py
  services/             ← process_service absorbs ProcessWorkflow
    __init__.py
    item_service.py     ← gains find_latest_report helper
    posting_service.py
    process_service.py
  ui/
    __init__.py
    cli/                ← moved from top-level schnapplist/cli/
      __init__.py
      display.py
    web/                ← moved from schnapplist/ui/
      __init__.py
      main.py
      pages/
        __init__.py
        home.py
        post.py
        process.py
        review.py
      state.py
```

### Package responsibilities

| Package | Responsibility |
|---|---|
| `core/` | Pure domain: models, LLM client, photo utilities, report I/O. No orchestration, no config reads (except `config.py` constants). |
| `agents/` | Pydantic-AI `Agent` definitions. Each file owns one agent: system prompt, tools, output type, and `run_*` entry point. |
| `services/` | Application API for CLI and web UI. Wires config → agents → report I/O. One file per user-facing operation. |
| `providers/` | Marketplace adapters behind a `BaseMarketplace` interface. eBay-specific export logic lives here too. |
| `ui/cli/` | CLI commands and Rich display. No business logic. |
| `ui/web/` | NiceGUI web UI. Not actively developed; preserved for future use. |

---

## Files to Delete

### Already deleted (staged in git)
- `docker-compose.ollama.nvidia.yml`
- `docker-compose.ollama.yml`
- `ollama.env`
- `ollama.env.example`
- `spec.md`

### Dead code (to remove)
- `schnapplist/workflows/image_search_agent.py` — nothing imports it; superseded by the ReAct agent
- `schnapplist/core/price_researcher.py` — superseded by `item_research_agent`'s web_search tool
- `schnapplist/core/item_analyzer.py` — `build_item` moves to `Item.from_analysis()`; `analyze_item` and `is_low_confidence` are dead code
- `schnapplist/workflows/review_pipeline.py` — only contains `find_latest_report`, which moves into `services/item_service.py`
- `schnapplist/workflows/process_pipeline.py` — `ProcessWorkflow` moves to `services/process_service.py`; file deleted
- `schnapplist/workflows/__init__.py` — entire `workflows/` package deleted (renamed to `agents/`)
- `schnapplist/ui/` top-level — moved to `ui/web/`; old location deleted

---

## Key Code Changes

### `Item.from_analysis()` classmethod (models.py)
Move `build_item()` from `core/item_analyzer.py` into `Item` as a classmethod:
```python
@classmethod
def from_analysis(cls, analysis: JsonDict, photos: list[Path], enhanced_paths: list[Path]) -> "Item":
    ...
```
`process_service.py` calls `Item.from_analysis(...)` instead of `build_item(...)`.

### `ProcessWorkflow` → `services/process_service.py`
The current `process_service.py` is a thin factory (builds `LLMClient`, calls `ProcessWorkflow`).
Merge `ProcessWorkflow`, `ProcessRunResult`, `ProcessRunState`, `ItemRunState`, `StageRecord`,
`ProgressCallback`, `DecisionCallback` all into `process_service.py`. Remove the separate
`workflows/process_pipeline.py` file.

### `find_latest_report` → `services/item_service.py`
Move the one-liner from `workflows/review_pipeline.py` into `item_service.py`.
Update all import sites.

### `ebay_csv_exporter.py` → `providers/`
Move from `core/` to `providers/`. Update import in the CLI.

### Entry points (`pyproject.toml`)
```toml
[project.scripts]
schnapplist     = "schnapplist.ui.cli:main"
schnapplist-ui  = "schnapplist.ui.web.main:run"
```

---

## Documentation

### Module docstrings
Each package `__init__.py` gets a docstring:
- `core/` — what "pure domain" means and what doesn't belong here
- `agents/` — pydantic-ai agent pattern: Agent + deps_type + output_type + tools + run_* function
- `services/` — application API; how each file maps to a user-facing command
- `providers/` — BaseMarketplace interface + how to add a new marketplace
- `ui/cli/` and `ui/web/` — UI-only, no business logic

### `ARCHITECTURE.md` (repo root)
Sections:
1. **Data flow** — `photos → process_service → item_research_agent → Item → report_generator → Markdown`
2. **Pydantic AI wiring** — how `Agent`, `deps_type`, `output_type`, tools, `UsageLimits`, and the `iter()` loop fit together in `item_research_agent.py`
3. **Config loading** — how `schnapplist.toml` + `.env` flow into `config.py` constants used throughout
4. **Package responsibilities** — one paragraph per package (mirrors docstrings above)
5. **How to add a marketplace provider** — concrete steps: new file in `providers/`, implement `BaseMarketplace`, register in `providers/__init__.py`

---

## What is NOT changing

- `config.py` — untouched
- `core/llm.py`, `core/photo_processor.py`, `core/report_generator.py`, `core/report_parser.py`, `core/web_search.py` — no changes beyond potential import path fixes
- `providers/base.py`, `providers/ebay.py`, `providers/kleinanzeigen.py` — no logic changes
- All tests — updated import paths only, no logic changes
- `schnapplist.toml`, `.env` files — untouched
