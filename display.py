import sys
import threading
import termios

from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

from store import DataStore, Bucket, BUCKET_SECONDS, NUM_BUCKETS

_chart_height = 5
_chart_height_lock = threading.Lock()
BLOCKS = " ▁▂▃▄▅▆▇█"
PALETTE = ["cyan", "yellow", "green", "magenta", "blue", "red", "bright_cyan", "bright_yellow"]


def _fmt_val(v: int) -> str:
    if v >= 1_000_000:
        return f"{v // 1_000_000}M"
    if v >= 1_000:
        return f"{v // 1_000}k"
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

    # Index height+1: x-axis line
    one_min_buckets = 60 // BUCKET_SECONDS  # 6 buckets = 1 minute
    one_min_pos = n - one_min_buckets       # column of the "-1m" marker

    axis_chars = list(" " * n)
    for i, c in enumerate("-5m"):
        axis_chars[i] = c
    for i, c in enumerate("-1m"):
        if 3 <= one_min_pos + i < n - 3:
            axis_chars[one_min_pos + i] = c
    for i, c in enumerate("now"):
        axis_chars[n - len("now") + i] = c

    lines.append(Text(" " * gutter + "".join(axis_chars), style="dim"))

    return lines


def _build_legend(dirs: list[str], dir_colors: dict[str, str], buckets: list[Bucket], lifetime_by_dir: dict[str, int], height: int) -> list[Text]:
    content: list[Text] = []

    total_5m = 0
    total_lifetime = 0
    if dirs:
        for d in dirs[:height]:
            tokens_5m = sum(b.by_dir.get(d, 0) for b in buckets)
            tokens_lifetime = lifetime_by_dir.get(d, 0)
            total_5m += tokens_5m
            total_lifetime += tokens_lifetime

            line = Text()
            line.append("■ ", style=dir_colors[d])
            line.append(f"{d}   {_fmt_val(tokens_5m)} / {_fmt_val(tokens_lifetime)}")
            content.append(line)

    # Separator line
    sep_width = 20
    content.append(Text("─" * sep_width, style="dim"))

    # Total line
    content.append(Text(f"  Total   {_fmt_val(total_5m)} / {_fmt_val(total_lifetime)}"))

    # height + 2 total lines; pad with blank Text("") at the top
    total_slots = height + 2
    pad_count = total_slots - len(content)
    result: list[Text] = [Text("") for _ in range(max(0, pad_count))]
    result.extend(content)
    return result


def _build_layout(store: DataStore) -> Group:
    buckets = store.buckets()

    with _chart_height_lock:
        height = _chart_height

    dirs = store.directories()
    dir_colors = {d: PALETTE[i % len(PALETTE)] for i, d in enumerate(dirs)}

    chart_lines = _render_area_chart(buckets, "Tokens / 10s", dirs, dir_colors, height)
    legend_lines = _build_legend(dirs, dir_colors, buckets, store.lifetime_by_dir(), height)

    merged = Text()
    for chart_line, legend_line in zip(chart_lines, legend_lines):
        merged.append_text(chart_line)
        merged.append("  ")
        merged.append_text(legend_line)
        merged.append("\n")

    status = Text()
    status.append("[q] quit", style="dim")
    status.append("  [+/-] chart height", style="dim")

    return Group(Text("◆ Claude Monitor", style="bold cyan"), merged, status)


class Display:
    def __init__(self, store: DataStore) -> None:
        self._store = store
        self._quit = threading.Event()
        self._console = Console()

    def _keyboard_thread(self) -> None:
        global _chart_height
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        new = termios.tcgetattr(fd)
        # Disable canonical mode and echo only — leave output processing (OPOST)
        # intact so \n still translates to \r\n and output stays left-aligned.
        new[3] = new[3] & ~(termios.ICANON | termios.ECHO)
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
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def run(self) -> None:
        with Live(
            console=self._console,
            auto_refresh=False,
        ) as live:
            kb = threading.Thread(target=self._keyboard_thread, daemon=True)
            kb.start()
            while not self._quit.is_set():
                live.update(_build_layout(self._store), refresh=True)
                self._quit.wait(timeout=1.0)
