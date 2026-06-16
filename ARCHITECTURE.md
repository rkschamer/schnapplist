# Schnapplist Architecture

## Data Flow

```
photos/
  └─ process_service.run_process()
       ├─ photo_processor: load → group → filter redundant → enhance
       └─ item_research_agent (ReAct loop, pydantic-ai):
            ├─ tool: analyze_photos  →  vision LLM call, returns identification JSON
            └─ tool: web_search      →  DuckDuckGo, returns snippets
            └─ structured output: ItemResearchOutput (pydantic BaseModel)
       └─ Item.from_analysis()  →  Item (pydantic BaseModel)
       └─ report_generator.write_item_report()  →  item-N.md (Markdown)

schnapplist-report-YYYYMMDD-HHMMSS/
  ├─ item-1.md
  ├─ item-2.md
  └─ pictures/
       └─ item-1-1.jpg (enhanced)

User edits Markdown → report_parser.parse_report() reads it back → posting_service
```

## Pydantic-AI Agent Wiring

The item research agent (`agents/item_research_agent.py`) uses pydantic-ai's `Agent` class:

```python
agent: Agent[_AgentDeps, ItemResearchOutput] = Agent(
    model_name,          # "anthropic:claude-sonnet-4-6" or "ollama:qwen3.6:35b"
    output_type=ItemResearchOutput,   # pydantic BaseModel — agent MUST return this shape
    deps_type=_AgentDeps,             # injected into every tool call via RunContext
    system_prompt=...,
    retries=2,
)
```

**`deps_type`** — `_AgentDeps` carries the photos list and `LLMClient`. Tools receive it via
`ctx: RunContext[_AgentDeps]`, e.g. `ctx.deps.photos`. This keeps tools pure functions with
no global state.

**`output_type`** — `ItemResearchOutput` is a pydantic `BaseModel`. Pydantic-AI validates the
model's final JSON response against this schema automatically; if validation fails, it retries
(up to `retries=2`).

**Tools** are registered with `@agent.tool` decorators inside `_build_agent()`. Two tools:
- `analyze_photos` — encodes photos as base64, calls `LLMClient.messages_create()` for
  vision identification, returns a dict.
- `web_search` — calls `web_search()` (DuckDuckGo), returns newline-separated snippets.

**`UsageLimits(request_limit=max_iterations)`** caps how many LLM round-trips the agent
can make. Default is 10 (configurable via `[agent] max_iterations` in `schnapplist.toml`).

**The `iter()` loop** in `run_item_research_agent()` streams node-by-node to collect timing
and token usage after each model request:

```python
async with agent.iter(...) as run:
    async for node in run:
        if isinstance(node, ModelRequestNode):
            request_start = ...
        elif isinstance(node, CallToolsNode):
            on_usage(run.usage(), gen_secs)
```

## Config Loading

Two sources, merged at import time in `config.py`:

| Source | Content |
|---|---|
| `.env` | Secrets: `ANTHROPIC_API_KEY`, `EBAY_AUTH_TOKEN`, `KLEINANZEIGEN_EMAIL`, etc. |
| `schnapplist.toml` | Behaviour: LLM provider/model, disclaimer text, confidence threshold. |

`config.py` exports plain module-level constants (`LLM_PROVIDER`, `CLAUDE_MODEL`, …).
All other modules import these constants directly — no config object is passed around.

TOML search order: `./schnapplist.toml` (project-local) → `<user config dir>/schnapplist/config.toml` (installed).

## Package Responsibilities

| Package | Responsibility |
|---|---|
| `core/` | Pure domain: models, LLM client, photo utilities, report I/O. Stateless. No config reads (uses imported constants only). |
| `agents/` | Pydantic-AI `Agent` definitions. Each file owns one agent: system prompt, tools, output type, `run_*` entry point. |
| `services/` | Application API. Wires config constants → LLMClient → agents → report I/O. One module per user-facing operation. |
| `providers/` | Marketplace adapters behind `BaseMarketplace`. eBay CSV export lives here too. |
| `ui/cli/` | Click commands + Rich terminal display. No business logic — delegates to `services/`. |
| `ui/web/` | NiceGUI web UI. Not actively developed; preserved for future use. |

## How to Add a Marketplace Provider

1. Create `schnapplist/providers/<name>.py`:

```python
from .base import BaseMarketplace
from ..core.models import Item

class MyMarketplace(BaseMarketplace):
    name = "mymarket"

    def is_available(self) -> bool:
        import os
        return bool(os.getenv("MYMARKET_API_KEY"))

    def post_listing(self, item: Item, options=None) -> str:
        # post and return the listing URL
        ...
```

2. Register it in `schnapplist/providers/__init__.py`:

```python
from .mymarket import MyMarketplace

MARKETPLACES: dict[str, BaseMarketplace] = {
    "kleinanzeigen": KleinanzeigenMarketplace(),
    "ebay": EbayMarketplace(),
    "mymarket": MyMarketplace(),
}
```

3. Add credentials to `.env.example` with a comment.

4. Add a section to `README.md` under `## Marketplaces`.
