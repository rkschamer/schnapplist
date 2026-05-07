"""Item listing service — reads processed items from the latest report."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.report_parser import parse_report
from ..workflows.review_pipeline import find_latest_report


def list_items(output_dir: Path) -> list[dict[str, Any]]:
    """Return parsed item dicts from the most recent run, or an empty list."""
    report_path = find_latest_report(output_dir)
    if not report_path:
        return []
    return parse_report(report_path)
