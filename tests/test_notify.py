"""Tests for faros.notify (T4.4)."""

from unittest.mock import patch, MagicMock

import pytest

from faros import db
from faros import notify


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


class TestTelegramNoOp:
    def test_no_token_returns_false(self, conn):
        assert notify.telegram(conn, "hello") is False

    def test_token_but_no_chat_returns_false(self, conn):
        from faros import settings
        settings.patch(conn, "owner", {"telegram_token": "fake-token"})
        assert notify.telegram(conn, "hello") is False

    def test_no_exception_on_missing_settings(self, conn):
        assert notify.on_run_failed(conn, "test-job", 1, "error") is False
        assert notify.on_verdict_fail(conn, "test-job", 1, "bad output") is False


class TestTelegramSend:
    def _configure(self, conn):
        from faros import settings
        settings.patch(conn, "owner", {
            "telegram_token": "123:ABC",
            "telegram_chat_id": "999",
        })

    @patch("faros.notify.httpx")
    def test_sends_when_configured(self, mock_httpx, conn):
        self._configure(conn)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_httpx.post.return_value = mock_resp
        assert notify.telegram(conn, "test message") is True
        mock_httpx.post.assert_called_once()
        args, kwargs = mock_httpx.post.call_args
        assert "123:ABC" in args[0]
        assert kwargs["json"]["chat_id"] == "999"
        assert kwargs["json"]["text"] == "test message"

    @patch("faros.notify.httpx")
    def test_on_run_failed_formats_message(self, mock_httpx, conn):
        self._configure(conn)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_httpx.post.return_value = mock_resp
        assert notify.on_run_failed(conn, "backup-job", 42, "timeout",
                                    "exceeded 1800s") is True
        text = mock_httpx.post.call_args[1]["json"]["text"]
        assert "run #42" in text
        assert "backup-job" in text
        assert "timeout" in text
        assert "1800s" in text

    @patch("faros.notify.httpx")
    def test_on_verdict_fail_formats_message(self, mock_httpx, conn):
        self._configure(conn)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_httpx.post.return_value = mock_resp
        assert notify.on_verdict_fail(conn, "email-job", 7,
                                      "output inventa remitente") is True
        text = mock_httpx.post.call_args[1]["json"]["text"]
        assert "run #7" in text
        assert "email-job" in text
        assert "no pasó" in text
        assert "inventa remitente" in text

    @patch("faros.notify.httpx")
    def test_http_error_returns_false(self, mock_httpx, conn):
        self._configure(conn)
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"
        mock_httpx.post.return_value = mock_resp
        assert notify.telegram(conn, "test") is False

    @patch("faros.notify.httpx")
    def test_exception_returns_false(self, mock_httpx, conn):
        self._configure(conn)
        mock_httpx.post.side_effect = ConnectionError("network down")
        assert notify.telegram(conn, "test") is False
