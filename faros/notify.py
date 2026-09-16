"""Telegram notifications for AgenticOS (T4.4).

Sends a message to el dueño's Telegram when a run fails (error/timeout)
or a verdict comes back as fail. No-op with a log line when token or
chat_id are empty — never raises.
"""

from __future__ import annotations

import logging
import sqlite3

import httpx

log = logging.getLogger("faros.notify")
logging.getLogger("httpx").setLevel(logging.WARNING)


def telegram(conn: sqlite3.Connection, text: str) -> bool:
    """Send `text` to the configured Telegram chat. Returns True if sent.

    Reads telegram_token and telegram_chat_id from settings (T0.4).
    No-op (returns False) when either is missing — logs, never raises.
    """
    from . import settings

    token = settings.get(conn, "telegram_token")
    chat_id = settings.get(conn, "telegram_chat_id")

    if not token or not chat_id:
        log.info("telegram skip: token=%s chat_id=%s", bool(token), bool(chat_id))
        return False

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        r = httpx.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
        if r.status_code == 200:
            log.info("telegram sent (%d chars) to chat %s", len(text), chat_id)
            return True
        log.warning("telegram HTTP %d: %s", r.status_code, r.text[:200])
        return False
    except Exception:
        log.exception("telegram send failed (non-fatal)")
        return False


def on_run_failed(conn: sqlite3.Connection, job_name: str, run_id: int,
                  status: str, summary: str | None = None) -> bool:
    """Called by jobs.finish_run when status is error/timeout/cancelled."""
    lines = [
        f"⚠️ FarOS — run #{run_id} de «{job_name}» terminó {status}.",
    ]
    if summary:
        lines.append(summary[:500])
    return telegram(conn, "\n".join(lines))


def on_verdict_fail(conn: sqlite3.Connection, job_name: str, run_id: int,
                    reason: str | None = None) -> bool:
    """Called by jobs.set_verdict when verdict is fail."""
    lines = [
        f"❌ FarOS — run #{run_id} de «{job_name}» no pasó la verificación.",
    ]
    if reason:
        lines.append(f"Motivo: {reason[:500]}")
    return telegram(conn, "\n".join(lines))
