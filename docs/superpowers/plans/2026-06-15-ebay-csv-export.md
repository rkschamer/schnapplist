# eBay CSV Draft Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a semicolon-delimited eBay DE draft listing CSV from validated Schnapplist items, uploadable via https://www.ebay.de/sh/reports/uploads.

**Architecture:** Add `ebay_category_id` to the data model, extend the LLM agent prompt to populate it, surface it in the Markdown report (editable), and implement a pure `export_to_csv()` function. Wire it into the CLI as both a post-process modal and a standalone `export` command.

**Tech Stack:** Python 3.11+, Pydantic, Rich, Click. No new dependencies required.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `schnapplist/core/models.py` | Modify | Add `ebay_category_id: str \| None` to `EbayListingOptions` |
| `schnapplist/core/ebay_csv_exporter.py` | **Create** | Pure `export_to_csv(items, output_path) -> int` function |
| `schnapplist/core/report_generator.py` | Modify | Write `eBay Category ID` row into eBay report section |
| `schnapplist/core/report_parser.py` | Modify | Parse `eBay Category ID` back into `ebay_options.ebay_category_id` |
| `schnapplist/workflows/item_research_agent.py` | Modify | Extend `ItemResearchOutput` + agent prompt to suggest eBay category ID |
| `schnapplist/config.py` | Modify | Read `[ebay] csv_action_header` from toml |
| `schnapplist.toml` | Modify | Add `[ebay]` section with default header |
| `schnapplist/cli/display.py` | Modify | Add `RichExportCallback` class |
| `schnapplist/cli/__init__.py` | Modify | Post-process modal after review + `export` command |
| `tests/test_ebay_csv_exporter.py` | **Create** | Unit tests for the exporter |

---

## Task 1: Add `ebay_category_id` to the data model

**Files:**
- Modify: `schnapplist/core/models.py`

- [ ] **Step 1: Write a failing test**

```python
# tests/test_ebay_csv_exporter.py  (create the file)
from __future__ import annotations

from schnapplist.core.models import EbayListingOptions


def test_ebay_listing_options_has_category_id_field():
    opts = EbayListingOptions(ebay_category_id="12345")
    assert opts.ebay_category_id == "12345"


def test_ebay_listing_options_category_id_defaults_none():
    opts = EbayListingOptions()
    assert opts.ebay_category_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_ebay_csv_exporter.py -v
```

Expected: `FAILED` — `EbayListingOptions` has no `ebay_category_id` field.

- [ ] **Step 3: Add the field to the model**

In `schnapplist/core/models.py`, find `EbayListingOptions` and add one line:

```python
class EbayListingOptions(BaseModel):
    listing_type: EbayListingType = EbayListingType.FIXED
    reserve_price: float | None = None
    duration_days: int = 7
    scheduled_start: datetime | None = None
    ebay_category_id: str | None = None      # ← add this
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_ebay_csv_exporter.py -v
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add schnapplist/core/models.py tests/test_ebay_csv_exporter.py
git commit -m "feat: add ebay_category_id field to EbayListingOptions"
```

---

## Task 2: Add `csv_action_header` config key

**Files:**
- Modify: `schnapplist/config.py`
- Modify: `schnapplist.toml`

- [ ] **Step 1: Add the `[ebay]` section to `schnapplist.toml`**

Append at the end of `schnapplist.toml`:

```toml
[ebay]
# Header string written into the generated eBay draft CSV.
# Change SiteID/Country/Currency for non-DE eBay sites.
csv_action_header = "Action(SiteID=Germany|Country=DE|Currency=EUR|Version=1193|CC=UTF-8)"
```

- [ ] **Step 2: Read the key in `config.py`**

In `schnapplist/config.py`, after the existing `_listing = _toml.get("listing", {})` line, add:

```python
_ebay = _toml.get("ebay", {})
```

Then after the existing config constants, add:

```python
EBAY_CSV_ACTION_HEADER: str = _ebay.get(
    "csv_action_header",
    "Action(SiteID=Germany|Country=DE|Currency=EUR|Version=1193|CC=UTF-8)",
)
```

