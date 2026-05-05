"""Kleinanzeigen posting workflow primitives and agent loop."""

from __future__ import annotations

import base64
import contextlib
import importlib
import json
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

from platformdirs import user_cache_dir
from pydantic import BaseModel, ValidationError

from ..models import Item

if TYPE_CHECKING:
    from playwright.sync_api import Page

    from ..llm import LLMClient


class WorkflowAction(BaseModel):
    action: Literal[
        "click_text",
        "click_role",
        "click_category_index",
        "click_category_text",
        "fill",
        "set_condition",
        "upload_next_photo",
        "submit",
        "press",
        "wait",
        "manual",
        "done",
    ]
    text: str | None = None
    role: str | None = None
    name: str | None = None
    field: str | None = None
    index: int | None = None
    key: str | None = None
    ms: int | None = None
    prompt: str | None = None


class PostingWorkflowState(BaseModel):
    uploaded: int = 0
    submitted: bool = False
    step: int = 0
    max_steps: int = 60
    category_signature: str = ""
    repeated_category_frames: int = 0
    category_probe_index: int = 1
    category_probe_cycles: int = 0


class _PydanticAiAgent(Protocol):
    def run_sync(self, user_prompt: str) -> object: ...


class _ActionPlanner:
    def __init__(self, client: LLMClient, *, use_pydanticai: bool) -> None:
        self._client = client
        self._pydanticai_agent: _PydanticAiAgent | None = None
        if use_pydanticai:
            self._pydanticai_agent = _make_pydanticai_agent()

    def plan(self, *, screenshot_b64: str, prompt_text: str) -> WorkflowAction | None:
        if self._pydanticai_agent is not None:
            action = _plan_with_pydanticai(
                self._pydanticai_agent,
                screenshot_b64=screenshot_b64,
                prompt_text=prompt_text,
            )
            if action is not None:
                return action

        response = self._client.messages_create(
            max_tokens=260,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": screenshot_b64,
                            },
                        },
                        {"type": "text", "text": prompt_text},
                    ],
                }
            ],
        )
        return _parse_action_model(response.content[0].text)


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


