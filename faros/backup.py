"""Daily DB backup with retention (T0.6).

Uses sqlite3.backup() for a consistent copy while the daemon holds the WAL.
Backups land in <data dir>/backups/ as faros_YYYY-MM-DD.db.
Files named agenticos_YYYY-MM-DD.db (before the rename) are still recognised.
Retention: 14 days by default; older files are pruned on each tick.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("faros.backup")

from .db import DATA_DIR
from . import env as _env

DEFAULT_BACKUP_DIR = DATA_DIR / "backups"

RETENTION_DAYS = 14
# RECONOCE LOS DOS PREFIJOS, escribe solo el nuevo. Si el regex solo mirara
# `faros_`, los backups ya existentes (19 en esta maquina, desde el 3-sep)
# dejarian de contarse: la retencion no los podaria nunca y un restore no
# los encontraria. Serian invisibles estando ahi — el mismo fallo callado
# que el de la base de datos, en el sitio donde mas duele, que es el backup.
FILENAME_RE = re.compile(r"^(?:faros|agenticos)_(\d{4}-\d{2}-\d{2})\.db$")
ROLLBACK_KEEP = 3
ROLLBACK_RES = (re.compile(r"^pre_\d+_\d{8}_\d{6}\.db$"),
                re.compile(r"^(?:faros|agenticos)_pre_migration_[\d_]+\.db$"))


def backup_dir() -> Path:
    d = Path(_env.get("BACKUP_DIR", str(DEFAULT_BACKUP_DIR)))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _backup_path(now: datetime) -> Path:
    return backup_dir() / f"faros_{now.strftime('%Y-%m-%d')}.db"


def _scrub_secrets(dest: Path) -> int:
    """Blank secret=1 settings in a backup copy. Returns count scrubbed."""
    bk = sqlite3.connect(str(dest))
    try:
        n = bk.execute(
            "UPDATE settings SET value = '\"\"' WHERE secret = 1 AND value != '\"\"'"
        ).rowcount
        # v3.1 (T5.6): los secretos del registro MCP (env/headers) tampoco viajan en el backup.
        try:
            import json as _json
            for r in bk.execute("SELECT id, env, headers FROM mcp_servers").fetchall():
                env = {k: "" for k in _json.loads(r[1] or "{}")}
                hdr = {k: "" for k in _json.loads(r[2] or "{}")}
                if env or hdr:
                    bk.execute("UPDATE mcp_servers SET env=?, headers=? WHERE id=?",
                               (_json.dumps(env), _json.dumps(hdr), r[0]))
                    n += 1
        except Exception:
            pass  # base anterior a v4: no hay tabla
        # #460: los valores de la lista de Claves tampoco viajan (el nombre, el tipo y el origen sí:
        # al restaurar se sabe qué hay que volver a pegar).
        try:
            n += bk.execute("UPDATE credentials SET value = '' WHERE value != ''").rowcount
        except sqlite3.OperationalError:
            pass  # base anterior a v6: no hay tabla
        bk.commit()
    finally:
        bk.close()
    return n


def create_backup(conn: sqlite3.Connection, now: datetime) -> Path:
    """sqlite3.backup() produces a consistent snapshot even under WAL.

    Secrets (telegram_token, API keys) are blanked in the copy after backup
    so backups never contain credentials in clear (decision #3, F-SEG-01).
    """
    dest = _backup_path(now)
    if dest.exists():
        log.info("backup already exists for %s, skipping", dest.name)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    dst_conn = sqlite3.connect(str(dest))
    try:
        conn.backup(dst_conn)
    finally:
        dst_conn.close()
    scrubbed = _scrub_secrets(dest)
    if scrubbed:
        log.info("backup secrets scrubbed: %d key(s) blanked in %s", scrubbed, dest.name)
    log.info("backup created: %s (%.1f KB)", dest.name, dest.stat().st_size / 1024)
    return dest


def prune_old(now: datetime, retention_days: int = RETENTION_DAYS) -> list[str]:
    """Delete backups older than retention_days. Returns names of pruned files."""
    cutoff = (now - timedelta(days=retention_days)).date()
    pruned = []
    for f in backup_dir().iterdir():
        m = FILENAME_RE.match(f.name)
        if m:
            try:
                fdate = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            except ValueError:
                continue
            if fdate <= cutoff:
                f.unlink()
                pruned.append(f.name)
                log.info("pruned old backup: %s", f.name)
    return pruned


def prune_rollback_copies(keep: int = ROLLBACK_KEEP) -> list[str]:
    """#988: las copias para volver atrás van enteras, con las claves. Se quedan las `keep` más
    recientes de cada familia, por fecha del fichero (`pre_339` va después de `pre_3320` como
    texto). También quita los -wal y -shm cuyo .db ya no existe."""
    pruned = []
    ficheros = sorted(backup_dir().iterdir(), key=lambda f: f.stat().st_mtime)
    for familia in ROLLBACK_RES:
        copias = [f for f in ficheros if familia.match(f.name)]
        for f in copias[:-keep]:
            f.unlink()
            pruned.append(f.name)
            log.info("pruned rollback copy: %s", f.name)
    for f in backup_dir().iterdir():
        if f.name.endswith(("-wal", "-shm")) and not f.with_name(f.name[:-4]).exists():
            f.unlink()
            log.info("pruned orphan: %s", f.name)
    return pruned


def daily_tick(conn: sqlite3.Connection, now: datetime) -> dict:
    """Called once per day from the scheduler loop.

    Returns a summary dict for logging / evidence.
    """
    path = create_backup(conn, now)
    pruned = prune_old(now) + prune_rollback_copies()
    ticket_count = conn.execute("SELECT COUNT(*) AS n FROM tickets").fetchone()[0]
    return {
        "backup": str(path),
        "size_kb": round(path.stat().st_size / 1024, 1),
        "production_tickets": ticket_count,
        "pruned": pruned,
    }


def verify_backup(backup_path: str | Path, production_conn: sqlite3.Connection) -> dict:
    """Open a backup, run integrity_check, compare ticket count to production.

    Returns {"ok": bool, "integrity": str, "backup_tickets": int,
             "production_tickets": int, "match": bool}.
    """
    backup_path = Path(backup_path)
    if not backup_path.exists():
        return {"ok": False, "error": f"file not found: {backup_path}"}

    bk = sqlite3.connect(str(backup_path))
    try:
        integrity = bk.execute("PRAGMA integrity_check").fetchone()[0]
        bk_tickets = bk.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    finally:
        bk.close()

    prod_tickets = production_conn.execute(
        "SELECT COUNT(*) FROM tickets"
    ).fetchone()[0]

    bk2 = sqlite3.connect(str(backup_path))
    try:
        blanked = [r[0] for r in bk2.execute(
            "SELECT key FROM settings WHERE secret = 1 AND value = '\"\"'"
        ).fetchall()]
        try:   # #460: las de la lista de Claves, por su nombre
            blanked += [f"Claves › {r[0]}" for r in bk2.execute(
                "SELECT name FROM credentials WHERE value = '' ORDER BY name").fetchall()]
        except sqlite3.OperationalError:
            pass
    finally:
        bk2.close()

    ok = integrity == "ok" and bk_tickets == prod_tickets
    result = {
        "ok": ok,
        "integrity": integrity,
        "backup_tickets": bk_tickets,
        "production_tickets": prod_tickets,
        "match": bk_tickets == prod_tickets,
    }
    if blanked:
        result["secrets_blanked"] = blanked
        result["restore_warning"] = (
            "This backup has secrets scrubbed. After restoring, "
            "re-enter these keys in Settings: " + ", ".join(blanked)
        )
    return result
