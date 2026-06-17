# Rich Modal Prompts — Post-Process Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two `click.confirm` TTY prompts in the `process` command with in-UI Rich modal dialogs, completing the migration away from all plain TTY input.

**Architecture:** Two new event branches (`"report_ready"` and `"ebay_export_prompt"`) are added to `RichDecisionCallback.__call__` in `display.py`, following the existing `"item_failed"` modal pattern. The `process` command in `__init__.py` calls these events instead of `click.confirm`, and the `_run_review` editor call is removed from the post-process flow.

**Tech Stack:** Python, Rich (`Panel`, `Text`, `Align`), existing `_read_single_key` helper.

---

## File Map

- Modify: `schnapplist/ui/cli/display.py` — add two new event branches to `RichDecisionCallback.__call__`
- Modify: `schnapplist/ui/cli/__init__.py` — replace `click.confirm` calls with `decision_cb` calls
- Modify: `tests/test_cli_display.py` — add tests for the two new modal event handlers

---

### Task 1: Add `"report_ready"` event to `RichDecisionCallback`

**Files:**
- Modify: `schnapplist/ui/cli/display.py`
- Test: `tests/test_cli_display.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli_display.py`:

```python
def test_decision_cb_report_ready_returns_empty_string(tmp_path, monkeypatch):
    """report_ready shows a modal and returns '' after a keypress."""
    from schnapplist.ui.cli.display import RichDecisionCallback, RichLiveCallback

    # Stub out the Live display so no terminal I/O happens
    live_cb = RichLiveCallback.__new__(RichLiveCallback)
    shown = []
    live_cb.show_modal = lambda r: shown.append(r)
    live_cb.restore_body = lambda: None

    # Stub _read_single_key to return immediately without blocking
    monkeypatch.setattr("schnapplist.ui.cli.display._read_single_key", lambda allowed, default: default)

    cb = RichDecisionCallback(live_cb)
    item_paths = [tmp_path / "item-1.md", tmp_path / "item-2.md"]
    result = cb("report_ready", report_path=tmp_path, item_paths=item_paths)

    assert result == ""
    assert len(shown) == 1  # modal was displayed
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_cli_display.py::test_decision_cb_report_ready_returns_empty_string -v
```

Expected: FAIL — `RichDecisionCallback.__call__` has no `"report_ready"` branch.

- [ ] **Step 3: Implement the `"report_ready"` branch in `RichDecisionCallback.__call__`**

In `schnapplist/ui/cli/display.py`, add this branch inside `RichDecisionCallback.__call__` after the `if event == "item_failed":` block:

```python
        elif event == "report_ready":
            report_path = kwargs["report_path"]
            item_paths: list = kwargs.get("item_paths", [])
            file_lines = "\n".join(f"  {p.name}" for p in item_paths) if item_paths else f"  {report_path}"
            modal = Panel(
                Text.assemble(
                    (str(report_path), "bold"),
                    "\n\n",
                    (file_lines, "dim"),
                    "\n\n",
                    ("  Press any key when done reviewing…", "dim italic"),
                ),
                title=Text("[bold blue]Reports ready[/bold blue]"),
                border_style="blue",
                width=60,
                padding=(1, 2),
            )
            self._progress_cb.show_modal(modal)
            try:
                _read_single_key(set("abcdefghijklmnopqrstuvwxyz \r\n"), default=" ")
            finally:
                self._progress_cb.restore_body()
            return ""
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_cli_display.py::test_decision_cb_report_ready_returns_empty_string -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add schnapplist/ui/cli/display.py tests/test_cli_display.py
git commit -m "feat: add report_ready modal to RichDecisionCallback"
```

---

### Task 2: Add `"ebay_export_prompt"` event to `RichDecisionCallback`

**Files:**
- Modify: `schnapplist/ui/cli/display.py`
- Test: `tests/test_cli_display.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli_display.py`:

```python
def test_decision_cb_ebay_export_prompt_yes(monkeypatch):
    """ebay_export_prompt returns 'yes' when user presses y."""
    from schnapplist.ui.cli.display import RichDecisionCallback, RichLiveCallback

    live_cb = RichLiveCallback.__new__(RichLiveCallback)
    live_cb.show_modal = lambda r: None
    live_cb.restore_body = lambda: None

    monkeypatch.setattr("schnapplist.ui.cli.display._read_single_key", lambda allowed, default: "y")

    cb = RichDecisionCallback(live_cb)
    result = cb("ebay_export_prompt", approved_count=2, total_ebay_count=3)
    assert result == "yes"


def test_decision_cb_ebay_export_prompt_no(monkeypatch):
    """ebay_export_prompt returns 'no' when user presses n."""
    from schnapplist.ui.cli.display import RichDecisionCallback, RichLiveCallback

    live_cb = RichLiveCallback.__new__(RichLiveCallback)
    live_cb.show_modal = lambda r: None
    live_cb.restore_body = lambda: None

    monkeypatch.setattr("schnapplist.ui.cli.display._read_single_key", lambda allowed, default: "n")

    cb = RichDecisionCallback(live_cb)
    result = cb("ebay_export_prompt", approved_count=2, total_ebay_count=3)
    assert result == "no"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_cli_display.py::test_decision_cb_ebay_export_prompt_yes tests/test_cli_display.py::test_decision_cb_ebay_export_prompt_no -v
```