def run_agentic_posting(
    page: Page,
    item: Item,
    client: LLMClient,
    *,
    start_url: str,
    max_steps: int,
) -> bool:
    payload = build_listing_payload(item)
    planner = _make_action_planner(client)

    state = PostingWorkflowState(max_steps=max_steps)
    photo_paths = [str(p) for p in payload.get("photo_paths", [])]

    item_desc = f"Item: {item.name}"
    if item.category:
        item_desc += f"\nCategory hint: {item.category}"

    for step in range(max_steps):
        state.step = step + 1
        if _is_page_closed(page):
            return _is_posting_complete(page, start_url=start_url, submitted=state.submitted)

        dismiss_cookie_banner(page)
        if _is_posting_complete(page, start_url=start_url, submitted=state.submitted):
            return True

        has_form = False
        with contextlib.suppress(Exception):
            has_form = page.locator("#postad-title").count() > 0
        category_options = _visible_category_options(page)

        if _maybe_advance_category_stall(
            page,
            state=state,
            client=client,
            item=item,
            has_form=has_form,
            category_options=category_options,
        ):
            if not _safe_wait(page, 1_100):
                return _is_posting_complete(
                    page,
                    start_url=start_url,
                    submitted=state.submitted,
                )
            continue

        screenshot_b64: str | None = None
        with contextlib.suppress(Exception):
            screenshot_b64 = base64.b64encode(page.screenshot()).decode()
        if screenshot_b64 is None:
            return _is_posting_complete(page, start_url=start_url, submitted=state.submitted)

        prompt_text = _build_action_prompt(
            page=page,
            item_desc=item_desc,
            payload=payload,
            uploaded=state.uploaded,
            photo_total=len(photo_paths),
            has_form=has_form,
            category_options=category_options,
        )
        action_model = planner.plan(screenshot_b64=screenshot_b64, prompt_text=prompt_text)
        if action_model is None:
            if _maybe_advance_category_stall(
                page,
                state=state,
                client=client,
                item=item,
                has_form=has_form,
                category_options=category_options,
            ):
                if not _safe_wait(page, 1_100):
                    return _is_posting_complete(
                        page,
                        start_url=start_url,
                        submitted=state.submitted,
                    )
                continue

            if not _safe_wait(page, 800):
                return _is_posting_complete(page, start_url=start_url, submitted=state.submitted)
            continue

        data = action_model.model_dump(exclude_none=True)

        action = action_model.action
        if action == "done":
            return _is_posting_complete(page, start_url=start_url, submitted=state.submitted)
        if action == "wait":
            ms = _coerce_int(data.get("ms"), default=1200)
            if not _safe_wait(page, max(300, min(ms, 10_000))):
                return _is_posting_complete(page, start_url=start_url, submitted=state.submitted)
            continue
        if action == "manual":
            prompt = str(data.get("prompt", "Complete the visible manual step.")).strip()
            print(f"\n>>> Manual step required: {prompt} <<<\n")
            if not _safe_wait(page, 20_000):
                return _is_posting_complete(page, start_url=start_url, submitted=state.submitted)
            continue

        acted = _execute_agent_action(
            page,
            data,
            payload=payload,
            photo_paths=photo_paths,
            uploaded=state.uploaded,
            category_options=category_options,
        )

        if action == "upload_next_photo" and acted and state.uploaded < len(photo_paths):
            state.uploaded += 1
        if action == "submit" and acted:
            state.submitted = True

        if not acted:
            if not has_form and category_options:
                preview = ", ".join(category_options[:6])
                print(f"Agent retry: visible category options -> {preview}")

            if _maybe_advance_category_stall(
                page,
                state=state,
                client=client,
                item=item,
                has_form=has_form,
                category_options=category_options,
            ):
                if not _safe_wait(page, 1_100):
                    return _is_posting_complete(
                        page,
                        start_url=start_url,
                        submitted=state.submitted,
                    )
                continue

            if not _safe_wait(page, 900):
                return _is_posting_complete(page, start_url=start_url, submitted=state.submitted)
            continue

        if not _safe_wait(page, 1_000):
            return _is_posting_complete(page, start_url=start_url, submitted=state.submitted)

    return _is_posting_complete(page, start_url=start_url, submitted=state.submitted)


