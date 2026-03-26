import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

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


@dataclass
class Bucket:
    input_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    cost_cents: float = 0.0

    def add(self, event: UsageEvent) -> None:
        self.input_tokens += event.input_tokens
        self.cache_creation_tokens += event.cache_creation_tokens
        self.cache_read_tokens += event.cache_read_tokens
        self.output_tokens += event.output_tokens
        self.cost_cents += event.cost_cents

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


def _bucket_index(ts: datetime) -> int:
    """Map a timestamp to a bucket slot (0–29) based on 10s intervals."""
    epoch_seconds = int(ts.timestamp())
    return (epoch_seconds // BUCKET_SECONDS) % NUM_BUCKETS


def _window_start() -> datetime:
    """Oldest timestamp still within the 5-minute window."""
    from datetime import timedelta
    return datetime.now(timezone.utc) - timedelta(seconds=BUCKET_SECONDS * NUM_BUCKETS)


class DataStore:
    def __init__(self) -> None:
        self._buckets: dict[int, tuple[int, Bucket]] = {}
        # Maps slot_index → (epoch_slot, Bucket)
        # epoch_slot = epoch_seconds // BUCKET_SECONDS (absolute, not modular)
        self._lock = threading.Lock()

    def add(self, event: UsageEvent) -> None:
        epoch_seconds = int(event.timestamp.timestamp())
        epoch_slot = epoch_seconds // BUCKET_SECONDS
        slot_index = epoch_slot % NUM_BUCKETS

        cutoff_slot = int(_window_start().timestamp()) // BUCKET_SECONDS

        with self._lock:
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
