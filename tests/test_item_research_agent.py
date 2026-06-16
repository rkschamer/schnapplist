from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from schnapplist.core.models import ItemCondition, KleinanzeigenListingOptions, PriceInfo
from schnapplist.agents.item_research_agent import ItemResearchOutput, _analyze_photos_impl, run_item_research_agent


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

    from tests.test_process_pipeline_agent import _make_mock_output
    mock_output = _make_mock_output("Canon EOS 400D")
    mock_client = MagicMock()

    with patch("schnapplist.agents.item_research_agent._build_agent") as mock_build:
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _fake_iter(*args, **kwargs):
            class _FakeRun:
                class result:
                    output = mock_output
                    @staticmethod
                    def usage():
                        from pydantic_ai.usage import RunUsage
                        return RunUsage()

                def __aiter__(self):
                    return self

                async def __anext__(self):
                    raise StopAsyncIteration

            yield _FakeRun()

        mock_agent = MagicMock()
        mock_agent.iter = _fake_iter
        mock_build.return_value = mock_agent

        result = run_item_research_agent([photo], mock_client)

    assert result.output.name == "Canon EOS 400D"


def test_on_stage_fires_when_tools_called(tmp_path):
    """on_stage is called with the correct tool name when each tool executes."""
    from schnapplist.agents.item_research_agent import _build_agent, _AgentDeps

    on_stage = MagicMock()
    mock_client = MagicMock()

    img_path = tmp_path / "item.jpg"
    Image.new("RGB", (10, 10)).save(img_path, "JPEG")

    with patch("schnapplist.agents.item_research_agent._resolve_model_name", return_value="test"):
        agent = _build_agent(on_stage=on_stage)

    deps = _AgentDeps(photos=[img_path], client=mock_client)
    ctx = MagicMock()
    ctx.deps = deps

    # _function_toolset is a private pydantic-ai internal; may break on upgrades
    analyze_fn = agent._function_toolset.tools["analyze_photos"].function
    web_search_fn = agent._function_toolset.tools["web_search"].function

    with patch("schnapplist.agents.item_research_agent._analyze_photos_impl", return_value={"name": "X"}):
        analyze_fn(ctx)

    with patch("schnapplist.agents.item_research_agent._ddg_search", return_value=[]):
        web_search_fn(ctx, query="test query", max_results=5)

    assert on_stage.call_count == 2
    on_stage.assert_any_call("analyze_photos")
    on_stage.assert_any_call("web_search")


def test_item_research_output_has_confidence_fields():
    from schnapplist.core.models import ItemCondition, PriceInfo

    output = ItemResearchOutput(
        name="Test",
        brand=None,
        model=None,
        condition=ItemCondition.GOOD,
        condition_notes="",
        title_de="Test",
        description_de="Test.",
        specs={},
        keywords=[],
        category="Other",
        price_info=PriceInfo(suggested_price=1.0, min_price=1.0, max_price=1.0, reasoning=""),
        ka_options=None,
        ebay_options=None,
        confidence=0.6,
        confidence_notes="Model uncertain",
    )
    assert output.confidence == 0.6
    assert output.confidence_notes == "Model uncertain"
