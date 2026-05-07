"""Helpers for locating existing run output."""

from __future__ import annotations

from pathlib import Path


def find_latest_report(output_dir: Path) -> Path | None:
    """Return the most recent schnapplist run folder inside output_dir, or None."""
    candidates = sorted(output_dir.glob("schnapplist-report-*/"), reverse=True)
    return candidates[0] if candidates else None
