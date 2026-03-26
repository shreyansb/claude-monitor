# Claude Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a terminal app that watches `~/.claude/projects/**/*.jsonl` and displays a live rolling bar chart of Claude token usage and estimated cost over the last 5 minutes.

**Architecture:** A `watchdog`-based file watcher feeds usage events into an in-memory ring-buffer data store (30 × 10s buckets). A `rich`-based TUI reads the store every second and renders two stacked bar charts (tokens and cents), plus a toggleable pricing panel.

**Tech Stack:** Python 3.9+, `rich`, `watchdog`, stdlib (`threading`, `dataclasses`, `tty`, `termios`, `json`, `pathlib`, `datetime`)

---

## File Map

| File | Responsibility |
|------|---------------|
| `pricing.py` | Per-model pricing table; `calculate_cost()` |
| `store.py` | `UsageEvent` dataclass, `DataStore` ring buffer |
| `watcher.py` | `LogWatcher` — watchdog handler, background thread |
| `display.py` | `Display` — rich Live TUI + keyboard thread |
| `main.py` | Wires components together, entry point |
| `requirements.txt` | `rich`, `watchdog` |
| `tests/test_pricing.py` | Tests for cost calculation |
| `tests/test_store.py` | Tests for bucket logic |
| `tests/test_watcher.py` | Tests for JSONL parsing |

---

## Task 1: Project scaffold and pricing module

**Files:**
- Create: `claude-monitor/requirements.txt`
- Create: `claude-monitor/pricing.py`
- Create: `claude-monitor/tests/__init__.py`
- Create: `claude-monitor/tests/test_pricing.py`

- [ ] **Step 1: Create `requirements.txt`**

```
rich>=13.0.0
watchdog>=3.0.0
```

- [ ] **Step 2: Install dependencies**

```bash
cd claude-monitor
pip install -r requirements.txt
```

Expected: both packages install without errors.

- [ ] **Step 3: Write failing tests for `pricing.py`**

Create `tests/test_pricing.py`:

```python
from pricing import calculate_cost, PRICING_TABLE

def test_known_model_output_tokens():
    cost = calculate_cost("claude-sonnet-4-6", {
        "input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 1_000_000,
    })
    # $15.00 per million output = 1500 cents
    assert abs(cost - 1500.0) < 0.001

def test_known_model_input_tokens():
    cost = calculate_cost("claude-sonnet-4-6", {
        "input_tokens": 1_000_000,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
    })
    # $3.00 per million input = 300 cents
    assert abs(cost - 300.0) < 0.001

def test_cache_read_cheaper_than_input():
    cache_cost = calculate_cost("claude-sonnet-4-6", {
        "input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 1_000_000,
        "output_tokens": 0,
    })
    input_cost = calculate_cost("claude-sonnet-4-6", {
        "input_tokens": 1_000_000,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
    })
    assert cache_cost < input_cost

def test_unknown_model_falls_back_to_sonnet():
    cost_unknown = calculate_cost("claude-unknown-99", {
        "input_tokens": 1_000_000,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
    })
    cost_sonnet = calculate_cost("claude-sonnet-4-6", {
        "input_tokens": 1_000_000,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
    })
    assert cost_unknown == cost_sonnet

def test_all_tokens_combined():
    cost = calculate_cost("claude-opus-4-6", {
        "input_tokens": 1_000,
        "cache_creation_input_tokens": 1_000,
        "cache_read_input_tokens": 1_000,
        "output_tokens": 1_000,
    })
    # opus: input=$15/M, cache_write=$18.75/M, cache_read=$1.50/M, output=$75/M
    # each 1000 tokens = 0.001M
    expected = (15.0 + 18.75 + 1.50 + 75.0) * 0.001 * 100  # in cents
    assert abs(cost - expected) < 0.001

def test_pricing_table_has_required_models():
    assert "claude-sonnet-4-6" in PRICING_TABLE
    assert "claude-opus-4-6" in PRICING_TABLE
    assert "claude-haiku-4-5" in PRICING_TABLE
```

- [ ] **Step 4: Run tests to verify they fail**

```bash
cd claude-monitor
python -m pytest tests/test_pricing.py -v
```

Expected: `ModuleNotFoundError: No module named 'pricing'`

- [ ] **Step 5: Implement `pricing.py`**

