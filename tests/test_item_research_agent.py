from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from schnapplist.core.models import ItemCondition, KleinanzeigenListingOptions, PriceInfo
from schnapplist.workflows.item_research_agent import ItemResearchOutput, _analyze_photos_impl, run_item_research_agent


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


def test_run_item_research_agent_returns_output(tmp_path):
    img = Image.new("RGB", (1, 1))
    photo = tmp_path / "item.jpg"
    img.save(photo, "JPEG")

    mock_output = MagicMock()
    mock_output.output = MagicMock(spec=["name", "brand", "model", "condition",
        "condition_notes", "title_de", "description_de", "specs", "keywords",
        "category", "price_info", "ka_options", "ebay_options"])
    mock_output.output.name = "Canon EOS 400D"

    mock_client = MagicMock()

    with patch(
        "schnapplist.workflows.item_research_agent._build_agent"
    ) as mock_build:
        mock_agent = MagicMock()
        mock_agent.run_sync.return_value = mock_output
        mock_build.return_value = mock_agent

        result = run_item_research_agent([photo], mock_client)

    assert result.name == "Canon EOS 400D"
    mock_agent.run_sync.assert_called_once()


def test_on_stage_fires_when_tools_called(tmp_path):
    """on_stage is called with the correct tool name when each tool executes."""
    from unittest.mock import MagicMock, patch
    from schnapplist.workflows.item_research_agent import _build_agent, _AgentDeps

    on_stage = MagicMock()
    mock_client = MagicMock()

    img_path = tmp_path / "item.jpg"
    from PIL import Image
    Image.new("RGB", (10, 10)).save(img_path, "JPEG")

    with patch("schnapplist.workflows.item_research_agent._resolve_model_name", return_value="test"):
        agent = _build_agent(on_stage=on_stage)

    deps = _AgentDeps(photos=[img_path], client=mock_client)
    ctx = MagicMock()
    ctx.deps = deps

    analyze_fn = agent._function_toolset.tools["analyze_photos"].function
    web_search_fn = agent._function_toolset.tools["web_search"].function

    with patch("schnapplist.workflows.item_research_agent._analyze_photos_impl", return_value={"name": "X"}):
        analyze_fn(ctx)

    with patch("schnapplist.workflows.item_research_agent._ddg_search", return_value=[]):
        web_search_fn(ctx, query="test query", max_results=5)

    assert on_stage.call_count == 2
    on_stage.assert_any_call("analyze_photos")
    on_stage.assert_any_call("web_search")