Expected: FAIL — no `"ebay_export_prompt"` branch exists.

- [ ] **Step 3: Implement the `"ebay_export_prompt"` branch**

In `schnapplist/ui/cli/display.py`, add after the `"report_ready"` branch:

```python
        elif event == "ebay_export_prompt":
            approved_count = kwargs["approved_count"]
            total_ebay_count = kwargs["total_ebay_count"]
            modal = Panel(
                Text.assemble(
                    (f"{approved_count} of {total_ebay_count} eBay item(s) approved.", ""),
                    "\n\n",
                    ("  Generate CSV draft?\n\n", ""),
                    ("  ", ""),
                    ("[ y ]", "bold black on green"),
                    ("  yes      ", ""),
                    ("[ n ]", "bold black on white"),
                    ("  no", ""),
                    "\n\n",
                    ("  press a key…", "dim italic"),
                ),
                title=Text("[bold green]eBay CSV Export[/bold green]"),
                border_style="green",
                width=60,
                padding=(1, 2),
            )
            self._progress_cb.show_modal(modal)
            try:
                choice = _read_single_key({"y", "n"}, default="n")
            finally:
                self._progress_cb.restore_body()
            return "yes" if choice == "y" else "no"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_cli_display.py::test_decision_cb_ebay_export_prompt_yes tests/test_cli_display.py::test_decision_cb_ebay_export_prompt_no -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add schnapplist/ui/cli/display.py tests/test_cli_display.py
git commit -m "feat: add ebay_export_prompt modal to RichDecisionCallback"
```

---

### Task 3: Update `process` command to use modal callbacks

**Files:**
- Modify: `schnapplist/ui/cli/__init__.py`

No new tests needed here — the callback logic is already tested; this is purely wiring.

- [ ] **Step 1: Replace the post-process block in `process`**

In `schnapplist/ui/cli/__init__.py`, replace lines 133–165 (from `report_path = result.report_path` to the end of the `ebay_items` block) with:

```python
    report_path = result.report_path
    if report_path is None:
        console.print("[red]Processing did not produce a report.[/red]")
        sys.exit(1)

    console.print(f"\n[bold green]Report:[/bold green] {report_path}")

    item_paths = sorted(
        report_path.glob("item-*.md"),
        key=lambda p: int(p.stem.split("-")[1]),
    )
    decision_cb("report_ready", report_path=report_path, item_paths=item_paths)

    from ...providers.ebay_csv_exporter import export_to_csv
    from ...services.posting_service import items_from_report_path

    items = items_from_report_path(report_path)
    ebay_items = [it for it in items if it.marketplace == "ebay"]
    approved_ebay = [it for it in ebay_items if it.approved]

    if ebay_items:
        choice = decision_cb(
            "ebay_export_prompt",
            approved_count=len(approved_ebay),
            total_ebay_count=len(ebay_items),
        )
        if choice == "yes":
            csv_path = report_path / "ebay-export.csv"
            count = export_to_csv(items, csv_path)
            if count == 0:
                console.print("[yellow]No approved eBay items — CSV not written.[/yellow]")
            else:
                console.print(
                    f"\n[bold green]eBay CSV written:[/bold green] {csv_path}  "
                    f"([bold]{count}[/bold] approved item(s))"
                )

    console.print(
        "\nWhen ready, run [bold]schnapplist post[/bold] to create listings."
    )
```

- [ ] **Step 2: Remove the unused `click.confirm` import guard**

Check whether `click` is still used elsewhere in the file (it is — `@click.group()`, `@click.option`, etc.). No import change needed.

- [ ] **Step 3: Run the full test suite**

```bash
pytest -v
```

Expected: all tests pass. Pay attention to any test that exercises the `process` command path.

- [ ] **Step 4: Commit**

```bash
git add schnapplist/ui/cli/__init__.py
git commit -m "refactor: replace click.confirm prompts with Rich modal dialogs in process command"
```
