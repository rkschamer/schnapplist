"""eBay provider via the eBay Trading API (AddItem call).

Requires: EBAY_APP_ID and EBAY_AUTH_TOKEN in .env
Set EBAY_SANDBOX=true to use the sandbox environment for testing.

eBay API docs: https://developer.ebay.com/api-docs/user-guides/static/trading-user-guide-landing.html
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from ..config import EBAY_APP_ID, EBAY_AUTH_TOKEN, EBAY_SANDBOX
from ..models import Item
from .base import BaseProvider

_TRADING_API_LIVE = "https://api.ebay.com/ws/api.dll"
_TRADING_API_SANDBOX = "https://api.sandbox.ebay.com/ws/api.dll"

_CATEGORY_IDS: dict[str, str] = {
    "Electronics": "293",
    "Clothing": "11450",
    "Books": "267",
    "Toys": "220",
    "Furniture": "3197",
    "Sports": "382",
    "Kitchen": "20625",
    "Garden": "159912",
    "Other": "99",
}


class EbayProvider(BaseProvider):
    name = "ebay"

    def is_available(self) -> bool:
        return bool(EBAY_APP_ID and EBAY_AUTH_TOKEN)

    def post_listing(self, item: Item) -> str:
        """Post an eBay fixed-price listing and return the item URL."""
        if not self.is_available():
            raise RuntimeError(
                "eBay credentials missing. Set EBAY_APP_ID and EBAY_AUTH_TOKEN in .env"
            )

        endpoint = _TRADING_API_SANDBOX if EBAY_SANDBOX else _TRADING_API_LIVE

        category_id = _CATEGORY_IDS.get(item.category or "Other", "99")
        price = item.price_info.suggested_price if item.price_info else 9.99
        condition_id = item.condition.to_ebay_condition()
        title = (item.title_de or item.name)[:80]

        # Build XML payload for AddItem
        xml_body = f"""<?xml version="1.0" encoding="utf-8"?>
<AddItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials>
    <eBayAuthToken>{EBAY_AUTH_TOKEN}</eBayAuthToken>
  </RequesterCredentials>
  <Item>
    <Title>{_xml_escape(title)}</Title>
    <Description><![CDATA[{item.description}]]></Description>
    <PrimaryCategory><CategoryID>{category_id}</CategoryID></PrimaryCategory>
    <StartPrice>{price:.2f}</StartPrice>
    <CategoryMappingAllowed>true</CategoryMappingAllowed>
    <ConditionID>{condition_id}</ConditionID>
    <Country>DE</Country>
    <Currency>EUR</Currency>
    <DispatchTimeMax>3</DispatchTimeMax>
    <ListingDuration>Days_30</ListingDuration>
    <ListingType>FixedPriceItem</ListingType>
    <Quantity>1</Quantity>
    <ShippingDetails>
      <ShippingType>Flat</ShippingType>
      <ShippingServiceOptions>
        <ShippingServicePriority>1</ShippingServicePriority>
        <ShippingService>DE_DHLPackchen</ShippingService>
        <ShippingServiceCost>4.99</ShippingServiceCost>
      </ShippingServiceOptions>
    </ShippingDetails>
    <Site>Germany</Site>
    {_build_picture_xml(item)}
  </Item>
</AddItemRequest>"""

        headers = {
            "X-EBAY-API-SITEID": "77",  # Germany
            "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
            "X-EBAY-API-CALL-NAME": "AddItem",
            "X-EBAY-API-APP-NAME": EBAY_APP_ID,
            "Content-Type": "text/xml",
        }

        response = requests.post(endpoint, data=xml_body.encode("utf-8"), headers=headers, timeout=30)
        response.raise_for_status()

        root = ET.fromstring(response.text)
        ns = {"ns": "urn:ebay:apis:eBLBaseComponents"}
        ack = root.findtext("ns:Ack", namespaces=ns)
        if ack not in ("Success", "Warning"):
            errors = root.findall(".//ns:ShortMessage", namespaces=ns)
            msgs = "; ".join(e.text or "" for e in errors)
            raise RuntimeError(f"eBay API error: {msgs}")

        item_id = root.findtext("ns:ItemID", namespaces=ns)
        domain = "sandbox.ebay.de" if EBAY_SANDBOX else "ebay.de"
        return f"https://www.{domain}/itm/{item_id}"


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _build_picture_xml(item: Item) -> str:
    # eBay allows up to 12 photos via URL only; for local files we reference the path
    # (in production you'd upload to EPS first)
    lines = ["<PictureDetails>"]
    for photo in item.photos[:12]:
        p = photo.enhanced_path or photo.original_path
        lines.append(f"  <PictureURL>{p.as_uri()}</PictureURL>")
    lines.append("</PictureDetails>")
    return "\n".join(lines)
