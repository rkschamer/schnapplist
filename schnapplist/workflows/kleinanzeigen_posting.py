"""Kleinanzeigen posting via Playwright MCP browser agent."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import AsyncIterable
from pathlib import Path
from typing import cast

from platformdirs import user_cache_dir
from pydantic_ai import Agent
from pydantic_ai.agent import EventStreamHandler
from pydantic_ai.mcp import MCPServerStdio
from pydantic_ai.messages import AgentStreamEvent, FunctionToolCallEvent
from pydantic_ai.tools import RunContext
from rich.console import Console

from ..config import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    LLM_PROVIDER,
    OLLAMA_HOST,
    OLLAMA_MODEL,
)
from ..models import Item

_console = Console()


def _make_event_handler() -> EventStreamHandler[None]:
    last_ts: list[float] = [time.monotonic()]

    async def handler(
        _ctx: RunContext[None],
        events: AsyncIterable[AgentStreamEvent],
    ) -> None:
        async for event in events:
            if isinstance(event, FunctionToolCallEvent):
                now = time.monotonic()
                delta, last_ts[0] = now - last_ts[0], now
                msg = _human_readable_event(event.part.tool_name, event.part.args_as_dict())
                _console.print(f"  [dim]·[/dim] {msg}  [dim]{_fmt_delta(delta)}[/dim]")

    return handler


def _fmt_delta(seconds: float) -> str:
    if seconds < 60:
        return f"+{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"+{m}m {s}s"


def _human_readable_event(name: str, args: dict[str, object]) -> str:
    if name == "browser_navigate":
        return f"Navigating to {args.get('url', '')}"
    if name in ("browser_snapshot", "browser_screenshot"):
        return "Reading page"
    if name in ("browser_click", "browser_hover"):
        element = str(args.get("element", args.get("selector", "")))
        label = "Hovering over" if name == "browser_hover" else "Clicking"
        return f"{label} {element[:60]}" if element else label
    if name in ("browser_type", "browser_fill"):
        text = str(args.get("text", args.get("value", "")))
        short = (text[:40] + "…") if len(text) > 40 else text
        return f"Typing: {short}"
    if name == "browser_select_option":
        val = args.get("values", args.get("option", args.get("value", "")))
        return f"Selecting: {val}"
    if name == "browser_file_upload":
        paths = args.get("paths", args.get("files", []))
        count = len(paths) if isinstance(paths, list) else 1  # type: ignore[arg-type]
        return f"Uploading {count} photo(s)"
    if name in ("browser_wait", "browser_wait_for_visible"):
        return "Waiting for page…"
    if name == "browser_press_key":
        return f"Pressing key: {args.get('key', '?')}"
    return name.removeprefix("browser_").replace("_", " ").capitalize()


def build_listing_payload(item: Item) -> dict[str, str | list[str]]:
    from ..config import LISTING_DISCLAIMER

    description = item.description
    if LISTING_DISCLAIMER:
        description = f"{description}\n\n{LISTING_DISCLAIMER}"

    condition_map = {
        "new": "Neu",
        "like_new": "Wie neu",
        "good": "Gut",
        "acceptable": "Akzeptabel",
        "poor": "Beschädigt",
    }

    photo_paths = [str(p.enhanced_path or p.original_path) for p in item.photos]

    payload: dict[str, str | list[str]] = {
        "title": (item.title_de or item.name)[:60],
        "description": description,
        "condition": condition_map.get(item.condition.value, ""),
        "photo_paths": photo_paths,
    }
    if item.price_info:
        payload["price"] = str(int(item.price_info.suggested_price))
    return payload


def run_mcp_posting(item: Item, *, max_steps: int = 80) -> str:
    """Run posting through Playwright MCP, letting the model use browser tools directly."""
    model_name = _resolve_model_name()

    payload = build_listing_payload(item)
    photo_values = cast(list[str], payload.get("photo_paths", []))
    photo_paths = [str(Path(p).expanduser().resolve()) for p in photo_values]

    output_dir = Path(user_cache_dir("schnapplist")) / "playwright-mcp"
    output_dir.mkdir(parents=True, exist_ok=True)

    _console.print("[bold]Kleinanzeigen[/bold] [dim](browser agent)[/dim]")

    server = MCPServerStdio(
        "npx",
        args=[
            "-y",
            "@playwright/mcp@latest",
            "--output-dir",
            str(output_dir),
            "--save-session",
            "--allow-unrestricted-file-access",
            "--viewport-size",
            "800x600",
        ],
        timeout=60,
        read_timeout=600,
        max_retries=3,
    )

    agent = Agent(
        model_name,
        toolsets=[server],
        retries=2,
        system_prompt=(
            "You are an autonomous browser operator posting one listing on kleinanzeigen.de. "
            "Use Playwright MCP tools only. Prefer reliable, visible controls "
            "over brittle guesses. "
            "If login/CAPTCHA is required, wait and continue after the user resolves it."
        ),
    )

    prompt = (
        "Create this Kleinanzeigen listing end-to-end and submit it.\n"
        "Required category behavior:\n"
        "1. Pick one category.\n"
        "2. Pick one sub-category when it appears.\n"
        "3. Pick one sub-sub-category when it appears.\n"
        "4. Click 'Weiter' to proceed after category depth is selected.\n\n"
        "Photo upload behavior:\n"
        "1. Click the photo upload area/button to trigger the file chooser.\n"
        "2. When the file chooser opens, use browser_file_upload with the absolute paths.\n"
        "3. Upload all photos one by one or in batch.\n\n"
        f"Maximum planning/tool iterations: {max_steps}.\n"
        f"Item name: {item.name}\n"
        f"Category hint: {item.category or 'none'}\n"
        f"Title: {payload.get('title', '')}\n"
        f"Description:\n{payload.get('description', '')}\n"
        f"Price (EUR): {payload.get('price', '')}\n"
        f"Condition label: {payload.get('condition', '')}\n"
        f"Photo files to upload (absolute paths): {json.dumps(photo_paths, ensure_ascii=True)}\n\n"
        "Navigate to https://www.kleinanzeigen.de/p-anzeige-aufgeben.html and complete everything. "
        "At the end, respond with exactly: FINAL_URL: <url>"
    )

    result = agent.run_sync(prompt, event_stream_handler=_make_event_handler())
    output = str(result.output).strip()

    final_match = re.search(r"FINAL_URL:\s*(https?://\S+)", output, flags=re.I)
    if final_match:
        return final_match.group(1).rstrip(".,)")

    url_match = re.search(r"https?://\S+", output)
    if url_match:
        return url_match.group(0).rstrip(".,)")

    raise RuntimeError(f"MCP posting finished without returning a final URL. Output: {output}")


def _resolve_model_name() -> str:
    if LLM_PROVIDER == "anthropic":
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is required for anthropic MCP mode.")
        return f"anthropic:{CLAUDE_MODEL}"

    if LLM_PROVIDER == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "").strip() or OLLAMA_HOST.strip()
        base_url = base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        os.environ["OLLAMA_BASE_URL"] = base_url
        return f"ollama:{OLLAMA_MODEL}"

    raise RuntimeError(
        "Unsupported llm.provider for MCP mode. Use 'anthropic' or 'ollama'."
    )