def run_mcp_posting(item: Item, *, max_steps: int) -> str:
    """Run posting through Playwright MCP, letting the model use browser tools directly."""
    from pydantic_ai import Agent
    from pydantic_ai.mcp import MCPServerStdio

    from ..config import (
        ANTHROPIC_API_KEY,
        CLAUDE_MODEL,
        LLM_PROVIDER,
        OLLAMA_HOST,
        OLLAMA_MODEL,
    )

    if LLM_PROVIDER == "anthropic":
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is required for anthropic MCP mode.")
        model_name = f"anthropic:{CLAUDE_MODEL}"
    elif LLM_PROVIDER == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "").strip() or OLLAMA_HOST.strip()
        base_url = base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        os.environ["OLLAMA_BASE_URL"] = base_url
        model_name = f"ollama:{OLLAMA_MODEL}"
    else:
        raise RuntimeError(
            "Unsupported llm.provider for MCP mode. Use 'anthropic' or 'ollama'."
        )

    payload = build_listing_payload(item)
    photo_values = cast(list[str], payload.get("photo_paths", []))
    photo_paths = [str(Path(p).expanduser().resolve()) for p in photo_values]

    output_dir = Path(user_cache_dir("schnapplist")) / "playwright-mcp"
    output_dir.mkdir(parents=True, exist_ok=True)

    server = MCPServerStdio(
        "npx",
        args=[
            "-y",
            "@playwright/mcp@latest",
            "--output-dir",
            str(output_dir),
            "--save-session",
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
        f"Maximum planning/tool iterations: {max_steps}.\n"
        f"Item name: {item.name}\n"
        f"Category hint: {item.category or 'none'}\n"
        f"Title: {payload.get('title', '')}\n"
        f"Description:\n{payload.get('description', '')}\n"
        f"Price (EUR): {payload.get('price', '')}\n"
        f"Condition label: {payload.get('condition', '')}\n"
        f"Photo files to upload: {json.dumps(photo_paths, ensure_ascii=True)}\n\n"
        "Navigate to https://www.kleinanzeigen.de/p-anzeige-aufgeben.html and complete everything. "
        "At the end, respond with exactly: FINAL_URL: <url>"
    )

    result = agent.run_sync(prompt)
    output = str(result.output).strip()

    final_match = re.search(r"FINAL_URL:\s*(https?://\S+)", output, flags=re.I)
    if final_match:
        return final_match.group(1).rstrip(".,)")

    url_match = re.search(r"https?://\S+", output)
    if url_match:
        return url_match.group(0).rstrip(".,)")

    raise RuntimeError(f"MCP posting finished without returning a final URL. Output: {output}")


def wait_for_login_completion(page: Page, *, timeout_ms: int) -> bool:
    """Poll for successful login across URL changes and SPA transitions."""
    deadline = time.monotonic() + (timeout_ms / 1000)
    login_tokens = ("einloggen", "login", "m-einloggen")
    account_hint = re.compile(r"mein\s*konto|profil|abmelden|logout", re.I)

    while time.monotonic() < deadline:
        dismiss_cookie_banner(page)
        url = page.url.lower()

        with contextlib.suppress(Exception):
            if page.locator("#postad-title").count() > 0:
                return True

        if not any(token in url for token in login_tokens):
            return True

        with contextlib.suppress(Exception):
            if page.get_by_role("link", name=account_hint).count() > 0:
                return True

        if not _safe_wait(page, 1_000):
            return False

    return False


def dismiss_cookie_banner(page: Page) -> None:
    """Click the accept-all cookie button if present; silent on failure."""
    with contextlib.suppress(Exception):
        page.get_by_role(
            "button",
            name=re.compile(r"alle.*akzeptieren|akzeptieren|accept\s*all|accept", re.I),
        ).first.click(timeout=3_000)
        _safe_wait(page, 500)


def goto_resilient(page: Page, url: str) -> None:
    """Navigate with relaxed waiting to avoid brittle SPA timing behavior."""
    page.goto(url, wait_until="commit")
    with contextlib.suppress(Exception):
        page.wait_for_load_state("domcontentloaded", timeout=8_000)
    _safe_wait(page, 1_000)


def _safe_wait(page: Page, ms: int) -> bool:
    with contextlib.suppress(Exception):
        page.wait_for_timeout(ms)
        return True
    return False


def _is_page_closed(page: Page) -> bool:
    with contextlib.suppress(Exception):
        return bool(page.is_closed())
    return True


def _maybe_advance_category_stall(
    page: Page,
    *,
    state: PostingWorkflowState,
    client: LLMClient,
    item: Item,
    has_form: bool,
    category_options: list[str],
) -> bool:
    if has_form or not category_options:
        if not has_form and _click_category_continue(page):
            print("Category flow: clicked 'Weiter' after category depth selection")
            return True

        state.category_signature = ""
        state.repeated_category_frames = 0
        state.category_probe_index = 1
        state.category_probe_cycles = 0
        return False

    signature = "|".join(category_options)
    if signature != state.category_signature:
        state.category_signature = signature
        state.repeated_category_frames = 0
        state.category_probe_index = 1
        state.category_probe_cycles = 0
        preview = ", ".join(category_options[:6])
        print(f"Category flow: visible level options -> {preview}")
    else:
        state.repeated_category_frames += 1

    # On some screens category choices remain visible after depth selection.
    # If we keep seeing the same options, try advancing explicitly.
    if state.repeated_category_frames >= 2 and _click_category_continue(page):
        print("Category flow: clicked 'Weiter' on stable category level")
        state.category_signature = ""
        state.repeated_category_frames = 0
        state.category_probe_index = 1
        state.category_probe_cycles = 0
        return True

    choice = _llm_pick_category_index(client, item=item, options=category_options)
    index = choice if choice is not None else 1
    index = max(1, min(index, len(category_options)))

    # If the same option is chosen repeatedly on the same screen, probe the next one.
    if (
        state.repeated_category_frames >= 1
        and index == state.category_probe_index
    ):
        index = (index % len(category_options)) + 1
        state.category_probe_cycles += 1

    clicked = _click_category_option(page, category_options, index)
    if not clicked:
        return False

    label = category_options[index - 1]
    print(
        "Category flow: selecting option "
        f"{index}/{len(category_options)} -> {label}"
    )

    state.category_probe_index = index

    if state.category_probe_cycles >= 2:
        print(
            "\n>>> Category selection appears stuck. "
            "Please select the correct category manually in the browser; "
            "automation will continue. <<<\n"
        )
        state.category_probe_cycles = 0
        _safe_wait(page, 20_000)

    return True


def _llm_pick_category_index(client: LLMClient, *, item: Item, options: list[str]) -> int | None:
    option_lines = "\n".join(f"{i + 1}. {opt}" for i, opt in enumerate(options))
    hints = [
        item.category or "",
        item.name,
        item.title_de or "",
        item.brand or "",
        item.model or "",
        ", ".join(item.tags),
    ]
    hints_text = "\n".join(line for line in hints if line.strip())

    response = client.messages_create(
        max_tokens=100,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Pick ONE best option from the CURRENT category level on kleinanzeigen.\n"
                            "Goal: progress category -> sub-category -> sub-sub-category.\n"
                            "If the list is broad, pick a broad parent; if already specific, pick a specific child.\n"
                            "Return ONLY JSON in this form: {\"index\": <number>}\n\n"
                            f"Item hints:\n{hints_text}\n\n"
                            f"Visible options:\n{option_lines}\n"
                        ),
                    }
                ],
            }
        ],
    )

    raw = response.content[0].text
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    with contextlib.suppress(json.JSONDecodeError, ValueError, TypeError):
        data = json.loads(m.group(0))
        if isinstance(data, dict):
            json_obj = cast(dict[str, object], data)
            raw_index = json_obj.get("index")
            if isinstance(raw_index, (int, float, str)):
                return int(raw_index)
    return None


