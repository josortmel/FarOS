"""Tests for faros.backup (T0.6)."""

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Point backups to a temp dir so tests don't touch production.
_tmp = tempfile.mkdtemp(prefix="agenticos_backup_test_")
os.environ["AGENTICOS_BACKUP_DIR"] = _tmp

from faros.db import connect  # noqa: E402
from faros import backup  # noqa: E402


@pytest.fixture
def conn():
    """In-memory DB with a few tickets for counting."""
    c = connect(":memory:")
    for i in range(5):
        c.execute(
            "INSERT INTO tickets (title, created_by, created_at) VALUES (?, 'test', ?)",
            (f"ticket-{i}", datetime.now().isoformat()),
        )
    c.commit()
    return c


class TestCreateBackup:
    def test_creates_file(self, conn):
        now = datetime(2026, 9, 2, 8, 0)
        path = backup.create_backup(conn, now)
        assert path.exists()
        assert path.name == "faros_2026-09-02.db"   # se escribe con el nombre nuevo
        assert path.stat().st_size > 0

    def test_idempotent(self, conn):
        now = datetime(2026, 9, 2, 8, 0)
        p1 = backup.create_backup(conn, now)
        size1 = p1.stat().st_size
        p2 = backup.create_backup(conn, now)
        assert p1 == p2
        assert p2.stat().st_size == size1

    def test_backup_has_same_tickets(self, conn):
        now = datetime(2026, 9, 2, 8, 0)
        path = backup.create_backup(conn, now)
        bk = sqlite3.connect(str(path))
        bk_count = bk.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        bk.close()
        prod_count = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        assert bk_count == prod_count == 5


class TestPruneOld:
    def test_prunes_old_keeps_recent(self, conn):
        bdir = backup.backup_dir()
        now = datetime(2026, 9, 2)
        old = now - timedelta(days=20)
        recent = now - timedelta(days=3)

        # A PROPOSITO con el prefijo VIEJO: este test es ahora la cobertura de
        # que la retencion sigue VIENDO los backups anteriores al rename. Si se
        # cambiaran a faros_, dejaria de probar lo unico que hay que probar aqui.
        old_file = bdir / f"agenticos_{old.strftime('%Y-%m-%d')}.db"
        recent_file = bdir / f"agenticos_{recent.strftime('%Y-%m-%d')}.db"
        old_file.write_bytes(b"old")
        recent_file.write_bytes(b"recent")

        pruned = backup.prune_old(now, retention_days=14)
        assert old_file.name in pruned
        assert not old_file.exists()
        assert recent_file.exists()

    def test_ignores_non_matching_files(self, conn):
        bdir = backup.backup_dir()
        rando = bdir / "some_other_file.db"
        rando.write_bytes(b"nope")
        pruned = backup.prune_old(datetime(2026, 9, 2))
        assert rando.name not in pruned
        assert rando.exists()
        rando.unlink()


class TestDailyTick:
    def test_returns_summary(self, conn):
        now = datetime(2026, 9, 2, 9, 0)
        result = backup.daily_tick(conn, now)
        assert result["production_tickets"] == 5
        assert result["size_kb"] > 0
        assert Path(result["backup"]).exists()

    def test_three_transitions(self, conn):
        """Create backups on 3 days, prune with retention=2, verify only 2 survive."""
        bdir = backup.backup_dir()
        day1 = datetime(2026, 8, 1, 9, 0)
        day2 = datetime(2026, 8, 2, 9, 0)
        day3 = datetime(2026, 8, 3, 9, 0)

        backup.create_backup(conn, day1)
        backup.create_backup(conn, day2)
        backup.create_backup(conn, day3)

        pruned = backup.prune_old(day3, retention_days=2)
        assert "faros_2026-08-01.db" in pruned
        assert (bdir / "faros_2026-08-02.db").exists()
        assert (bdir / "faros_2026-08-03.db").exists()


class TestSecretScrub:
    def test_secrets_blanked_in_backup(self, conn):
        """Backup must NOT contain secret settings in clear (F-SEG-01)."""
        from faros import settings
        settings.patch(conn, "carol", {"telegram_token": "SECRET_TOKEN_VALUE",
                                       "deepseek_api_key": "SECRET_KEY_VALUE"})
        now = datetime(2026, 9, 3, 8, 0)
        path = backup.create_backup(conn, now)
        bk = sqlite3.connect(str(path))
        rows = {r[0]: r[1] for r in bk.execute(
            "SELECT key, value FROM settings WHERE secret = 1"
        ).fetchall()}
        bk.close()
        for key in ("telegram_token", "deepseek_api_key"):
            assert rows.get(key) == '""', f"{key} not scrubbed in backup"
        prod_token = conn.execute(
            "SELECT value FROM settings WHERE key = 'telegram_token'"
        ).fetchone()[0]
        assert "SECRET_TOKEN_VALUE" in prod_token, "production DB must keep secrets intact"

    def test_verify_warns_about_blanked_secrets(self, conn):
        from faros import settings
        settings.patch(conn, "carol", {"telegram_token": "TOK"})
        now = datetime(2026, 9, 3, 9, 0)
        path = backup.create_backup(conn, now)
        result = backup.verify_backup(path, conn)
        assert "secrets_blanked" in result
        assert "telegram_token" in result["secrets_blanked"]
        assert "restore_warning" in result


class TestVerifyBackup:
    def test_valid_backup(self, conn):
        now = datetime(2026, 9, 2, 10, 0)
        path = backup.create_backup(conn, now)
        result = backup.verify_backup(path, conn)
        assert result["ok"] is True
        assert result["integrity"] == "ok"
        assert result["match"] is True
        assert result["backup_tickets"] == 5
        assert result["production_tickets"] == 5

    def test_missing_file(self, conn):
        result = backup.verify_backup("/nonexistent/file.db", conn)
        assert result["ok"] is False

    def test_mismatch_after_insert(self, conn):
        now = datetime(2026, 9, 2, 11, 0)
        path = backup.create_backup(conn, now)
        conn.execute(
            "INSERT INTO tickets (title, created_by, created_at) VALUES ('new', 'test', ?)",
            (datetime.now().isoformat(),),
        )
        conn.commit()
        result = backup.verify_backup(path, conn)
        assert result["ok"] is False
        assert result["match"] is False
        assert result["backup_tickets"] == 5
        assert result["production_tickets"] == 6
