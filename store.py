import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

BUCKET_SECONDS = 10
NUM_BUCKETS = 30  # 30 × 10s = 5 minutes


@dataclass
class UsageEvent:
    timestamp: datetime
    model: str
    input_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    output_tokens: int
    cost_cents: float
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
    cost_cents: float = 0.0
    by_dir: dict[str, int] = field(default_factory=dict)

    def add(self, event: UsageEvent) -> None:
        self.input_tokens += event.input_tokens
        self.cache_creation_tokens += event.cache_creation_tokens
        self.cache_read_tokens += event.cache_read_tokens
        self.output_tokens += event.output_tokens
        self.cost_cents += event.cost_cents
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
class Totals:
    input_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    cost_cents: float = 0.0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
            + self.output_tokens
        )


def _window_start() -> datetime:
    """Oldest timestamp still within the 5-minute window."""
    return datetime.now(timezone.utc) - timedelta(seconds=BUCKET_SECONDS * NUM_BUCKETS)


class DataStore:
    def __init__(self) -> None:
        self._buckets: dict[int, tuple[int, Bucket]] = {}
        # Maps slot_index → (epoch_slot, Bucket)
        # epoch_slot = epoch_seconds // BUCKET_SECONDS (absolute, not modular)
        self._lifetime = Totals()  # accumulated since app start, never evicted
        self._lock = threading.Lock()
        self._lifetime_by_dir: dict[str, int] = {}
        self._dir_order: list[str] = []

    def add(self, event: UsageEvent) -> None:
        epoch_seconds = int(event.timestamp.timestamp())
        epoch_slot = epoch_seconds // BUCKET_SECONDS
        slot_index = epoch_slot % NUM_BUCKETS

        with self._lock:
            cutoff_slot = int(_window_start().timestamp()) // BUCKET_SECONDS
            if epoch_slot < cutoff_slot:
                return  # too old, discard

            existing = self._buckets.get(slot_index)
            if existing is None or existing[0] != epoch_slot:
                # New epoch cycle — replace stale bucket
                bucket = Bucket()
                self._buckets[slot_index] = (epoch_slot, bucket)
            else:
                bucket = existing[1]

            bucket.add(event)
            self._lifetime.input_tokens += event.input_tokens
            self._lifetime.cache_creation_tokens += event.cache_creation_tokens
            self._lifetime.cache_read_tokens += event.cache_read_tokens
            self._lifetime.output_tokens += event.output_tokens
            self._lifetime.cost_cents += event.cost_cents
            if event.directory not in self._lifetime_by_dir:
                self._dir_order.append(event.directory)
            self._lifetime_by_dir[event.directory] = (
                self._lifetime_by_dir.get(event.directory, 0) + event.total_tokens
            )

    def buckets(self) -> list[Bucket]:
        """Return 30 buckets oldest→newest. Empty Bucket for gaps."""
        now_epoch_slot = int(datetime.now(timezone.utc).timestamp()) // BUCKET_SECONDS
        result = []
        with self._lock:
            for i in range(NUM_BUCKETS):
                slot_index = (now_epoch_slot - NUM_BUCKETS + 1 + i) % NUM_BUCKETS
                expected_epoch_slot = now_epoch_slot - NUM_BUCKETS + 1 + i
                entry = self._buckets.get(slot_index)
                if entry and entry[0] == expected_epoch_slot:
                    result.append(entry[1])
                else:
                    result.append(Bucket())
        return result

    def lifetime_totals(self) -> Totals:
        """Cumulative totals since app start (never evicted)."""
        with self._lock:
            return Totals(
                input_tokens=self._lifetime.input_tokens,
                cache_creation_tokens=self._lifetime.cache_creation_tokens,
                cache_read_tokens=self._lifetime.cache_read_tokens,
                output_tokens=self._lifetime.output_tokens,
                cost_cents=self._lifetime.cost_cents,
            )

    def session_totals(self) -> Totals:
        buckets = self.buckets()
        t = Totals()
        for b in buckets:
            t.input_tokens += b.input_tokens
            t.cache_creation_tokens += b.cache_creation_tokens
            t.cache_read_tokens += b.cache_read_tokens
            t.output_tokens += b.output_tokens
            t.cost_cents += b.cost_cents
        return t

    def directories(self) -> list[str]:
        """Return directories in first-seen order."""
        with self._lock:
            return list(self._dir_order)

    def lifetime_by_dir(self) -> dict[str, int]:
        """Session totals per directory since app start."""
        with self._lock:
            return dict(self._lifetime_by_dir)
