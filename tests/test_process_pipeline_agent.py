from __future__ import annotations

from unittest.mock import MagicMock, patch

from PIL import Image

from schnapplist.workflows.process_pipeline import ProcessWorkflow


def _make_mock_client():
    return MagicMock()


def _make_mock_output(name: str = "Test Item"):
    from schnapplist.core.models import (
        ItemCondition, KleinanzeigenListingOptions, PriceInfo,
        KaShipping, KaPriceType,
    )
    from schnapplist.workflows.item_research_agent import ItemResearchOutput
    return ItemResearchOutput(
        name=name,
        brand="TestBrand",
        model="X1",
        condition=ItemCondition.GOOD,
        condition_notes="",
        title_de=f"{name} zu verkaufen",
        description_de="Ein gutes Gerät.",
        specs={"RAM": "4 GB"},
        keywords=["test"],
        category="Electronics",
        price_info=PriceInfo(
            suggested_price=50.0, min_price=40.0, max_price=60.0, reasoning="market"
        ),
        ka_options=KleinanzeigenListingOptions(
            shipping=KaShipping.VERSAND, price_type=KaPriceType.FESTPREIS
        ),
        ebay_options=None,
    )


def _make_agent_result(output=None):
    from schnapplist.workflows.item_research_agent import AgentResult
    from pydantic_ai.usage import RunUsage
    if output is None:
        output = _make_mock_output()
    return AgentResult(output=output, usage=RunUsage())


def test_pipeline_uses_agent_on_success(tmp_path):
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir()
    img = Image.new("RGB", (10, 10))
    img.save(photos_dir / "item.jpg", "JPEG")

    mock_output = _make_mock_output("Canon EOS")

    with (
        patch("schnapplist.workflows.process_pipeline.group_photos_by_item", return_value=[[photos_dir / "item.jpg"]]),
        patch("schnapplist.workflows.process_pipeline.filter_redundant_photos", return_value=[photos_dir / "item.jpg"]),
        patch("schnapplist.workflows.process_pipeline.enhance_photo", return_value=photos_dir / "item.jpg"),
        patch("schnapplist.workflows.process_pipeline.run_item_research_agent", return_value=_make_agent_result(mock_output)),
        patch("schnapplist.workflows.process_pipeline.write_item_report"),
    ):
        result = ProcessWorkflow(_make_mock_client()).run(
            photos_dir=photos_dir,
            output_dir=tmp_path / "output",
            single_item=False,
        )

    assert len(result.items) == 1
    assert result.items[0].name == "Canon EOS"


def test_pipeline_calls_decision_callback_on_agent_failure(tmp_path):
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir()
    img = Image.new("RGB", (10, 10))
    img.save(photos_dir / "item.jpg", "JPEG")

    decision_cb = MagicMock(return_value="skip")

    with (
        patch("schnapplist.workflows.process_pipeline.group_photos_by_item", return_value=[[photos_dir / "item.jpg"]]),
        patch("schnapplist.workflows.process_pipeline.filter_redundant_photos", return_value=[photos_dir / "item.jpg"]),
        patch("schnapplist.workflows.process_pipeline.enhance_photo", return_value=photos_dir / "item.jpg"),
        patch("schnapplist.workflows.process_pipeline.run_item_research_agent", side_effect=RuntimeError("LLM error")),
        patch("schnapplist.workflows.process_pipeline.write_item_report"),
    ):
        result = ProcessWorkflow(_make_mock_client(), on_decision=decision_cb).run(
            photos_dir=photos_dir,
            output_dir=tmp_path / "output",
            single_item=False,
        )

    decision_cb.assert_called_once()
    assert result.items == []


def test_pipeline_retries_then_skips(tmp_path):
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir()
    img = Image.new("RGB", (10, 10))
    img.save(photos_dir / "item.jpg", "JPEG")

    # always retry, exhausts retries, item is skipped
    decision_cb = MagicMock(return_value="retry")

    with (
        patch("schnapplist.workflows.process_pipeline.group_photos_by_item", return_value=[[photos_dir / "item.jpg"]]),
        patch("schnapplist.workflows.process_pipeline.filter_redundant_photos", return_value=[photos_dir / "item.jpg"]),
        patch("schnapplist.workflows.process_pipeline.enhance_photo", return_value=photos_dir / "item.jpg"),
        patch("schnapplist.workflows.process_pipeline.run_item_research_agent", side_effect=RuntimeError("LLM error")),
        patch("schnapplist.workflows.process_pipeline.write_item_report"),
    ):
        result = ProcessWorkflow(_make_mock_client(), on_decision=decision_cb).run(
            photos_dir=photos_dir,
            output_dir=tmp_path / "output",
            single_item=False,
        )

    # 1 initial attempt + 2 retries = 3 total calls to decision_cb
    assert decision_cb.call_count == 3
    assert result.items == []