- [ ] **Step 3: Verify config is importable**

```bash
uv run python -c "from schnapplist.config import EBAY_CSV_ACTION_HEADER; print(EBAY_CSV_ACTION_HEADER)"
```

Expected: `Action(SiteID=Germany|Country=DE|Currency=EUR|Version=1193|CC=UTF-8)`

- [ ] **Step 4: Commit**

```bash
git add schnapplist/config.py schnapplist.toml
git commit -m "feat: add [ebay] csv_action_header config key"
```

---

## Task 3: Implement the CSV exporter

**Files:**
- Create: `schnapplist/core/ebay_csv_exporter.py`
- Modify: `tests/test_ebay_csv_exporter.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_ebay_csv_exporter.py`:

```python
import csv
import io
from pathlib import Path

from schnapplist.core.ebay_csv_exporter import export_to_csv
from schnapplist.core.models import (
    EbayListingOptions,
    EbayListingType,
    Item,
    ItemCondition,
    Photo,
    PriceInfo,
)


def _make_ebay_item(
    *,
    approved: bool = True,
    category_id: str | None = "12345",
    listing_type: EbayListingType = EbayListingType.FIXED,
    tmp_path: Path,
) -> Item:
    photo = Photo(original_path=tmp_path / "photo.jpg")
    return Item(
        id="abc12345",
        name="Sony WH-1000XM5",
        title_de="Sony WH-1000XM5 Kopfhörer",
        description="Hochwertige Kopfhörer.",
        condition=ItemCondition.GOOD,
        photos=[photo],
        price_info=PriceInfo(
            suggested_price=180.0,
            min_price=150.0,
            max_price=220.0,
            reasoning="Market",
        ),
        approved=approved,
        marketplace="ebay",
        ebay_options=EbayListingOptions(
            listing_type=listing_type,
            ebay_category_id=category_id,
        ),
    )


def test_export_writes_info_header_lines(tmp_path: Path):
    item = _make_ebay_item(tmp_path=tmp_path)
    out = tmp_path / "export.csv"
    export_to_csv([item], out)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("#INFO")
    assert lines[1].startswith("#INFO")
    assert lines[2].startswith("#INFO")
    assert lines[3].startswith("#INFO")


def test_export_column_header_is_fifth_line(tmp_path: Path):
    item = _make_ebay_item(tmp_path=tmp_path)
    out = tmp_path / "export.csv"
    export_to_csv([item], out)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[4].startswith("Action(SiteID=Germany")


def test_export_one_row_per_approved_ebay_item(tmp_path: Path):
    items = [
        _make_ebay_item(tmp_path=tmp_path),
        _make_ebay_item(tmp_path=tmp_path),
    ]
    out = tmp_path / "export.csv"
    count = export_to_csv(items, out)
    assert count == 2
    lines = out.read_text(encoding="utf-8").splitlines()
    # 4 #INFO + 1 header + 2 data rows
    assert len(lines) == 7


def test_export_skips_unapproved_items(tmp_path: Path):
    items = [
        _make_ebay_item(approved=True, tmp_path=tmp_path),
        _make_ebay_item(approved=False, tmp_path=tmp_path),
    ]
    out = tmp_path / "export.csv"
    count = export_to_csv(items, out)
    assert count == 1


def test_export_skips_non_ebay_items(tmp_path: Path):
    photo = Photo(original_path=tmp_path / "photo.jpg")
    ka_item = Item(
        id="ka000001",
        name="Chair",
        title_de="Stuhl",
        description="Ein Stuhl.",
        condition=ItemCondition.GOOD,
        photos=[photo],
        approved=True,
        marketplace="kleinanzeigen",
    )
    item = _make_ebay_item(tmp_path=tmp_path)
    out = tmp_path / "export.csv"
    count = export_to_csv([ka_item, item], out)
    assert count == 1


def test_export_data_row_columns(tmp_path: Path):
    item = _make_ebay_item(tmp_path=tmp_path)
    out = tmp_path / "export.csv"
    export_to_csv([item], out)
    lines = out.read_text(encoding="utf-8").splitlines()
    row = lines[5].split(";")  # index 5 = first data row (0-based)
    assert row[0] == "Draft"
    assert row[1] == "abc12345"   # Custom label = item.id
    assert row[2] == "12345"      # Category ID
    assert row[3] == "Sony WH-1000XM5 Kopfhörer"  # Title
    assert row[4] == ""           # UPC empty
    assert row[5] == "180.0"      # Price
    assert row[6] == "1"          # Quantity
    assert row[7] == ""           # Photo URL empty
    assert row[8] == "3000"       # Condition ID for GOOD
    assert "<p>" in row[9]        # Description wrapped in <p>
    assert row[10] == "FixedPrice"


def test_export_auction_format(tmp_path: Path):
    item = _make_ebay_item(listing_type=EbayListingType.AUCTION, tmp_path=tmp_path)
    out = tmp_path / "export.csv"
    export_to_csv([item], out)
    lines = out.read_text(encoding="utf-8").splitlines()
    row = lines[5].split(";")
    assert row[10] == "Chinese"


def test_export_returns_zero_when_nothing_to_export(tmp_path: Path):
    photo = Photo(original_path=tmp_path / "photo.jpg")
    item = Item(
        id="ka000001",
        name="Chair",
        title_de="Stuhl",
        description="Ein Stuhl.",
        condition=ItemCondition.GOOD,
        photos=[photo],
        approved=True,
        marketplace="kleinanzeigen",
    )
    out = tmp_path / "export.csv"
    count = export_to_csv([item], out)
    assert count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_ebay_csv_exporter.py -v
```

