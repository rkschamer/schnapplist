"""Logfire-based agent tracing, activated only when SCHNAPPLIST_DEBUG=1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import IO, TextIO

import logfire
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
from opentelemetry.trace import Context, Span

_CONTENT_ATTRS = (
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.result",
)


class ContentSpanProcessor(SpanProcessor):
    """Writes prompt/response content to *out* when each instrumented span ends."""

    def __init__(self, out: IO[str]) -> None:
        self._out = out

    def on_end(self, span: ReadableSpan) -> None:
        attrs = span.attributes or {}
        lines = [f"=== {span.name} ==="]
        for key in _CONTENT_ATTRS:
            if key in attrs:
                lines.append(f"[{key}]")
                try:
                    parsed = json.loads(attrs[key])  # type: ignore[arg-type]
                    lines.append(json.dumps(parsed, indent=2, ensure_ascii=False))
                except (ValueError, TypeError):
                    lines.append(str(attrs[key]))
        if len(lines) > 1:
            self._out.write("\n".join(lines) + "\n\n")
            self._out.flush()

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def configure_agent_tracing(log_path: Path) -> None:
    """Configure logfire to write agent traces (timeline + content) to *log_path*."""
    out: TextIO = open(log_path, "a", buffering=1, encoding="utf-8")  # noqa: SIM115
    logfire.configure(
        send_to_logfire=False,
        console=logfire.ConsoleOptions(output=out, colors="never"),
        inspect_arguments=False,
        additional_span_processors=[ContentSpanProcessor(out)],
    )