def test_pipeline_emits_item_usage(tmp_path):
    """item_usage event is emitted live via on_usage callback during agent run."""
    from pydantic_ai.usage import RunUsage
    from schnapplist.workflows.item_research_agent import AgentResult

    photos_dir = tmp_path / "photos"
    photos_dir.mkdir()
    Image.new("RGB", (10, 10)).save(photos_dir / "item.jpg", "JPEG")

    mock_output = _make_mock_output("Test Item")
    agent_result = _make_agent_result(mock_output)

    events = []
    progress_cb = lambda event, **kwargs: events.append((event, kwargs))

    def fake_agent(photos, client, on_stage=None, on_usage=None):
        # Simulate two mid-run usage callbacks (cumulative) then return
        if on_usage is not None:
            on_usage(RunUsage(input_tokens=50, output_tokens=25, cache_read_tokens=10, requests=1, tool_calls=1))
            on_usage(RunUsage(input_tokens=100, output_tokens=50, cache_read_tokens=20, requests=3, tool_calls=2))
        return agent_result

    with (
        patch("schnapplist.workflows.process_pipeline.group_photos_by_item", return_value=[[photos_dir / "item.jpg"]]),
        patch("schnapplist.workflows.process_pipeline.filter_redundant_photos", return_value=[photos_dir / "item.jpg"]),
        patch("schnapplist.workflows.process_pipeline.enhance_photo", return_value=photos_dir / "item.jpg"),
        patch("schnapplist.workflows.process_pipeline.run_item_research_agent", side_effect=fake_agent),
        patch("schnapplist.workflows.process_pipeline.write_item_report"),
    ):
        ProcessWorkflow(MagicMock(), on_progress=progress_cb).run(
            photos_dir=photos_dir,
            output_dir=tmp_path / "output",
            single_item=False,
        )

    usage_events = [e for e in events if e[0] == "item_usage"]
    assert len(usage_events) == 2
    assert usage_events[0][1]["idx"] == 1
    # First delta: 50 in, 25 out
    assert usage_events[0][1]["output_tokens"] == 25
    assert usage_events[0][1]["input_tokens"] == 50
    # Second delta: 50 more in, 25 more out
    assert usage_events[1][1]["output_tokens"] == 25
    assert usage_events[1][1]["input_tokens"] == 50
    assert usage_events[1][1]["cache_read_tokens"] == 10


def test_pipeline_passes_on_stage_to_agent(tmp_path):
    """on_stage callback is forwarded to run_item_research_agent."""
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir()
    Image.new("RGB", (10, 10)).save(photos_dir / "item.jpg", "JPEG")

    mock_output = _make_mock_output("Test Item")
    captured_kwargs = {}

    def fake_agent(photos, client, **kwargs):
        captured_kwargs.update(kwargs)
        return _make_agent_result(mock_output)

    with (
        patch("schnapplist.workflows.process_pipeline.group_photos_by_item", return_value=[[photos_dir / "item.jpg"]]),
        patch("schnapplist.workflows.process_pipeline.filter_redundant_photos", return_value=[photos_dir / "item.jpg"]),
        patch("schnapplist.workflows.process_pipeline.enhance_photo", return_value=photos_dir / "item.jpg"),
        patch("schnapplist.workflows.process_pipeline.run_item_research_agent", side_effect=fake_agent),
        patch("schnapplist.workflows.process_pipeline.write_item_report"),
    ):
        ProcessWorkflow(MagicMock()).run(
            photos_dir=photos_dir,
            output_dir=tmp_path / "output",
            single_item=False,
        )

    assert "on_stage" in captured_kwargs
    assert callable(captured_kwargs["on_stage"])