Expected: `FAILED` — `ebay_csv_exporter` module does not exist.

- [ ] **Step 3: Implement the exporter**

Create `schnapplist/core/ebay_csv_exporter.py`:

```python
"""Generate an eBay draft listing CSV for bulk upload via Seller Hub Reports."""

from __future__ import annotations

from pathlib import Path

from ..config import EBAY_CSV_ACTION_HEADER
from .models import EbayListingType, Item

_INFO_LINES = [
    "#INFO;Version=0.0.2;Template= eBay-draft-listings-template_DE",
    "#INFO Action und Category ID sind erforderliche Felder. "
    "1) Stellen Sie Action auf Draft ein. "
    "2) Die Kategorie-ID finden Sie hier: "
    "https://pages.ebay.com/sellerinformation/news/categorychanges.html",
    "#INFO Nachdem Sie Ihren Entwurf erfolgreich hochgeladen haben; "
    "können Sie die Entwürfe hier vervollständigen: "
    "https://www.ebay.de/sh/lst/drafts",
    "#INFO",
]

_COLUMNS = [
    EBAY_CSV_ACTION_HEADER,
    "Custom label (SKU)",
    "Category ID",
    "Title",
    "UPC",
    "Price",
    "Quantity",
    "Item photo URL",
    "Condition ID",
    "Description",
    "Format",
]

_FORMAT_MAP = {
    EbayListingType.FIXED: "FixedPrice",
    EbayListingType.AUCTION: "Chinese",
    EbayListingType.BOTH: "FixedPrice",
}


def export_to_csv(items: list[Item], output_path: Path) -> int:
    """Write approved eBay items to a semicolon-delimited CSV draft file.

    Returns the number of items written (0 if no approved eBay items).
    """
    rows = [_item_to_row(item) for item in items
            if item.approved and item.marketplace == "ebay"]

    lines = _INFO_LINES + [";".join(_COLUMNS)]
    for row in rows:
        lines.append(";".join(row))

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(rows)


def _item_to_row(item: Item) -> list[str]:
    opts = item.ebay_options
    price = str(item.price_info.suggested_price) if item.price_info else ""
    category_id = (opts.ebay_category_id or "") if opts else ""
    listing_type = opts.listing_type if opts else EbayListingType.FIXED
    fmt = _FORMAT_MAP[listing_type]
    description = f"<p>{item.description}</p>" if item.description else ""

    return [
        "Draft",
        item.id,
        category_id,
        item.title_de or item.name,
        "",                              # UPC
        price,
        "1",
        "",                              # Item photo URL
        item.condition.to_ebay_condition(),
        description,
        fmt,
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_ebay_csv_exporter.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add schnapplist/core/ebay_csv_exporter.py tests/test_ebay_csv_exporter.py
git commit -m "feat: implement ebay_csv_exporter with full test coverage"
```

