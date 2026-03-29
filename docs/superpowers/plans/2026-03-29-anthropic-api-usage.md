# Anthropic API Usage Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional feature that fetches real monthly cost data from the Anthropic Admin API and displays it as a new row below the Total line in the legend, with a `[a]` key input for entering/persisting the API key.

**Architecture:** New `anthropic_usage.py` module owns key storage, HTTP polling, and cost state. `display.py` gains a key-input UI (toggled with `[a]`) and a new legend row. `main.py` wires `AnthropicUsage` into both the display and shutdown lifecycle.

**Tech Stack:** Python stdlib (`urllib.request`, `threading`, `json`), existing `rich` terminal UI.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `anthropic_usage.py` | Create | Key storage, HTTP fetch, poll loop, cost state |
| `display.py` | Modify | `_fmt_cost`, legend row, `[a]` input UI, status bar |
| `main.py` | Modify | Instantiate + wire `AnthropicUsage` |
| `tests/test_anthropic_usage.py` | Create | Unit tests for new module |

---

### Task 1: `AnthropicUsage` — key storage and state

**Files:**
- Create: `anthropic_usage.py`
- Create: `tests/test_anthropic_usage.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_anthropic_usage.py
import tempfile
from pathlib import Path
from unittest.mock import patch
from anthropic_usage import AnthropicUsage


def test_no_key_returns_zero(tmp_path):
    key_path = tmp_path / "monitor_key"
    usage = AnthropicUsage(key_path=key_path)
    assert usage.cost_month_cents == 0.0
    assert usage.cost_session_delta_cents == 0.0
    assert usage.has_key is False


def test_key_file_written_and_read(tmp_path):
    key_path = tmp_path / "monitor_key"
    usage = AnthropicUsage(key_path=key_path)
    usage.set_key("sk-ant-admin-test")
    assert key_path.exists()
    assert oct(key_path.stat().st_mode)[-3:] == "600"
    assert key_path.read_text() == "sk-ant-admin-test"
    assert usage.has_key is True
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /Users/shreyans/Library/CloudStorage/Dropbox/Code/puck/claude-monitor
python -m pytest tests/test_anthropic_usage.py::test_no_key_returns_zero tests/test_anthropic_usage.py::test_key_file_written_and_read -v
```

Expected: `ModuleNotFoundError: No module named 'anthropic_usage'`

- [ ] **Step 3: Implement `AnthropicUsage` core**

```python
# anthropic_usage.py
import threading
from pathlib import Path

_DEFAULT_KEY_PATH = Path.home() / ".claude" / "monitor_key"


class AnthropicUsage:
    def __init__(self, key_path: Path = _DEFAULT_KEY_PATH) -> None:
        self._key_path = key_path
        self._lock = threading.Lock()
        self._fetch_event = threading.Event()
        self._shutdown = threading.Event()
        self._cost_month_cents: float = 0.0
        self._cost_session_delta_cents: float = 0.0
        self._session_start_cents: float | None = None

    @property
    def cost_month_cents(self) -> float:
        with self._lock:
            return self._cost_month_cents

    @property
    def cost_session_delta_cents(self) -> float:
        with self._lock:
            return self._cost_session_delta_cents

    @property
    def has_key(self) -> bool:
        return self._key_path.exists() and bool(self._key_path.read_text().strip())

    def set_key(self, key: str) -> None:
        with self._lock:
            self._key_path.write_text(key)
            self._key_path.chmod(0o600)
            self._session_start_cents = None
        self.trigger_fetch()

    def trigger_fetch(self) -> None:
        self._fetch_event.set()

    def start(self) -> None:
        t = threading.Thread(target=self._poll_loop, daemon=True)
        t.start()

    def stop(self) -> None:
        self._shutdown.set()
        self._fetch_event.set()  # unblock any wait

    def _poll_loop(self) -> None:
        while not self._shutdown.is_set():
            self._fetch_once()
            self._fetch_event.clear()
            self._fetch_event.wait(timeout=60)
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_anthropic_usage.py::test_no_key_returns_zero tests/test_anthropic_usage.py::test_key_file_written_and_read -v
```

Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add anthropic_usage.py tests/test_anthropic_usage.py
git commit -m "feat: add AnthropicUsage core — key storage and state"
```

---

### Task 2: `AnthropicUsage` — HTTP fetch and polling

**Files:**
- Modify: `anthropic_usage.py`
- Modify: `tests/test_anthropic_usage.py`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/test_anthropic_usage.py
import json
from unittest.mock import patch, MagicMock


def _make_response(data: dict) -> MagicMock:
    """Helper: mock urlopen response returning JSON body."""
    body = json.dumps(data).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def test_fetch_error_retains_last_known(tmp_path):
    key_path = tmp_path / "monitor_key"
    key_path.write_text("sk-ant-admin-test")
    key_path.chmod(0o600)
    usage = AnthropicUsage(key_path=key_path)

    # First successful fetch: 500 cents = $5.00
    good_response = _make_response({
        "data": [{"starting_at": "2026-03-01T00:00:00Z", "ending_at": "2026-03-02T00:00:00Z",
                  "results": [{"amount": "500.00", "currency": "USD", "cost_type": "tokens"}]}],
        "has_more": False, "next_page": None
    })
    with patch("urllib.request.urlopen", return_value=good_response):
        usage._fetch_once()
    assert usage.cost_month_cents == 500.0

    # Second fetch fails with exception
    with patch("urllib.request.urlopen", side_effect=Exception("network error")):
        usage._fetch_once()
    # Last-known value retained
    assert usage.cost_month_cents == 500.0
```

