# Claude Monitor — Design Spec
**Date:** 2026-03-26

## Overview

A terminal app that displays a live rolling graph of Claude Code token usage and estimated cost, sourced entirely from local `~/.claude/projects/**/*.jsonl` logs. No network calls.

## Goals

- Show tokens/10s and cents/10s as bar charts over a rolling 5-minute window
- Update in real time as Claude Code writes new log entries
- Show running session totals (tokens + cost)
- Press `p` to toggle a pricing panel; `q` / Ctrl+C to quit

## Non-Goals

- Anthropic API integration
- Historical reporting beyond the current 5-minute window
- Multi-session aggregation across separate terminal windows

---

## Architecture

Three modules wired together in `main.py`:

### `watcher.py` — Log Watcher

Uses `watchdog` (`FileSystemEventHandler`) to monitor `~/.claude/projects/` recursively for `.jsonl` file modifications. On each event:

1. Seek to the last-read byte offset for that file (tracked in a dict)
2. Read new lines only
3. Parse each line as JSON; skip non-assistant entries or entries without `message.usage`
4. Extract: `timestamp`, `message.model`, and usage fields
5. Push a `UsageEvent` dataclass onto the shared `DataStore`

Runs in a background thread. Thread-safe hand-off to `DataStore` via a `threading.Lock`.

### `store.py` — Data Store

In-memory ring buffer of 30 buckets, each representing a 10-second interval (covers 5 minutes total).

**Bucket contents:**
- `input_tokens` (uncached)
- `cache_creation_tokens`
- `cache_read_tokens`
- `output_tokens`
- `cost_cents` (computed on insert)

**Key methods:**
- `add(event: UsageEvent)` — places the event in the correct bucket based on timestamp; discards events older than 5 minutes
- `buckets() -> list[Bucket]` — returns all 30 buckets in chronological order (oldest → newest), with empty buckets for gaps
- `session_totals() -> Totals` — sum across all buckets

Buckets advance automatically: when `now` moves past the current bucket boundary, old buckets are evicted.

### `pricing.py` — Cost Calculator

Hardcoded per-model pricing table (USD per million tokens):

| Model | Input | Cache Read | Cache Write | Output |
|-------|-------|------------|-------------|--------|
| claude-sonnet-4-6 | $3.00 | $0.30 | $3.75 | $15.00 |
| claude-opus-4-6 | $15.00 | $1.50 | $18.75 | $75.00 |
| claude-haiku-4-5 | $0.80 | $0.08 | $1.00 | $4.00 |
| _default_ | $3.00 | $0.30 | $3.75 | $15.00 |

`calculate_cost(model, usage) -> float` returns cost in cents.

### `display.py` — TUI

Uses `rich.live.Live` with a 1-second refresh. Layout:

```
┌─ Claude Monitor ──────────────────────────────────┐
│  Tokens/10s                                        │
│  ████░░░░░░░░░░░░░░░░░░░░░░░░░░  ← 30 bars        │
│                                                    │
│  Cents/10s                                         │
│  ██░░░░░░░░░░░░░░░░░░░░░░░░░░░░  ← 30 bars        │
│                                                    │
│  Session: 142,300 tokens  |  $0.43  |  [p] pricing │
└────────────────────────────────────────────────────┘
```

Bar height scales to the max value across all visible buckets. Each bar is a single `rich` character column using block elements (`▁▂▃▄▅▆▇█`).

**Keyboard input** is handled in a background thread reading `sys.stdin` (raw mode via `tty`/`termios`):
- `p` — toggles pricing panel overlay (table of model rates)
- `q` — exits

### `main.py` — Entry Point

```
store = DataStore()
watcher = LogWatcher(store)
watcher.start()          # background thread
display = Display(store)
display.run()            # blocks; handles keyboard + rich Live loop
watcher.stop()
```

---

## File Layout

```
claude-monitor/
  main.py
  watcher.py
  store.py
  pricing.py
  display.py
  requirements.txt       # watchdog, rich
```

---

## Error Handling

- Malformed JSONL lines: silently skip
- Unknown model: fall back to Sonnet pricing, no crash
- `~/.claude/projects/` missing: print error and exit gracefully
- File permission errors on individual files: skip that file, continue watching others

---

## Dependencies

- `rich` — TUI rendering
- `watchdog` — filesystem events
- Python 3.9+ (stdlib only beyond those two)
