"""Tests de la migración v2 → v3 (T0.1): idempotente, con backup previo, sin
perder filas ni vínculos job↔ticket."""

import sqlite3

import pytest

from faros import db

# Esquema v2 tal como estaba desplegado el 1-sep (solo las tablas que cambian
# de forma; el resto las crea SCHEMA con IF NOT EXISTS).
SCHEMA_V2 = """
CREATE TABLE projects (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, color TEXT,
  position INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL);
CREATE TABLE workflows (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, project_id INTEGER,
  kind TEXT NOT NULL DEFAULT 'construccion', spec_path TEXT, plan_path TEXT,
  phases TEXT NOT NULL DEFAULT '[]', coordinator TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, closed_at TEXT);
CREATE TABLE tickets (
  id INTEGER PRIMARY KEY, project_id INTEGER REFERENCES projects(id),
  workflow_id INTEGER REFERENCES workflows(id), phase TEXT, plan_key TEXT,
  plan_data TEXT, suggested_agent TEXT, dispatched_to TEXT, dispatched_at TEXT,
  kind TEXT NOT NULL DEFAULT 'manual' CHECK (kind IN ('manual','agentic')),
  title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', owner TEXT,
  source TEXT NOT NULL DEFAULT 'proposed', status TEXT NOT NULL DEFAULT 'proposed',
  priority TEXT NOT NULL DEFAULT 'media', due_at TEXT, verify_criteria TEXT,
  blocked_reason TEXT, verification_level TEXT NOT NULL DEFAULT 'self',
  evidence_type TEXT, evidence TEXT, verified_by TEXT, expires_at TEXT,
  cadence_days INTEGER, last_done_at TEXT, created_by TEXT NOT NULL,
  created_at TEXT NOT NULL, closed_at TEXT);
CREATE TABLE ticket_history (
  id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES tickets(id),
  old_status TEXT, new_status TEXT NOT NULL, changed_by TEXT NOT NULL,
  changed_at TEXT NOT NULL, note TEXT);
CREATE TABLE agent_jobs (
  id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL UNIQUE REFERENCES tickets(id),
  harness TEXT NOT NULL DEFAULT 'claude-cli', model TEXT NOT NULL DEFAULT 'haiku',
  prompt TEXT NOT NULL, cwd TEXT, permission_mode TEXT NOT NULL DEFAULT 'dontAsk',
  allowed_tools TEXT, mcp_config TEXT, max_budget_usd REAL,
  timeout_s INTEGER NOT NULL DEFAULT 1800,
  schedule TEXT NOT NULL DEFAULT '{"type":"manual"}',
  enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE runs (
  id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES agent_jobs(id),
  ticket_id INTEGER NOT NULL REFERENCES tickets(id), started_at TEXT NOT NULL,
  finished_at TEXT, status TEXT NOT NULL DEFAULT 'running'
    CHECK (status IN ('running','ok','error','timeout','cancelled')),
  exit_code INTEGER, cost_usd REAL, session_id TEXT, output_path TEXT,
  result_summary TEXT);
CREATE UNIQUE INDEX one_running_per_job ON runs(job_id) WHERE status = 'running';
CREATE VIRTUAL TABLE tickets_fts USING fts5(title, description);
"""


@pytest.fixture
def v2_db(tmp_path):
    path = tmp_path / "faros.db"
    c = sqlite3.connect(str(path))
    c.executescript(SCHEMA_V2)
    c.execute("INSERT INTO projects (id, name, created_at) VALUES (1, 'Casa', '2026-09-01')")
    for i in range(1, 4):
        c.execute("INSERT INTO tickets (id, project_id, kind, title, created_by, created_at)"
                  " VALUES (?, 1, ?, ?, 'alice', '2026-09-01')",
                  (i, "agentic" if i == 3 else "manual", f"ticket {i}"))
        c.execute("INSERT INTO ticket_history (ticket_id, new_status, changed_by, changed_at)"
                  " VALUES (?, 'proposed', 'alice', '2026-09-01')", (i,))
    c.execute("INSERT INTO workflows (id, name, phases, coordinator, status, created_at)"
              " VALUES (5, 'wf-v2', '[\"core\"]', 'alice', 'closed', '2026-09-01')")
    c.execute("INSERT INTO agent_jobs (id, ticket_id, prompt, enabled, created_at)"
              " VALUES (7, 3, 'di hola', 0, '2026-09-01')")
    c.execute("INSERT INTO runs (id, job_id, ticket_id, started_at, status, cost_usd)"
              " VALUES (11, 7, 3, '2026-09-01T10:00', 'ok', 0.03)")
    c.commit()
    c.close()
    return path


