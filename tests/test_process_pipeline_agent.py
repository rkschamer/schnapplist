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
        patch("schnapplist.workflows.process_pipeline.run_item_research_agent", return_value=mock_output),
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
