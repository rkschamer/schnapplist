from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from schnapplist.core.models import ItemCondition, KleinanzeigenListingOptions, PriceInfo
from schnapplist.workflows.item_research_agent import ItemResearchOutput, _analyze_photos_impl


def test_item_research_output_round_trips():
    output = ItemResearchOutput(
        name="Sony WH-1000XM5",
        brand="Sony",
        model="WH-1000XM5",
        condition=ItemCondition.GOOD,
        condition_notes="Minor scratches on headband",
        title_de="Sony WH-1000XM5 Kopfhörer",
        description_de="Hochwertige Kopfhörer mit aktiver Geräuschunterdrückung.",
        specs={"Typ": "Over-Ear", "Konnektivität": "Bluetooth 5.2"},
        keywords=["Sony", "Kopfhörer", "ANC"],
        category="Electronics",
        price_info=PriceInfo(
            suggested_price=180.0,
            min_price=150.0,
            max_price=220.0,
            reasoning="Current market",
        ),
        ka_options=KleinanzeigenListingOptions(),
        ebay_options=None,
    )
    assert output.name == "Sony WH-1000XM5"
    assert output.specs["Typ"] == "Over-Ear"
    assert output.ka_options is not None


def test_item_research_output_minimal():
    """ka_options and ebay_options can both be None."""
    output = ItemResearchOutput(
        name="Unknown",
        brand=None,
        model=None,
        condition=ItemCondition.ACCEPTABLE,
        condition_notes="",
        title_de="Unbekanntes Gerät",
        description_de="Beschreibung fehlt.",
        specs={},
        keywords=[],
        category="Other",
        price_info=PriceInfo(
            suggested_price=5.0,
            min_price=1.0,
            max_price=10.0,
            reasoning="No data",
        ),
        ka_options=None,
        ebay_options=None,
    )
    assert output.brand is None
    assert output.specs == {}


def test_analyze_photos_returns_identification(tmp_path: Path) -> None:
    # Create a minimal 1x1 JPEG
    from PIL import Image
    img = Image.new("RGB", (1, 1), color=(128, 128, 128))
    photo = tmp_path / "test.jpg"
    img.save(photo, "JPEG")

    mock_client = MagicMock()
    mock_client.messages_create.return_value = MagicMock(
        content=[MagicMock(text='{"name": "Sony WH-1000XM5", "brand": "Sony", '
                                '"model": "WH-1000XM5", "condition": "good", '
                                '"condition_notes": "light wear", '
                                '"category": "Electronics", '
                                '"keywords": ["Sony", "Headphones"]}')]
    )

    result = _analyze_photos_impl([photo], mock_client)

    assert result["name"] == "Sony WH-1000XM5"
    assert result["brand"] == "Sony"
    assert "specs" not in result
    assert "description_de" not in result
    mock_client.messages_create.assert_called_once()
