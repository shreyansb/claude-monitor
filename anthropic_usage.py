import threading
from pathlib import Path

_DEFAULT_KEY_PATH = Path.home() / ".claude" / "monitor_key"


class AnthropicUsage:
    def __init__(self, key_path: Path = _DEFAULT_KEY_PATH) -> None:
        self._key_path = key_path
        self._lock = threading.Lock()
        self._fetch_event = threading.Event()
        self._shutdown = threading.Event()
        self._cost_month_cents: float = 0.0
        self._cost_session_delta_cents: float = 0.0
        self._session_start_cents: float | None = None

    @property
    def cost_month_cents(self) -> float:
        with self._lock:
            return self._cost_month_cents

    @property
    def cost_session_delta_cents(self) -> float:
        with self._lock:
            return self._cost_session_delta_cents

    @property
    def has_key(self) -> bool:
        return self._key_path.exists() and bool(self._key_path.read_text().strip())

    def set_key(self, key: str) -> None:
        try:
            with self._lock:
                self._key_path.write_text(key)
                self._key_path.chmod(0o600)
                self._session_start_cents = None
        except OSError:
            pass
        self.trigger_fetch()

    def trigger_fetch(self) -> None:
        self._fetch_event.set()

    def start(self) -> None:
        t = threading.Thread(target=self._poll_loop, daemon=True)
        t.start()

    def stop(self) -> None:
        self._shutdown.set()
        self._fetch_event.set()  # unblock any wait

    def _poll_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                self._fetch_once()
            except Exception:
                pass
            self._fetch_event.clear()
            self._fetch_event.wait(timeout=60)