def _click_category_continue(page: Page) -> bool:
    names = [
        re.compile(r"weiter", re.I),
        re.compile(r"fortfahren", re.I),
        re.compile(r"naechste|n[äa]chste", re.I),
    ]

    for name in names:
        with contextlib.suppress(Exception):
            page.get_by_role("button", name=name).first.click(timeout=3_000)
            return True

    with contextlib.suppress(Exception):
        page.get_by_text(re.compile(r"weiter", re.I), exact=False).first.click(timeout=3_000)
        return True

    return False


def _make_action_planner(client: LLMClient) -> _ActionPlanner:
    from ..config import WORKFLOW_ENGINE

    return _ActionPlanner(client, use_pydanticai=WORKFLOW_ENGINE == "pydanticai")


def _make_pydanticai_agent() -> _PydanticAiAgent | None:
    """Build a PydanticAI agent for typed action planning when available."""
    with contextlib.suppress(ImportError, AttributeError, TypeError, ValueError):
        module = importlib.import_module("pydantic_ai")
        agent_cls = module.Agent

        from ..config import CLAUDE_MODEL, LLM_PROVIDER

        if LLM_PROVIDER != "anthropic":
            return None

        model = f"anthropic:{CLAUDE_MODEL}"
        agent = agent_cls(
            model,
            result_type=WorkflowAction,
            system_prompt=(
                "You are a cautious browser automation planner for kleinanzeigen.de. "
                "Return exactly one valid action for the next step."
            ),
        )
        return cast(_PydanticAiAgent, agent)
    return None


def _plan_with_pydanticai(
    agent: _PydanticAiAgent,
    *,
    screenshot_b64: str,
    prompt_text: str,
) -> WorkflowAction | None:
    """Try PydanticAI planning; fall back silently to legacy planning on any issue."""
    with contextlib.suppress(Exception):
        img_hint = screenshot_b64[:2000]
        result = agent.run_sync(
            "Screenshot (base64 PNG, truncated):\n"
            f"{img_hint}\n\n"
            f"{prompt_text}"
        )
        data = getattr(result, "data", None)
        if isinstance(data, WorkflowAction):
            return data
        if isinstance(data, dict):
            return WorkflowAction.model_validate(data)
    return None


def _parse_action_model(raw: str) -> WorkflowAction | None:
    data = _parse_json_action(raw)
    if not data:
        return None
    with contextlib.suppress(ValidationError):
        return WorkflowAction.model_validate(data)
    return None


