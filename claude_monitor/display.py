import os
import sys
import threading
import termios
from datetime import date, timedelta

from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

from .store import DataStore, Bucket, DayTotals, BUCKET_SECONDS, NUM_BUCKETS

_chart_height = 5
_chart_height_lock = threading.Lock()
_view_mode = 0  # 0=live, 1=monthly total, 2=monthly breakdown
_view_mode_lock = threading.Lock()
_page_offset = 0  # 0 = most recent 30 days, 1 = 30-59 days ago, etc.
_page_offset_lock = threading.Lock()
BLOCKS = " ▁▂▃▄▅▆▇█"

PALETTE = ["cyan", "yellow", "green", "magenta", "blue", "red", "bright_cyan", "bright_yellow"]


def _fmt_val(v: int) -> str:
    if v >= 100_000_000:
        return f"{v // 1_000_000}M"             # "100M"–"999M"   ≤ 5 chars
    if v >= 10_000_000:
        return f"{v // 100_000 / 10:.1f}M"      # "10.0M"–"99.9M" = 5 chars (floor)
    if v >= 1_000_000:
        return f"{v // 10_000 / 100:.2f}M"      # "1.00M"–"9.99M" = 5 chars (floor)
    if v >= 1_000:
        return f"{v // 1_000}k"                 # "1k"–"999k"     ≤ 4 chars
    return str(v)


def _render_area_chart(buckets: list[Bucket], label: str, dirs: list[str], dir_colors: dict[str, str], height: int) -> list[Text]:
    values = [b.total_tokens for b in buckets]
    max_val = max(values) if any(v > 0 for v in values) else 1
    n = len(values)
    gutter = 5

    lines: list[Text] = []

    # Index 0: label line, padded to gutter + n width
    lines.append(Text(f"{label:<{gutter + n}}", style="bold"))

    # Indices 1 to height: data rows (row height-1 down to 0, top to bottom)
    for row in range(height - 1, -1, -1):
        line = Text()
        if row == height - 1:
            line.append(f"{_fmt_val(max_val):>{gutter}}", style="dim")
        elif row == 0:
            line.append(f"{'0':>{gutter}}", style="dim")
        else:
            line.append(" " * gutter)

        if not dirs:
            # Fallback: single-color rendering using "cyan"
            for v in values:
                bar_height = v / max_val * height
                if bar_height >= row + 1:
                    line.append("█", style="cyan")
                elif bar_height > row:
                    frac = bar_height - row
                    idx = max(1, int(frac * 8))
                    line.append(BLOCKS[idx], style="cyan")
                else:
                    line.append(" ")
        else:
            for b in buckets:
                # Precompute cumulative heights from bottom for this column
                cum = 0.0
                seg_tops: list[tuple[float, str]] = []
                for d in dirs:
                    cum += b.by_dir.get(d, 0) / max_val * height
                    seg_tops.append((cum, d))

                # Find first segment whose top exceeds row
                char_appended = False
                for i, (seg_top, d) in enumerate(seg_tops):
                    if seg_top > row:
                        color = dir_colors[d]
                        if seg_top >= row + 1:
                            line.append("█", style=color)
                        else:
                            # partial block — find the next segment above this boundary
                            idx = max(1, int((seg_top - row) * 8))
                            # find the next segment with actual tokens above this boundary
                            upper_color = None
                            for j in range(i + 1, len(seg_tops)):
                                if seg_tops[j][0] > seg_tops[i][0]:
                                    upper_color = dir_colors[seg_tops[j][1]]
                                    break
                            if upper_color:
                                line.append(BLOCKS[idx], style=f"{color} on {upper_color}")
                            else:
                                line.append(BLOCKS[idx], style=color)
                        char_appended = True
                        break
                if not char_appended:
                    line.append(" ")

        lines.append(line)

    # Index height+1: x-axis line — place minute markers counting back from right
    buckets_per_min = 60 // BUCKET_SECONDS  # 6 buckets = 1 minute
    total_mins = n // buckets_per_min
    if total_mins <= 10:
        step = 1
    elif total_mins <= 30:
        step = 5
    else:
        step = 10

    axis_chars = list(" " * n)
    for min_ago in range(step, total_mins + 1, step):
        pos = n - min_ago * buckets_per_min
        label = f"-{min_ago}m"
        if 0 <= pos and pos + len(label) <= n:
            for i, c in enumerate(label):
                axis_chars[pos + i] = c

    lines.append(Text(" " * gutter + "".join(axis_chars), style="dim"))

    return lines


