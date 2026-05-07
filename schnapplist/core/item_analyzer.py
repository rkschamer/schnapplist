"""Identify items and assess condition from grouped photos using Claude."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any, cast

from PIL import Image

from ..config import API_IMAGE_MAX_PX, DEFAULT_MARKETPLACE
from .llm import LLMClient
from .models import (
    EbayListingOptions,
    EbayListingType,
    Item,
    ItemCondition,
    KaPriceType,
    KaShipping,
    KleinanzeigenListingOptions,
    Photo,
)

_SYSTEM_PROMPT = """\
You are an expert at identifying second-hand items for resale on German marketplaces \
(Kleinanzeigen, eBay.de). You assess items from photos, write compelling German listings, \
and judge fair market condition.\
"""


JsonDict = dict[str, Any]


def _encode(path: Path) -> tuple[str, str]:
    img = Image.open(path).convert("RGB")
    img.thumbnail((API_IMAGE_MAX_PX, API_IMAGE_MAX_PX), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return base64.standard_b64encode(buf.getvalue()).decode(), "image/jpeg"


def analyze_item(photos: list[Path], client: LLMClient) -> JsonDict:
    """Return a dict with item metadata derived from the provided photos."""
    content: list[JsonDict] = []
    for photo in photos:
        data, media_type = _encode(photo)
        content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})

    if DEFAULT_MARKETPLACE == "kleinanzeigen":
        marketplace_fields = (
            '  "ka_category": "e.g. Elektronik > PC-Zubehör & Software",\n'
            '  "ka_shipping": "versand|abholung",\n'
            '  "ka_shipping_methods": ["Hermes Päckchen", "DHL Paket 2 kg"],\n'
            '  "ka_price_type": "festpreis|vb|zu_verschenken"\n'
        )
        marketplace_guide = (
            "KA category guide — use format 'Parent > Subcategory', pick the most specific match:\n"
            "Auto, Rad & Boot > Autos | Autoteile & Reifen | Fahrräder & Zubehör | "
            "Motorräder & Motorroller | Motorradteile & Zubehör | Nutzfahrzeuge & Anhänger | "
            "Boote & Bootszubehör | Wohnwagen & -mobile\n"
            "Elektronik > Audio & Hifi | Foto | Handy & Telefon | Haushaltsgeräte | Konsolen | "
            "Notebooks | PCs | PC-Zubehör & Software | Tablets & Reader | TV & Video | "
            "Videospiele | Wearables | Weitere Elektronik\n"
            "Haus & Garten > Badezimmer | Büro | Dekoration | Gartenzubehör & Pflanzen | "
            "Heimtextilien | Heimwerken | Küche & Esszimmer | Lampen & Licht | "
            "Schlafzimmer | Wohnzimmer\n"
            "Familie, Kind & Baby > Baby- & Kinderkleidung | Baby-Ausstattung | "
            "Babyschalen & Kindersitze | Kinderwagen & Buggys | Kinderzimmermöbel | Spielzeug\n"
            "Haustiere > Fische | Hunde | Katzen | Kleintiere | Pferde | Vögel | Zubehör\n"
            "Mode & Beauty > Beauty & Gesundheit | Damenbekleidung | Damenschuhe | "
            "Herrenbekleidung | Herrenschuhe | Taschen & Accessoires | Uhren & Schmuck\n"
            "Eintrittskarten & Tickets > Bahn & ÖPNV | Gutscheine | Konzerte | Sport | "
            "Theater & Musical\n"
            "Freizeit, Hobby & Nachbarschaft > Handarbeit, Basteln & Kunsthandwerk | "
            "Kunst & Antiquitäten | Modellbau | Sammeln | Sport & Camping | Trödel\n\n"
            "Shipping guide:\n"
            "- ka_shipping 'versand' for shippable items; 'abholung' for large/heavy items "
            "(furniture, appliances, bikes) where local pickup is the only practical option.\n"
            "- ka_shipping_methods: choose appropriate methods for the item's size/weight. "
            "Available: 'Hermes Päckchen' (small, <500 g), 'Hermes S-Paket' (medium, <2 kg), "
            "'DHL Paket 2 kg', 'DHL Paket 5 kg'. List multiple if several sizes fit.\n"
            "- ka_price_type: 'festpreis' for items with a clear market price, "
            "'vb' (Verhandlungsbasis) if you'd negotiate, 'zu_verschenken' if giving away free."
        )
    else:  # ebay
        marketplace_fields = (
            '  "ebay_listing_type": "auction|fixed|both",\n'
            '  "ebay_duration_days": 7,\n'
            '  "ebay_reserve_price": null\n'
        )
        marketplace_guide = (
            "eBay listing type guide: 'auction' for rare/collectible items or unknown value; "
            "'fixed' for items with predictable market price; 'both' (fixed + best offer) for "
            "items where you'd accept reasonable offers. Set ebay_reserve_price only for auctions "
            "where you need a minimum — use the lower end of the fair market price range."
        )

    content.append({
        "type": "text",
        "text": (
            "Analyze these photos of an item being prepared for resale on a German marketplace.\n\n"
            "Return ONLY a JSON object with these fields:\n"
            "{\n"
            '  "name": "Brand Model (English, concise)",\n'
            '  "title_de": "Verkaufstitel auf Deutsch, max 60 Zeichen",\n'
            '  "description_de": "Beschreibung auf Deutsch, 80-150 Wörter, sachlich und verkaufsfördernd",\n'
            '  "condition": "new|like_new|good|acceptable|poor",\n'
            '  "condition_notes": "visible wear/damage details",\n'
            '  "keywords": ["search keyword 1", "keyword 2"],\n'
            '  "category": "Electronics|Clothing|Books|Toys|Furniture|Sports|Kitchen|Garden|Other",\n'
            '  "brand": "brand name or null",\n'
            '  "model": "model name or null",\n'
            + marketplace_fields +
            "}\n\n"
            "Condition guide: new=unused/sealed, like_new=barely used/no visible wear, "
            "good=light wear/fully functional, acceptable=noticeable wear/functional, "
            "poor=heavy wear/defects.\n\n"
            + marketplace_guide
        ),
    })

    response = client.messages_create(
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": content}],
    )

    text = response.content[0].text
    start, end = text.find("{"), text.rfind("}") + 1
    return cast(JsonDict, json.loads(text[start:end]))


def is_low_confidence(analysis: JsonDict) -> bool:
    """Return True if the LLM could not reliably identify the item from photos."""
    name = str(analysis.get("name", "")).strip().lower()
    return not name or "unknown" in name


def build_item(analysis: JsonDict, photos: list[Path], enhanced_paths: list[Path]) -> Item:
    """Construct an Item from raw analysis dict and photo paths."""
    photo_models = [
        Photo(original_path=orig, enhanced_path=enh)
        for orig, enh in zip(photos, enhanced_paths, strict=False)
    ]

    condition_raw = analysis.get("condition", "good")
    try:
        condition = ItemCondition(condition_raw)
    except ValueError:
        condition = ItemCondition.GOOD

    # Build marketplace-specific options from LLM suggestions
    ebay_opts: EbayListingOptions | None = None
    ka_opts: KleinanzeigenListingOptions | None = None
    marketplace = DEFAULT_MARKETPLACE
    if marketplace == "ebay":
        lt_raw = analysis.get("ebay_listing_type", "fixed")
        try:
            listing_type = EbayListingType(lt_raw)
        except ValueError:
            listing_type = EbayListingType.FIXED
        duration = int(analysis.get("ebay_duration_days") or 7)
        if duration not in (1, 3, 5, 7, 10):
            duration = 7
        reserve_raw = analysis.get("ebay_reserve_price")
        reserve = float(reserve_raw) if reserve_raw else None
        ebay_opts = EbayListingOptions(
            listing_type=listing_type,
            duration_days=duration,
            reserve_price=reserve,
        )
    else:  # kleinanzeigen
        try:
            shipping = KaShipping(analysis.get("ka_shipping", "versand"))
        except ValueError:
            shipping = KaShipping.VERSAND
        try:
            price_type = KaPriceType(analysis.get("ka_price_type", "festpreis"))
        except ValueError:
            price_type = KaPriceType.FESTPREIS
        methods_raw = analysis.get("ka_shipping_methods", [])
        methods: list[str] = (
            [str(m) for m in cast(list[Any], methods_raw)]
            if isinstance(methods_raw, list)
            else []
        )
        ka_opts = KleinanzeigenListingOptions(
            ka_category=analysis.get("ka_category") or None,
            shipping=shipping,
            shipping_methods=methods,
            price_type=price_type,
        )

    return Item(
        name=analysis.get("name", "Unknown Item"),
        title_de=analysis.get("title_de", ""),
        description=str(analysis.get("description_de", analysis.get("description", "")) or ""),
        condition=condition,
        photos=photo_models,
        tags=analysis.get("keywords", []),
        category=analysis.get("category"),
        brand=analysis.get("brand"),
        model=analysis.get("model"),
        marketplace=marketplace,
        ebay_options=ebay_opts,
        ka_options=ka_opts,
    )
