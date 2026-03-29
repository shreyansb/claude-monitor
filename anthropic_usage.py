import json
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timezone
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

    def _fetch_once(self) -> None:
        try:
            key = self._key_path.read_text().strip() if self._key_path.exists() else ""
        except OSError:
            return
        if not key:
            return

        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        params = urllib.parse.urlencode({
            "bucket_width": "1d",
            "starting_at": month_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ending_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        url = f"https://api.anthropic.com/v1/organizations/cost_report?{params}"
        total_cents = 0.0
        next_page = None

        while True:
            page_url = f"{url}&page={urllib.parse.quote(next_page)}" if next_page else url
            req = urllib.request.Request(page_url, headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            })
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    body = json.loads(resp.read())
            except Exception:
                return  # retain last-known values

            for bucket in body.get("data", []):
                for result in bucket.get("results", []):
                    try:
                        total_cents += float(result["amount"])
                    except (KeyError, ValueError):
                        pass

            if body.get("has_more") and body.get("next_page"):
                next_page = body.get("next_page")
            else:
                break

        with self._lock:
            self._cost_month_cents = total_cents
            if self._session_start_cents is None:
                self._session_start_cents = total_cents
            self._cost_session_delta_cents = total_cents - self._session_start_cents

    def _poll_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                self._fetch_once()
            except Exception:
                pass
            self._fetch_event.clear()
            self._fetch_event.wait(timeout=60)
