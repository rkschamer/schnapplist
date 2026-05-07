"""Process service — builds LLMClient from config/args and runs the workflow."""

from __future__ import annotations

from pathlib import Path

from ..config import CLAUDE_MODEL, LLM_PROVIDER, OLLAMA_HOST, OLLAMA_MODEL
from ..core.llm import LLMClient
from ..workflows.process_pipeline import (
    ProcessRunResult,
    ProcessWorkflow,
    ProgressCallback,
)


def run_process(
    photos_dir: Path,
    output_dir: Path,
    *,
    single_item: bool = False,
    marketplace: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    ollama_host: str | None = None,
    on_progress: ProgressCallback | None = None,
) -> ProcessRunResult:
    """Analyse photos and write a report.

    Raises:
        ValueError: if provider is "anthropic" and ANTHROPIC_API_KEY is not set.
        Any exception propagated from the workflow stages.
    """
    from ..config import ANTHROPIC_API_KEY

    provider = llm_provider or LLM_PROVIDER

    if provider == "anthropic":
        api_key = ANTHROPIC_API_KEY
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set. "
                "Add it to a .env file or export it in your shell."
            )
        model = llm_model or CLAUDE_MODEL
        client = LLMClient("anthropic", model, api_key=api_key)
    else:
        model = llm_model or OLLAMA_MODEL
        host = ollama_host or OLLAMA_HOST
        client = LLMClient("ollama", model, ollama_host=host)

    workflow = ProcessWorkflow(client, on_progress=on_progress)
    return workflow.run(
        photos_dir=photos_dir,
        output_dir=output_dir,
        single_item=single_item,
        marketplace=marketplace,
    )
