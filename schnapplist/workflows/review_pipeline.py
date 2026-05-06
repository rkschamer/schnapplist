from __future__ import annotations

from pathlib import Path


def find_latest_report(output_dir: Path) -> Path | None:
    candidates = sorted(output_dir.glob("schnapplist_report_*.md"), reverse=True)
    return candidates[0] if candidates else None
