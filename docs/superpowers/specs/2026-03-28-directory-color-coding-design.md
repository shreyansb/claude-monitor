# Directory Color-Coding — Design Spec
**Date:** 2026-03-28

## Overview

Color-code the token usage chart by the working directory of each Claude session, so you can see at a glance which projects are consuming tokens. Each bar in the rolling chart becomes a stacked column with per-directory color segments. A legend panel to the right of the chart shows directory names with their 5-minute and session totals.

## Goals

- Stacked bar chart: each bar column is vertically split by directory, proportional to token contribution
- Right-side legend: directory name + 5-min tokens / session tokens per row, total at bottom
- Stable color assignment: directories keep the same color throughout a session
- No new dependencies

## Non-Goals

- Persisting directory→color assignments across sessions
- Filtering by directory
- More than one chart (one stacked chart covers all directories)

---

## Architecture

### `watcher.py` — extract directory name from path

The JSONL files live at `~/.claude/projects/<encoded-path>/<session>.jsonl`. The `<encoded-path>` uses `-` as a path separator (e.g. `-Users-shreyans-Code-puck-claude-monitor`). The directory name is the last `-`-delimited component of `path.parent.name`.

**Changes:**
- `parse_jsonl_line(line, directory)` — gains a `directory: str` parameter
- `_Handler._read_new_lines(path)` — extracts directory name and passes it to `parse_jsonl_line`
- `preload_recent` — same extraction when scanning existing files
- `UsageEvent` — gains `directory: str` field

Extraction logic:
```python
def _dir_name(path: Path) -> str:
    parts = [p for p in path.parent.name.split("-") if p]
    return parts[-1] if parts else path.parent.name
```

### `store.py` — per-directory tracking in buckets and lifetime totals

**Changes to `Bucket`:**
- Add `by_dir: dict[str, int]` — maps directory name → total tokens for that bucket
- `Bucket.add(event)` updates `by_dir[event.directory]` in addition to the existing token fields

**Changes to `DataStore`:**
- Add `_lifetime_by_dir: dict[str, int]` — session totals per directory
- Add `_dir_order: list[str]` — directories in first-seen order (for stable color assignment)
- `add(event)` updates both `_lifetime_by_dir` and `_dir_order`
- New method `directories() -> list[str]` — returns `_dir_order` (copy)
- New method `lifetime_by_dir() -> dict[str, int]` — returns copy of `_lifetime_by_dir`

### `display.py` — stacked bars + right legend panel

**Color palette:**
A fixed list of 6–8 Rich color names cycles through directories in first-seen order:
```python
PALETTE = ["cyan", "yellow", "green", "magenta", "blue", "red", "bright_cyan", "bright_yellow"]
```

**Stacked bar rendering (`_render_area_chart`):**
Instead of filling each column uniformly, iterate directories bottom-up. For each column (bucket), compute each directory's proportional height, then draw segments using block characters in the directory's color. The total bar height is still determined by `bucket.total_tokens / max_val * height`.

**Layout — side-by-side chart + legend:**
`_build_layout` constructs the chart `Text` as before, then builds a legend `Text` of the same height, and combines them horizontally. Rich's `Columns` widget handles side-by-side placement, or a manual line-by-line concatenation if more control is needed.

Legend format (right panel):
```
■ claude-monitor   42k / 142k
■ puck             18k /  38k
■ my-app            8k /  12k
──────────────────────────────
  Total             68k / 192k
```
- Left number: sum of `bucket.by_dir[dir]` across the 30-bucket window
- Right number: `lifetime_by_dir()[dir]`
- Total row: sum across all directories (matches existing session/lifetime totals)

**Status line simplification:**
The existing "Session: X tokens | Last 5m: X tokens" line is removed. That information now lives in the legend. The status line becomes just `[q] quit  [+/-] chart height`.

---

## Data Flow

```
JSONL file path
    → _dir_name(path)          → directory: str
    → parse_jsonl_line(line, directory)
    → UsageEvent(... directory=...)
    → DataStore.add(event)
        → Bucket.by_dir[directory] += tokens
        → _lifetime_by_dir[directory] += tokens
    → display render loop
        → store.buckets()          → stacked bar chart
        → store.directories()      → color assignments
        → store.lifetime_by_dir()  → legend right column
```

---

## Error Handling

- Directory name extraction falls back to the full encoded segment if splitting produces no parts
- Directories with zero tokens in the 5-min window still appear in the legend if they have session totals > 0
- More than 8 directories: palette wraps (colors repeat); unlikely in practice

---

## File Changes Summary

| File | Change |
|------|--------|
| `store.py` | Add `directory` field to `UsageEvent`; add `by_dir` to `Bucket`; add `_lifetime_by_dir` + `_dir_order` to `DataStore`; add `directories()` and `lifetime_by_dir()` methods |
| `watcher.py` | Add `_dir_name(path)`; pass `directory` to `parse_jsonl_line`; update `preload_recent` |
| `display.py` | Add `PALETTE`; rewrite bar rendering to stack by directory; add legend panel; simplify status line |