def _date_range_label(start: date, end: date) -> str:
    """Human-readable label for a date range."""
    if start.year == end.year and start.month == end.month:
        return f"{start.strftime('%b')} {start.day}–{end.day}, {end.year}"
    if start.year == end.year:
        return f"{start.strftime('%b')} {start.day} – {end.strftime('%b')} {end.day}, {end.year}"
    return f"{start.strftime('%b %d, %Y')} – {end.strftime('%b %d, %Y')}"


def _get_page_range(page_offset: int) -> tuple[date, date]:
    """Return (start, end) dates for a 30-day page.

    page_offset=0 → last 30 days ending today.
    page_offset=1 → 30 days ending 30 days ago. Etc.
    """
    today = date.today()
    end = today - timedelta(days=30 * page_offset)
    start = end - timedelta(days=29)
    return start, end


def _render_monthly_chart(day_data: dict[date, DayTotals], start: date, end: date, height: int, term_width: int) -> list[Text]:
    num_days = (end - start).days + 1
    days = [start + timedelta(days=i) for i in range(num_days)]

    gutter = 5
    col_per_day = max(1, min(4, (term_width - gutter - 1) // num_days))
    total_cols = num_days * col_per_day

    empty = DayTotals()
    values = [day_data.get(d, empty).total_tokens for d in days]
    max_val = max(values, default=1) or 1

    range_label = _date_range_label(start, end)
    lines: list[Text] = []
    lines.append(Text(f"{'Tokens / day — ' + range_label:<{gutter + total_cols}}", style="bold"))

    for row in range(height - 1, -1, -1):
        line = Text()
        if row == height - 1:
            line.append(f"{_fmt_val(max_val):>{gutter}}", style="dim")
        elif row == 0:
            line.append(f"{'0':>{gutter}}", style="dim")
        else:
            line.append(" " * gutter)

        for day_idx in range(num_days):
            v = values[day_idx]
            bar_height = v / max_val * height
            for _ in range(col_per_day):
                if bar_height >= row + 1:
                    line.append("█", style="cyan")
                elif bar_height > row:
                    frac = bar_height - row
                    line.append(BLOCKS[max(1, int(frac * 8))], style="cyan")
                else:
                    line.append(" ")

        lines.append(line)

    # X-axis: day numbers
    axis_chars = list(" " * total_cols)
    label_step = 1 if col_per_day >= 2 else 5
    for i, d in enumerate(days):
        day_num = d.day
        if i == 0 or day_num == 1 or (i + 1) % label_step == 0 or i == num_days - 1:
            col_start = i * col_per_day
            label = str(day_num)
            for j, c in enumerate(label):
                pos = col_start + j
                if pos < total_cols and axis_chars[pos] == " ":
                    axis_chars[pos] = c
    lines.append(Text(" " * gutter + "".join(axis_chars), style="dim"))

    return lines


def _render_monthly_table(day_data: dict[date, DayTotals], start: date, end: date) -> list[Text]:
    num_days = (end - start).days + 1
    days = [start + timedelta(days=i) for i in range(num_days)]
    empty = DayTotals()

    range_label = _date_range_label(start, end)
    lines: list[Text] = []
    lines.append(Text(f"Daily breakdown — {range_label}", style="bold"))

    col_w = 8
    header = Text()
    header.append(f"{'date':>6}", style="dim")
    header.append(f"{'input':>{col_w}}", style="white")
    header.append(f"{'cache_r':>{col_w}}", style="yellow")
    header.append(f"{'cache_w':>{col_w}}", style="green")
    header.append(f"{'output':>{col_w}}", style="magenta")
    header.append(f"{'total':>{col_w}}", style="cyan")
    lines.append(header)

    sep_w = 6 + col_w * 5
    lines.append(Text("─" * sep_w, style="dim"))

    totals = DayTotals()
    for d in days:
        dt = day_data.get(d, empty)
        if dt.total_tokens == 0:
            continue
        totals.input_tokens += dt.input_tokens
        totals.cache_read_tokens += dt.cache_read_tokens
        totals.cache_creation_tokens += dt.cache_creation_tokens
        totals.output_tokens += dt.output_tokens

        row = Text()
        row.append(f"{d.strftime('%m/%d'):>6}", style="dim")
        row.append(f"{_fmt_val(dt.input_tokens):>{col_w}}", style="white")
        row.append(f"{_fmt_val(dt.cache_read_tokens):>{col_w}}", style="yellow")
        row.append(f"{_fmt_val(dt.cache_creation_tokens):>{col_w}}", style="green")
        row.append(f"{_fmt_val(dt.output_tokens):>{col_w}}", style="magenta")
        row.append(f"{_fmt_val(dt.total_tokens):>{col_w}}", style="cyan")
        lines.append(row)

    lines.append(Text("─" * sep_w, style="dim"))
    total_row = Text()
    total_row.append(f"{'Σ':>6}", style="bold")
    total_row.append(f"{_fmt_val(totals.input_tokens):>{col_w}}", style="white bold")
    total_row.append(f"{_fmt_val(totals.cache_read_tokens):>{col_w}}", style="yellow bold")
    total_row.append(f"{_fmt_val(totals.cache_creation_tokens):>{col_w}}", style="green bold")
    total_row.append(f"{_fmt_val(totals.output_tokens):>{col_w}}", style="magenta bold")
    total_row.append(f"{_fmt_val(totals.total_tokens):>{col_w}}", style="cyan bold")
    lines.append(total_row)

    return lines


def _build_legend(dirs: list[str], dir_colors: dict[str, str], buckets: list[Bucket], today_by_dir: dict[str, int], height: int, window_label: str = "5m") -> list[Text]:
    content: list[Text] = []

    visible_dirs = dirs[:max(0, height - 1)]
    col_w = 6  # fixed width for each number column

    # Pre-compute values
    rows: list[tuple[str, int, int]] = []
    total_5m = 0
    total_today = 0
    for d in visible_dirs:
        t5 = sum(b.by_dir.get(d, 0) for b in buckets)
        tl = today_by_dir.get(d, 0)
        total_5m += t5
        total_today += tl
        rows.append((d, t5, tl))

    # Column widths: name column = longest visible dir name (min 4)
    name_w = max((len(d) for d in visible_dirs), default=4)

    if rows:
        # Header: "■ " prefix + name column + two number columns
        header = Text()
        header.append("   " + " " * name_w + " ")  # "■ " prefix + name
        header.append(f"{window_label:>{col_w}}", style="dim")
        header.append("  ")
        header.append(f"{'today':>{col_w}}", style="dim")
        content.append(header)

        for d, t5, tl in rows:
            line = Text()
            line.append("■ ", style=dir_colors[d])
            line.append(f"{d:<{name_w}} ")
            line.append(f"{_fmt_val(t5):>{col_w}}")
            line.append("  ")
            line.append(f"{_fmt_val(tl):>{col_w}}")
            content.append(line)

    # Separator line
    sep_width = 3 + name_w + 1 + col_w + 2 + col_w
    content.append(Text("─" * sep_width, style="dim"))

    # Total line — aligned with directory rows
    total_line = Text()
    total_line.append(f"{'Total':<{2 + name_w + 1}}", style="bold")
    total_line.append(f"{_fmt_val(total_5m):>{col_w}}")
    total_line.append("  ")
    total_line.append(f"{_fmt_val(total_today):>{col_w}}")
    content.append(total_line)

    # height + 2 total lines; pad with blank Text("") at the top
    total_slots = height + 2
    pad_count = total_slots - len(content)
    result: list[Text] = [Text("") for _ in range(max(0, pad_count))]
    result.extend(content)
    return result


def _build_layout(store: DataStore, console=None) -> Group:
    with _chart_height_lock:
        height = _chart_height
    with _view_mode_lock:
        view_mode = _view_mode
    with _page_offset_lock:
        page_offset = _page_offset

    term_width = console.size.width if console is not None else 80

    # ── Monthly view ──────────────────────────────────────────────────────────
    if view_mode == 1:
        start, end = _get_page_range(page_offset)
        day_data = store.days_in_range(start, end)
        chart_lines = _render_monthly_chart(day_data, start, end, height, term_width)

        # Can go back if there's data older than this page's start
        oldest = store.oldest_date()
        can_prev = oldest is not None and oldest < start
        can_next = page_offset > 0

        status = Text()
        status.append("[q] quit", style="dim")
        status.append("  [+/-] height", style="dim")
        if can_prev:
            status.append("  [p] prev 30d", style="dim")
        if can_next:
            status.append("  [n] next 30d", style="dim")
        status.append("  [m] table", style="dim")

        return Group(Text("◆ Claude Monitor", style="bold cyan"), *chart_lines, status)

    if view_mode == 2:
        start, end = _get_page_range(page_offset)
        day_data = store.days_in_range(start, end)
        table_lines = _render_monthly_table(day_data, start, end)

        oldest = store.oldest_date()
        can_prev = oldest is not None and oldest < start
        can_next = page_offset > 0

        status = Text()
        status.append("[q] quit", style="dim")
        if can_prev:
            status.append("  [p] prev 30d", style="dim")
        if can_next:
            status.append("  [n] next 30d", style="dim")
        status.append("  [m] back to live", style="dim")

        return Group(Text("◆ Claude Monitor", style="bold cyan"), *table_lines, status)

    # ── Live view ─────────────────────────────────────────────────────────────
    dirs = store.directories()
    dir_colors = {d: PALETTE[i % len(PALETTE)] for i, d in enumerate(dirs)}

    gutter = 5
    name_w = max((len(d) for d in dirs), default=4)
    legend_w = name_w + 18
    n = max(6, term_width - gutter - 2 - legend_w)
    n = min(n, NUM_BUCKETS)

    buckets = store.buckets(n)
    window_seconds = n * BUCKET_SECONDS
    window_label = f"{window_seconds // 60}m" if window_seconds >= 60 else f"{window_seconds}s"

    chart_lines = _render_area_chart(buckets, "Tokens / 10s", dirs, dir_colors, height)
    legend_lines = _build_legend(dirs, dir_colors, buckets, store.today_by_dir(), height, window_label)

    merged = Text()
    for chart_line, legend_line in zip(chart_lines, legend_lines):
        merged.append_text(chart_line)
        merged.append("  ")
        merged.append_text(legend_line)
        merged.append("\n")

    status = Text()
    status.append("[q] quit", style="dim")
    status.append("  [+/-] chart height", style="dim")
    status.append("  [m] monthly", style="dim")

    return Group(Text("◆ Claude Monitor", style="bold cyan"), merged, status)


class Display:
    def __init__(self, store: DataStore) -> None:
        self._store = store
        self._quit = threading.Event()
        self._console = Console()

    def _keyboard_thread(self) -> None:
        global _chart_height, _view_mode, _page_offset
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        new = termios.tcgetattr(fd)
        # Disable canonical mode, echo, and signal generation (ISIG) so Ctrl+C
        # sends \x03 to stdin rather than raising SIGINT.  Leave output
        # processing (OPOST) intact so \n still translates to \r\n.
        new[3] = new[3] & ~(termios.ICANON | termios.ECHO | termios.ISIG)
        new[6][termios.VMIN] = 1
        new[6][termios.VTIME] = 0
        try:
            termios.tcsetattr(fd, termios.TCSANOW, new)
            while not self._quit.is_set():
                ch = os.read(fd, 1).decode("utf-8", errors="replace")
                if ch in ("q", "Q", "\x03"):
                    self._quit.set()
                elif ch == "+":
                    with _chart_height_lock:
                        _chart_height = min(_chart_height + 1, 30)
                elif ch == "-":
                    with _chart_height_lock:
                        _chart_height = max(_chart_height - 1, 1)
                elif ch in ("m", "M"):
                    with _view_mode_lock:
                        _view_mode = (_view_mode + 1) % 3
                    # Reset page offset when switching views
                    with _page_offset_lock:
                        _page_offset = 0
                elif ch in ("p", "P"):
                    with _view_mode_lock:
                        in_monthly = _view_mode in (1, 2)
                    if in_monthly:
                        oldest = self._store.oldest_date()
                        with _page_offset_lock:
                            start, _ = _get_page_range(_page_offset)
                            if oldest is not None and oldest < start:
                                _page_offset += 1
                elif ch in ("n", "N"):
                    with _view_mode_lock:
                        in_monthly = _view_mode in (1, 2)
                    if in_monthly:
                        with _page_offset_lock:
                            if _page_offset > 0:
                                _page_offset -= 1
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def run(self) -> None:
        fd = sys.stdin.fileno()
        old_attrs = termios.tcgetattr(fd)
        try:
            with Live(
                console=self._console,
                auto_refresh=False,
            ) as live:
                kb = threading.Thread(target=self._keyboard_thread, daemon=True)
                kb.start()
                try:
                    while not self._quit.is_set():
                        live.update(_build_layout(self._store, self._console), refresh=True)
                        self._quit.wait(timeout=1.0)
                except KeyboardInterrupt:
                    self._quit.set()
        finally:
            # Safety net: restore terminal even if the keyboard thread's finally
            # didn't run (e.g. killed by an external SIGINT).
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
            except termios.error:
                pass
