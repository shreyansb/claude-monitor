import tempfile
from pathlib import Path
from unittest.mock import patch
from anthropic_usage import AnthropicUsage


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
