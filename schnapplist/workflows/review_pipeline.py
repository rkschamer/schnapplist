"""Deterministic workflow for syncing edited reports back to state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


@dataclass
class ReviewRunResult:
    state_file: Path
    parsed_items: int
    changed_fields: int


class ReviewWorkflow:
    """Apply parsed report edits to the persisted item state."""

    def run(self, *, output_dir: Path, report_path: Path) -> ReviewRunResult:
        from ..report_parser import parse_report

        state_file = output_dir / "items.json"
        if not state_file.exists():
            raise FileNotFoundError(f"items.json not found at {state_file}")

        diffs = parse_report(report_path)
        if not diffs:
            return ReviewRunResult(
                state_file=state_file,
                parsed_items=0,
                changed_fields=0,
            )

        items_data: list[dict[str, Any]] = json.loads(state_file.read_text(encoding="utf-8"))
        index: dict[str, dict[str, Any]] = {d["id"]: d for d in items_data}
        changed = 0

        for diff in diffs:
            item_id = diff.get("id")
            if not item_id or item_id not in index:
                continue

            existing = index[item_id]
            for key, new_val in diff.items():
                if key == "id":
                    continue
                if key == "suggested_price":
                    price_info = cast(dict[str, Any], existing.get("price_info"))
                    if price_info and price_info.get("suggested_price") != new_val:
                        price_info["suggested_price"] = new_val
                        changed += 1
                elif key == "ebay_options":
                    if "ebay_options" not in existing or not existing["ebay_options"]:
                        existing["ebay_options"] = {}
                    ebay_opts = cast(dict[str, Any], existing["ebay_options"])
                    for opt_key, opt_val in cast(dict[str, Any], new_val).items():
                        if ebay_opts.get(opt_key) != opt_val:
                            ebay_opts[opt_key] = opt_val
                            changed += 1
                elif existing.get(key) != new_val:
                    existing[key] = new_val
                    changed += 1

        state_file.write_text(
            json.dumps(items_data, indent=2, default=str),
            encoding="utf-8",
        )
        return ReviewRunResult(
            state_file=state_file,
            parsed_items=len(diffs),
            changed_fields=changed,
        )


def find_latest_report(output_dir: Path) -> Path | None:
    candidates = sorted(output_dir.glob("schnapplist_report_*.md"), reverse=True)
    return candidates[0] if candidates else None