---

## Task 4: Surface `ebay_category_id` in the Markdown report

**Files:**
- Modify: `schnapplist/core/report_generator.py`
- Modify: `schnapplist/core/report_parser.py`
- Modify: `tests/test_ebay_csv_exporter.py`

- [ ] **Step 1: Write failing tests for report generation and parsing**

Add to `tests/test_ebay_csv_exporter.py`:

```python
from schnapplist.core.models import EbayListingOptions
from schnapplist.core.report_generator import write_item_report
from schnapplist.core.report_parser import parse_report


def test_report_generator_writes_ebay_category_id(tmp_path: Path):
    item = _make_ebay_item(category_id="12345", tmp_path=tmp_path)
    write_item_report(item, index=1, run_dir=tmp_path)
    text = (tmp_path / "item-1.md").read_text(encoding="utf-8")
    assert "eBay Category ID" in text
    assert "12345" in text


def test_report_parser_reads_ebay_category_id(tmp_path: Path):
    item = _make_ebay_item(category_id="12345", tmp_path=tmp_path)
    write_item_report(item, index=1, run_dir=tmp_path)
    parsed = parse_report(tmp_path / "item-1.md")
    assert parsed[0]["ebay_options"]["ebay_category_id"] == "12345"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_ebay_csv_exporter.py::test_report_generator_writes_ebay_category_id tests/test_ebay_csv_exporter.py::test_report_parser_reads_ebay_category_id -v
```

Expected: both `FAILED`.

- [ ] **Step 3: Add `eBay Category ID` row to report generator**

In `schnapplist/core/report_generator.py`, find the eBay section (around line 54). After the `eBay scheduled start` row, add:

```python
        category_id = opts.ebay_category_id if opts else None
        lines += [
            f"| **eBay listing type** | {lt} |",
            f"| **eBay duration (days)** | {dur} |",
            f"| **eBay reserve price (EUR)** | {reserve} |",
            f"| **eBay scheduled start** | {sched_str} |",
            f"| **eBay Category ID** | {category_id or '—'} |",   # ← add this line
        ]
```

- [ ] **Step 4: Add `eBay Category ID` parsing to report parser**

In `schnapplist/core/report_parser.py`, find `_parse_ebay_options`. Add after the `scheduled_start` block:

```python
    cat_id = table.get("eBay Category ID", "").strip()
    if cat_id and cat_id != "—":
        ebay["ebay_category_id"] = cat_id
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_ebay_csv_exporter.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add schnapplist/core/report_generator.py schnapplist/core/report_parser.py tests/test_ebay_csv_exporter.py
git commit -m "feat: add eBay Category ID to report and parser"
```

---

## Task 5: Extend LLM agent to suggest eBay category ID

**Files:**
- Modify: `schnapplist/workflows/item_research_agent.py`

- [ ] **Step 1: Add `ebay_category_id` to `ItemResearchOutput`**

In `schnapplist/workflows/item_research_agent.py`, find `ItemResearchOutput` and add:

```python
class ItemResearchOutput(BaseModel):
    name: str
    brand: str | None
    model: str | None
    condition: ItemCondition
    condition_notes: str
    title_de: str
    description_de: str
    specs: dict[str, str]
    keywords: list[str]
    category: str
    ebay_category_id: str | None    # ← add this field
    price_info: PriceInfo
    ka_options: KleinanzeigenListingOptions | None
    ebay_options: EbayListingOptions | None
```

- [ ] **Step 2: Update `_AGENT_SYSTEM_PROMPT` to instruct the LLM**

Find `_AGENT_SYSTEM_PROMPT` and extend the rules section:

