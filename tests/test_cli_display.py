from __future__ import annotations

from schnapplist.ui.cli.display import ItemRow, RunState, ToolLogEntry, _render_items


def _make_state() -> RunState:
    return RunState()


def test_runstate_defaults():
    s = _make_state()
    assert s.photo_count == 0
    assert s.total_items == 0
    assert s.input_tokens == 0
    assert s.tool_log == []


def test_apply_scan_done():
    from schnapplist.ui.cli.display import apply_event
    s = _make_state()
    apply_event(s, "scan_done", count=7)
    assert s.photo_count == 7
    assert s.scan_done is True


def test_apply_group_done():
    from schnapplist.ui.cli.display import apply_event
    s = _make_state()
    apply_event(s, "group_done", count=3)
    assert s.group_count == 3
    assert s.total_items == 3
    assert s.group_done is True


def test_apply_item_start():
    from schnapplist.ui.cli.display import apply_event
    s = _make_state()
    apply_event(s, "group_done", count=3)
    apply_event(s, "item_start", idx=1, total=3)
    assert 1 in s.items
    assert s.items[1].status == "active"
    assert s.active_idx == 1


def test_apply_item_stage():
    from schnapplist.ui.cli.display import apply_event
    s = _make_state()
    apply_event(s, "group_done", count=2)
    apply_event(s, "item_start", idx=1, total=2)
    apply_event(s, "item_stage", idx=1, stage="web_search")
    assert s.items[1].stage == "web_search"
    assert len(s.tool_log) == 1
    assert s.tool_log[0].tool == "web_search"


def test_apply_item_stage_non_tool_does_not_log():
    from schnapplist.ui.cli.display import apply_event
    s = _make_state()
    apply_event(s, "group_done", count=1)
    apply_event(s, "item_start", idx=1, total=1)
    apply_event(s, "item_stage", idx=1, stage="enhance")
    assert len(s.tool_log) == 0


def test_apply_item_done():
    from schnapplist.ui.cli.display import apply_event
    s = _make_state()
    apply_event(s, "group_done", count=1)
    apply_event(s, "item_start", idx=1, total=1)
    apply_event(s, "item_done", idx=1, name="Test RAM", price="5.00 EUR")
    assert s.items[1].status == "done"
    assert s.items[1].name == "Test RAM"
    assert s.completed_items == 1


def test_apply_item_usage_accumulates():
    from schnapplist.ui.cli.display import apply_event
    s = _make_state()
    apply_event(s, "item_usage", idx=1, input_tokens=100, output_tokens=50,
                cache_read_tokens=20, requests=3, tool_calls=2)
    apply_event(s, "item_usage", idx=2, input_tokens=80, output_tokens=30,
                cache_read_tokens=10, requests=2, tool_calls=1)
    assert s.input_tokens == 180
    assert s.output_tokens == 80
    assert s.cache_tokens == 30
    assert s.requests == 5
    # tool_calls is now counted from item_stage events, not item_usage


def test_tool_calls_counted_from_item_stage():
    from schnapplist.ui.cli.display import apply_event
    s = _make_state()
    apply_event(s, "group_done", count=1)
    apply_event(s, "item_start", idx=1, total=1)
    apply_event(s, "item_stage", idx=1, stage="analyze_photos")
    apply_event(s, "item_stage", idx=1, stage="web_search")
    apply_event(s, "item_stage", idx=1, stage="web_search")
    apply_event(s, "item_stage", idx=1, stage="enhance")  # not a tool stage
    assert s.tool_calls == 3


def test_tool_log_capped_at_five():
    from schnapplist.ui.cli.display import apply_event
    s = _make_state()
    apply_event(s, "group_done", count=1)
    apply_event(s, "item_start", idx=1, total=1)
    for _ in range(7):
        apply_event(s, "item_stage", idx=1, stage="web_search")
    assert len(s.tool_log) == 5


def test_render_header_returns_renderable():
    from schnapplist.ui.cli.display import _render_header
    from rich.console import Console
    s = _make_state()
    renderable = _render_header(s)
    console = Console(force_terminal=True, width=80)
    with console.capture():
        console.print(renderable)


def test_render_items_returns_renderable():
    from schnapplist.ui.cli.display import _render_items, apply_event
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
    from schnapplist.ui.cli.display import _render_llm, apply_event
    from rich.console import Console
    s = _make_state()
    apply_event(s, "item_usage", idx=1, input_tokens=500, output_tokens=200,
                cache_read_tokens=100, requests=4, tool_calls=3)
    renderable = _render_llm(s)
    console = Console(force_terminal=True, width=40)
    with console.capture():
        console.print(renderable)


