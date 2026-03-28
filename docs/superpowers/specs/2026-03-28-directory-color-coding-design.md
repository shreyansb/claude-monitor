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

The JSONL files live at `~/.claude/projects/<encoded-path>/<session>.jsonl`. The `<encoded-path>` encodes the full absolute path by replacing each `/` with `-` (e.g. `/Users/shreyans/Code/puck/claude-monitor` becomes `-Users-shreyans-Code-puck-claude-monitor`). Because directory names themselves can contain hyphens, the separator and name characters are indistinguishable in the encoded form.

**Extraction strategy (best-effort heuristic):**
1. Strip the encoded home directory prefix (reconstructed by replacing `/` with `-` in `str(Path.home())`)
2. Of the remaining hyphen-delimited parts, take the last 2 and join with `-`

This correctly handles single-level-hyphenated names like `claude-monitor`, `content-calendar`, `maven-mcp`. For projects with more than one hyphen in the final path component(s), the label is a truncated approximation — acceptable for a visual display aid.

```python
def _dir_name(path: Path) -> str:
    encoded = path.parent.name  # e.g. '-Users-shreyans-Code-puck-claude-monitor'
    home_prefix = str(Path.home()).replace('/', '-')  # '-Users-shreyans'
    if encoded.startswith(home_prefix):
        relative = encoded[len(home_prefix):].lstrip('-')  # 'Code-puck-claude-monitor'
    else:
        relative = encoded.lstrip('-')
    parts = [p for p in relative.split('-') if p]
    if not parts:
        return encoded
    return '-'.join(parts[-2:]) if len(parts) >= 2 else parts[-1]
```

**Changes to `watcher.py`:**
- Add `_dir_name(path: Path) -> str` module-level function (above)
- `parse_jsonl_line(line, directory)` — gains a `directory: str` parameter
- `_Handler._read_new_lines(path)` — calls `_dir_name(path)` and passes result to `parse_jsonl_line`
- `preload_recent` — calls `_dir_name(jsonl)` for each `jsonl` path object and passes the result to `parse_jsonl_line`:

```python
# in preload_recent, replace:
#   event = parse_jsonl_line(line)
# with:
    directory = _dir_name(jsonl)
    event = parse_jsonl_line(line, directory)
```

### `store.py` — per-directory tracking in buckets and lifetime totals

**`UsageEvent`** (defined in `store.py`): gains `directory: str` field. Add a `total_tokens` property consistent with `Bucket.total_tokens`:
```python
@property
def total_tokens(self) -> int:
    return (
        self.input_tokens
        + self.cache_creation_tokens
        + self.cache_read_tokens
        + self.output_tokens
    )
```

**Changes to `Bucket`:**
- Add `by_dir: dict[str, int] = field(default_factory=dict)` — maps directory name → token contribution for that bucket. Because `Bucket` is a `@dataclass`, a mutable default requires `dataclasses.field(default_factory=dict)` — not a bare `= {}`.
- `Bucket.add(event)` — after updating the four individual token fields as before, also do `self.by_dir[event.directory] = self.by_dir.get(event.directory, 0) + event.total_tokens`. `event.total_tokens` uses the new property above and equals the sum of the four fields; `Bucket.total_tokens` is a `@property` over those same four fields. The two are always consistent.

**Changes to `DataStore`:**
- In `__init__`, add two plain instance variables (plain class, not dataclass — use regular assignment):
  ```python
  self._lifetime_by_dir: dict[str, int] = {}
  self._dir_order: list[str] = []
  ```
  Both are protected under the existing `self._lock` in all reads and writes.
- `add(event)` — under `self._lock`, after the existing bucket update: if `event.directory` not in `_lifetime_by_dir`, append to `_dir_order`; then `self._lifetime_by_dir[event.directory] = self._lifetime_by_dir.get(event.directory, 0) + event.total_tokens`
- New method `directories() -> list[str]` — acquires `self._lock`, returns `list(self._dir_order)`
- New method `lifetime_by_dir() -> dict[str, int]` — acquires `self._lock`, returns `dict(self._lifetime_by_dir)`

**Dead code removal (coordinated):**
`lifetime_totals()` is currently called from `display.py`'s `_build_layout`. `session_totals()` is not called from `display.py` (it was already dead code there). After the display refactor removes the `lifetime_totals()` call, both methods can be removed from `DataStore`. **Remove the calls from `display.py` first**, then remove the methods from `store.py`.

### `display.py` — stacked bars + right legend panel

**Color palette and assignment:**
```python
PALETTE = ["cyan", "yellow", "green", "magenta", "blue", "red", "bright_cyan", "bright_yellow"]
```
In `_build_layout`, build the color map once before rendering:
```python
dirs = store.directories()
dir_colors = {d: PALETTE[i % len(PALETTE)] for i, d in enumerate(dirs)}
```

**Refactored `_render_area_chart` — returns `list[Text]` instead of `Text`:**

Change the return type to `list[Text]` (one `Text` per line). This avoids the need to split a styled `Text` later and makes the side-by-side merge straightforward.

Updated signature:
```python
def _render_area_chart(buckets, label, dirs, dir_colors, height) -> list[Text]
```

The function returns exactly `height + 2` items:
1. Index 0: label line (`Text(label, style="bold")`)
2. Indices 1 to `height`: data rows (row `height-1` down to `0`, top to bottom)
3. Index `height + 1`: x-axis line