- [ ] **Step 2: Run test — verify it fails**

```bash
python -m pytest tests/test_anthropic_usage.py::test_fetch_error_retains_last_known -v
```

Expected: `AttributeError: 'AnthropicUsage' object has no attribute '_fetch_once'`

- [ ] **Step 3: Implement `_fetch_once`**

Add to `anthropic_usage.py` (add `import json`, `import urllib.request`, `import urllib.parse`, `from datetime import datetime, timezone` at top):

```python
    def _fetch_once(self) -> None:
        try:
            key = self._key_path.read_text().strip() if self._key_path.exists() else ""
        except OSError:
            return
        if not key:
            return

        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        params = urllib.parse.urlencode({
            "bucket_width": "1d",
            "starting_at": month_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ending_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        url = f"https://api.anthropic.com/v1/organizations/cost_report?{params}"
        total_cents = 0.0
        next_page = None

        while True:
            page_url = f"{url}&page={urllib.parse.quote(next_page)}" if next_page else url
            req = urllib.request.Request(page_url, headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            })
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    body = json.loads(resp.read())
            except Exception:
                return  # retain last-known values

            for bucket in body.get("data", []):
                for result in bucket.get("results", []):
                    try:
                        total_cents += float(result["amount"])
                    except (KeyError, ValueError):
                        pass

            if body.get("has_more"):
                next_page = body.get("next_page")
            else:
                break

        with self._lock:
            self._cost_month_cents = total_cents
            if self._session_start_cents is None:
                self._session_start_cents = total_cents
            self._cost_session_delta_cents = total_cents - self._session_start_cents
```

- [ ] **Step 4: Run all `test_anthropic_usage` tests**

```bash
python -m pytest tests/test_anthropic_usage.py -v
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add anthropic_usage.py tests/test_anthropic_usage.py
git commit -m "feat: add AnthropicUsage HTTP fetch and 60s poll loop"
```

---

### Task 3: `display.py` — `_fmt_cost` and legend row

**Files:**
- Modify: `display.py`
- Modify: `tests/test_anthropic_usage.py`

- [ ] **Step 1: Write failing test for `_fmt_cost`**

```python
# Add to tests/test_anthropic_usage.py
from display import _fmt_cost


def test_fmt_cost_ranges():
    assert _fmt_cost(0.5) == "$0.00"     # sub-cent — not shown in practice
    assert _fmt_cost(1.0) == "$0.01"     # exactly 1 cent
    assert _fmt_cost(123_45) == "$123.45"  # mid-range dollar value (12345 cents)
    assert _fmt_cost(99_999) == "$999.99"  # just below k threshold
    assert _fmt_cost(100_000) == "$1.00k"  # exactly at k threshold ($1,000)
    assert _fmt_cost(123_000) == "$1.23k"  # above threshold ($1,230)
```

- [ ] **Step 2: Run test — verify it fails**

```bash
python -m pytest tests/test_anthropic_usage.py::test_fmt_cost_ranges -v
```

Expected: `ImportError: cannot import name '_fmt_cost' from 'display'`

- [ ] **Step 3: Add `_fmt_cost` and update `_build_legend` signature**

In `display.py`, add `_fmt_cost` after `_fmt_val`:

