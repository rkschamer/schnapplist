# Snaplist

AI-powered CLI that turns a folder of photos into ready-to-post listings on [Kleinanzeigen.de](https://www.kleinanzeigen.de) and [eBay.de](https://www.ebay.de).

```
photos/  →  group by item  →  enhance  →  identify + price  →  report.md  →  post
```

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for dependency management
- An [Anthropic API key](https://console.anthropic.com/) (Claude is used for vision, item analysis, and pricing)

## Installation

```bash
git clone https://github.com/yourname/snaplist
cd snaplist
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
|---|---|---|
| `ANTHROPIC_API_KEY` | Everything | Get one at console.anthropic.com |
| `KLEINANZEIGEN_EMAIL` | Kleinanzeigen posting | Your login e-mail |
| `KLEINANZEIGEN_PASSWORD` | Kleinanzeigen posting | Your login password |
| `EBAY_APP_ID` | eBay posting | From developer.ebay.com |
| `EBAY_AUTH_TOKEN` | eBay posting | OAuth user token |
| `EBAY_SANDBOX` | eBay testing | Set to `true` to use sandbox |

## Workflow

### Step 1 — Process photos

Drop all photos (any mix of items) into a single folder, then run:

```bash
uv run snaplist process --photos-dir ./photos
```

What happens:

1. **Grouping** — Claude vision inspects every photo and clusters them by physical item.
2. **Enhancement** — Each photo is auto-levelled, contrast-boosted, sharpened, and cropped to 4:3 at up to 1200 px wide. Enhanced copies land in `output/enhanced/`.
3. **Item analysis** — Claude identifies the item (brand, model, condition) and writes a German title and description ready for pasting into a listing.
4. **Price research** — DuckDuckGo searches Kleinanzeigen and eBay.de for comparable listings. Claude reads the results and suggests a price with a min/max range.
5. **Report** — A Markdown file `output/snaplist_report_<timestamp>.md` is written with all items, photos, prices, and descriptions for your review.

### Step 2 — Review the report

Open `output/snaplist_report_*.md` in any Markdown viewer. Check:

- Item identification is correct
- German title and description read well
- Suggested price seems fair
- Enhanced photos look good

Edit `output/items.json` directly if you want to tweak a title, description, or price before posting.

### Step 3 — List and post

```bash
# See all processed items and their IDs
uv run snaplist list

# Post a specific item (add --dry-run to preview without posting)
uv run snaplist post --item-id <id> --provider kleinanzeigen
uv run snaplist post --item-id <id> --provider ebay
```

The `post` command marks the item as approved in `items.json` after a successful post.

## Commands

```
snaplist process   Analyse photos and write inspection report
snaplist list      Show all processed items
snaplist post      Post an item to a marketplace
```

Run any command with `--help` for full option details.

## Providers

### Kleinanzeigen

Uses Playwright to automate the Kleinanzeigen web UI — no official posting API exists. The browser runs **in headed mode** so you can solve any CAPTCHA that appears. Requires `KLEINANZEIGEN_EMAIL` and `KLEINANZEIGEN_PASSWORD` in `.env`.

### eBay

Uses the [eBay Trading API](https://developer.ebay.com/api-docs/user-guides/static/trading-user-guide-landing.html) (`AddItem` call), site ID 77 (Germany). Listings are created as 30-day fixed-price items with DHL shipping. Requires `EBAY_APP_ID` and `EBAY_AUTH_TOKEN` in `.env`. Set `EBAY_SANDBOX=true` to test against the sandbox before going live.

### Adding a new provider

Create a class in `snaplist/providers/` that extends `BaseProvider`, implement `is_available()` and `post_listing(item) -> str`, then register it in `snaplist/providers/__init__.py`:

```python
from .myprovider import MyProvider

PROVIDERS = {
    ...
    "myprovider": MyProvider(),
}
```

## Project layout

```
snaplist/
├── cli.py               Entry point (Click commands)
├── config.py            Environment-based configuration
├── models.py            Pydantic data models (Item, Photo, PriceInfo)
├── photo_processor.py   Photo loading, Claude-based grouping, Pillow enhancement
├── item_analyzer.py     Claude vision → item metadata + German listing copy
├── price_researcher.py  DuckDuckGo search + Claude price recommendation
├── report_generator.py  Markdown inspection report
└── providers/
    ├── base.py          Abstract BaseProvider
    ├── kleinanzeigen.py Playwright browser automation
    └── ebay.py          eBay Trading API
```

## Cost notes

Claude API calls are used for:

- **Photo grouping** — one call per batch of up to 10 photos
- **Item analysis** — one call per item group
- **Price suggestion** — one call per item (text only, no images)

Images are resized to 800 px before being sent to the API to keep token costs low. System prompts use [prompt caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching) to avoid re-charging repeated context.
