import json
from datetime import datetime
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from store import DataStore, UsageEvent

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def _try_decode_path(base: Path, remaining: str) -> Path | None:
    """Greedily reconstruct filesystem path from hyphen-encoded remainder."""
    current = base
    while remaining:
        remaining = remaining.lstrip('-')
        if not remaining:
            break
        try:
            entries = sorted(current.iterdir(), key=lambda p: len(p.name), reverse=True)
        except (PermissionError, OSError):
            return current
        found = False
        for entry in entries:
            name = entry.name
            if remaining == name or remaining.startswith(name + '-'):
                current = entry
                remaining = remaining[len(name):]
                found = True
                break
        if not found:
            return None
    return current


def _git_repo_name(path: Path) -> str | None:
    """Walk up from path to find git repo root, return its directory name."""
    check = path
    while True:
        if (check / '.git').exists():
            return check.name
        parent = check.parent
        if parent == check:
            return None
        check = parent


def _dir_name(path: Path) -> str:
    # Subagents write into <encoded>/<UUID>/subagents/ — skip UUID and subagents dirs.
    if path.parent.name == "subagents":
        project_dir = path.parent.parent.parent
    else:
        project_dir = path.parent

    encoded = project_dir.name  # e.g. '-Users-shreyans-Code-puck-claude-monitor'
    home = Path.home()
    home_prefix = str(home).replace('/', '-')  # '-Users-shreyans'
    if encoded.startswith(home_prefix):
        relative = encoded[len(home_prefix):].lstrip('-')  # 'Code-puck-claude-monitor'
    else:
        relative = encoded.lstrip('-')

    # Try to resolve the actual path and use git repo name.
    decoded = _try_decode_path(home, relative) if relative else None
    if decoded:
        repo_name = _git_repo_name(decoded)
        if repo_name:
            return repo_name

    parts = [p for p in relative.split('-') if p]
    if not parts:
        return encoded
    return '-'.join(parts[-2:]) if len(parts) >= 2 else parts[-1]


def parse_jsonl_line(line: str, directory: str = "") -> UsageEvent | None:
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

    return UsageEvent(
        timestamp=ts,
        model=model,
        input_tokens=usage.get("input_tokens", 0),
        cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
        cache_read_tokens=usage.get("cache_read_input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        directory=directory,
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

        directory = _dir_name(path)
        for raw in new_data.decode("utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line:
                continue
            usage_event = parse_jsonl_line(line, directory)
            if usage_event:
                self._store.add(usage_event)


def preload_recent(store: DataStore, projects_dir: Path) -> None:
    """Read all historical events into the store.

    Seeds the real-time ring buffer, today counter, and daily totals
    so users can page back through 30-day windows.
    """
    for jsonl in projects_dir.rglob("*.jsonl"):
        try:
            text = jsonl.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        directory = _dir_name(jsonl)
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            event = parse_jsonl_line(line, directory)
            if event:
                store.add(event)
    # Freeze all past days into cache now that bulk load is done
    store.freeze_past_days()


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
        # Record current end-of-file for all existing files so the counter
        # only includes usage that occurs after the app starts.
        for jsonl in self._projects_dir.rglob("*.jsonl"):
            try:
                self._offsets[str(jsonl.resolve())] = jsonl.stat().st_size
            except OSError:
                pass

        self._observer.schedule(handler, str(self._projects_dir), recursive=True)
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()
