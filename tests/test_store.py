from datetime import datetime, timezone
from claude_monitor.store import Bucket, DataStore, UsageEvent

def _event(ts: datetime, input_tokens=100, output_tokens=50, directory=""):
    return UsageEvent(
        timestamp=ts,
        model="claude-sonnet-4-6",
        input_tokens=input_tokens,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        output_tokens=output_tokens,
        directory=directory,
    )

def _now():
    return datetime.now(timezone.utc)


def test_buckets_returns_requested_count():
    store = DataStore()
    assert len(store.buckets(30)) == 30
    assert len(store.buckets(60)) == 60

def test_event_lands_in_correct_bucket():
    store = DataStore()
    now = _now()
    store.add(_event(now, input_tokens=100))
    buckets = store.buckets()
    # Allow for bucket boundary race: event may land in bucket[-1] or[-2]
    assert buckets[-1].input_tokens > 0 or buckets[-2].input_tokens > 0

def test_old_events_discarded():
    from datetime import timedelta
    store = DataStore()
    # Use a timestamp older than the full retention window (100+ minutes)
    old_ts = _now() - timedelta(minutes=120)
    store.add(_event(old_ts, input_tokens=9999))
    # Verify old events are not stored in any bucket
    buckets = store.buckets()
    total_tokens = sum(b.input_tokens for b in buckets)
    assert total_tokens == 0

def test_multiple_events_same_bucket():
    store = DataStore()
    now = _now()
    store.add(_event(now, input_tokens=100))
    store.add(_event(now, input_tokens=200))
    buckets = store.buckets()
    total_input_tokens = sum(b.input_tokens for b in buckets)
    assert total_input_tokens == 300


# --- Per-directory tracking tests ---

def test_bucket_by_dir_updated_on_add():
    bucket = Bucket()
    event = _event(_now(), input_tokens=100, output_tokens=50, directory="/project/foo")
    bucket.add(event)
    assert bucket.by_dir == {"/project/foo": 150}  # 100 + 50


def test_bucket_by_dir_accumulates_same_dir():
    bucket = Bucket()
    event1 = _event(_now(), input_tokens=100, output_tokens=50, directory="/project/foo")
    event2 = _event(_now(), input_tokens=200, output_tokens=75, directory="/project/foo")
    bucket.add(event1)
    bucket.add(event2)
    assert bucket.by_dir["/project/foo"] == 425  # 150 + 275


def test_bucket_by_dir_multiple_dirs():
    bucket = Bucket()
    bucket.add(_event(_now(), input_tokens=100, output_tokens=0, directory="/a"))
    bucket.add(_event(_now(), input_tokens=200, output_tokens=0, directory="/b"))
    assert bucket.by_dir["/a"] == 100
    assert bucket.by_dir["/b"] == 200


def test_datastore_directories_first_seen_order():
    store = DataStore()
    now = _now()
    store.add(_event(now, directory="/project/c"))
    store.add(_event(now, directory="/project/a"))
    store.add(_event(now, directory="/project/b"))
    store.add(_event(now, directory="/project/a"))  # duplicate, should not reappear
    assert store.directories() == ["/project/c", "/project/a", "/project/b"]


def test_datastore_today_by_dir_correct_totals():
    store = DataStore()
    now = _now()
    store.add(_event(now, input_tokens=100, output_tokens=50, directory="/x"))
    store.add(_event(now, input_tokens=200, output_tokens=100, directory="/y"))
    by_dir = store.today_by_dir()
    assert by_dir["/x"] == 150
    assert by_dir["/y"] == 300


def test_datastore_today_by_dir_accumulates():
    store = DataStore()
    now = _now()
    store.add(_event(now, input_tokens=100, output_tokens=50, directory="/proj"))
    store.add(_event(now, input_tokens=200, output_tokens=75, directory="/proj"))
    by_dir = store.today_by_dir()
    assert by_dir["/proj"] == 425  # (100+50) + (200+75)


def test_datastore_empty_directory_does_not_crash():
    store = DataStore()
    now = _now()
    store.add(_event(now, input_tokens=100, output_tokens=50, directory=""))
    assert store.directories() == [""]
    assert store.today_by_dir()[""] == 150


def test_datastore_today_by_dir_returns_copy():
    store = DataStore()
    now = _now()
    store.add(_event(now, directory="/proj"))
    result = store.today_by_dir()
    result["/proj"] = 999999  # mutate the returned copy
    # Original should be unchanged
    assert store.today_by_dir()["/proj"] != 999999


def test_datastore_directories_returns_copy():
    store = DataStore()
    now = _now()
    store.add(_event(now, directory="/proj"))
    dirs = store.directories()
    dirs.append("/injected")
    assert "/injected" not in store.directories()
