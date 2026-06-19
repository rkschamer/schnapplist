"""eBay marketplace via the eBay Trading API (AddItem call).

Requires: EBAY_APP_ID, EBAY_DEV_ID, EBAY_CERT_ID, EBAY_AUTH_TOKEN in .env
Set EBAY_SANDBOX=true to use the sandbox environment for testing.

eBay API docs: https://developer.ebay.com/api-docs/user-guides/static/trading-user-guide-landing.html
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
from rich.console import Console

from ..config import (
    EBAY_APP_ID,
    EBAY_AUTH_TOKEN,
    EBAY_CERT_ID,
    EBAY_DEV_ID,
    EBAY_SANDBOX,
    LISTING_DISCLAIMER,
)
from ..core.models import EbayListingOptions, EbayListingType, Item
from .base import BaseMarketplace

_TRADING_API_LIVE = "https://api.ebay.com/ws/api.dll"
_TRADING_API_SANDBOX = "https://api.sandbox.ebay.com/ws/api.dll"

_console = Console()

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

_VALID_DURATIONS = {1, 3, 5, 7, 10}


class EbayMarketplace(BaseMarketplace):
    name = "ebay"

    def is_available(self) -> bool:
        return bool(EBAY_APP_ID and EBAY_DEV_ID and EBAY_CERT_ID and EBAY_AUTH_TOKEN)

    def post_listing(self, item: Item, options: EbayListingOptions | None = None) -> str:
        """Post an eBay listing and return the item URL."""
        if not self.is_available():
            raise RuntimeError(
                "eBay credentials missing. "
                "Set EBAY_APP_ID, EBAY_DEV_ID, EBAY_CERT_ID, and EBAY_AUTH_TOKEN in .env"
            )

        opts = options or EbayListingOptions()
        endpoint = _TRADING_API_SANDBOX if EBAY_SANDBOX else _TRADING_API_LIVE

        category_id = _CATEGORY_IDS.get(item.category or "Other", "99")
        base_price = item.price_info.suggested_price if item.price_info else 9.99
        condition_id = item.condition.to_ebay_condition()
        title = (item.title_de or item.name)[:80]
        duration = opts.duration_days if opts.duration_days in _VALID_DURATIONS else 7
        description = item.description
        if LISTING_DISCLAIMER:
            description = f"{description}\n\n{LISTING_DISCLAIMER}"

        listing_type_xml, extra_xml = _build_listing_type_xml(opts, base_price)
        schedule_xml = _build_schedule_xml(opts)

        _console.print(
            f"[bold]eBay Trading API[/bold] [dim]({'sandbox' if EBAY_SANDBOX else 'live'})[/dim]"
        )
        picture_urls = _upload_photos(item, endpoint)

        xml_body = f"""<?xml version="1.0" encoding="utf-8"?>
<AddItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials>
    <eBayAuthToken>{EBAY_AUTH_TOKEN}</eBayAuthToken>
  </RequesterCredentials>
  <Item>
    <Title>{_xml_escape(title)}</Title>
    <Description><![CDATA[{description}]]></Description>
    <PrimaryCategory><CategoryID>{category_id}</CategoryID></PrimaryCategory>
    <StartPrice>{base_price:.2f}</StartPrice>
    <CategoryMappingAllowed>true</CategoryMappingAllowed>
    <ConditionID>{condition_id}</ConditionID>
    <Country>DE</Country>
    <Currency>EUR</Currency>
    <DispatchTimeMax>3</DispatchTimeMax>
    <ListingDuration>Days_{duration}</ListingDuration>
    {listing_type_xml}
    {extra_xml}
    {schedule_xml}
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
    {_build_picture_xml(picture_urls)}
  </Item>
