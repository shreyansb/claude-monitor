import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from watcher import parse_jsonl_line, _dir_name, LogWatcher
from store import DataStore

VALID_ASSISTANT_LINE = json.dumps({
    "type": "assistant",
    "timestamp": "2026-03-26T10:00:00.000Z",
    "message": {
        "model": "claude-sonnet-4-6",
        "role": "assistant",
        "usage": {
            "input_tokens": 100,
            "cache_creation_input_tokens": 50,
            "cache_read_input_tokens": 200,
            "output_tokens": 75,
        }
    }
})

def test_parse_valid_line():
    event = parse_jsonl_line(VALID_ASSISTANT_LINE)
    assert event is not None
    assert event.input_tokens == 100
    assert event.cache_creation_tokens == 50
    assert event.cache_read_tokens == 200
    assert event.output_tokens == 75
    assert event.model == "claude-sonnet-4-6"

def test_parse_user_line_returns_none():
    line = json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}})
    assert parse_jsonl_line(line) is None

def test_parse_no_usage_returns_none():
    line = json.dumps({"type": "assistant", "message": {"role": "assistant", "content": []}})
    assert parse_jsonl_line(line) is None

def test_parse_malformed_json_returns_none():
    assert parse_jsonl_line("not json {{{") is None

def test_parse_missing_timestamp_returns_none():
    line = json.dumps({
        "type": "assistant",
        "message": {
            "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 1, "output_tokens": 1}
        }
    })
    assert parse_jsonl_line(line) is None

def _fresh_line(input_tokens=100, output_tokens=75):
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return json.dumps({
        "type": "assistant",
        "timestamp": now_ts,
        "message": {
            "model": "claude-sonnet-4-6",
            "role": "assistant",
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": output_tokens,
            }
        }
    })

def test_dir_name_under_home():
    home = str(Path.home()).replace('/', '-')
    encoded_dir = f"{home}-Code-puck-myapp"
    fake_path = Path(f"/doesnt-matter/{encoded_dir}/session.jsonl")
    assert _dir_name(fake_path) == "puck-myapp"


def test_dir_name_not_under_home():
    fake_path = Path("/doesnt-matter/-some-other-root-project-name/session.jsonl")
    result = _dir_name(fake_path)
    assert result == "project-name"


def test_parse_jsonl_line_propagates_directory():
    event = parse_jsonl_line(VALID_ASSISTANT_LINE, "puck-myapp")
    assert event is not None
    assert event.directory == "puck-myapp"


def test_watcher_ignores_existing_content_on_start():
    """Content written before start() should not be counted."""
    store = DataStore()
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir)
        (projects_dir / "test-project").mkdir()
        jsonl_file = projects_dir / "test-project" / "session.jsonl"
        jsonl_file.write_text(_fresh_line(input_tokens=999) + "\n")

        watcher = LogWatcher(store, projects_dir=projects_dir)
        watcher.start()
        time.sleep(0.3)
        watcher.stop()

        # Verify no events were loaded from pre-existing files
        by_dir = store.lifetime_by_dir()
        total_input_tokens = sum(store.buckets()[i].input_tokens for i in range(len(store.buckets())))
        assert total_input_tokens == 0
        assert len(by_dir) == 0

def test_watcher_picks_up_new_writes_after_start():
    """Content appended after start() should be counted."""
    store = DataStore()
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir)
        (projects_dir / "test-project").mkdir()
        jsonl_file = projects_dir / "test-project" / "session.jsonl"
        jsonl_file.write_text("")  # pre-existing empty file

        watcher = LogWatcher(store, projects_dir=projects_dir)
        watcher.start()
        with open(jsonl_file, "a") as f:
            f.write(_fresh_line(input_tokens=100, output_tokens=75) + "\n")
        time.sleep(0.5)
        watcher.stop()

        # Verify events written after start are properly counted
        buckets = store.buckets()
        total_input_tokens = sum(b.input_tokens for b in buckets)
        total_output_tokens = sum(b.output_tokens for b in buckets)
        assert total_input_tokens == 100
        assert total_output_tokens == 75
