import sys

from store import DataStore
from watcher import LogWatcher, CLAUDE_PROJECTS_DIR
from display import Display


def main() -> None:
    projects_dir = CLAUDE_PROJECTS_DIR
    if not projects_dir.exists():
        print(f"Error: Claude projects directory not found: {projects_dir}", file=sys.stderr)
        print("Is Claude Code installed?", file=sys.stderr)
        sys.exit(1)

    store = DataStore()
    watcher = LogWatcher(store, projects_dir=projects_dir)
    try:
        watcher.start()
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        display = Display(store)
        display.run()
    finally:
        watcher.stop()


if __name__ == "__main__":
    main()
