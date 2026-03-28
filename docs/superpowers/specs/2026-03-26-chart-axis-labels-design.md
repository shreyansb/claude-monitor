# Chart Axis Labels Design

**Date:** 2026-03-26
**Status:** Approved

## Problem

The token chart in `display.py` has no axis context — no indication of time range on x or token scale on y. The only hint is a `max=N` label in the header line.

## Goal

Add minimal axis labels to the existing hand-rolled Rich chart without new dependencies or structural changes.

## Approach

Modify `_render_area_chart` in `display.py` only.

### Fixed dimensions

`store.buckets()` always returns exactly 30 buckets (defined by `NUM_BUCKETS = 30` in `store.py`), each covering 10 seconds (`BUCKET_SECONDS = 10`), for a total window of 5 minutes. All width calculations are based on this fixed 30-column chart width.

### Y-axis gutter (left side)

- 5 characters wide, right-aligned, padded with spaces
- Top chart row only: formatted `max` value
- Bottom chart row only: `0`
- All other rows: 5 spaces

**Value formatting** (must fit in 5 chars, no overflow):
- `value < 1,000` → plain integer (e.g. `847`)
- `1,000 ≤ value < 1,000,000` → `Nk` where N = `value // 1000` (e.g. `142,800 → 142k`; max is `999k`)
- `value ≥ 1,000,000` → `NM` where N = `value // 1,000,000` (e.g. `2,100,000 → 2M`)

This guarantees formatted values are at most 4 characters, always fitting in the 5-char gutter.

### Chart body

Unchanged — 30 Unicode block columns using existing `BLOCKS` characters.

### Header line

The existing label line (e.g. `Tokens / 10s`) is retained. Only the `  max=142,800` suffix is removed, since that information now appears in the y-axis gutter.

### X-axis label row

- One line appended after the last chart row
- Total width = 35 characters (5-char gutter + 30-char chart)
- `-5m` appears at position 5 (after the 5-char gutter of spaces)
- `now` appears right-aligned at position 32 (total_width - 3)
- Remaining positions filled with spaces

Concrete example (35 chars total):
```
"     -5m                         now"
  ^^^^^   = 5-char gutter (spaces)
       ^^^  = "-5m"
          ^^^^^^^^^^^^^^^^^^^^^^^^^  = spaces
                                  ^^^ = "now"
```

## Non-goals

- No drawn axis lines (`─`, `│`)
- No mid-point y-tick (e.g. 50%)
- No terminal charting library
- No changes to `_build_layout`, `Display`, or any other file

## Files Changed

- `display.py` — `_render_area_chart` function only
