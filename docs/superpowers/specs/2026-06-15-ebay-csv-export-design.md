# eBay CSV Draft Export — Design Spec

**Date:** 2026-06-15  
**Status:** Approved

## Background

eBay developer program access has been unavailable. The eBay Seller Hub Reports tab
(`https://www.ebay.de/sh/reports/uploads`) accepts bulk listing uploads via a
semicolon-delimited CSV that creates draft listings. This feature generates that CSV
from validated Schnapplist items, enabling eBay listings without API access.

## CSV Format

Based on the official eBay DE template (`eBay-draft-listings-template_DE`):

- **Delimiter:** semicolon (`;`)
- **Encoding:** UTF-8
- **Structure:** 4 `#INFO` comment lines + 1 column header line + 1 data row per item
- **Columns (11):** `Action(...)`, `Custom label (SKU)`, `Category ID`, `Title`, `UPC`, `Price`, `Quantity`, `Item photo URL`, `Condition ID`, `Description`, `Format`

The `Action(...)` header value (e.g. `Action(SiteID=Germany|Country=DE|Currency=EUR|Version=1193|CC=UTF-8)`)
is configurable via `schnapplist.toml` under `[ebay] csv_action_header`, defaulting to the DE format.

**Photo URL column is intentionally left empty.** Photos are uploaded manually when completing the draft on eBay.

## Column Mapping

| CSV Column        | Source                                              |
|-------------------|-----------------------------------------------------|
| `Action`          | Always `Draft`                                      |
| `Custom label`    | `item.id`                                           |
| `Category ID`     | `item.ebay_options.ebay_category_id`                |
| `Title`           | `item.title_de`                                     |
| `UPC`             | empty                                               |
| `Price`           | `item.price_info.suggested_price`                   |
| `Quantity`        | Always `1`                                          |
| `Item photo URL`  | empty                                               |
| `Condition ID`    | `item.condition.to_ebay_condition()`                |
| `Description`     | `item.description` wrapped in `<p>…</p>`            |
| `Format`          | `FixedPrice` / `Chinese` / `FixedPrice` for fixed/auction/both |

## Data Model Changes

### `EbayListingOptions` (models.py)

Add one field:

```python
ebay_category_id: str | None = None
```

The LLM populates this during the `process` step alongside the existing free-text
`category` field. The item research agent prompt is extended to also suggest the
numeric eBay DE category ID.

### Markdown Report

A new row is added to the eBay section of the report table:

```
| **eBay Category ID** | 12345 |
```

This field is editable by the user in the review step. `report_parser.py` parses it
back into `ebay_options.ebay_category_id`.

### `schnapplist.toml`

New optional key under `[ebay]`:

```toml
[ebay]
csv_action_header = "Action(SiteID=Germany|Country=DE|Currency=EUR|Version=1193|CC=UTF-8)"
```

## New Module: `core/ebay_csv_exporter.py`

Single public function:

```python
def export_to_csv(items: list[Item], output_path: Path) -> int:
    """Write approved eBay items to a semicolon-delimited CSV draft file.
    
    Returns the number of items written.
    """
```

- Filters to `item.approved is True` and `item.marketplace == "ebay"`
- Writes the 4 `#INFO` lines, column header, then one row per item
- Returns item count (0 if nothing to export)
- No side effects beyond writing the file

## CLI Integration

### Post-process modal

After `process` completes and the user closes the `$EDITOR` review:

1. Count approved items with `marketplace="ebay"`
2. If count is 0: exit as today — no modal shown
3. If count ≥ 1: show a Rich modal (same style as `item_failed` modal):
   ```
   ┌─ eBay CSV Export ──────────────────────────────┐
   │                                                  │
   │   2 approved eBay item(s) ready for export.     │
   │                                                  │
   │   [ y ]  generate CSV      [ n ]  skip          │
   │                                                  │
   │   press a key…                                   │
   └──────────────────────────────────────────────────┘
   ```
4. `[y]`: calls `export_to_csv()`, writes `<run-folder>/ebay-export.csv`, shows
   success panel with path and count, then exits
5. `[n]`: exits as today

The modal is implemented via the existing `RichLiveCallback.show_modal()` /
`_read_single_key()` infrastructure in `cli/display.py`.
A new `RichExportCallback` class (added to `cli/display.py`, following the same
pattern as `RichDecisionCallback`) handles the prompt.

### Standalone command

```
schnapplist export --marketplace ebay [--output PATH] [--run-dir PATH]
```

- `--run-dir`: path to a specific run folder (default: latest in `output/`)
- `--output`: output CSV path (default: `<run-dir>/ebay-export.csv`)
- Parses Markdown reports → `Item` objects via existing `report_parser.py` +
  `item_service.py`, calls `export_to_csv()`, prints path and count

## Files Touched

| File | Change |
|------|--------|
| `schnapplist/core/models.py` | Add `ebay_category_id` to `EbayListingOptions` |
| `schnapplist/core/ebay_csv_exporter.py` | **New** — exporter function |
| `schnapplist/core/report_generator.py` | Add `eBay Category ID` row to eBay section |
| `schnapplist/core/report_parser.py` | Parse `eBay Category ID` from table |
| `schnapplist/workflows/item_research_agent.py` | Extend LLM prompt to suggest eBay category ID |
| `schnapplist/cli/__init__.py` | Post-process modal + `export` command |
| `schnapplist/cli/display.py` | `RichExportCallback` or inline modal logic |
| `schnapplist/config.py` | `[ebay] csv_action_header` config key |
| `schnapplist.toml` | Add `[ebay]` section with default header |
| `tests/test_ebay_csv_exporter.py` | **New** — unit tests for exporter |

## Out of Scope

- Photo hosting / automatic photo URL population
- Support for other eBay country sites beyond DE (configurable via `csv_action_header`)
- Kleinanzeigen CSV export
