from __future__ import annotations

from pathlib import Path

from schnapplist.config import AGENT_TARGET_CONFIDENCE
from schnapplist.core.models import Item, ItemCondition, Photo, PriceInfo
from schnapplist.core.report_generator import write_item_report


def _make_item(tmp_path: Path, confidence: float) -> Item:
    photo = Photo(original_path=tmp_path / "photo.jpg")
    return Item(
        name="Test Item",
        title_de="Test",
        description="Test description.",
        condition=ItemCondition.GOOD,
        photos=[photo],
        price_info=PriceInfo(suggested_price=10.0, min_price=8.0, max_price=12.0, reasoning="test"),
        confidence=confidence,
        confidence_notes="Model uncertain"
        if confidence < AGENT_TARGET_CONFIDENCE
        else "Fully verified",
    )


def test_report_includes_confidence_row_when_low(tmp_path: Path) -> None:
    item = _make_item(tmp_path, confidence=0.5)
    path = write_item_report(item, 1, tmp_path / "run")
    content = path.read_text()
    assert "Confidence" in content
    assert "0.50" in content
    assert "Model uncertain" in content


def test_report_no_confidence_row_when_high(tmp_path: Path) -> None:
    item = _make_item(tmp_path, confidence=0.9)
    path = write_item_report(item, 1, tmp_path / "run")
    content = path.read_text()
    assert "Confidence" not in content


def test_report_no_confidence_row_at_exact_threshold(tmp_path: Path) -> None:
    """confidence == target is NOT low-confidence (strict <)."""
    item = _make_item(tmp_path, confidence=AGENT_TARGET_CONFIDENCE)
    path = write_item_report(item, 1, tmp_path / "run")
    content = path.read_text()
    assert "Confidence" not in content


def test_report_confidence_row_shown_without_price_info(tmp_path: Path) -> None:
    """Confidence table row appears even when there's no Recherche section."""
    photo = Photo(original_path=tmp_path / "photo.jpg")
    item = Item(
        name="Mystery Item",
        title_de="Unbekannt",
        description="Unbekanntes Gerät.",
        condition=ItemCondition.GOOD,
        photos=[photo],
        price_info=None,
        confidence=0.3,
        confidence_notes="Could not identify item",
    )
    path = write_item_report(item, 1, tmp_path / "run")
    content = path.read_text()
    assert "Confidence" in content
    assert "0.30" in content
    assert "Could not identify item" in content