```python
def _fmt_cost(cents: float) -> str:
    if cents < 1:
        return "$0.00"
    if cents < 100_000:
        return f"${cents / 100:.2f}"
    return f"${cents / 100_000:.2f}k"
```

Update `_build_legend` signature to accept `cost_month_cents` and `cost_session_delta_cents` as plain floats (snapshots taken by the caller — avoids a race between two property reads):

```python
def _build_legend(dirs: list[str], dir_colors: dict[str, str], buckets: list[Bucket], lifetime_by_dir: dict[str, int], height: int, api_month_cents: float = 0.0, api_delta_cents: float = 0.0) -> list[Text]:
```

At the end of `_build_legend`, after appending the Total line and before the padding calculation, add:

```python
    # Optional Anthropic API cost row
    show_api = api_month_cents >= 1
    if show_api:
        delta_col_w = 7  # one wider to fit '+' prefix
        api_line = Text()
        api_line.append(f"{'  Anthropic':<{2 + name_w + 1}}", style="dim")
        api_line.append(f"{_fmt_cost(api_month_cents):>{col_w}}", style="dim")
        api_line.append("  ")
        api_line.append(f"{'+' + _fmt_cost(api_delta_cents):>{delta_col_w}}", style="dim")
        content.append(api_line)
```

Update the padding calculation to accommodate the optional extra row:

```python
    total_slots = height + 2 + (1 if show_api else 0)
```

- [ ] **Step 4: Run test — verify `test_fmt_cost_ranges` passes**

```bash
python -m pytest tests/test_anthropic_usage.py::test_fmt_cost_ranges -v
```

Expected: `1 passed`

- [ ] **Step 5: Update `_build_layout` to snapshot cost values and forward to both chart and legend**

Snapshot both values once so the `zip` alignment decision and the legend row use the same data:

```python
def _build_layout(store: DataStore, usage=None) -> Group:
    buckets = store.buckets()

    with _chart_height_lock:
        height = _chart_height

    # Snapshot API cost once to keep chart/legend line counts in sync
    api_month_cents = usage.cost_month_cents if usage is not None else 0.0
    api_delta_cents = usage.cost_session_delta_cents if usage is not None else 0.0

    dirs = store.directories()
    dir_colors = {d: PALETTE[i % len(PALETTE)] for i, d in enumerate(dirs)}

    chart_lines = _render_area_chart(buckets, "Tokens / 10s", dirs, dir_colors, height)
    # Add a blank chart line when the legend has an extra Anthropic row
    if api_month_cents >= 1:
        chart_lines.append(Text(""))
    legend_lines = _build_legend(dirs, dir_colors, buckets, store.lifetime_by_dir(), height, api_month_cents, api_delta_cents)

    merged = Text()
    for chart_line, legend_line in zip(chart_lines, legend_lines):
        merged.append_text(chart_line)
        merged.append("  ")
        merged.append_text(legend_line)
        merged.append("\n")

    with _api_input_lock:
        input_active = _api_input_active
        input_buf = _api_input_buffer

    status = Text()
    status.append("[q] quit", style="dim")
    status.append("  [+/-] chart height", style="dim")
    if input_active:
        status.append("  [a] cancel", style="dim")
        status.append("  ▎" + "*" * len(input_buf))
    else:
        status.append("  [a] Anthropic key", style="dim")

    return Group(Text("◆ Claude Monitor", style="bold cyan"), merged, status)
```

Note: this replaces the entire `_build_layout` function body. Remove the old `status = Text() ...` block from the original.

- [ ] **Step 6: Run all tests to verify no regressions**

```bash
python -m pytest tests/ -v
```

Expected: all previously passing tests plus `test_fmt_cost_ranges` pass (total increases by 1).

- [ ] **Step 7: Commit**

```bash
git add display.py tests/test_anthropic_usage.py
git commit -m "feat: add _fmt_cost and Anthropic cost legend row"
```

---

### Task 4: `display.py` — `[a]` key input UI

**Files:**
- Modify: `display.py`

- [ ] **Step 1: Add input globals and update `Display.__init__`**

Below the existing `_chart_height_lock` globals, add:

```python
_api_input_active = False
_api_input_buffer = ""
_api_input_lock = threading.Lock()
```

Update `Display.__init__` to accept `usage`:

```python
    def __init__(self, store: DataStore, usage=None) -> None:
        self._store = store
        self._usage = usage
        self._quit = threading.Event()
        self._console = Console()
```

