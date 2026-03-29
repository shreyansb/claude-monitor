import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from anthropic_usage import AnthropicUsage
from display import _fmt_cost


def test_no_key_returns_zero(tmp_path):
    key_path = tmp_path / "monitor_key"
    usage = AnthropicUsage(key_path=key_path)
    assert usage.cost_month_cents == 0.0
    assert usage.cost_session_delta_cents == 0.0
    assert usage.has_key is False


def test_key_file_written_and_read(tmp_path):
    key_path = tmp_path / "monitor_key"
    usage = AnthropicUsage(key_path=key_path)
    usage.set_key("sk-ant-admin-test")
    assert key_path.exists()
    assert oct(key_path.stat().st_mode)[-3:] == "600"
    assert key_path.read_text() == "sk-ant-admin-test"
    assert usage.has_key is True


def _make_response(data: dict) -> MagicMock:
    """Helper: mock urlopen response returning JSON body."""
    body = json.dumps(data).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def test_fetch_error_retains_last_known(tmp_path):
    key_path = tmp_path / "monitor_key"
    key_path.write_text("sk-ant-admin-test")
    key_path.chmod(0o600)
    usage = AnthropicUsage(key_path=key_path)

    # First successful fetch: 500 cents = $5.00
    good_response = _make_response({
        "data": [{"starting_at": "2026-03-01T00:00:00Z", "ending_at": "2026-03-02T00:00:00Z",
                  "results": [{"amount": "500.00", "currency": "USD", "cost_type": "tokens"}]}],
        "has_more": False, "next_page": None
    })
    with patch("urllib.request.urlopen", return_value=good_response):
        usage._fetch_once()
    assert usage.cost_month_cents == 500.0

    # Second fetch fails with exception
    with patch("urllib.request.urlopen", side_effect=Exception("network error")):
        usage._fetch_once()
    # Last-known value retained
    assert usage.cost_month_cents == 500.0


def test_fmt_cost_ranges():
    assert _fmt_cost(0.5) == "$0.00"     # sub-cent — not shown in practice
    assert _fmt_cost(1.0) == "$0.01"     # exactly 1 cent
    assert _fmt_cost(123_45) == "$123.45"  # mid-range dollar value (12345 cents)
    assert _fmt_cost(99_999) == "$999.99"  # just below k threshold
    assert _fmt_cost(100_000) == "$1.00k"  # exactly at k threshold ($1,000)
    assert _fmt_cost(123_000) == "$1.23k"  # above threshold ($1,230)
