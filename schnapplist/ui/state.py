"""Per-session state for the NiceGUI UI."""

from __future__ import annotations

import dataclasses
import tempfile
from pathlib import Path

from ..core.models import Item
from ..services.process_service import ProcessRunResult


@dataclasses.dataclass
class ProcessingEvent:
    event: str
    kwargs: dict


@dataclasses.dataclass
class SessionState:
    upload_dir: Path = dataclasses.field(
        default_factory=lambda: Path(tempfile.mkdtemp(prefix="schnapplist-upload-"))
    )
    output_dir: Path = dataclasses.field(default_factory=lambda: Path("./output"))
    llm_provider: str = "anthropic"
    llm_model: str = ""
    single_item: bool = False
    processing: bool = False
    processing_done: bool = False
    processing_error: str = ""
    progress_events: list[ProcessingEvent] = dataclasses.field(default_factory=list)
    result: ProcessRunResult | None = None
    items: list[Item] = dataclasses.field(default_factory=list)

    def reset_processing(self) -> None:
        self.processing = False
        self.processing_done = False
        self.processing_error = ""
        self.progress_events = []
        self.result = None
        self.items = []