def _build_action_prompt(
    *,
    page: Page,
    item_desc: str,
    payload: dict[str, str | list[str]],
    uploaded: int,
    photo_total: int,
    has_form: bool,
    category_options: list[str],
) -> str:
    return (
        "You are controlling Playwright for kleinanzeigen.de posting.\n"
        "Goal: Create the listing fully and submit it.\n"
        f"Current URL: {page.url}\n"
        f"{item_desc}\n"
        f"Known listing values: "
        f"title='{payload.get('title', '')}', "
        f"price='{payload.get('price', '')}', "
        f"condition='{payload.get('condition', '')}'.\n"
        f"Description to use exactly:\n{payload.get('description', '')}\n\n"
        f"Photo upload progress: {uploaded}/{photo_total}.\n"
        f"Listing form visible: {has_form}.\n"
        "Visible category options with index "
        "(choose these when form is not visible):\n"
        f"{_format_indexed_options(category_options)}\n"
        "Return ONLY one JSON object with one action:\n"
        '{"action":"click_text","text":"visible text"}\n'
        '{"action":"click_role","role":"button|link","name":"visible label"}\n'
        '{"action":"click_category_index","index":1}\n'
        '{"action":"click_category_text","text":"exact option text"}\n'
        '{"action":"fill","field":"title|description|price"}\n'
        '{"action":"set_condition"}\n'
        '{"action":"upload_next_photo"}\n'
        '{"action":"submit"}\n'
        '{"action":"press","key":"Escape|Enter|Tab"}\n'
        '{"action":"wait","ms":1200}\n'
        '{"action":"manual","prompt":"what user should do"}\n'
        '{"action":"done"}\n\n'
        "Rules: use provided values only; do not invent listing text. "
        "Prefer text/role actions over css selectors. "
        "If category options stay the same after a click, choose a different "
        "category index next."
    )


def _parse_json_action(raw: str) -> dict[str, object] | None:
    m = re.search(r"\{[\s\S]*\}", raw.strip())
    if not m:
        return None
    with contextlib.suppress(json.JSONDecodeError):
        data = json.loads(m.group(0))
        if isinstance(data, dict):
            out: dict[str, object] = {}
            json_obj = cast(dict[object, object], data)
            for k, v in json_obj.items():
                out[str(k)] = v
            return out
    return None


def _coerce_int(value: object, *, default: int) -> int:
    if isinstance(value, (int, float, str)):
        with contextlib.suppress(ValueError):
            return int(value)
    return default


def _execute_agent_action(
    page: Page,
    data: dict[str, object],
    *,
    payload: dict[str, str | list[str]],
    photo_paths: list[str],
    uploaded: int,
    category_options: list[str],
) -> bool:
    action = str(data.get("action", "")).strip().lower()
    acted = False

    if action == "click_text" and data.get("text"):
        with contextlib.suppress(Exception):
            page.get_by_text(str(data["text"]), exact=False).first.click(timeout=3_500)
            acted = True

    elif action == "click_role" and data.get("role") and data.get("name"):
        role = str(data["role"]).strip().lower()
        name = re.compile(re.escape(str(data["name"])), re.I)
        if role == "button":
            with contextlib.suppress(Exception):
                page.get_by_role("button", name=name).first.click(timeout=3_500)
                acted = True
        elif role == "link":
            with contextlib.suppress(Exception):
                page.get_by_role("link", name=name).first.click(timeout=3_500)
                acted = True

    elif action == "click_category_index":
        idx = _coerce_int(data.get("index"), default=0)
        acted = _click_category_option(page, category_options, idx)

    elif action == "click_category_text" and data.get("text"):
        target = str(data["text"]).strip()
        if target:
            with contextlib.suppress(Exception):
                page.get_by_text(target, exact=True).first.click(timeout=3_500)
                acted = True
            if not acted:
                with contextlib.suppress(Exception):
                    page.get_by_text(target, exact=False).first.click(timeout=3_500)
                    acted = True

    elif action == "fill" and data.get("field"):
        field = str(data["field"]).strip().lower()
        value = str(payload.get(field, "")).strip()
        if value:
            acted = _fill_field_by_semantic_name(page, field, value)

    elif action == "set_condition":
        condition = str(payload.get("condition", "")).strip()
        if condition:
            with contextlib.suppress(Exception):
                page.get_by_label(condition).first.click(timeout=3_500)
                acted = True
            if not acted:
                with contextlib.suppress(Exception):
                    page.get_by_text(condition, exact=False).first.click(timeout=3_500)
                    acted = True

    elif action == "upload_next_photo":
        if uploaded < len(photo_paths):
            with contextlib.suppress(Exception):
                page.locator("input[type=file]").first.set_input_files(photo_paths[uploaded])
                acted = True

    elif action == "submit":
        with contextlib.suppress(Exception):
            page.locator("#postad-submit").first.click(timeout=3_500)
            acted = True
        if not acted:
            with contextlib.suppress(Exception):
                page.get_by_role(
                    "button",
                    name=re.compile(r"weiter|veroeffentlichen|anzeigen", re.I),
                ).first.click(timeout=3_500)
                acted = True

    elif action == "press" and data.get("key"):
        with contextlib.suppress(Exception):
            page.keyboard.press(str(data["key"]))
            acted = True

    return acted


