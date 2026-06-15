from __future__ import annotations

from schnapplist.cli.display import ItemRow, RunState, ToolLogEntry


def _make_state() -> RunState:
    return RunState()


def test_runstate_defaults():
    s = _make_state()
    assert s.photo_count == 0
    assert s.total_items == 0
    assert s.input_tokens == 0
    assert s.tool_log == []


def test_apply_scan_done():
    from schnapplist.cli.display import apply_event
    s = _make_state()
    apply_event(s, "scan_done", count=7)
    assert s.photo_count == 7
    assert s.scan_done is True


def test_apply_group_done():
    from schnapplist.cli.display import apply_event
    s = _make_state()
    apply_event(s, "group_done", count=3)
    assert s.group_count == 3
    assert s.total_items == 3
    assert s.group_done is True


def test_apply_item_start():
    from schnapplist.cli.display import apply_event
    s = _make_state()
    apply_event(s, "group_done", count=3)
    apply_event(s, "item_start", idx=1, total=3)
    assert 1 in s.items
    assert s.items[1].status == "active"
    assert s.active_idx == 1


def test_apply_item_stage():
    from schnapplist.cli.display import apply_event
    s = _make_state()
    apply_event(s, "group_done", count=2)
    apply_event(s, "item_start", idx=1, total=2)
    apply_event(s, "item_stage", idx=1, stage="web_search")
    assert s.items[1].stage == "web_search"
    assert len(s.tool_log) == 1
    assert s.tool_log[0].tool == "web_search"


def test_apply_item_stage_non_tool_does_not_log():
    from schnapplist.cli.display import apply_event
    s = _make_state()
    apply_event(s, "group_done", count=1)
    apply_event(s, "item_start", idx=1, total=1)
    apply_event(s, "item_stage", idx=1, stage="enhance")
    assert len(s.tool_log) == 0


def test_apply_item_done():
    from schnapplist.cli.display import apply_event
    s = _make_state()
    apply_event(s, "group_done", count=1)
    apply_event(s, "item_start", idx=1, total=1)
    apply_event(s, "item_done", idx=1, name="Test RAM", price="5.00 EUR")
    assert s.items[1].status == "done"
    assert s.items[1].name == "Test RAM"
    assert s.completed_items == 1


def test_apply_item_usage_accumulates():
    from schnapplist.cli.display import apply_event
    s = _make_state()
    apply_event(s, "item_usage", idx=1, input_tokens=100, output_tokens=50,
                cache_read_tokens=20, requests=3, tool_calls=2)
    apply_event(s, "item_usage", idx=2, input_tokens=80, output_tokens=30,
                cache_read_tokens=10, requests=2, tool_calls=1)
    assert s.input_tokens == 180
    assert s.output_tokens == 80
    assert s.cache_tokens == 30
    assert s.requests == 5
    assert s.tool_calls == 3


def test_tool_log_capped_at_five():
    from schnapplist.cli.display import apply_event
    s = _make_state()
    apply_event(s, "group_done", count=1)
    apply_event(s, "item_start", idx=1, total=1)
    for _ in range(7):
        apply_event(s, "item_stage", idx=1, stage="web_search")
    assert len(s.tool_log) == 5


def test_render_header_returns_renderable():
    from schnapplist.cli.display import _render_header
    from rich.console import Console
    s = _make_state()
    renderable = _render_header(s)
    console = Console(force_terminal=True, width=80)
    with console.capture():
        console.print(renderable)


def test_render_items_returns_renderable():
    from schnapplist.cli.display import _render_items, apply_event
    from rich.console import Console
    s = _make_state()
    apply_event(s, "group_done", count=2)
    apply_event(s, "item_start", idx=1, total=2)
    apply_event(s, "item_done", idx=1, name="RAM", price="5.00 EUR")
    apply_event(s, "item_start", idx=2, total=2)
    renderable = _render_items(s)
    console = Console(force_terminal=True, width=80)
    with console.capture():
        console.print(renderable)


def test_render_llm_returns_renderable():
    from schnapplist.cli.display import _render_llm, apply_event
    from rich.console import Console
    s = _make_state()
    apply_event(s, "item_usage", idx=1, input_tokens=500, output_tokens=200,
                cache_read_tokens=100, requests=4, tool_calls=3)
    renderable = _render_llm(s)
    console = Console(force_terminal=True, width=40)
    with console.capture():
        console.print(renderable)


def test_toks_uses_gen_secs():
    """tok/s uses accumulated generation time, not wall-clock elapsed."""
    from schnapplist.cli.display import _render_llm, apply_event
    from rich.console import Console

    s = _make_state()
    # Simulate a usage event with explicit gen_secs
    apply_event(s, "item_usage", idx=1, input_tokens=100, output_tokens=200,
                cache_read_tokens=0, requests=2, tool_calls=1, gen_secs=5.0)
    assert s.gen_secs == 5.0

    renderable = _render_llm(s)
    console = Console(force_terminal=True, width=40)
    with console.capture() as cap:
        console.print(renderable)
    output = cap.get()
    assert "tok/s" in output
    # 200 tokens / 5.0s = 40.0 tok/s
    assert "40.0" in output
