import sys
import threading
import termios

from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

from store import DataStore, Bucket, BUCKET_SECONDS, NUM_BUCKETS

_chart_height = 5
_chart_height_lock = threading.Lock()
_api_input_active = False
_api_input_buffer = ""
_api_input_lock = threading.Lock()
BLOCKS = " ▁▂▃▄▅▆▇█"
PALETTE = ["cyan", "yellow", "green", "magenta", "blue", "red", "bright_cyan", "bright_yellow"]


def _fmt_val(v: int) -> str:
    if v >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"{v // 1_000}k"
    return str(v)


def _fmt_cost(cents: float) -> str:
    if cents < 1:
        return "$0.00"
    if cents < 100_000:
        return f"${cents / 100:.2f}"
    return f"${cents / 100_000:.2f}k"


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

    # Index height+1: x-axis line
    one_min_pos = n - int(n * 0.2)  # 20% from the right = 1 minute ago in a 5-minute window

    axis_chars = list(" " * n)
    for i, c in enumerate("-5m"):
        axis_chars[i] = c
    for i, c in enumerate("-1m"):
        if 3 <= one_min_pos + i < n - 3:
            axis_chars[one_min_pos + i] = c

    lines.append(Text(" " * gutter + "".join(axis_chars), style="dim"))

    return lines


def _build_legend(dirs: list[str], dir_colors: dict[str, str], buckets: list[Bucket], lifetime_by_dir: dict[str, int], height: int, api_month_cents: float = 0.0, api_delta_cents: float = 0.0) -> list[Text]:
    content: list[Text] = []

    visible_dirs = dirs[:max(0, height - 1)]
    col_w = 6  # fixed width for each number column

    # Pre-compute values
    rows: list[tuple[str, int, int]] = []
    total_5m = 0
    total_lifetime = 0
    for d in visible_dirs:
        t5 = sum(b.by_dir.get(d, 0) for b in buckets)
        tl = lifetime_by_dir.get(d, 0)
        total_5m += t5
        total_lifetime += tl
        rows.append((d, t5, tl))

    # Column widths: name column = longest visible dir name (min 4)
    name_w = max((len(d) for d in visible_dirs), default=4)

    if rows:
        # Header: "■ " prefix + name column + two number columns
        header = Text()
        header.append("   " + " " * name_w + " ")  # "■ " prefix + name
        header.append(f"{'5m':>{col_w}}", style="dim")
        header.append("  ")
        header.append(f"{'session':>{col_w}}", style="dim")
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
    total_line.append(f"{_fmt_val(total_lifetime):>{col_w}}")
    content.append(total_line)

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

    # height + 2 total lines; pad with blank Text("") at the top
    total_slots = height + 2 + (1 if show_api else 0)
    pad_count = total_slots - len(content)
    result: list[Text] = [Text("") for _ in range(max(0, pad_count))]
    result.extend(content)
    return result


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


class Display:
    def __init__(self, store: DataStore, usage=None) -> None:
        self._store = store
        self._usage = usage
        self._quit = threading.Event()
        self._console = Console()

    def _keyboard_thread(self) -> None:
        global _chart_height, _api_input_active, _api_input_buffer
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
                        live.update(_build_layout(self._store, self._usage), refresh=True)
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