```python
_AGENT_SYSTEM_PROMPT = """\
You are an expert reseller assistant for German marketplaces (Kleinanzeigen, eBay.de).

Your job:
1. Call analyze_photos to identify the item (name, brand, model, condition).
2. Call web_search to look up the exact manufacturer specifications for the identified model.
3. Call web_search to find current prices on kleinanzeigen.de and ebay.de.
4. If any spec or price is unclear, call web_search again with a refined query.
5. When you have enough verified information, produce your final structured output.

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
"""
```

- [ ] **Step 3: Wire `ebay_category_id` into the returned `EbayListingOptions`**

Find the place in `item_research_agent.py` where the agent result is mapped to `EbayListingOptions`. This happens in `process_pipeline.py`, not the agent itself — search for where `ItemResearchOutput` fields are mapped:

```bash
grep -n "ebay_options\|ItemResearchOutput" schnapplist/workflows/process_pipeline.py | head -20
```

Open `schnapplist/workflows/process_pipeline.py` at the relevant line and ensure `ebay_category_id` is passed through:

```python
# Find the block that constructs EbayListingOptions from output, e.g.:
ebay_opts = EbayListingOptions(
    listing_type=...,
    duration_days=...,
    reserve_price=...,
    scheduled_start=...,
    ebay_category_id=output.ebay_category_id,   # ← add this
)
```

- [ ] **Step 4: Verify existing agent tests still pass**

```bash
uv run pytest tests/test_item_research_agent.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add schnapplist/workflows/item_research_agent.py schnapplist/workflows/process_pipeline.py
git commit -m "feat: agent now suggests eBay DE category ID"
```

---

## Task 6: Add `RichExportCallback` to the display module

**Files:**
- Modify: `schnapplist/cli/display.py`

- [ ] **Step 1: Add `RichExportCallback` at the end of `display.py`**

Append to `schnapplist/cli/display.py` after `RichDecisionCallback`:

```python
class RichExportCallback:
    """Prompts the user to generate the eBay CSV export after reviewing."""

    def __init__(self, progress_cb: RichLiveCallback) -> None:
        self._progress_cb = progress_cb

    def ask(self, ebay_item_count: int) -> bool:
        """Show a modal asking whether to generate the eBay CSV.

        Returns True if the user pressed 'y', False otherwise.
        """
        modal = Panel(
            Text.assemble(
                (f"{ebay_item_count} eBay item(s) found.\n", ""),
                ("Generate CSV for all approved ones?\n\n", ""),
                ("  ", ""),
                ("[ y ]", "bold black on green"),
                ("  generate CSV      ", ""),
                ("[ n ]", "bold black on white"),
                ("  skip", ""),
                "\n\n",
                ("  press a key…", "dim italic"),
            ),
            title=Text.assemble(("📦  eBay CSV Export", "bold green")),
            border_style="green",
            width=60,
            padding=(1, 2),
        )
        self._progress_cb.show_modal(modal)
        try:
            choice = _read_single_key({"y", "n"}, default="n")
        finally:
            self._progress_cb.restore_body()
        return choice == "y"
```

- [ ] **Step 2: Verify the module still imports cleanly**

```bash
uv run python -c "from schnapplist.cli.display import RichExportCallback; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add schnapplist/cli/display.py
git commit -m "feat: add RichExportCallback for eBay CSV export prompt"
```

---

## Task 7: Wire the export modal into the `process` command

**Files:**
- Modify: `schnapplist/cli/__init__.py`

- [ ] **Step 1: Update the import at the top of `__init__.py`**

Find the existing import line:

```python
from .display import RichDecisionCallback, RichLiveCallback
```

Replace it with:

```python
from .display import RichDecisionCallback, RichExportCallback, RichLiveCallback
```

- [ ] **Step 2: Replace the post-review block in the `process` command**

Find this block in the `process` command (around line 117):

```python
    if click.confirm("\nReview and edit the report now?", default=True):
        _run_review(report_path)

    console.print(
        "\nWhen ready, run [bold]schnapplist post[/bold] to create listings."
    )
```

Replace it with:

