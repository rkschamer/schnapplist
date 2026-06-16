"""Pure domain layer — models, LLM client, and stateless utilities.

What belongs here:
- Data models (pydantic BaseModel subclasses)
- The LLM client adapter (llm.py)
- Photo loading, grouping, and enhancement (photo_processor.py)
- Report file I/O: generating Markdown (report_generator.py) and parsing it back (report_parser.py)
- The DuckDuckGo search helper (web_search.py)

What does NOT belong here:
- Agent definitions (→ agents/)
- Orchestration or pipeline logic (→ services/)
- Marketplace-specific code (→ providers/)
- Config reads beyond importing constants from config.py
"""
