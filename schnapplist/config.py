"""Configuration loaded from environment / .env file and schnapplist.toml."""

import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# TOML config (schnapplist.toml next to pyproject.toml)
# ---------------------------------------------------------------------------

_TOML_PATH = Path(__file__).parent.parent / "schnapplist.toml"

def _load_toml() -> dict:
    if _TOML_PATH.exists():
        with open(_TOML_PATH, "rb") as f:
            return tomllib.load(f)
    return {}

_toml = _load_toml()
_listing = _toml.get("listing", {})
_llm = _toml.get("llm", {})

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

EBAY_APP_ID: str = os.getenv("EBAY_APP_ID", "")
EBAY_AUTH_TOKEN: str = os.getenv("EBAY_AUTH_TOKEN", "")
EBAY_SANDBOX: bool = os.getenv("EBAY_SANDBOX", "false").lower() == "true"

KLEINANZEIGEN_EMAIL: str = os.getenv("KLEINANZEIGEN_EMAIL", "")
KLEINANZEIGEN_PASSWORD: str = os.getenv("KLEINANZEIGEN_PASSWORD", "")

# Max pixel dimensions when encoding photos for the Claude API (keeps token cost low)
API_IMAGE_MAX_PX = 800

# Max pixel width for saved enhanced photos
ENHANCED_IMAGE_MAX_WIDTH = 1200

PHOTO_QUALITY = 90

# Max photos per Claude grouping request (to stay within context limits)
GROUP_BATCH_SIZE = 10

LLM_PROVIDER: str = _llm.get("provider", "anthropic")
CLAUDE_MODEL = _llm.get("model") or os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

OLLAMA_HOST: str = _llm.get("ollama_host") or os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL: str = _llm.get("model") or os.getenv("OLLAMA_MODEL", "qwen3:14b")

# ---------------------------------------------------------------------------
# Listing settings (from schnapplist.toml)
# ---------------------------------------------------------------------------

# Disclaimer text appended to every listing description at posting time.
LISTING_DISCLAIMER: str = _listing.get("disclaimer", "").strip()

# Default marketplace when the LLM does not suggest one.
DEFAULT_MARKETPLACE: str = _listing.get("default_marketplace", "kleinanzeigen")
