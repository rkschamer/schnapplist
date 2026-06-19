# Schnapplist

Creates listing in different marketplaces just based on a set of photos you dump into it. The AI tries to group items, enhances photos, writes German descriptions (more languages might be supported in the future), looks up prices, and produces an editable Markdown report.

```
photos  →  group  →  enhance  →  identify + price  →  report.md  →  review  →  post
```

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- An LLM backend — either an [Anthropic API key](https://console.anthropic.com/) or a local [Ollama](https://ollama.com/) instance (see below)

## Install

```bash
git clone https://github.com/yourname/schnapplist
cd schnapplist
uv sync
```

For Kleinanzeigen posting (browser automation):

```bash
uv sync --extra playwright
uv run playwright install chromium
```

### Install as a global CLI tool

To use `schnapplist` and `schnapplist-ui` from anywhere without `uv run`:

```bash
uv tool install /path/to/schnapplist
```

To update after code changes:

```bash
uv tool install --reinstall /path/to/schnapplist
```

## Configure

Copy `.env.example` to `.env` and fill in what you need:

```bash
cp .env.example .env
```

Credentials go in `.env`. Everything else — LLM provider, default marketplace, disclaimer text — lives in `schnapplist.toml`. Generate it with:

```bash
uv run schnapplist config init
```

Check what's active:

```bash
uv run schnapplist config show
```

## LLM providers

### Anthropic (default)

Set `ANTHROPIC_API_KEY` in `.env`. Fast, accurate, no local setup required. Uses `claude-sonnet-4-6` by default.

### Ollama (local, free)

Ollama runs models on your own hardware. You need a **vision-capable** model — text-only models won't work for photo grouping and item analysis. I've good experience with qwen3.6:35b (24GB)

```bash
# Install Ollama from https://ollama.com, then pull a model:
ollama pull qwen3.6:35b
ollama pull gemma4:e4b   # if VRAM is tight (~8 GB)
```

Switch to Ollama in `schnapplist.toml`:

```toml
[llm]
provider = "ollama"
model = "qwen3.6:35b"
```

Ollama supports partial GPU offloading, so models larger than your VRAM still run — just slower. On Windows with an NVIDIA GPU, make sure the CUDA driver is installed and Ollama will pick it up automatically.

## Run

```bash
# Process a folder of photos
uv run schnapplist process --photos-dir ./photos

# Re-open the report in $EDITOR to tweak anything
uv run schnapplist review

# See all processed items
uv run schnapplist list

# Post (add --dry-run to preview first)
uv run schnapplist post --item-id <id>
uv run schnapplist post --item-id <id> --marketplace ebay
uv run schnapplist post --item-id <id> --schedule 2026-05-10T18:00:00
```

After processing you'll be asked whether to open the report right away — say yes, edit whatever you like, save and close. Schnapplist syncs the changes back to `items.json` automatically.

## Marketplaces

### Kleinanzeigen

Uses Playwright to drive the web UI (no public API). Runs the browser **in headed mode** so you can solve CAPTCHAs manually. Needs `KLEINANZEIGEN_EMAIL` and `KLEINANZEIGEN_PASSWORD` in `.env`.

If you want a tool-using browser agent (Playwright MCP) instead of the local selector loop, set:

```toml
[workflow]
engine = "mcp"
```

MCP mode launches `@playwright/mcp` through `npx` and lets the model inspect/control the page with browser tools directly.
It works with both providers:

- `llm.provider = "anthropic"` (requires `ANTHROPIC_API_KEY`)
- `llm.provider = "ollama"` (requires a tool-capable Ollama model such as qwen3/llama3.1 families)

### eBay

Uses the eBay Trading API (`AddItem`, site ID 77 — Germany). Needs `EBAY_APP_ID` and `EBAY_AUTH_TOKEN` in `.env`. Set `EBAY_SANDBOX=true` to test before going live.

The LLM suggests listing type (`auction` / `fixed` / `both`), duration, and reserve price. All of that shows up in the Markdown report and can be edited before posting.

## Debugging

Set `SCHNAPPLIST_DEBUG=1` before running `process` to enable detailed agent traces:

```bash
SCHNAPPLIST_DEBUG=1 uv run schnapplist process --photos-dir ./photos
```

Two log files are written to the current directory:

| File | Contents |
|---|---|
| `schnapplist-debug.log` | Structured DEBUG messages — tool calls, token counts, LLM latency per turn |
| `schnapplist-agent.log` | Full agent trace — turn-by-turn timeline plus the complete prompts, model responses, tool arguments, and tool return values |

The agent log has two sections per run. The timeline (from logfire) looks like:

```
11:17:41.150 agent run
11:17:41.156   chat qwen3.6:35b
11:17:45.486     running tool: analyze_photos
11:17:49.644   chat qwen3.6:35b
11:17:55.896     running tool: web_search
...
```

Followed by `=== span name ===` blocks with the full JSON content of each span — what the model was sent (`gen_ai.input.messages`), what it replied (`gen_ai.output.messages`), and what each tool received and returned.
