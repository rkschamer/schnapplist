"""Tests for services.item_service."""

from __future__ import annotations

from pathlib import Path

import pytest

from schnapplist.services.item_service import list_items


def test_list_items_returns_items(output_dir: Path) -> None:
    items = list_items(output_dir)
    assert len(items) == 1
    item = items[0]
    assert item["id"] == "abc12345"
    assert item["name"] == "Vintage Camera"


def test_list_items_condition(output_dir: Path) -> None:
    items = list_items(output_dir)
    assert items[0]["condition"] == "good"


def test_list_items_price(output_dir: Path) -> None:
    items = list_items(output_dir)
    assert items[0]["suggested_price"] == pytest.approx(49.99)


def test_list_items_empty_output_dir(tmp_path: Path) -> None:
    empty = tmp_path / "empty_output"
    empty.mkdir()
    assert not list_items(empty)


def test_list_items_approved_false(output_dir: Path) -> None:
    items = list_items(output_dir)
    assert items[0]["approved"] is False