def _fill_field_by_semantic_name(page: Page, field: str, value: str) -> bool:
    label_patterns = {
        "title": re.compile(r"titel", re.I),
        "description": re.compile(r"beschreibung", re.I),
        "price": re.compile(r"preis", re.I),
    }
    selector_map = {
        "title": "#postad-title",
        "description": "#postad-description",
        "price": "#postad-price",
    }

    selector = selector_map.get(field)
    if selector:
        with contextlib.suppress(Exception):
            page.locator(selector).first.fill(value, timeout=3_500)
            return True

    pattern = label_patterns.get(field)
    if pattern:
        with contextlib.suppress(Exception):
            page.get_by_label(pattern).first.fill(value, timeout=3_500)
            return True

    if field == "description":
        with contextlib.suppress(Exception):
            page.locator("textarea").first.fill(value, timeout=3_500)
            return True

    if field in {"title", "price"}:
        with contextlib.suppress(Exception):
            page.locator("input").first.fill(value, timeout=3_500)
            return True

    return False


def _visible_category_options(page: Page) -> list[str]:
    """Collect category options visible in the posting category widget."""
    with contextlib.suppress(Exception):
        options = page.evaluate(
            r"""
            () => {
                            const clickSel = ['a', 'button', '[role="button"]', '[role="option"]', 'label', 'li'].join(',');
                            const bucketSize = 140;
                            const maxOptions = 20;
                            const out = [];

              const isVisible = (el) => {
                const st = window.getComputedStyle(el);
                if (st.display === 'none' || st.visibility === 'hidden') return false;
                const r = el.getBoundingClientRect();
                if (r.width < 20 || r.height < 14) return false;
                if (r.bottom < 0 || r.top > window.innerHeight) return false;
                return true;
              };

                            const normalize = (s) => (s || '').replace(/\s+/g, ' ').trim();

                            const isLikelyCategoryRoot = (root) => {
                const txt = (root.textContent || '').toLowerCase();
                if (txt.includes('kategorie')) return true;
                const attrs = [root.id || '', root.className || ''].join(' ').toLowerCase();
                return attrs.includes('category') || attrs.includes('kategorie');
              };

                            const markerSel = 'label,h2,h3,h4,legend,strong,span,div,p';
                            const markers = Array.from(document.querySelectorAll(markerSel)).filter((el) => {
                                if (!isVisible(el)) return false;
                                const txt = normalize(el.textContent).toLowerCase();
                                if (!txt || txt.length > 80) return false;
                                return txt.includes('kategorie');
                            });

                            const rootCandidates = Array.from(new Set(markers
                                .map((el) => el.closest('section, fieldset, form, div'))
                                .filter((el) => el && isVisible(el))));

                            if (!rootCandidates.length) return out;

                            const scoreRoot = (root) => {
                                const clickables = root.querySelectorAll(clickSel).length;
                                if (!clickables) return -1;
                                let score = clickables;
                                if (isLikelyCategoryRoot(root)) score += 30;
                                return score;
                            };

                            let root = document.body;
                            let best = -1;
                            for (const candidate of rootCandidates) {
                                const s = scoreRoot(candidate);
                                if (s > best) {
                                    best = s;
                                    root = candidate;
                                }
                            }

                            const groups = new Map();
              const banned = new Set([
                'weiter', 'zurueck', 'zuruck', 'abbrechen', 'hilfe', 'impressum',
                'datenschutz', 'agb', 'mein konto', 'einloggen', 'registrieren'
              ]);

                            for (const el of Array.from(root.querySelectorAll(clickSel))) {
                                if (!isVisible(el)) continue;
                                const txt = normalize(el.textContent);
                                if (!txt) continue;
                                if (txt.length < 2 || txt.length > 64) continue;
                                const norm = txt.toLowerCase();
                                if (banned.has(norm)) continue;
                                if (!(/[A-Za-z\u00c4\u00d6\u00dc\u00e4\u00f6\u00fc\u00df]/.test(txt))) continue;
                                const r = el.getBoundingClientRect();
                                const bucket = Math.round((r.left + r.width / 2) / bucketSize);
                                if (!groups.has(bucket)) groups.set(bucket, []);
                                const arr = groups.get(bucket);
                                if (!arr.some((entry) => entry.norm === norm)) {
                                    arr.push({ text: txt, norm });
                }
              }

                            const orderedBuckets = Array.from(groups.keys()).sort((a, b) => a - b);
                            const candidateBuckets = orderedBuckets.filter((bucket) => groups.get(bucket).length >= 2);
                            const activeBucket = (candidateBuckets.length ? candidateBuckets : orderedBuckets).at(-1);
                            if (activeBucket === undefined) return out;

                            for (const entry of groups.get(activeBucket)) {
                                out.push(entry.text);
                                if (out.length >= maxOptions) break;
                            }

                            const nonCategoryHints = [
                                'ich biete',
                                'ich suche',
                                'versand',
                                'zustand',
                                'preis',
                                'bitte wählen',
                                'bitte waehlen'
                            ];
                            if (out.some((txt) => {
                                const norm = txt.toLowerCase();
                                return nonCategoryHints.some((hint) => norm.includes(hint));
                            })) {
                                return [];
                            }

              return out;
            }
            """
        )
        if isinstance(options, list):
            cleaned: list[str] = []
            for opt in cast(list[object], options):
                if isinstance(opt, str) and opt.strip():
                    cleaned.append(opt.strip())
            return cleaned
    return []


