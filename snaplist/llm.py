"""Unified LLM client supporting Anthropic and Ollama backends.

Exposes a single messages_create() method that mirrors the Anthropic SDK signature
so all callers stay provider-agnostic. For Ollama, messages and images are translated
to the OpenAI-compatible format served at /v1/chat/completions.
"""

from __future__ import annotations

import re
import types
from typing import Any

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
        self.model = model
        self._ollama_host = ollama_host.rstrip("/")
        self._anthropic: Any = None

        if provider == "anthropic":
            import anthropic
            self._anthropic = anthropic.Anthropic(api_key=api_key)

    def messages_create(
        self,
        *,
        max_tokens: int,
        messages: list[MessageDict],
        system: str | list[MessageDict] | None = None,
    ) -> _Response:
        """Create a chat completion. Returns an object with .content[0].text."""
        if self.provider == "anthropic":
            return self._anthropic_create(max_tokens=max_tokens, messages=messages, system=system)
        return self._ollama_create(max_tokens=max_tokens, messages=messages, system=system)

    # ------------------------------------------------------------------
    # Anthropic path
    # ------------------------------------------------------------------

    def _anthropic_create(
        self, *, max_tokens: int, messages: list[MessageDict], system: str | list[MessageDict] | None
    ) -> _Response:
        kwargs: dict[str, Any] = dict(model=self.model, max_tokens=max_tokens, messages=messages)
        if system is not None:
            kwargs["system"] = system
        raw = self._anthropic.messages.create(**kwargs)
        text = str(raw.content[0].text)
        return self._Response(text)

    # ------------------------------------------------------------------
    # Ollama path (OpenAI-compatible /v1/chat/completions)
    # ------------------------------------------------------------------

    def _ollama_create(
        self, *, max_tokens: int, messages: list[MessageDict], system: str | list[MessageDict] | None
    ) -> _Response:
        import requests

        oai_messages: list[MessageDict] = []

        # Translate Anthropic-style system (string or list of blocks) to a system message
        if system is not None:
            if isinstance(system, list):
                system_text = "\n\n".join(b["text"] for b in system if b.get("type") == "text")
            else:
                system_text = system
            if system_text:
                oai_messages.append({"role": "system", "content": system_text})

        # Translate user/assistant messages
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if isinstance(content, str):
                oai_messages.append({"role": role, "content": content})
            elif isinstance(content, list):
                oai_content: list[MessageDict] = []
                for block in content:
                    if block["type"] == "text":
                        oai_content.append({"type": "text", "text": block["text"]})
                    elif block["type"] == "image":
                        src = block["source"]
                        if src["type"] == "base64":
                            url = f"data:{src['media_type']};base64,{src['data']}"
                            oai_content.append({"type": "image_url", "image_url": {"url": url}})
                oai_messages.append({"role": role, "content": oai_content})

        payload = {
            "model": self.model,
            "messages": oai_messages,
            "max_tokens": max_tokens,
            "stream": False,
            "think": False,  # disable Qwen3-style chain-of-thought blocks
        }
        resp = requests.post(
            f"{self._ollama_host}/v1/chat/completions",
            json=payload,
            timeout=600,
        )
        if resp.status_code == 404:
            body = resp.json() if resp.content else {}
            detail = body.get("error", "model not found")
            raise RuntimeError(
                f"Ollama returned 404 for model '{self.model}': {detail}\n"
                f"Pull it first: curl {self._ollama_host}/api/pull -d '{{\"model\":\"{self.model}\"}}'"
            )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        return self._Response(_strip_thinking(text))


def _strip_thinking(text: str) -> str:
    """Remove <think>…</think> blocks emitted by reasoning models (e.g. Qwen3)."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
