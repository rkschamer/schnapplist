"""Configuration loaded from environment / .env file."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

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

CLAUDE_MODEL = "claude-sonnet-4-6"
