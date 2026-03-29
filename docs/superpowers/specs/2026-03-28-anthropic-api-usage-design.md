# Anthropic API Usage Display — Design Spec

**Date:** 2026-03-28

## Overview

Optional feature that fetches real cost data from the Anthropic Admin API and displays it as a new row below the existing Total line in the legend. Only visible when an Admin API key is configured and the month-to-date cost is >= 1 cent.

## Data Fetching (`anthropic_usage.py` — new file)

### `AnthropicUsage` class

Responsibilities:
- Stores the API key path: `~/.claude/monitor_key` (written with mode `0o600`)
- Loads persisted key on init
- Fetches cost data from `GET https://api.anthropic.com/v1/organizations/cost_report` on a 60-second polling loop
- Exposes `cost_month_cents: float` and `cost_session_delta_cents: float`

### Fetch logic

- Parameters: `bucket_width=1d`, `starting_at=<first of current month>T00:00:00Z`, `ending_at=<now>`
- Handles pagination (`has_more` / `next_page` token)
- Response shape: `data[].results[].amount` (string, decimal cents e.g. `"123.45"` = $1.23); sum all `amount` values across all buckets and result entries
- HTTP via `urllib.request` (stdlib, no new dependency); headers: `x-api-key`, `anthropic-version: 2023-06-01`
- On first successful fetch, records `_session_start_cents`; delta = current − start
- On error (network, 401, etc.): retains last-known values, does not crash; values stay 0.0 if never fetched successfully

### Lifecycle

- `start()`: launches background daemon thread; fetches immediately, then calls `_fetch_event.wait(timeout=60)` in loop — wakes on timeout or explicit signal
- `stop()`: sets `_shutdown` event; thread checks it after each wait and exits
- `trigger_fetch()`: calls `_fetch_event.set()` to wake the poll loop immediately
- `set_key(key: str)`: acquires `_lock`, writes key to `~/.claude/monitor_key` (mode `0o600`), resets `_session_start_cents = None`, releases lock, then calls `trigger_fetch()`
- All reads/writes of `cost_month_cents`, `cost_session_delta_cents`, `_session_start_cents`, and the key are protected by `_lock`

### Values exposed (all read under `_lock`)

- `cost_month_cents: float` → 0.0 if no key, no successful fetch, or error
- `cost_session_delta_cents: float` → 0.0 until first successful fetch
- `has_key: bool` → True if key file exists and non-empty

## Key Input UI (`display.py` changes)

### State

Two globals protected by `_api_input_lock` (a `threading.Lock`):
- `_api_input_active: bool` — whether the input field is visible
- `_api_input_buffer: str` — characters typed so far

### Keyboard handling

- `[a]`: toggles input mode
  - Opening: sets `_api_input_active = True`, clears buffer
  - Closing via second `[a]`: sets `_api_input_active = False`; if buffer is non-empty, calls `usage.set_key(buffer)`
  - Closing via ESC (`\x1b`): sets `_api_input_active = False`, discards buffer without saving; consume and discard subsequent bytes in the ESC sequence (read with `VMIN=0` / `VTIME=1` to drain without blocking, discard any `[`, `A`–`Z` that follow within the same sequence)
- While active: printable ASCII (`0x20`–`0x7e`) appended to buffer; `\x7f` / `\x08` (backspace/DEL) removes last char; all other bytes ignored

### Status bar rendering

The status bar gains a third segment:
- Inactive: `[a] Anthropic key`
- Active: `[a] cancel  ▎<masked>` where `<masked>` = `*` × `len(buffer)`

## Legend Row (`display.py` changes)

### Placement

After the Total row in `_build_legend`, if `usage.cost_month_cents >= 1`:

```
  Anthropic   $12.34  +$1.23
```

- Label: `Anthropic`, left-aligned in name column with 2-space indent (no color square)
- First column: month-to-date cost
- Second column: session delta, prefixed with `+`
- Month column uses `col_w = 6` (same as existing columns); delta column uses `delta_col_w = 7` to accommodate the `+` prefix without overflow
- Adding this row increases the legend to `height + 3` lines; `_build_layout` must add one blank `Text("")` to the chart side to keep the `zip` aligned

### `_fmt_cost(cents: float) -> str`

- `< 1`: `"$0.00"` (not shown in practice due to >= 1 guard)
- `< 100_000`: `"${:.2f}".format(cents / 100)` e.g. `"$12.34"` (up to $999.99)
- `>= 100_000`: `"${:.2f}k".format(cents / 100_000)` e.g. `"$1.23k"` meaning $1,230 (consistent with `_fmt_val`'s k-suffix convention)

### Passing `AnthropicUsage` through

- `AnthropicUsage` instance created in `main.py`, started before display, stopped in `finally`
- Passed to `Display.__init__`, stored as `self._usage`
- `_build_layout(store, usage)` — `usage` added as explicit parameter (module-level function, not a method), forwarded to `_build_legend(..., usage)`

## Testing

New tests in `tests/test_anthropic_usage.py`:
- `test_fmt_cost_ranges`: verifies `_fmt_cost` at sub-cent, dollar, and k boundaries
- `test_key_file_written_and_read`: writes a key via `set_key()`, verifies file exists and is readable, verifies `has_key` is True
- `test_no_key_returns_zero`: `AnthropicUsage` with no key file has `cost_month_cents == 0.0`
- `test_fetch_error_retains_last_known`: mock `urllib.request.urlopen` via `unittest.mock.patch` returning 401 after a successful fetch; verify values remain at last-known, not reset to 0

## Files Changed

| File | Change |
|------|--------|
| `anthropic_usage.py` | New — polling, key storage, cost state |
| `display.py` | Key input UI + legend row + `_build_layout` signature |
| `main.py` | Instantiate and wire `AnthropicUsage` |
| `tests/test_anthropic_usage.py` | New — unit tests for new module |

## Out of Scope

- Displaying per-workspace or per-model cost breakdown
- Historical session tracking across runs
- Any UI for viewing/deleting the stored key beyond re-entering it
