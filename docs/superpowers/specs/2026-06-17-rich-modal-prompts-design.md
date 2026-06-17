# Rich Modal Prompts — Post-Process Flow

**Date:** 2026-06-17

## Context

After `schnapplist process` completes, two plain TTY prompts (via `click.confirm`) are shown:

1. "Review and edit the report now?" — opens `$EDITOR` on confirmation
2. "N approved eBay item(s) ready. Generate CSV draft?" — triggers CSV export

Both break out of the Rich Live UI. The goal is to replace them with in-UI modal dialogs consistent with the existing `item_failed` modal pattern.

## Behaviour Change

**Prompt 1 — Report ready**

- No longer opens `$EDITOR`. The user reviews the report in their own editor/terminal.
- Shows a Rich modal panel displaying:
  - The report folder path
  - The list of `item-*.md` filenames
  - Footer: "Press any key when done reviewing…"
- Blocks until any keypress; returns nothing (caller ignores return value).

**Prompt 2 — eBay CSV export**

- Shows a Rich modal panel displaying:
  - "N approved eBay item(s) ready. Generate CSV draft?"
  - `[ y ]  yes` / `[ n ]  no` key hints
- Blocks until `y` or `n` keypress; returns `"yes"` or `"no"`.
- Only shown when eBay items exist (same gate as today).

## Architecture

### `display.py` — `RichDecisionCallback.__call__`

Two new event branches added alongside the existing `"item_failed"` branch:

| Event | Kwargs | Return |
|---|---|---|
| `"report_ready"` | `report_path: Path`, `item_paths: list[Path]` | `""` |
| `"ebay_export_prompt"` | `approved_count: int`, `total_ebay_count: int` | `"yes"` \| `"no"` |

Both follow the same show-modal / read-key / restore-body pattern as `item_failed`.

**Styling:**
- `width=60`, `padding=(1, 2)`, centered via `Align.center(..., vertical="middle")`
- `report_ready`: `border_style="blue"`
- `ebay_export_prompt`: `border_style="green"`

### `ui/cli/__init__.py` — `process` command

Remove:
- `click.confirm("Review and edit the report now?", ...)` call
- `_run_review(report_path)` call from post-process flow
- `click.confirm("N approved eBay item(s) ready...", ...)` call

Add:
1. Build `item_paths` from `sorted(report_path.glob("item-*.md"), ...)`
2. `decision_cb("report_ready", report_path=report_path, item_paths=item_paths)`
3. `result = decision_cb("ebay_export_prompt", approved_count=..., total_ebay_count=...)` (only when `ebay_items` is non-empty)
4. Branch on `result == "yes"` to run CSV export

The standalone `review` command keeps `_run_review` unchanged.

## Out of scope

- No changes to `_run_review` (used by the `review` command)
- No changes to the `item_failed` modal
- No new classes or abstractions
