import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, date

BUCKET_SECONDS = 10
NUM_BUCKETS = 600  # 600 × 10s = 100 minutes (max history for wide terminals)


@dataclass
class UsageEvent:
    timestamp: datetime
    model: str
    input_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    output_tokens: int
    directory: str = ""

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
            + self.output_tokens
        )


@dataclass
class Bucket:
    input_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    by_dir: dict[str, int] = field(default_factory=dict)

    def add(self, event: UsageEvent) -> None:
        self.input_tokens += event.input_tokens
        self.cache_creation_tokens += event.cache_creation_tokens
        self.cache_read_tokens += event.cache_read_tokens
        self.output_tokens += event.output_tokens
        self.by_dir[event.directory] = self.by_dir.get(event.directory, 0) + event.total_tokens

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
            + self.output_tokens
        )


@dataclass
class DayTotals:
    input_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
            + self.output_tokens
        )


@dataclass
class Totals:
    input_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
            + self.output_tokens
        )


def _window_start() -> datetime:
    """Oldest timestamp still within the retention window."""
    return datetime.now(timezone.utc) - timedelta(seconds=BUCKET_SECONDS * NUM_BUCKETS)


def _local_today_start() -> datetime:
    """Midnight of today in local timezone, as a timezone-aware datetime."""
    local_now = datetime.now().astimezone()
    return local_now.replace(hour=0, minute=0, second=0, microsecond=0)


class DataStore:
    def __init__(self) -> None:
        self._buckets: dict[int, tuple[int, Bucket]] = {}
        self._lock = threading.Lock()
        self._today = Totals()
        self._today_by_dir: dict[str, int] = {}
        self._today_start: datetime = _local_today_start()
        self._dir_seen: set[str] = set()
        self._dir_order: list[str] = []
        # Daily totals keyed by date object — supports arbitrary date ranges
        self._daily: dict[date, DayTotals] = {}
        # Cache: frozen past-day totals (days before today, won't change)
        self._daily_cache: dict[date, DayTotals] = {}

    def add(self, event: UsageEvent) -> None:
        epoch_seconds = int(event.timestamp.timestamp())
        epoch_slot = epoch_seconds // BUCKET_SECONDS
        slot_index = epoch_slot % NUM_BUCKETS

        with self._lock:
            # Reset today counters if the day has rolled over
            current_today_start = _local_today_start()
            if current_today_start > self._today_start:
                # Freeze yesterday's totals into cache before resetting
                self._freeze_past_days()
                self._today = Totals()
                self._today_by_dir = {}
                self._today_start = current_today_start
                self._dir_seen = set()
                self._dir_order = []

            if event.timestamp >= self._today_start:
                if event.directory not in self._dir_seen:
                    self._dir_seen.add(event.directory)
                    self._dir_order.append(event.directory)
                self._today.input_tokens += event.input_tokens
                self._today.cache_creation_tokens += event.cache_creation_tokens
                self._today.cache_read_tokens += event.cache_read_tokens
                self._today.output_tokens += event.output_tokens
                self._today_by_dir[event.directory] = (
                    self._today_by_dir.get(event.directory, 0) + event.total_tokens
                )

            # Daily tracking keyed by date — skip if already cached (past day)
            local_ts = event.timestamp.astimezone()
            ev_date = local_ts.date()
            if ev_date not in self._daily_cache:
                if ev_date not in self._daily:
                    self._daily[ev_date] = DayTotals()
                dt = self._daily[ev_date]
                dt.input_tokens += event.input_tokens
                dt.cache_creation_tokens += event.cache_creation_tokens
                dt.cache_read_tokens += event.cache_read_tokens
                dt.output_tokens += event.output_tokens

            # Bucket update for real-time chart — discard events outside the window
            cutoff_slot = int(_window_start().timestamp()) // BUCKET_SECONDS
            if epoch_slot < cutoff_slot:
                return

            existing = self._buckets.get(slot_index)
            if existing is None or existing[0] != epoch_slot:
                bucket = Bucket()
                self._buckets[slot_index] = (epoch_slot, bucket)
            else:
                bucket = existing[1]
            bucket.add(event)

    def _freeze_past_days(self) -> None:
        """Move completed (past) days from _daily into _daily_cache."""
        today = date.today()
        past_dates = [d for d in self._daily if d < today]
        for d in past_dates:
            self._daily_cache[d] = self._daily.pop(d)

    def freeze_past_days(self) -> None:
        """Public version — acquires lock, then freezes past days into cache."""
        with self._lock:
            self._freeze_past_days()

    def buckets(self, n: int = NUM_BUCKETS) -> list[Bucket]:
        """Return the n most recent buckets oldest→newest. Empty Bucket for gaps."""
        n = min(n, NUM_BUCKETS)
        now_epoch_slot = int(datetime.now(timezone.utc).timestamp()) // BUCKET_SECONDS
        result = []
        with self._lock:
            for i in range(n):
                slot_index = (now_epoch_slot - n + 1 + i) % NUM_BUCKETS
                expected_epoch_slot = now_epoch_slot - n + 1 + i
                entry = self._buckets.get(slot_index)
                if entry and entry[0] == expected_epoch_slot:
                    result.append(entry[1])
                else:
                    result.append(Bucket())
        return result

    def directories(self) -> list[str]:
        """Return directories in first-seen order."""
        with self._lock:
            return list(self._dir_order)

    def today_by_dir(self) -> dict[str, int]:
        """Token totals per directory since 00:00 local time today."""
        with self._lock:
            return dict(self._today_by_dir)

    def days_in_range(self, start: date, end: date) -> dict[date, DayTotals]:
        """Daily token totals for dates in [start, end] inclusive.

        Returns copies so callers can't mutate internal state.
        Merges cached past days with live accumulating days.
        """
        with self._lock:
            result: dict[date, DayTotals] = {}
            d = start
            one_day = timedelta(days=1)
            while d <= end:
                src = self._daily_cache.get(d) or self._daily.get(d)
                if src:
                    result[d] = DayTotals(
                        src.input_tokens, src.cache_creation_tokens,
                        src.cache_read_tokens, src.output_tokens,
                    )
                d += one_day
            return result

    def oldest_date(self) -> date | None:
        """Return the earliest date with data, or None."""
        with self._lock:
            dates = list(self._daily_cache.keys()) + list(self._daily.keys())
            return min(dates) if dates else None