```python
from dataclasses import dataclass

@dataclass
class ModelPricing:
    input_per_m: float        # USD per million input tokens
    cache_read_per_m: float   # USD per million cache-read tokens
    cache_write_per_m: float  # USD per million cache-creation tokens
    output_per_m: float       # USD per million output tokens

PRICING_TABLE: dict[str, ModelPricing] = {
    "claude-sonnet-4-6": ModelPricing(3.00, 0.30, 3.75, 15.00),
    "claude-opus-4-6":   ModelPricing(15.00, 1.50, 18.75, 75.00),
    "claude-haiku-4-5":  ModelPricing(0.80, 0.08, 1.00, 4.00),
}

_DEFAULT = PRICING_TABLE["claude-sonnet-4-6"]


def calculate_cost(model: str, usage: dict) -> float:
    """Return estimated cost in cents."""
    p = PRICING_TABLE.get(model, _DEFAULT)
    usd = (
        usage.get("input_tokens", 0) * p.input_per_m / 1_000_000
        + usage.get("cache_read_input_tokens", 0) * p.cache_read_per_m / 1_000_000
        + usage.get("cache_creation_input_tokens", 0) * p.cache_write_per_m / 1_000_000
        + usage.get("output_tokens", 0) * p.output_per_m / 1_000_000
    )
    return usd * 100  # convert to cents
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
python -m pytest tests/test_pricing.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 7: Commit**

```bash
git init  # only if not already a repo
git add requirements.txt pricing.py tests/
git commit -m "feat: add pricing module with per-model cost calculation"
```

---

## Task 2: Data store (ring buffer)

**Files:**
- Create: `claude-monitor/store.py`
- Create: `claude-monitor/tests/test_store.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_store.py`:

```python
from datetime import datetime, timezone
from store import DataStore, UsageEvent

def _event(ts: datetime, input_tokens=100, output_tokens=50, cost_cents=0.5):
    return UsageEvent(
        timestamp=ts,
        model="claude-sonnet-4-6",
        input_tokens=input_tokens,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        output_tokens=output_tokens,
        cost_cents=cost_cents,
    )

def _now():
    return datetime.now(timezone.utc)

def test_add_and_totals():
    store = DataStore()
    store.add(_event(_now(), input_tokens=1000, output_tokens=500, cost_cents=5.0))
    totals = store.session_totals()
    assert totals.input_tokens == 1000
    assert totals.output_tokens == 500
    assert abs(totals.cost_cents - 5.0) < 0.001

def test_buckets_returns_30():
    store = DataStore()
    buckets = store.buckets()
    assert len(buckets) == 30

def test_event_lands_in_correct_bucket():
    store = DataStore()
    now = _now()
    store.add(_event(now, cost_cents=1.0))
    buckets = store.buckets()
    # Most recent bucket should contain the event
    assert buckets[-1].cost_cents > 0 or buckets[-2].cost_cents > 0

def test_old_events_discarded():
    from datetime import timedelta
    store = DataStore()
    old_ts = _now() - timedelta(minutes=10)
    store.add(_event(old_ts, cost_cents=99.0))
    totals = store.session_totals()
    assert totals.cost_cents == 0.0

def test_multiple_events_same_bucket():
    store = DataStore()
    now = _now()
    store.add(_event(now, input_tokens=100, cost_cents=1.0))
    store.add(_event(now, input_tokens=200, cost_cents=2.0))
    totals = store.session_totals()
    assert totals.input_tokens == 300
    assert abs(totals.cost_cents - 3.0) < 0.001

def test_session_totals_empty():
    store = DataStore()
    totals = store.session_totals()
    assert totals.input_tokens == 0
    assert totals.output_tokens == 0
    assert totals.cost_cents == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_store.py -v
```

Expected: `ModuleNotFoundError: No module named 'store'`

- [ ] **Step 3: Implement `store.py`**

```python
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

BUCKET_SECONDS = 10
NUM_BUCKETS = 30  # 30 × 10s = 5 minutes


@dataclass
class UsageEvent:
    timestamp: datetime
    model: str
    input_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    output_tokens: int
    cost_cents: float


@dataclass
class Bucket:
    input_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    cost_cents: float = 0.0

    def add(self, event: UsageEvent) -> None:
        self.input_tokens += event.input_tokens
        self.cache_creation_tokens += event.cache_creation_tokens
        self.cache_read_tokens += event.cache_read_tokens
        self.output_tokens += event.output_tokens
        self.cost_cents += event.cost_cents

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
            + self.output_tokens
        )


@dataclass
class Totals:
    input_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    cost_cents: float = 0.0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
            + self.output_tokens
        )