def test_migrates_v2_to_v3_keeping_rows_and_links(v2_db):
    conn = db.connect(v2_db)
    assert db.schema_version(conn) == db.SCHEMA_VERSION
    tcols = {r["name"] for r in conn.execute("PRAGMA table_info(tickets)")}
    assert {"scheduled_at", "duration_min", "all_day", "preferred_time",
            "next_due_override"} <= tcols
    jcols = {r["name"]: r for r in conn.execute("PRAGMA table_info(agent_jobs)")}
    assert {"name", "status", "project_id", "inherit_mcp", "add_dirs", "effort",
            "billing_mode", "verify_level"} <= set(jcols)
    assert jcols["ticket_id"]["notnull"] == 0  # jobs desligados (decisión 3)
    rcols = {r["name"]: r for r in conn.execute("PRAGMA table_info(runs)")}
    assert {"verdict", "verdict_reason", "verified_at", "cost_kind"} <= set(rcols)
    assert rcols["ticket_id"]["notnull"] == 0
    # filas y vínculos intactos
    assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM ticket_history").fetchone()[0] == 3
    job = conn.execute("SELECT * FROM agent_jobs WHERE id=7").fetchone()
    assert job["ticket_id"] == 3 and job["name"] == "ticket 3" and job["status"] == "paused"
    run = conn.execute("SELECT * FROM runs WHERE id=11").fetchone()
    assert run["job_id"] == 7 and run["ticket_id"] == 3 and run["cost_usd"] == 0.03
    # índice anti-solape recreado
    idx = {r["name"] for r in conn.execute("PRAGMA index_list(runs)")}
    assert "one_running_per_job" in idx
    # v4: workflows reconstruida (cancelled/archived, archived_at, repo_root) con su fila intacta
    wcols = {r["name"] for r in conn.execute("PRAGMA table_info(workflows)")}
    assert {"archived_at", "repo_root"} <= wcols
    wf = conn.execute("SELECT * FROM workflows WHERE id=5").fetchone()
    assert wf["name"] == "wf-v2" and wf["status"] == "closed" and wf["archived_at"] is None
    conn.execute("UPDATE workflows SET status='archived' WHERE id=5")  # el CHECK nuevo lo admite
    # tablas nuevas
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"settings", "decisions", "decision_history", "schema_version", "mcp_servers"} <= tables
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_backup_created_before_migration(v2_db):
    conn = db.connect(v2_db)
    conn.close()
    backups = list((v2_db.parent / "backups").glob("faros_pre_migration_*.db"))
    assert len(backups) == 1
    b = sqlite3.connect(str(backups[0]))
    # la copia es la base v2, ANTES de alterar
    cols = {r[1] for r in b.execute("PRAGMA table_info(agent_jobs)")}
    assert "name" not in cols
    assert b.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 3
    b.close()


def test_migration_is_idempotent(v2_db):
    conn = db.connect(v2_db)
    conn.close()
    conn = db.connect(v2_db)
    conn.close()
    backups = list((v2_db.parent / "backups").glob("faros_pre_migration_*.db"))
    assert len(backups) == 1  # el segundo connect no migró (ni copió) nada
    conn = db.connect(v2_db)
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM agent_jobs").fetchone()[0] == 1
    conn.close()


def test_fresh_db_is_v4_without_backup(tmp_path):
    path = tmp_path / "fresh.db"
    conn = db.connect(path)
    assert db.schema_version(conn) == db.SCHEMA_VERSION
    assert not (tmp_path / "backups").exists()
    conn.close()


def test_memory_db_is_v4():
    conn = db.connect(":memory:")
    assert db.schema_version(conn) == db.SCHEMA_VERSION
    conn.close()
