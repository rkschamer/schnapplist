from __future__ import annotations

from schnapplist.core.models import EbayListingOptions


def test_ebay_listing_options_has_category_id_field():
    opts = EbayListingOptions(ebay_category_id="12345")
    assert opts.ebay_category_id == "12345"


def test_ebay_listing_options_category_id_defaults_none():
    opts = EbayListingOptions()
    assert opts.ebay_category_id is None
