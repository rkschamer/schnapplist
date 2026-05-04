"""Provider registry."""

from .ebay import EbayProvider
from .kleinanzeigen import KleinanzeigenProvider

PROVIDERS: dict = {
    "kleinanzeigen": KleinanzeigenProvider(),
    "ebay": EbayProvider(),
}
