import sys
import threading
import tty
import termios

from rich.console import Console, Group
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


def _build_layout(store: DataStore, show_pricing: bool) -> Group:
    buckets = store.buckets()
    totals = store.session_totals()

    items = [
        Text("◆ Claude Monitor", style="bold cyan"),
        _render_bar_chart(buckets, lambda b: b.total_tokens, "Tokens / 10s", "cyan"),
        _render_bar_chart(buckets, lambda b: b.cost_cents, "Cents / 10s", "green"),
    ]

    status = Text()
    status.append("Session: ", style="dim")
    status.append(f"{totals.total_tokens:,} tokens", style="bold")
    status.append("  |  ", style="dim")
    status.append(f"${totals.cost_cents / 100:.4f}", style="bold green")
    status.append("  |  ", style="dim")
    status.append("[p] pricing  [q] quit", style="dim")
    items.append(status)

    if show_pricing:
        pt = Table(title="Pricing (USD per million tokens)", box=box.SIMPLE, show_header=True, expand=False)
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
        items.append(pt)

    return Group(*items)


class Display:
    def __init__(self, store: DataStore) -> None:
        self._store = store
        self._pricing_event = threading.Event()
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
                    if self._pricing_event.is_set():
                        self._pricing_event.clear()
                    else:
                        self._pricing_event.set()
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
                live.update(
                    _build_layout(self._store, self._pricing_event.is_set()),
                    refresh=True,
                )
                self._quit.wait(timeout=1.0)