def test_item_done_low_confidence_sets_flag():
    from schnapplist.ui.cli.display import apply_event
    state = RunState()
    apply_event(state, "item_start", idx=1, total=1)
    apply_event(state, "item_done", idx=1, name="Toshiba SRAM", price="6.00 EUR",
                confidence=0.55, low_confidence=True)
    assert state.items[1].low_confidence is True
    assert state.items[1].confidence == 0.55


def test_item_done_high_confidence_no_flag():
    from schnapplist.ui.cli.display import apply_event
    state = RunState()
    apply_event(state, "item_start", idx=1, total=1)
    apply_event(state, "item_done", idx=1, name="Sony WH-1000XM5", price="180.00 EUR",
                confidence=0.9, low_confidence=False)
    assert state.items[1].low_confidence is False


def test_render_items_low_confidence_shows_warning_icon():
    from schnapplist.ui.cli.display import apply_event
    from rich.console import Console
    state = RunState()
    apply_event(state, "item_start", idx=1, total=1)
    apply_event(state, "item_done", idx=1, name="Toshiba SRAM", price="6.00 EUR",
                confidence=0.55, low_confidence=True)
    panel = _render_items(state)
    console = Console(force_terminal=True, width=80)
    with console.capture() as cap:
        console.print(panel)
    rendered = cap.get()
    assert "⚠" in rendered
    assert "0.55" in rendered


def test_toks_uses_gen_secs():
    """tok/s uses accumulated generation time, not wall-clock elapsed."""
    from schnapplist.ui.cli.display import _render_llm, apply_event
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


def test_decision_cb_report_ready_returns_empty_string(tmp_path, monkeypatch):
    """report_ready shows a modal and returns '' after a keypress."""
    from schnapplist.ui.cli.display import RichDecisionCallback, RichLiveCallback

    # Stub out the Live display so no terminal I/O happens
    live_cb = RichLiveCallback.__new__(RichLiveCallback)
    shown = []
    live_cb.show_modal = lambda r: shown.append(r)
    restored = []
    live_cb.restore_body = lambda: restored.append(True)

    # Stub _read_single_key to return immediately without blocking
    monkeypatch.setattr("schnapplist.ui.cli.display._read_single_key", lambda allowed, default: default)

    cb = RichDecisionCallback(live_cb)
    item_paths = [tmp_path / "item-1.md", tmp_path / "item-2.md"]
    result = cb("report_ready", report_path=tmp_path, item_paths=item_paths)

    assert result == ""
    assert len(shown) == 1  # modal was displayed
    assert len(restored) == 1  # restore_body was called (terminal cleanup)


def test_decision_cb_ebay_export_prompt_yes(monkeypatch):
    """ebay_export_prompt returns 'yes' when user presses y."""
    from schnapplist.ui.cli.display import RichDecisionCallback, RichLiveCallback

    live_cb = RichLiveCallback.__new__(RichLiveCallback)
    shown = []
    live_cb.show_modal = lambda r: shown.append(r)
    restored = []
    live_cb.restore_body = lambda: restored.append(True)

    monkeypatch.setattr("schnapplist.ui.cli.display._read_single_key", lambda allowed, default: "y")

    cb = RichDecisionCallback(live_cb)
    result = cb("ebay_export_prompt", approved_count=2, total_ebay_count=3)

    assert result == "yes"
    assert len(shown) == 1
    assert len(restored) == 1


def test_decision_cb_ebay_export_prompt_no(monkeypatch):
    """ebay_export_prompt returns 'no' when user presses n."""
    from schnapplist.ui.cli.display import RichDecisionCallback, RichLiveCallback

    live_cb = RichLiveCallback.__new__(RichLiveCallback)
    shown = []
    live_cb.show_modal = lambda r: shown.append(r)
    restored = []
    live_cb.restore_body = lambda: restored.append(True)

    monkeypatch.setattr("schnapplist.ui.cli.display._read_single_key", lambda allowed, default: "n")

    cb = RichDecisionCallback(live_cb)
    result = cb("ebay_export_prompt", approved_count=2, total_ebay_count=3)

    assert result == "no"
    assert len(shown) == 1
    assert len(restored) == 1
