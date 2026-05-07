"""Unified LLM client powered by LiteLLM.

Exposes a single messages_create() method that mirrors the Anthropic SDK signature
so all callers stay provider-agnostic.
"""

from __future__ import annotations

import re
import types
from typing import Any

from litellm import completion

MessageDict = dict[str, Any]


class LLMClient:
    """Thin adapter over Anthropic and Ollama.

    Construction picks the backend; callers call messages_create() uniformly.
    """

    class _Response:
        """Normalized response with Anthropic-style .content[0].text interface."""

        def __init__(self, text: str) -> None:
            self.content = [types.SimpleNamespace(text=text)]

    def __init__(
        self,
        provider: str,
        model: str,
        *,
        api_key: str = "",
        ollama_host: str = "http://localhost:11434",
    ) -> None:
        if provider not in ("anthropic", "ollama"):
            raise ValueError(f"Unknown LLM provider {provider!r}. Choose 'anthropic' or 'ollama'.")
        self.provider = provider
        self.model = _normalize_model_name(provider=provider, model=model)
        self._ollama_host = ollama_host.rstrip("/")
        self._api_key = api_key

    def messages_create(
        self,
        *,
        max_tokens: int,
        messages: list[MessageDict],
        system: str | list[MessageDict] | None = None,
    ) -> _Response:
        """Create a chat completion. Returns an object with .content[0].text."""
        oai_messages = _to_openai_messages(messages=messages, system=system)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": oai_messages,
            "max_tokens": max_tokens,
        }
        if self.provider == "ollama":
            kwargs["api_base"] = self._ollama_host
            kwargs["think"] = False
        elif self._api_key:
            kwargs["api_key"] = self._api_key

        try:
            response = completion(**kwargs)
            text = _extract_text_from_response(response)
            return self._Response(_strip_thinking(text))
        except Exception as exc:
            if self.provider == "ollama":
                raise RuntimeError(
                    f"Ollama request failed for model '{self.model}' at '{self._ollama_host}'. "
                    f"Original error: {exc}"
                ) from exc
            raise


def _normalize_model_name(provider: str, model: str) -> str:
    if provider == "anthropic" and not model.startswith("anthropic/"):
        return f"anthropic/{model}"
    if provider == "ollama" and not (model.startswith("ollama/") or model.startswith("ollama_chat/")):
        return f"ollama_chat/{model}"
    return model


def _to_openai_messages(
    *, messages: list[MessageDict], system: str | list[MessageDict] | None
) -> list[MessageDict]:
    oai_messages: list[MessageDict] = []

    if system is not None:
        if isinstance(system, list):
            system_text = "\n\n".join(str(block.get("text", "")) for block in system if block.get("type") == "text")
        else:
            system_text = system
        if system_text:
            oai_messages.append({"role": "system", "content": system_text})

    for msg in messages:
        role = str(msg.get("role", "user"))
        content = msg.get("content", "")

        if isinstance(content, str):
            oai_messages.append({"role": role, "content": content})
            continue

        if not isinstance(content, list):
            oai_messages.append({"role": role, "content": str(content)})
            continue

        oai_content: list[MessageDict] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                oai_content.append({"type": "text", "text": str(block.get("text", ""))})
            elif block_type == "image":
                source = block.get("source", {})
                if isinstance(source, dict) and source.get("type") == "base64":
                    media_type = str(source.get("media_type", "image/jpeg"))
                    data = str(source.get("data", ""))
                    oai_content.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{data}"}})
        oai_messages.append({"role": role, "content": oai_content})

    return oai_messages


def _extract_text_from_response(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except Exception as exc:
        raise RuntimeError(f"Unexpected response shape from LiteLLM: {response}") from exc

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" or isinstance(item, dict) and "text" in item:
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(p for p in parts if p)
    return str(content)


def _strip_thinking(text: str) -> str:
    """Remove <think>…</think> blocks emitted by reasoning models (e.g. Qwen3)."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