The Y-axis gutter (5-character left margin with `max_val` / `"0"` / spaces) is retained unchanged — it is prepended to each data row `Text` as before.

**Stacked bar rendering per column:**

For each column (bucket `b`), precompute cumulative heights from the bottom:
```python
cum = 0.0
seg_tops = []  # list of (cumulative_height, dir)
for d in dirs:
    cum += b.by_dir.get(d, 0) / max_val * height
    seg_tops.append((cum, d))
# seg_tops[-1][0] == b.total_tokens / max_val * height
```

For cell at row `r` (0 = bottom of chart, `height-1` = top):
- Find the lowest `seg_top` where `seg_top > r` — that directory "owns" this cell
- If `seg_top >= r + 1`: full block `█` in that directory's color
- If `r < seg_top < r + 1`: partial block `BLOCKS[max(1, int((seg_top - r) * 8))]` in that directory's color
- If no `seg_top > r` (cell above all segments): space

The partial block at a segment boundary always uses the color of the directory whose segment ends in that cell (the one whose `seg_top` is the fractional boundary). This means the topmost filled character of each directory segment uses that directory's color.

**Legend lines — `list[Text]`, bottom-aligned:**

Build a list of exactly `height + 2` `Text` objects for the legend panel. The legend content has `len(dirs) + 2` lines (N directory rows + separator + total). Separator and total are always at the bottom of the panel.

```
blank × (height + 2 - len(dirs) - 2)   ← top padding (may be 0)
■ claude-monitor   42k / 142k           ← one per directory, in dirs order
■ puck             18k /  38k
──────────────────────────────
  Total             68k / 192k
```

- `■` colored with `dir_colors[dir]`
- Left number: `sum(b.by_dir.get(dir, 0) for b in store.buckets())` (5-min window)
- Right number: `lifetime_by_dir[dir]`
- Total row: sum across all dirs in both columns
- When `dirs` is empty (no events yet): just separator + `Total  0 / 0` (2 content lines), padded to `height + 2` blank lines above

If `len(dirs) + 2 > height + 2` (many directories): legend overflows below — accept this as a known edge case.

The legend iterates `dirs` (all directories ever seen, ordered by first appearance). A directory with 0 tokens in the 5-min window but non-zero session total still appears (with `0 / session_total`). Since `dirs == list(lifetime_by_dir.keys())` (both populated in `DataStore.add`), iterating `dirs` covers all directories that have any session total.

**Layout — build merged lines directly:**

```python
chart_lines = _render_area_chart(buckets, "Tokens / 10s", dirs, dir_colors, height)
legend_lines = _build_legend(dirs, dir_colors, buckets, lifetime_by_dir, height)
# both are list[Text] of length height + 2

merged = Text()
for chart_line, legend_line in zip(chart_lines, legend_lines):
    merged.append_text(chart_line)
    merged.append("  ")
    merged.append_text(legend_line)
    merged.append("\n")
```

Extract legend construction into a helper `_build_legend(dirs, dir_colors, buckets, lifetime_by_dir, height) -> list[Text]` for testability.

**Status line simplification:**
Remove `store.lifetime_totals()` call. The status line becomes:
```
[q] quit  [+/-] chart height
```

---

## Data Flow

```
JSONL file path (both _Handler._read_new_lines and preload_recent)
    → _dir_name(path)                  → directory: str
    → parse_jsonl_line(line, directory)
    → UsageEvent(... directory=...)
    → DataStore.add(event)  [under self._lock]
        → Bucket.by_dir[directory] += event.total_tokens
        → _lifetime_by_dir[directory] += event.total_tokens
        → _dir_order.append(directory) if first-seen
    → display render loop (every 1s)
        → store.buckets()              → per-bucket by_dir for stacked bars
        → store.directories()          → ordered dir list → dir_colors dict
        → store.lifetime_by_dir()      → legend right column (session totals)
```

---

## Error Handling

- `_dir_name` falls back to the raw encoded segment name if home prefix is not found or parts list is empty
- Directories with zero tokens in the 5-min window but non-zero session total still appear in the legend
- More than 8 directories: `PALETTE` wraps via modulo (colors repeat); unlikely in practice
- More than `height` directories: legend overflows below chart; accepted

---

## File Changes Summary

| File | Change |
|------|--------|
| `store.py` | Add `directory: str` and `total_tokens` property to `UsageEvent`; add `by_dir` (with `field(default_factory=dict)`) to `Bucket` and update `Bucket.add`; add `_lifetime_by_dir`, `_dir_order` to `DataStore.__init__` (both under `self._lock`); update `DataStore.add`; add `directories()` and `lifetime_by_dir()` methods; remove `lifetime_totals()` and `session_totals()` after display is updated |
| `watcher.py` | Add `_dir_name(path)`; add `directory` param to `parse_jsonl_line`; update both `_Handler._read_new_lines` and `preload_recent` to extract and pass directory |
| `display.py` | Remove `lifetime_totals()` call first; add `PALETTE`; build `dir_colors` in `_build_layout`; change `_render_area_chart` to return `list[Text]` with stacked rendering; add `_build_legend` helper; merge chart and legend line-by-line; simplify status line |

**Recommended change order:** `watcher.py` → `store.py` (add all new fields/methods, keep `lifetime_totals` temporarily) → `display.py` (full refactor, remove `lifetime_totals` call) → `store.py` (remove now-dead `lifetime_totals` and `session_totals`).