def _bucket_index(ts: datetime) -> int:
    """Map a timestamp to a bucket slot (0–29) based on 10s intervals."""
    epoch_seconds = int(ts.timestamp())
    return (epoch_seconds // BUCKET_SECONDS) % NUM_BUCKETS


def _window_start() -> datetime:
    """Oldest timestamp still within the 5-minute window."""
    from datetime import timedelta
    return datetime.now(timezone.utc) - timedelta(seconds=BUCKET_SECONDS * NUM_BUCKETS)


class DataStore:
    def __init__(self) -> None:
        self._buckets: dict[int, tuple[int, Bucket]] = {}
        # Maps slot_index → (epoch_slot, Bucket)
        # epoch_slot = epoch_seconds // BUCKET_SECONDS (absolute, not modular)
        self._lock = threading.Lock()

    def add(self, event: UsageEvent) -> None:
        epoch_seconds = int(event.timestamp.timestamp())
        epoch_slot = epoch_seconds // BUCKET_SECONDS
        slot_index = epoch_slot % NUM_BUCKETS

        cutoff_slot = int(_window_start().timestamp()) // BUCKET_SECONDS

        with self._lock:
            if epoch_slot < cutoff_slot:
                return  # too old, discard

            existing = self._buckets.get(slot_index)
            if existing is None or existing[0] != epoch_slot:
                # New epoch cycle — replace stale bucket
                bucket = Bucket()
                self._buckets[slot_index] = (epoch_slot, bucket)
            else:
                bucket = existing[1]

            bucket.add(event)

    def buckets(self) -> list[Bucket]:
        """Return 30 buckets oldest→newest. Empty Bucket for gaps."""
        now_epoch_slot = int(datetime.now(timezone.utc).timestamp()) // BUCKET_SECONDS
        result = []
        with self._lock:
            for i in range(NUM_BUCKETS):
                slot_index = (now_epoch_slot - NUM_BUCKETS + 1 + i) % NUM_BUCKETS
                expected_epoch_slot = now_epoch_slot - NUM_BUCKETS + 1 + i
                entry = self._buckets.get(slot_index)
                if entry and entry[0] == expected_epoch_slot:
                    result.append(entry[1])
                else:
                    result.append(Bucket())
        return result

    def session_totals(self) -> Totals:
        buckets = self.buckets()
        t = Totals()
        for b in buckets:
            t.input_tokens += b.input_tokens
            t.cache_creation_tokens += b.cache_creation_tokens
            t.cache_read_tokens += b.cache_read_tokens
            t.output_tokens += b.output_tokens
            t.cost_cents += b.cost_cents
        return t
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_store.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add store.py tests/test_store.py
git commit -m "feat: add ring-buffer data store with 30x10s buckets"
```

---

## Task 3: Log watcher

**Files:**
- Create: `claude-monitor/watcher.py`
- Create: `claude-monitor/tests/test_watcher.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_watcher.py`:

```python
import json
import tempfile
import time
from pathlib import Path
from watcher import parse_jsonl_line, LogWatcher
from store import DataStore

VALID_ASSISTANT_LINE = json.dumps({
    "type": "assistant",
    "timestamp": "2026-03-26T10:00:00.000Z",
    "message": {
        "model": "claude-sonnet-4-6",
        "role": "assistant",
        "usage": {
            "input_tokens": 100,
            "cache_creation_input_tokens": 50,
            "cache_read_input_tokens": 200,
            "output_tokens": 75,
        }
    }
})

def test_parse_valid_line():
    event = parse_jsonl_line(VALID_ASSISTANT_LINE)
    assert event is not None
    assert event.input_tokens == 100
    assert event.cache_creation_tokens == 50
    assert event.cache_read_tokens == 200
    assert event.output_tokens == 75
    assert event.model == "claude-sonnet-4-6"

def test_parse_user_line_returns_none():
    line = json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}})
    assert parse_jsonl_line(line) is None

def test_parse_no_usage_returns_none():
    line = json.dumps({"type": "assistant", "message": {"role": "assistant", "content": []}})
    assert parse_jsonl_line(line) is None

def test_parse_malformed_json_returns_none():
    assert parse_jsonl_line("not json {{{") is None

def test_parse_missing_timestamp_returns_none():
    line = json.dumps({
        "type": "assistant",
        "message": {
            "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 1, "output_tokens": 1}
        }
    })
    assert parse_jsonl_line(line) is None

