from __future__ import annotations

import csv as _csv
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
from schnapplist.core.report_generator import write_item_report
from schnapplist.core.report_parser import parse_report


def _parse_row(line: str) -> list[str]:
    return next(_csv.reader([line], delimiter=";"))


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
    row = _parse_row(lines[5])  # index 5 = first data row (0-based)
    assert row[0] == "Draft"
    assert row[1] == "abc12345"   # Custom label = item.id
    assert row[2] == "12345"      # Category ID
    assert row[3] == "Sony WH-1000XM5 Kopfhörer"  # Title
    assert row[4] == ""           # UPC empty
    assert row[5] == "180.00"      # Price
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
    row = _parse_row(lines[5])
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


def test_export_category_id_none_writes_empty_column(tmp_path: Path):
    item = _make_ebay_item(category_id=None, tmp_path=tmp_path)
    out = tmp_path / "export.csv"
    export_to_csv([item], out)
    lines = out.read_text(encoding="utf-8").splitlines()
    row = _parse_row(lines[5])
    assert row[2] == ""  # Category ID column is empty when None


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
