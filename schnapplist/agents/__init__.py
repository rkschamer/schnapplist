"""Pydantic-AI agent definitions.

Each module owns one agent: its system prompt, tools, output type, and run_* entry point.
Agents are stateless — dependencies (photos, LLM client) are injected via deps_type.
"""

from .item_research_agent import AgentResult, ItemResearchOutput, run_item_research_agent

__all__ = [
    "AgentResult",
    "ItemResearchOutput",
    "run_item_research_agent",
]
