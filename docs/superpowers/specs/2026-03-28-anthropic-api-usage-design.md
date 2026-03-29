# Anthropic API Usage Display — Design Spec

**Date:** 2026-03-28

## Overview

Optional feature that fetches real cost data from the Anthropic Admin API and displays it as a new row below the existing Total line in the legend. Only visible when an Admin API key is configured and the month-to-date cost is > $0.

## Data Fetching (`anthropic_usage.py` — new file)

### `AnthropicUsage` class

Responsibilities:
- Stores the API key path: `~/.claude/monitor_key`
- Loads persisted key on init
- Fetches cost data from `GET https://api.anthropic.com/v1/organizations/cost_report` on a 60-second polling loop
- Exposes `cost_month_cents: float` and `cost_session_delta_cents: float`

### Fetch logic

- Parameters: `bucket_width=1d`, `starting_at=<first of current month>T00:00:00Z`, `ending_at=<now>`
- Handles pagination (`has_more` / `next_page`)
- Sums all returned cost buckets (tokens + web_search + code_execution fields)
- On first successful fetch, records `_session_start_cents`; delta = current − start
- On error (network, 401, etc.): retains last-known values, does not crash; values stay 0 if never fetched successfully

### Lifecycle

- `start()`: launches background daemon thread; fetches immediately, then sleeps 60s between fetches
- `stop()`: sets shutdown event, thread exits cleanly
- `trigger_fetch()`: signals thread to fetch immediately (called when key is saved)
- `set_key(key: str)`: writes key to `~/.claude/monitor_key`, resets session start, calls `trigger_fetch()`

### Values exposed

- `cost_month_cents` → 0.0 if no key, no fetch, or API error
- `cost_session_delta_cents` → 0.0 until first successful fetch
- `has_key` → bool, True if key file exists and non-empty

## Key Input UI (`display.py` changes)

### State

Two globals protected by a lock:
- `_api_input_active: bool` — whether the input field is visible
- `_api_input_buffer: str` — characters typed so far

### Keyboard handling

- `[a]`: toggles input mode
  - Opening: sets `_api_input_active = True`, clears buffer
  - Closing (second `[a]` or ESC): sets `_api_input_active = False`; if closing via `[a]` and buffer is non-empty, calls `usage.set_key(buffer)`
- While active: printable ASCII appended to buffer; backspace removes last char
- ESC: cancels without saving

### Status bar rendering

The status bar gains a third segment:
- Inactive: `[a] Anthropic key`
- Active: `[a] cancel  ▎<masked>` where `<masked>` = `*` × `len(buffer)`

## Legend Row (`display.py` changes)

### Placement

After the Total row in `_build_legend`, if `usage.cost_month_cents > 0`:

```
  Anthropic  $12.34   +$1.23
```

- Label: `Anthropic`, left-aligned in name column (no color square prefix, 2-space indent instead)
- First number column: month-to-date cost (`cost_month_cents / 100`)
- Second number column: session delta (`cost_session_delta_cents / 100`), prefixed with `+`
- Both columns right-aligned to `col_w` (same width as existing columns)

### `_fmt_cost(cents: float) -> str`

- `< 1`: `"$0.00"`
- `< 100_000`: `"${:.2f}".format(cents / 100)` e.g. `"$12.34"`
- `>= 100_000`: `"${:.2f}k".format(cents / 100_000)` e.g. `"$1.23k"`

### Passing `AnthropicUsage` through

- `AnthropicUsage` instance created in `main.py`, started before display, stopped in `finally`
- Passed to `Display.__init__`, stored as `self._usage`
- Passed from `_build_layout` into `_build_legend`

## Files Changed

| File | Change |
|------|--------|
| `anthropic_usage.py` | New — polling, key storage, cost state |
| `display.py` | Key input UI + legend row |
| `main.py` | Instantiate and wire `AnthropicUsage` |
| `requirements.txt` | Add `requests` (or use stdlib `urllib`) |

## Out of Scope

- Displaying per-workspace or per-model cost breakdown
- Historical session tracking across runs
- Any UI for viewing/deleting the stored key beyond re-entering it
