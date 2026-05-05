"""Parse an edited Schnapplist Markdown report back to item field diffs.

Only the fields the user is expected to edit are extracted:
  name, title_de, description, tags, category, brand, model,
  price_info.suggested_price, marketplace,
  ebay_options (listing_type, duration_days, reserve_price)

The ID row in the table is the anchor — it is never editable.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_report(report_path: Path) -> list[dict[str, Any]]:
    """Return a list of partial item dicts parsed from *report_path*.

    Each dict contains at minimum ``{"id": "<8-char id>"}`` plus any editable
    fields found.  Fields that could not be parsed are omitted so callers can
    merge diffs without overwriting good existing data.
    """
    text = report_path.read_text(encoding="utf-8")
    raw_sections = re.split(r"\n(?=## )", text)
    results: list[dict[str, Any]] = []
    for section in raw_sections:
        item_diff = _parse_item_section(section.strip())
        if item_diff:
            results.append(item_diff)
    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_item_section(section: str) -> dict[str, Any] | None:
    """Parse one item section and return a partial diff dict, or None."""
    lines = section.splitlines()
    if not lines or not lines[0].startswith("## "):
        return None

    table = _extract_table(lines)
    id_raw = table.get("ID", "")
    m_id = re.search(r"`([^`]+)`", id_raw)
    if not m_id:
        return None

    diff: dict[str, Any] = {"name": lines[0][3:].strip(), "id": m_id.group(1)}
    diff.update(_parse_table_fields(table))
    ebay = _parse_ebay_options(table)
    if ebay:
        diff["ebay_options"] = ebay
    diff.update(_parse_named_sections(section))
    return diff


def _extract_table(lines: list[str]) -> dict[str, str]:
    """Build a key→value map from all pipe table rows in the section."""
    table: dict[str, str] = {}
    for line in lines:
        m = re.match(r"\|\s*\*\*(.+?)\*\*\s*\|\s*(.+?)\s*\|", line)
        if m:
            table[m.group(1).strip()] = m.group(2).strip()
    return table


def _parse_table_fields(table: dict[str, str]) -> dict[str, Any]:
    """Extract scalar editable fields from the table key→value map."""
    out: dict[str, Any] = {}

    title = table.get("Title (DE)", "").strip()
    if title and title != "—":
        out["title_de"] = title

    cat = table.get("Category", "").strip()
    if cat and cat != "—":
        out["category"] = cat

    bm = table.get("Brand / Model", "").strip()
    if bm and bm not in ("—", "— / —"):
        parts = bm.split(" / ", 1)
        brand = parts[0].strip()
        model = parts[1].strip() if len(parts) > 1 else ""
        if brand and brand != "—":
            out["brand"] = brand
        if model and model != "—":
            out["model"] = model

    price_cell = table.get("Suggested price", "")
    m_price = re.search(r"(\d[\d ]*[.,]\d{2})", price_cell.replace(" ", " "))
    if m_price:
        with contextlib.suppress(ValueError):
            out["suggested_price"] = float(
                m_price.group(1).replace(",", ".").replace(" ", "")
            )

    mkt = table.get("Marketplace", "").strip().lower()
    if mkt in ("ebay", "kleinanzeigen"):
        out["marketplace"] = mkt

    approved_raw = table.get("Approved", "").strip().lower()
    if approved_raw in ("true", "false"):
        out["approved"] = approved_raw == "true"

    return out


def _parse_ebay_options(table: dict[str, str]) -> dict[str, Any]:
    """Extract eBay-specific option fields; returns empty dict when absent."""
    ebay: dict[str, Any] = {}

    lt = table.get("eBay listing type", "").strip().lower()
    if lt in ("auction", "fixed", "both"):
        ebay["listing_type"] = lt

    dur_raw = table.get("eBay duration (days)", "").strip()
    if dur_raw.isdigit() and int(dur_raw) in (1, 3, 5, 7, 10):
        ebay["duration_days"] = int(dur_raw)

    reserve_raw = table.get("eBay reserve price (EUR)", "").strip()
    if reserve_raw and reserve_raw != "—":
        m_r = re.search(r"(\d+[.,]\d{2})", reserve_raw)
        if m_r:
            with contextlib.suppress(ValueError):
                ebay["reserve_price"] = float(m_r.group(1).replace(",", "."))

    return ebay


def _parse_named_sections(section: str) -> dict[str, Any]:
    """Extract #### Beschreibung and #### Tags from an item section."""
    out: dict[str, Any] = {}

    m = re.search(
        r"#{3,4}\s+Beschreibung\s*\n(.*?)(?=\n#{3,4}|\n---|\Z)",
        section,
        re.DOTALL,
    )
    if m:
        description = m.group(1).strip()
        if description:
            out["description"] = description

    m = re.search(
        r"#{3,4}\s+Tags\s*\n(.*?)(?=\n#{3,4}|\n---|\Z)",
        section,
        re.DOTALL,
    )
    if m:
        tags_raw = m.group(1).strip()
        tags = [t.strip().strip("`") for t in tags_raw.split(",") if t.strip()]
        if tags:
            out["tags"] = tags

    return out
