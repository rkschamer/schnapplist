"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from schnapplist.core.models import Item, ItemCondition, Photo, PriceInfo

_SAMPLE_ITEM_MD = """\
## Vintage Camera

### Inserat

| Field | Value |
|---|---|
| **ID** | `abc12345` |
| **Title (DE)** | Vintage Kamera |
| **Condition** | Good (Gut) |
| **Category** | Electronics |
| **Brand / Model** | Leica / M3 |
| **Suggested price** | **49.99 EUR** |
| **Marketplace** | kleinanzeigen |
| **Approved** | false |
| **KA Category** | Foto & Kameras |
| **Shipping** | versand |
| **Shipping methods** | Hermes Päckchen |
| **Price type** | festpreis |

#### Beschreibung

Eine wunderschöne Vintage-Kamera in gutem Zustand.

#### Tags

`vintage`, `kamera`, `leica`

#### Fotos

![camera.jpg](pictures/camera.jpg)

---
"""


@pytest.fixture()
def report_dir(tmp_path: Path) -> Path:
    """A minimal schnapplist run folder with one item file."""
    run_dir = tmp_path / "schnapplist-report-20260101-120000"
    run_dir.mkdir()
    (run_dir / "item-1.md").write_text(_SAMPLE_ITEM_MD, encoding="utf-8")
    return run_dir


@pytest.fixture()
def output_dir(tmp_path: Path, report_dir: Path) -> Path:
    """An output dir containing report_dir."""
    output = tmp_path / "output"
    output.mkdir()
    report_dir.rename(output / report_dir.name)
    return output


@pytest.fixture()
def sample_item(tmp_path: Path) -> Item:
    photo = Photo(original_path=tmp_path / "photo.jpg")
    return Item(
        id="abc12345",
        name="Vintage Camera",
        title_de="Vintage Kamera",
        description="Eine wunderschöne Vintage-Kamera.",
        condition=ItemCondition.GOOD,
        photos=[photo],
        price_info=PriceInfo(
            suggested_price=49.99,
            min_price=40.0,
            max_price=60.0,
            reasoning="Market average",
        ),
        approved=True,
        marketplace="kleinanzeigen",
    )
