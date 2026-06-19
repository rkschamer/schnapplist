"""Tests for Item.from_analysis()."""

from __future__ import annotations

from pathlib import Path

from schnapplist.core.models import (
    EbayListingType,
    Item,
    ItemCondition,
    KaPriceType,
    KaShipping,
)


def _analysis(overrides: dict | None = None) -> dict:
    base = {
        "name": "Sony WH-1000XM5",
        "title_de": "Sony Kopfhörer zu verkaufen",
        "description_de": "Tolle Kopfhörer.",
        "condition": "good",
        "condition_notes": "Minor scratch",
        "keywords": ["sony", "kopfhörer"],
        "category": "Electronics",
        "brand": "Sony",
        "model": "WH-1000XM5",
        "ka_category": "Elektronik > Audio & Hifi",
        "ka_shipping": "versand",
        "ka_shipping_methods": ["DHL Paket 2 kg"],
        "ka_price_type": "festpreis",
        "confidence": 0.9,
        "confidence_notes": "Fully verified",
    }
    if overrides:
        base.update(overrides)
    return base


def test_from_analysis_basic_fields(tmp_path: Path) -> None:
    photos = [tmp_path / "a.jpg"]
    enhanced = [tmp_path / "a_enh.jpg"]
    item = Item.from_analysis(_analysis(), photos, enhanced)
    assert item.name == "Sony WH-1000XM5"
    assert item.condition == ItemCondition.GOOD
    assert item.brand == "Sony"
    assert item.model == "WH-1000XM5"
    assert item.confidence == 0.9


def test_from_analysis_photos_wired(tmp_path: Path) -> None:
    photos = [tmp_path / "orig.jpg"]
    enhanced = [tmp_path / "enh.jpg"]
    item = Item.from_analysis(_analysis(), photos, enhanced)
    assert item.photos[0].original_path == tmp_path / "orig.jpg"
    assert item.photos[0].enhanced_path == tmp_path / "enh.jpg"


def test_from_analysis_ka_options(tmp_path: Path) -> None:
    item = Item.from_analysis(_analysis(), [tmp_path / "a.jpg"], [tmp_path / "b.jpg"])
    assert item.marketplace == "kleinanzeigen"
    assert item.ka_options is not None
    assert item.ka_options.shipping == KaShipping.VERSAND
    assert item.ka_options.price_type == KaPriceType.FESTPREIS
    assert item.ka_options.shipping_methods == ["DHL Paket 2 kg"]


def test_from_analysis_ebay_options(tmp_path: Path) -> None:
    analysis = _analysis(
        {
            "ebay_listing_type": "auction",
            "ebay_duration_days": 7,
            "ebay_reserve_price": 30.0,
            "ebay_category_id": "293",
        }
    )
    for k in ("ka_category", "ka_shipping", "ka_shipping_methods", "ka_price_type"):
        analysis.pop(k, None)
    item = Item.from_analysis(analysis, [tmp_path / "a.jpg"], [], marketplace="ebay")
    assert item.marketplace == "ebay"
    assert item.ebay_options is not None
    assert item.ebay_options.listing_type == EbayListingType.AUCTION
    assert item.ebay_options.reserve_price == 30.0
    assert item.ebay_options.ebay_category_id == "293"


def test_from_analysis_invalid_condition_falls_back(tmp_path: Path) -> None:
    item = Item.from_analysis(_analysis({"condition": "not_a_condition"}), [tmp_path / "a.jpg"], [])
    assert item.condition == ItemCondition.GOOD


def test_from_analysis_invalid_shipping_falls_back(tmp_path: Path) -> None:
    item = Item.from_analysis(_analysis({"ka_shipping": "invalid"}), [tmp_path / "a.jpg"], [])
    assert item.ka_options is not None
    assert item.ka_options.shipping == KaShipping.VERSAND


def test_from_analysis_description_fallback(tmp_path: Path) -> None:
    analysis = _analysis()
    del analysis["description_de"]
    analysis["description"] = "Fallback description."
    item = Item.from_analysis(analysis, [tmp_path / "a.jpg"], [])
    assert item.description == "Fallback description."
