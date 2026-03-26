from datetime import datetime, timezone
from store import DataStore, UsageEvent

def _event(ts: datetime, input_tokens=100, output_tokens=50, cost_cents=0.5):
    return UsageEvent(
        timestamp=ts,
        model="claude-sonnet-4-6",
        input_tokens=input_tokens,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        output_tokens=output_tokens,
        cost_cents=cost_cents,
    )

def _now():
    return datetime.now(timezone.utc)

def test_add_and_totals():
    store = DataStore()
    store.add(_event(_now(), input_tokens=1000, output_tokens=500, cost_cents=5.0))
    totals = store.session_totals()
    assert totals.input_tokens == 1000
    assert totals.output_tokens == 500
    assert abs(totals.cost_cents - 5.0) < 0.001

def test_buckets_returns_30():
    store = DataStore()
    buckets = store.buckets()
    assert len(buckets) == 30

def test_event_lands_in_correct_bucket():
    store = DataStore()
    now = _now()
    store.add(_event(now, cost_cents=1.0))
    buckets = store.buckets()
    # Most recent bucket should contain the event
    assert buckets[-1].cost_cents > 0 or buckets[-2].cost_cents > 0

def test_old_events_discarded():
    from datetime import timedelta
    store = DataStore()
    old_ts = _now() - timedelta(minutes=10)
    store.add(_event(old_ts, cost_cents=99.0))
    totals = store.session_totals()
    assert totals.cost_cents == 0.0

def test_multiple_events_same_bucket():
    store = DataStore()
    now = _now()
    store.add(_event(now, input_tokens=100, cost_cents=1.0))
    store.add(_event(now, input_tokens=200, cost_cents=2.0))
    totals = store.session_totals()
    assert totals.input_tokens == 300
    assert abs(totals.cost_cents - 3.0) < 0.001

def test_session_totals_empty():
    store = DataStore()
    totals = store.session_totals()
    assert totals.input_tokens == 0
    assert totals.output_tokens == 0
    assert totals.cost_cents == 0.0
