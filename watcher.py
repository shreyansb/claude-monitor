import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from pricing import calculate_cost
from store import DataStore, UsageEvent

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def parse_jsonl_line(line: str) -> UsageEvent | None:
    """Parse one JSONL line. Returns UsageEvent or None if not a usage entry."""
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None

    if data.get("type") != "assistant":
        return None

    msg = data.get("message", {})
    usage = msg.get("usage")
    if not usage:
        return None

    ts_str = data.get("timestamp")
    if not ts_str:
        return None

    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        return None

    model = msg.get("model", "claude-sonnet-4-6")
    cost = calculate_cost(model, usage)

    return UsageEvent(
        timestamp=ts,
        model=model,
        input_tokens=usage.get("input_tokens", 0),
        cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
        cache_read_tokens=usage.get("cache_read_input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cost_cents=cost,
    )


class _Handler(FileSystemEventHandler):
    def __init__(self, store: DataStore, offsets: dict) -> None:
        self._store = store
        self._offsets = offsets  # path -> byte offset

    def on_modified(self, event):
        if event.is_directory or not event.src_path.endswith(".jsonl"):
            return
        self._read_new_lines(Path(event.src_path))

    def _read_new_lines(self, path: Path) -> None:
        key = str(path.resolve())
        offset = self._offsets.get(key, 0)
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                new_data = f.read()
                self._offsets[key] = offset + len(new_data)
        except (OSError, PermissionError):
            return

        for raw in new_data.decode("utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line:
                continue
            event = parse_jsonl_line(line)
            if event:
                self._store.add(event)


class LogWatcher:
    def __init__(self, store: DataStore, projects_dir: Path = CLAUDE_PROJECTS_DIR) -> None:
        self._store = store
        self._projects_dir = projects_dir
        self._offsets: dict[str, int] = {}
        self._observer = Observer()

    def start(self) -> None:
        if not self._projects_dir.exists():
            raise FileNotFoundError(
                f"Claude projects directory not found: {self._projects_dir}"
            )
        handler = _Handler(self._store, self._offsets)
        # Seed existing files first (so startup shows recent history)
        for jsonl in self._projects_dir.rglob("*.jsonl"):
            handler._read_new_lines(jsonl)

        self._observer.schedule(handler, str(self._projects_dir), recursive=True)
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()
