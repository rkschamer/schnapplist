"""Configuration loaded from environment / .env file and schnapplist.toml."""

import os
import tomllib
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from platformdirs import user_config_dir

load_dotenv()

# ---------------------------------------------------------------------------
# TOML config — searched in order, first found wins:
#   1. ./schnapplist.toml        (repo root / project-local / dev mode)
#   2. <user config>/schnapplist/config.toml  (installed mode, platform-specific)
# ---------------------------------------------------------------------------

TOML_USER_PATH = Path(user_config_dir("schnapplist")) / "config.toml"

def _find_toml() -> Path | None:
    local = Path.cwd() / "schnapplist.toml"
    if local.exists():
        return local
    if TOML_USER_PATH.exists():
        return TOML_USER_PATH
    return None

def _load_toml() -> dict[str, Any]:
    path = _find_toml()
    if path is None:
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)

_toml = _load_toml()
_listing = _toml.get("listing", {})
_llm = _toml.get("llm", {})
_ebay = _toml.get("ebay", {})

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

EBAY_APP_ID: str = os.getenv("EBAY_APP_ID", "")
EBAY_DEV_ID: str = os.getenv("EBAY_DEV_ID", "")
EBAY_CERT_ID: str = os.getenv("EBAY_CERT_ID", "")
EBAY_AUTH_TOKEN: str = os.getenv("EBAY_AUTH_TOKEN", "")
EBAY_SANDBOX: bool = os.getenv("EBAY_SANDBOX", "false").lower() == "true"

# Max pixel dimensions when encoding photos for the Claude API (keeps token cost low)
API_IMAGE_MAX_PX = 800

# Max pixel width for saved enhanced photos
ENHANCED_IMAGE_MAX_WIDTH = 1200

PHOTO_QUALITY = 90

# Max photos per Claude grouping request (to stay within context limits)
GROUP_BATCH_SIZE = 5

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

# ---------------------------------------------------------------------------
# eBay settings (from schnapplist.toml)
# ---------------------------------------------------------------------------

# Header string written into the generated eBay draft CSV.
EBAY_CSV_ACTION_HEADER: str = _ebay.get(
    "csv_action_header",
    "Action(SiteID=Germany|Country=DE|Currency=EUR|Version=1193|CC=UTF-8)",
).strip()