def _format_indexed_options(options: list[str]) -> str:
    if not options:
        return "none"
    return "\n".join(f"{i + 1}. {text}" for i, text in enumerate(options))


def _click_category_option(page: Page, options: list[str], index: int) -> bool:
    if index < 1 or index > len(options):
        return False
    target = options[index - 1]

    with contextlib.suppress(Exception):
        clicked = page.evaluate(
            r"""
            (targetText) => {
                            const clickSel = ['a', 'button', '[role="button"]', '[role="option"]', 'label', 'li', 'div'].join(',');
                            const actionSel = ['a', 'button', '[role="button"]', '[role="option"]', 'label'].join(',');
                            const bucketSize = 140;

                            const isVisible = (el) => {
                                const st = window.getComputedStyle(el);
                                if (st.display === 'none' || st.visibility === 'hidden') return false;
                                const r = el.getBoundingClientRect();
                                return r.width > 20 && r.height > 14 && r.bottom >= 0 && r.top <= window.innerHeight;
                            };

                            const normalize = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
                            const targetNorm = normalize(targetText);
                            if (!targetNorm) return false;

                            const isLikelyCategoryRoot = (root) => {
                                const txt = (root.textContent || '').toLowerCase();
                                if (txt.includes('kategorie')) return true;
                                const attrs = [root.id || '', root.className || ''].join(' ').toLowerCase();
                                return attrs.includes('category') || attrs.includes('kategorie');
                            };

                            const markerSel = 'label,h2,h3,h4,legend,strong,span,div,p';
                            const markers = Array.from(document.querySelectorAll(markerSel)).filter((el) => {
                                if (!isVisible(el)) return false;
                                const txt = normalize(el.textContent || '');
                                if (!txt || txt.length > 80) return false;
                                return txt.includes('kategorie');
                            });

                            const rootCandidates = Array.from(new Set(markers
                                .map((el) => el.closest('section, fieldset, form, div'))
                                .filter((el) => el && isVisible(el))));

                            if (!rootCandidates.length) return false;

                            const scoreRoot = (root) => {
                                const clickables = root.querySelectorAll(clickSel).length;
                                if (!clickables) return -1;
                                let score = clickables;
                                if (isLikelyCategoryRoot(root)) score += 30;
                                return score;
                            };

                            let root = document.body;
                            let best = -1;
                            for (const candidate of rootCandidates) {
                                const s = scoreRoot(candidate);
                                if (s > best) {
                                    best = s;
                                    root = candidate;
                                }
                            }

                            const all = Array.from(root.querySelectorAll(clickSel)).filter((el) => isVisible(el));
                            if (!all.length) return false;

                            const groups = new Map();
                            for (const el of all) {
                                const txt = normalize(el.textContent || '');
                                if (!txt) continue;
                                const r = el.getBoundingClientRect();
                                const bucket = Math.round((r.left + r.width / 2) / bucketSize);
                                if (!groups.has(bucket)) groups.set(bucket, []);
                                groups.get(bucket).push(el);
                            }

                            const orderedBuckets = Array.from(groups.keys()).sort((a, b) => a - b);
                            const candidateBuckets = orderedBuckets.filter((bucket) => groups.get(bucket).length >= 2);
                            const activeBucket = (candidateBuckets.length ? candidateBuckets : orderedBuckets).at(-1);

                            const scoped = activeBucket === undefined ? all : groups.get(activeBucket);
                            let candidates = scoped.filter((el) => {
                                if (!isVisible(el)) return false;
                                const txt = normalize(el.textContent || '');
                                if (!txt) return false;
                                if (txt === targetNorm) return true;
                                return txt.includes(targetNorm);
                            });

                            if (!candidates.length) {
                                candidates = all.filter((el) => {
                                    const txt = normalize(el.textContent || '');
                                    if (!txt) return false;
                                    if (txt === targetNorm) return true;
                                    return txt.includes(targetNorm);
                                });
                            }
                            if (!candidates.length) return false;

                            candidates.sort((a, b) => {
                                const ta = normalize(a.textContent || '');
                                const tb = normalize(b.textContent || '');
                                const sa = (ta === targetNorm ? 10 : 0) + (ta.length <= 40 ? 1 : 0);
                                const sb = (tb === targetNorm ? 10 : 0) + (tb.length <= 40 ? 1 : 0);
                                return sb - sa;
                            });

                                                        const pick = candidates[0];
                                                        const actionable = pick.matches(actionSel)
                                                            ? pick
                                                            : (pick.closest(actionSel) || pick.querySelector(actionSel) || pick);

                                                        actionable.scrollIntoView({ block: 'center', inline: 'center' });
                                                        actionable.click();
                                                        actionable.dispatchEvent(new MouseEvent('click', {
                                                            bubbles: true,
                                                            cancelable: true,
                                                            view: window,
                                                        }));
                            return true;
                        }
            """,
            target,
        )
        if bool(clicked):
            return True

    with contextlib.suppress(Exception):
        page.get_by_text(target, exact=True).first.click(timeout=3_500)
        return True
    with contextlib.suppress(Exception):
        page.get_by_text(target, exact=False).first.click(timeout=3_500)
        return True
    return False


def _is_posting_complete(page: Page, *, start_url: str, submitted: bool) -> bool:
    url: str | None = None
    with contextlib.suppress(Exception):
        url = page.url
    if url is None:
        return submitted

    if not submitted and "p-anzeige-aufgeben" in url:
        return False

    success_hints = re.compile(
        r"anzeige.*(veroeffentlicht|online|aktiv)|deine anzeige|geschafft",
        re.I,
    )
    with contextlib.suppress(Exception):
        if page.get_by_text(success_hints, exact=False).count() > 0:
            return True

    return submitted and url != start_url and "p-anzeige-aufgeben" not in url