</AddItemRequest>"""

        headers = _api_headers("AddItem")
        _console.print("  [dim]·[/dim] Calling AddItem…")
        response = requests.post(
            endpoint, data=xml_body.encode("utf-8"), headers=headers, timeout=30
        )
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


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def _api_headers(call_name: str) -> dict[str, str]:
    return {
        "X-EBAY-API-SITEID": "77",  # Germany
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME": call_name,
        "X-EBAY-API-APP-NAME": EBAY_APP_ID,
        "X-EBAY-API-DEV-NAME": EBAY_DEV_ID,
        "X-EBAY-API-CERT-NAME": EBAY_CERT_ID,
        "Content-Type": "text/xml",
    }


# ---------------------------------------------------------------------------
# Photo upload via eBay Picture Services (EPS)
# ---------------------------------------------------------------------------


def _upload_photos(item: Item, endpoint: str) -> list[str]:
    """Upload item photos to eBay EPS and return their hosted URLs."""
    urls: list[str] = []
    for photo in item.photos[:12]:
        path = photo.enhanced_path or photo.original_path
        url = _upload_single_photo(path, endpoint)
        if url:
            urls.append(url)
            _console.print(f"  [dim]·[/dim] Uploaded photo: [link={url}]{path.name}[/link]")
    return urls


def _upload_single_photo(path: Path, endpoint: str) -> str | None:
    """Upload one photo to EPS; return the hosted HTTPS URL or None on failure."""
    picture_name = f"schnapplist_{uuid.uuid4().hex[:8]}"
    xml_part = f"""<?xml version="1.0" encoding="utf-8"?>
<UploadSiteHostedPicturesRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials>
    <eBayAuthToken>{EBAY_AUTH_TOKEN}</eBayAuthToken>
  </RequesterCredentials>
  <PictureName>{picture_name}</PictureName>
  <PictureSet>Standard</PictureSet>
</UploadSiteHostedPicturesRequest>"""

    boundary = f"MIMEBoundary_{uuid.uuid4().hex}"
    suffix = path.suffix.lower()
    mime_type = "image/jpeg" if suffix in (".jpg", ".jpeg") else f"image/{suffix.lstrip('.')}"

    with open(path, "rb") as f:
        image_data = f.read()

    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="XML Payload"\r\n'
            "Content-Type: text/xml;charset=utf-8\r\n\r\n"
            f"{xml_part}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="{path.name}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode()
        + image_data
        + f"\r\n--{boundary}--\r\n".encode()
    )

    headers = _api_headers("UploadSiteHostedPictures")
    headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"

    try:
        resp = requests.post(endpoint, data=body, headers=headers, timeout=60)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        ns = {"ns": "urn:ebay:apis:eBLBaseComponents"}
        ack = root.findtext("ns:Ack", namespaces=ns)
        if ack not in ("Success", "Warning"):
            errors = root.findall(".//ns:ShortMessage", namespaces=ns)
            msgs = "; ".join(e.text or "" for e in errors)
            _console.print(f"  [yellow]Warning:[/yellow] EPS upload failed for {path.name}: {msgs}")
            return None
        return root.findtext(".//ns:FullURL", namespaces=ns)
    except Exception as exc:
        _console.print(f"  [yellow]Warning:[/yellow] EPS upload error for {path.name}: {exc}")
        return None


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------


def _build_listing_type_xml(opts: EbayListingOptions, price: float) -> tuple[str, str]:
    """Return (ListingType element, extra elements) for the given options."""
    if opts.listing_type == EbayListingType.AUCTION:
        reserve = (
            f"<ReservePrice>{opts.reserve_price:.2f}</ReservePrice>" if opts.reserve_price else ""
        )
        return "<ListingType>Chinese</ListingType>", reserve
    if opts.listing_type == EbayListingType.BOTH:
        return (
            "<ListingType>FixedPriceItem</ListingType>",
            "<BestOfferDetails><BestOfferEnabled>true</BestOfferEnabled></BestOfferDetails>",
        )
    # Default: FIXED
    return "<ListingType>FixedPriceItem</ListingType>", ""


def _build_schedule_xml(opts: EbayListingOptions) -> str:
    if opts.scheduled_start is None:
        return ""
    return f"<ScheduleTime>{opts.scheduled_start.isoformat()}</ScheduleTime>"


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _build_picture_xml(picture_urls: list[str]) -> str:
    if not picture_urls:
        return ""
    lines = ["<PictureDetails>"]
    for url in picture_urls:
        lines.append(f"  <PictureURL>{url}</PictureURL>")
    lines.append("</PictureDetails>")
    return "\n".join(lines)