```python
    if click.confirm("\nReview and edit the report now?", default=True):
        _run_review(report_path)

    # After review: offer eBay CSV export if any item targets eBay
    from ..core.report_parser import parse_report
    from ..core.ebay_csv_exporter import export_to_csv

    parsed = parse_report(report_path)
    ebay_items = [d for d in parsed if d.get("marketplace") == "ebay"]

    if ebay_items:
        export_cb = RichExportCallback(rich_cb)
        if export_cb.ask(len(ebay_items)):
            from ..services.posting_service import load_items_from_report
            _, items = load_items_from_report(output_dir)
            csv_path = report_path / "ebay-export.csv"
            count = export_to_csv(items, csv_path)
            console.print(
                f"\n[bold green]eBay CSV written:[/bold green] {csv_path}  "
                f"([bold]{count}[/bold] approved item(s))"
            )

    console.print(
        "\nWhen ready, run [bold]schnapplist post[/bold] to create listings."
    )
```

- [ ] **Step 3: Verify the CLI still starts without errors**

```bash
uv run schnapplist --help
uv run schnapplist process --help
```

Expected: both print help text without errors.

- [ ] **Step 4: Commit**

```bash
git add schnapplist/cli/__init__.py
git commit -m "feat: show eBay CSV export modal after process review"
```

---

## Task 8: Add standalone `export` command

**Files:**
- Modify: `schnapplist/cli/__init__.py`

- [ ] **Step 1: Add the `export` command after the `post` command**

In `schnapplist/cli/__init__.py`, add this command group and command before the `config` group:

```python
# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

@main.group()
def export() -> None:
    """Export items to external formats."""


@export.command("ebay")
@click.option(
    "--output-dir", "-o",
    default="./output",
    type=click.Path(path_type=Path),
    show_default=True,
    help="Root output directory to search for the latest run.",
)
@click.option(
    "--run-dir",
    default=None,
    type=click.Path(path_type=Path),
    help="Explicit run folder (default: most recent in --output-dir).",
)
@click.option(
    "--output",
    default=None,
    type=click.Path(path_type=Path),
    help="Output CSV path (default: <run-dir>/ebay-export.csv).",
)
def export_ebay(output_dir: Path, run_dir: Path | None, output: Path | None) -> None:
    """Generate an eBay draft listing CSV for bulk upload."""
    from ..core.ebay_csv_exporter import export_to_csv
    from ..services.posting_service import load_items_from_report
    from ..workflows.review_pipeline import find_latest_report

    if run_dir:
        report_path = Path(run_dir)
    else:
        report_path = find_latest_report(output_dir)

    if report_path is None:
        console.print("[yellow]No report found. Run 'process' first.[/yellow]")
        sys.exit(1)

    try:
        _, items = load_items_from_report(output_dir if not run_dir else run_dir.parent)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    csv_path = Path(output) if output else report_path / "ebay-export.csv"
    count = export_to_csv(items, csv_path)
    console.print(
        f"[bold green]eBay CSV written:[/bold green] {csv_path}  "
        f"([bold]{count}[/bold] approved item(s))"
    )
```

- [ ] **Step 2: Verify the command is registered**

```bash
uv run schnapplist export --help
uv run schnapplist export ebay --help
```

Expected: both print help text.

- [ ] **Step 3: Commit**

```bash
git add schnapplist/cli/__init__.py
git commit -m "feat: add schnapplist export ebay command"
```

---

## Task 9: Run the full test suite

- [ ] **Step 1: Run all tests**

```bash
uv run pytest -v
```

Expected: all tests pass, no regressions.

- [ ] **Step 2: Run the linter**

```bash
uv run ruff check schnapplist/ tests/
```

Expected: no errors. Fix any that appear.

- [ ] **Step 3: Final commit if any lint fixes were needed**

```bash
git add -p
git commit -m "fix: lint corrections"
```

---

## Verification

After all tasks are complete, do a quick end-to-end smoke test:

```bash
# 1. Process some photos (or use existing output)
uv run schnapplist process --photos-dir ./photos --marketplace ebay

# 2. Review the report — set Approved: true and verify eBay Category ID row is present

# 3. The export modal should appear after editor closes — press y

# 4. Verify the CSV exists and looks correct
cat output/schnapplist-report-*/ebay-export.csv

# 5. Also test the standalone command
uv run schnapplist export ebay
```
