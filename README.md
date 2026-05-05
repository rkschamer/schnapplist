# Schnapplist

AI-powered CLI that turns a folder of photos into ready-to-post listings on [Kleinanzeigen.de](https://www.kleinanzeigen.de) and [eBay.de](https://www.ebay.de).

```text
photos/  →  group by item  →  enhance  →  identify + price  →  report.md  →  review  →  post
```

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for dependency management
- An [Anthropic API key](https://console.anthropic.com/) (Claude is used for vision, item analysis, and pricing) — or a local Ollama instance

## Installation

```bash
git clone https://github.com/yourname/schnapplist
cd schnapplist
uv sync
```

For posting to Kleinanzeigen (requires browser automation):

```bash
uv sync --extra playwright
uv run playwright install chromium
```

## Configuration

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

| Variable | Required for | Notes |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | Anthropic provider | Get one at console.anthropic.com |
| `KLEINANZEIGEN_EMAIL` | Kleinanzeigen posting | Your login e-mail |
| `KLEINANZEIGEN_PASSWORD` | Kleinanzeigen posting | Your login password |
| `EBAY_APP_ID` | eBay posting | From developer.ebay.com |
| `EBAY_AUTH_TOKEN` | eBay posting | OAuth user token |
| `EBAY_SANDBOX` | eBay testing | Set to `true` to use sandbox |

## Local Open-Weight LLM (Ollama)

Schnapplist can use a local Ollama model instead of the Anthropic API.

### Install Ollama and pull a model

#### macOS (Apple Silicon)

Run Ollama natively — Docker on macOS cannot access the Metal GPU, so a containerised Ollama runs CPU-only and is unusably slow. The native install uses Metal automatically:

```bash
brew install ollama
ollama serve   # starts the server at http://localhost:11434
```

#### Windows / Linux with NVIDIA GPU

```bash
cp ollama.env.example ollama.env
docker compose --env-file ollama.env \
  -f docker-compose.ollama.yml \
  -f docker-compose.ollama.nvidia.yml up -d
```

### Pull a vision-capable model

The photo grouping and item analysis steps send images to the model.
**You must use a vision-capable model** — text-only models will fail or produce poor results on those steps.

Recommended model:

```bash
ollama pull gemma4
```

[Gemma 4](https://ollama.com/library/gemma4) is Google's latest multimodal model and performs well on item recognition and German listing copy. Alternatives if you need lower VRAM:

| Model | VRAM | Notes |
| --- | --- | --- |
| `gemma4` | ~16 GB | Recommended — best quality |
| `gemma3:12b` | ~8 GB | Good quality, fits in 8 GB VRAM |
| `llava:13b` | ~8 GB | Older, reliable fallback |
| `moondream` | ~2 GB | Very fast, lower quality |

### Use Ollama as the LLM backend

```bash
uv run schnapplist process --photos-dir ./photos --llm-provider ollama
```

The model and host default to `$OLLAMA_MODEL` / `$OLLAMA_HOST` from your env file, or can be overridden per run:

```bash
uv run schnapplist process --photos-dir ./photos \
  --llm-provider ollama \
  --llm-model gemma4 \
  --ollama-host http://localhost:11434
```

## Workflow

### Step 1 — Process photos

Drop all photos (any mix of items) into a single folder, then run:

```bash
uv run schnapplist process --photos-dir ./photos
```

What happens:

1. **Grouping** — Claude vision inspects every photo and clusters them by physical item.
2. **Enhancement** — Each photo is auto-levelled, contrast-boosted, sharpened, and cropped to 4:3 at up to 1200 px wide. Enhanced copies land in `output/enhanced/`.
3. **Item analysis** — Claude identifies the item (brand, model, condition) and writes a German title and description ready for pasting into a listing. It also suggests which marketplace to use (eBay vs. Kleinanzeigen) and fills in eBay listing parameters (type, duration, reserve price).
4. **Price research** — DuckDuckGo searches Kleinanzeigen and eBay.de for comparable listings. Claude reads the results and suggests a price with a min/max range.
5. **Report** — A Markdown file `output/schnapplist_report_<timestamp>.md` is written with all items, photos, prices, descriptions, and listing options for your review.

At the end of processing you will be prompted:

```text
Review and edit the report now? [Y/n]
```

### Step 2 — Review and edit the report

The Markdown report is the **single source of truth**. Open it in any editor and change whatever you like:

| Field | What it controls |
| --- | --- |
| `## Item Name` heading | Item name stored in `items.json` |
| **Title (DE)** table row | German listing title |
| **Suggested price** table row | Selling price (edit the number directly) |
| **Marketplace** table row | `ebay` or `kleinanzeigen` |
| **eBay listing type** table row | `auction`, `fixed`, or `both` (best offer) |
| **eBay duration (days)** table row | `1`, `3`, `5`, `7`, or `10` |
| **eBay reserve price (EUR)** table row | Minimum auction price (`—` to remove) |
| `### Beschreibung` section | Full German description |
| `### Tags` section | Search keywords |

Save and close the editor — Schnapplist reads the file back and syncs all changes into `items.json` automatically.

To re-open the report for editing at any time:

```bash
uv run schnapplist review
```

This uses `$EDITOR` (falls back to `nano` / `vi`).

### Step 3 — List and post

```bash
# See all processed items and their IDs
uv run schnapplist list

# Post a specific item (add --dry-run to preview without posting)
uv run schnapplist post --item-id <id>

# Override the marketplace from the report
uv run schnapplist post --item-id <id> --marketplace ebay
uv run schnapplist post --item-id <id> --marketplace kleinanzeigen

# Schedule an eBay listing (ISO 8601)
uv run schnapplist post --item-id <id> --schedule 2026-05-10T18:00:00
```

The `post` command reads the marketplace and all eBay options (listing type, duration, reserve price) directly from `items.json` — set by the LLM and reviewed/edited in the report. Use `--marketplace` only if you want to override the report's suggestion.

The command marks the item as approved in `items.json` after a successful post.

## Commands

```text
schnapplist process   Analyse photos and write inspection report
schnapplist review    Open the Markdown report in $EDITOR and sync edits back
schnapplist list      Show all processed items
schnapplist post      Post an item to a marketplace
```

Run any command with `--help` for full option details.

## Marketplaces

### Kleinanzeigen

Uses Playwright to automate the Kleinanzeigen web UI — no official posting API exists. The browser runs **in headed mode** so you can solve any CAPTCHA that appears. Requires `KLEINANZEIGEN_EMAIL` and `KLEINANZEIGEN_PASSWORD` in `.env`.

No additional listing options beyond price and description — Kleinanzeigen keeps it simple.

### eBay

Uses the [eBay Trading API](https://developer.ebay.com/api-docs/user-guides/static/trading-user-guide-landing.html) (`AddItem` call), site ID 77 (Germany). Requires `EBAY_APP_ID` and `EBAY_AUTH_TOKEN` in `.env`. Set `EBAY_SANDBOX=true` to test against the sandbox before going live.

The following listing parameters are suggested by the LLM and editable in the report:

| Parameter | Values | Default |
| --- | --- | --- |
| **Listing type** | `auction` · `fixed` (Buy It Now) · `both` (fixed + best offer) | `fixed` |
| **Duration** | `1` · `3` · `5` · `7` · `10` days | `7` |
| **Reserve price** | any EUR amount, or `—` for none | `—` |

An optional scheduled start time can be set at post time with `--schedule <ISO 8601>`.

### Adding a new marketplace

Create a class in `schnapplist/providers/` that extends `BaseMarketplace`, implement `is_available()` and `post_listing(item, options=None) -> str`, then register it in `schnapplist/providers/__init__.py`:

```python
from .mymarketplace import MyMarketplace

MARKETPLACES = {
    ...,
    "mymarketplace": MyMarketplace(),
}
```

## Project layout

```text
schnapplist/
├── cli.py               Entry point (Click commands)
├── config.py            Environment-based configuration
├── models.py            Pydantic data models (Item, Photo, PriceInfo, EbayListingOptions)
├── orchestration.py     Pipeline orchestrator with live progress reporting
├── photo_processor.py   Photo loading, Claude-based grouping, Pillow enhancement
├── item_analyzer.py     Claude vision → item metadata + German listing copy
├── price_researcher.py  DuckDuckGo search + Claude price recommendation
├── report_generator.py  Markdown inspection report (editable source of truth)
├── report_parser.py     Parse edited Markdown report back to items.json
└── providers/
    ├── base.py          Abstract BaseMarketplace
    ├── kleinanzeigen.py Playwright browser automation
    └── ebay.py          eBay Trading API
```

## Cost notes

Claude API calls are used for:

- **Photo grouping** — one call per batch of up to 10 photos
- **Item analysis** — one call per item group (includes marketplace + eBay option suggestions)
- **Price suggestion** — one call per item (text only, no images)

Images are resized to 800 px before being sent to the API to keep token costs low. System prompts use [prompt caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching) to avoid re-charging repeated context.