def test_watcher_reads_existing_file_on_start():
    store = DataStore()
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir)
        proj_dir = projects_dir / "test-project"
        proj_dir.mkdir()
        jsonl_file = proj_dir / "session.jsonl"
        jsonl_file.write_text(VALID_ASSISTANT_LINE + "\n")

        watcher = LogWatcher(store, projects_dir=projects_dir)
        watcher.start()
        time.sleep(0.5)
        watcher.stop()

        totals = store.session_totals()
        assert totals.input_tokens == 100
        assert totals.output_tokens == 75
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_watcher.py -v
```

Expected: `ModuleNotFoundError: No module named 'watcher'`

- [ ] **Step 3: Implement `watcher.py`**

```python
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from pricing import calculate_cost
from store import DataStore, UsageEvent

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def parse_jsonl_line(line: str) -> UsageEvent | None:
    """Parse one JSONL line. Returns UsageEvent or None if not a usage entry."""
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None

    if data.get("type") != "assistant":
        return None

    msg = data.get("message", {})
    usage = msg.get("usage")
    if not usage:
        return None

    ts_str = data.get("timestamp")
    if not ts_str:
        return None

    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        return None

    model = msg.get("model", "claude-sonnet-4-6")
    cost = calculate_cost(model, usage)

    return UsageEvent(
        timestamp=ts,
        model=model,
        input_tokens=usage.get("input_tokens", 0),
        cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
        cache_read_tokens=usage.get("cache_read_input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cost_cents=cost,
    )


class _Handler(FileSystemEventHandler):
    def __init__(self, store: DataStore, offsets: dict) -> None:
        self._store = store
        self._offsets = offsets  # path -> byte offset

    def on_modified(self, event):
        if event.is_directory or not event.src_path.endswith(".jsonl"):
            return
        self._read_new_lines(Path(event.src_path))

    def _read_new_lines(self, path: Path) -> None:
        offset = self._offsets.get(str(path), 0)
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                new_data = f.read()
                self._offsets[str(path)] = offset + len(new_data)
        except (OSError, PermissionError):
            return

        for raw in new_data.decode("utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line:
                continue
            event = parse_jsonl_line(line)
            if event:
                self._store.add(event)


class LogWatcher:
    def __init__(self, store: DataStore, projects_dir: Path = CLAUDE_PROJECTS_DIR) -> None:
        self._store = store
        self._projects_dir = projects_dir
        self._offsets: dict[str, int] = {}
        self._observer = Observer()

    def start(self) -> None:
        if not self._projects_dir.exists():
            raise FileNotFoundError(
                f"Claude projects directory not found: {self._projects_dir}"
            )
        handler = _Handler(self._store, self._offsets)
        # Seed existing files first (so startup shows recent history)
        for jsonl in self._projects_dir.rglob("*.jsonl"):
            handler._read_new_lines(jsonl)

        self._observer.schedule(handler, str(self._projects_dir), recursive=True)
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_watcher.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Run all tests**

```bash
python -m pytest tests/ -v
```

Expected: all 18 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add watcher.py tests/test_watcher.py
git commit -m "feat: add log watcher with watchdog and JSONL parser"
```

---

## Task 4: TUI display

**Files:**
- Create: `claude-monitor/display.py`

No unit tests for this module — it's pure rendering with side effects. Manual verification instead.

- [ ] **Step 1: Implement `display.py`**

```python
import sys
import threading
import tty
import termios
from typing import TYPE_CHECKING

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text
from rich import box

from pricing import PRICING_TABLE
from store import DataStore, Bucket

BARS = " ▁▂▃▄▅▆▇█"


def _render_bar_chart(buckets: list[Bucket], value_fn, label: str, color: str) -> Text:
    values = [value_fn(b) for b in buckets]
    max_val = max(values) if any(v > 0 for v in values) else 1
    text = Text()
    text.append(f"{label}\n", style="bold")
    for v in values:
        level = min(int(v / max_val * (len(BARS) - 1)), len(BARS) - 1)
        text.append(BARS[level], style=color)
    text.append(f"  max={_fmt_value(max_val, label)}\n")
    return text


def _fmt_value(v: float, label: str) -> str:
    if "Cent" in label:
        return f"{v:.3f}¢"
    return f"{int(v):,}"


def _build_layout(store: DataStore, show_pricing: bool) -> Table:
    buckets = store.buckets()
    totals = store.session_totals()

    root = Table.grid(padding=1)
    root.add_column()

    title = Text("◆ Claude Monitor", style="bold cyan")
    root.add_row(title)

    tokens_chart = _render_bar_chart(
        buckets,
        lambda b: b.total_tokens,
        "Tokens / 10s",
        "cyan",
    )
    root.add_row(tokens_chart)

    cents_chart = _render_bar_chart(
        buckets,
        lambda b: b.cost_cents,
        "Cents / 10s",
        "green",
    )
    root.add_row(cents_chart)

    status = Text()
    status.append(f"Session: ", style="dim")
    status.append(f"{totals.total_tokens:,} tokens", style="bold")
    status.append("  |  ", style="dim")
    status.append(f"${totals.cost_cents / 100:.4f}", style="bold green")
    status.append("  |  ", style="dim")
    status.append("[p] pricing  [q] quit", style="dim")
    root.add_row(status)

    if show_pricing:
        pt = Table(title="Pricing (USD per million tokens)", box=box.SIMPLE, show_header=True)
        pt.add_column("Model", style="cyan")
        pt.add_column("Input", justify="right")
        pt.add_column("Cache Read", justify="right")
        pt.add_column("Cache Write", justify="right")
        pt.add_column("Output", justify="right")
        for model, p in PRICING_TABLE.items():
            pt.add_row(
                model,
                f"${p.input_per_m:.2f}",
                f"${p.cache_read_per_m:.2f}",
                f"${p.cache_write_per_m:.2f}",
                f"${p.output_per_m:.2f}",
            )
        root.add_row(pt)

    return root


class Display:
    def __init__(self, store: DataStore) -> None:
        self._store = store
        self._show_pricing = False
        self._quit = threading.Event()
        self._console = Console()

    def _keyboard_thread(self) -> None:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while not self._quit.is_set():
                ch = sys.stdin.read(1)
                if ch in ("q", "Q", "\x03"):  # q or Ctrl+C
                    self._quit.set()
                elif ch in ("p", "P"):
                    self._show_pricing = not self._show_pricing
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def run(self) -> None:
        with Live(
            _build_layout(self._store, self._show_pricing),
            console=self._console,
            refresh_per_second=1,
        ) as live:
            kb = threading.Thread(target=self._keyboard_thread, daemon=True)
            kb.start()
            while not self._quit.is_set():
                live.update(_build_layout(self._store, self._show_pricing))
                self._quit.wait(timeout=1.0)
```

- [ ] **Step 2: Manually verify rendering (smoke test)**

Create a quick smoke test script (do not commit):

```python
# smoke.py
from datetime import datetime, timezone
from store import DataStore, UsageEvent
from display import Display

store = DataStore()
# Inject fake events
from datetime import timedelta
now = datetime.now(timezone.utc)
for i in range(10):
    store.add(UsageEvent(
        timestamp=now - timedelta(seconds=i * 10),
        model="claude-sonnet-4-6",
        input_tokens=1000 * (i + 1),
        cache_creation_tokens=0,
        cache_read_tokens=500,
        output_tokens=200,
        cost_cents=0.5 * (i + 1),
    ))

d = Display(store)
d.run()
```

Run: `python smoke.py`

Expected: TUI renders with two bar charts, press `p` to see pricing table, press `q` to quit.

- [ ] **Step 3: Delete smoke.py**

```bash
rm smoke.py
```

- [ ] **Step 4: Commit**

```bash
git add display.py
git commit -m "feat: add rich TUI with bar charts and pricing panel"
```

---

## Task 5: Entry point and end-to-end wiring

**Files:**
- Create: `claude-monitor/main.py`

- [ ] **Step 1: Implement `main.py`**

```python
import sys
from pathlib import Path

from store import DataStore
from watcher import LogWatcher, CLAUDE_PROJECTS_DIR
from display import Display


def main() -> None:
    projects_dir = CLAUDE_PROJECTS_DIR
    if not projects_dir.exists():
        print(f"Error: Claude projects directory not found: {projects_dir}", file=sys.stderr)
        print("Is Claude Code installed?", file=sys.stderr)
        sys.exit(1)

    store = DataStore()

    watcher = LogWatcher(store, projects_dir=projects_dir)
    try:
        watcher.start()
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        display = Display(store)
        display.run()
    finally:
        watcher.stop()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the app end-to-end**

```bash
cd claude-monitor
python main.py
```

Expected: TUI launches, shows any recent usage from `~/.claude/projects/`, updates live as Claude Code runs in another terminal.

- [ ] **Step 3: Run full test suite one final time**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 4: Final commit**

```bash
git add main.py
git commit -m "feat: wire entry point — claude-monitor is ready to run"
```

---

## Done

Run with:
```bash
cd claude-monitor
python main.py
```

Press `p` to toggle pricing, `q` to quit.
