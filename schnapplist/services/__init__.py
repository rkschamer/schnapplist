"""Application service layer — wires config, agents, and core I/O for CLI and web UI.

One module per user-facing operation:
- process_service.py  →  schnapplist process  (photo → report pipeline + ProcessWorkflow)
- posting_service.py  →  schnapplist post     (load items from report, call providers)
- item_service.py     →  schnapplist list     (read items from latest run)

Services are the only layer that reads config constants directly.
CLI and web UI call services; they do not call agents or core modules directly.
"""