- [ ] **Step 2: Update `_keyboard_thread` to handle `[a]` and input mode**

Replace the entire `_keyboard_thread` method. The `global` declarations must cover both the existing `_chart_height` and the new input globals — match the pattern already used in the existing method (line 200: `global _chart_height`):

```python
    def _keyboard_thread(self) -> None:
        global _chart_height, _api_input_active, _api_input_buffer
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        new = termios.tcgetattr(fd)
        new[3] = new[3] & ~(termios.ICANON | termios.ECHO | termios.ISIG)
        new[6][termios.VMIN] = 1
        new[6][termios.VTIME] = 0
        try:
            termios.tcsetattr(fd, termios.TCSANOW, new)
            while not self._quit.is_set():
                ch = sys.stdin.read(1)
                if ch in ("q", "Q", "\x03"):  # q or Ctrl+C
                    self._quit.set()
                elif ch == "+":
                    with _chart_height_lock:
                        _chart_height = min(_chart_height + 1, 30)
                elif ch == "-":
                    with _chart_height_lock:
                        _chart_height = max(_chart_height - 1, 1)
                elif ch == "a":
                    with _api_input_lock:
                        if _api_input_active:
                            buf = _api_input_buffer
                            _api_input_active = False
                            _api_input_buffer = ""
                        else:
                            buf = None
                            _api_input_active = True
                            _api_input_buffer = ""
                    if buf and self._usage is not None:
                        self._usage.set_key(buf)
                elif ch == "\x1b":  # ESC — cancel input, drain sequence
                    with _api_input_lock:
                        _api_input_active = False
                        _api_input_buffer = ""
                    # Drain multi-byte ESC sequence (e.g. arrow keys send \x1b[A)
                    new[6][termios.VMIN] = 0
                    new[6][termios.VTIME] = 1  # 100ms timeout
                    termios.tcsetattr(fd, termios.TCSANOW, new)
                    while True:
                        leftover = sys.stdin.read(1)
                        if not leftover:
                            break
                    new[6][termios.VMIN] = 1
                    new[6][termios.VTIME] = 0
                    termios.tcsetattr(fd, termios.TCSANOW, new)
                else:
                    with _api_input_lock:
                        if _api_input_active:
                            if ch in ("\x7f", "\x08"):  # backspace / DEL
                                _api_input_buffer = _api_input_buffer[:-1]
                            elif "\x20" <= ch <= "\x7e":  # printable ASCII
                                _api_input_buffer += ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
```

- [ ] **Step 3: Pass `usage` from `Display.run` into `_build_layout`**

The status bar is now built inside `_build_layout` (done in Task 3 Step 5). Just update the call in `Display.run`:

```python
                        live.update(_build_layout(self._store, self._usage), refresh=True)
```

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add display.py
git commit -m "feat: add [a] key input UI for Anthropic API key"
```

---

### Task 5: `main.py` — wire `AnthropicUsage`

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Update `main.py`**

```python
import sys

from store import DataStore
from watcher import LogWatcher, preload_recent, CLAUDE_PROJECTS_DIR
from display import Display
from anthropic_usage import AnthropicUsage


def main() -> None:
    projects_dir = CLAUDE_PROJECTS_DIR
    if not projects_dir.exists():
        print(f"Error: Claude projects directory not found: {projects_dir}", file=sys.stderr)
        print("Is Claude Code installed?", file=sys.stderr)
        sys.exit(1)

    store = DataStore()
    preload_recent(store, projects_dir)
    watcher = LogWatcher(store, projects_dir=projects_dir)
    try:
        watcher.start()
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    usage = AnthropicUsage()
    usage.start()

    try:
        display = Display(store, usage=usage)
        display.run()
    finally:
        watcher.stop()
        usage.stop()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat: wire AnthropicUsage into main lifecycle"
```

---

### Task 6: Manual smoke test

- [ ] **Step 1: Launch the monitor**

```bash
python main.py
```

Verify:
- Monitor starts normally, no errors
- Status bar shows `[a] Anthropic key`
- Press `[a]` — input field appears with `[a] cancel  ▎`
- Type a few chars — they appear masked as `***`
- Press backspace — last `*` removed
- Press `[a]` again — field closes (key saved if non-empty)
- Press `[a]` again, then ESC — field closes without saving
- If a valid Admin API key was entered, within 60s the Anthropic row appears below Total showing `$X.XX  +$X.XX`
- Press `q` — exits cleanly