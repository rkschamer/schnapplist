"""Tests for services.posting_service."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from schnapplist.core.models import Item
from schnapplist.services.posting_service import PostResult, load_items_from_report, post_item


def test_load_items_from_report_returns_items(output_dir: Path) -> None:
    report_path, items = load_items_from_report(output_dir)
    assert len(items) == 1
    assert items[0].id == "abc12345"
    assert items[0].name == "Vintage Camera"


def test_load_items_from_report_raises_when_no_report(tmp_path: Path) -> None:
    empty = tmp_path / "empty_output"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        load_items_from_report(empty)


def test_load_items_price(output_dir: Path) -> None:
    _, items = load_items_from_report(output_dir)
    assert items[0].price_info is not None
    assert items[0].price_info.suggested_price == pytest.approx(49.99)


def test_post_item_dry_run(sample_item: Item) -> None:
    result = post_item(sample_item, "kleinanzeigen", dry_run=True)
    assert result.dry_run is True
    assert result.success is True
    assert result.dry_run_summary is not None
    assert result.dry_run_summary["marketplace"] == "kleinanzeigen"


def test_post_item_unknown_marketplace(sample_item: Item) -> None:
    result = post_item(sample_item, "unknown_market")
    assert result.success is False
    assert "Unknown marketplace" in (result.error or "")


def test_post_item_success(sample_item: Item) -> None:
    mock_mkt = MagicMock()
    mock_mkt.is_available.return_value = True
    mock_mkt.post_listing.return_value = "https://example.com/listing/123"

    with patch("schnapplist.providers.MARKETPLACES", {"kleinanzeigen": mock_mkt}):
        result = post_item(sample_item, "kleinanzeigen")

    assert result.success is True
    assert result.url == "https://example.com/listing/123"
    mock_mkt.post_listing.assert_called_once()


def test_post_item_marketplace_unavailable(sample_item: Item) -> None:
    mock_mkt = MagicMock()
    mock_mkt.is_available.return_value = False

    with patch("schnapplist.providers.MARKETPLACES", {"kleinanzeigen": mock_mkt}):
        result = post_item(sample_item, "kleinanzeigen")

    assert result.success is False
    assert "not configured" in (result.error or "")


def test_post_result_success_property() -> None:
    r = PostResult(item_id="1", item_name="test", marketplace="ebay", url="http://x.com")
    assert r.success is True

    r2 = PostResult(item_id="1", item_name="test", marketplace="ebay", error="fail")
    assert r2.success is False
