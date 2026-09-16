"""Conexión y esquema de AgenticOS (SPEC §3, v3).

Esquema v3 (2-sep): tickets con campos temporales; agent_jobs como entidad
propia (ticket_id nullable, name, status); runs con veredicto; settings,
decisions (bisagras) y schema_version. Las bases v1/v2 desplegadas migran de
forma idempotente en _migrate — con copia de seguridad ANTES de alterar nada.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from . import env as _env

# FAROS_HOME / AGENTICOS_HOME mandan (daemon desechable = casa aparte). Si no,
# %LOCALAPPDATA%: se prefiere FarOS y se CAE a AgenticOS si la nueva no existe.
#
# EL FALLBACK NO ES CORTESIA, ES LO QUE IMPIDE UNA CATASTROFE SILENCIOSA. Una
# instalacion viva tiene su casa en %LOCALAPPDATA%\AgenticOS y su base se llama
# agenticos.db. Si el codigo renombrado busca FarOS/faros.db y no los encuentra,
# NO falla: crea una base nueva y vacia, y el duenyo abre la app y ve un tablero
# sin nada. Los datos siguen intactos al lado y el parece que lo ha perdido todo.
#
# Se descubrio a los diez minutos del rename (#286) porque la sustitucion global
# cambio `agenticos.db` por `faros.db` sin que nadie lo pidiera, y la suite no lo
# vio: los tests usan casas temporales donde el fichero se crea nuevo, asi que el
# unico entorno donde el fallo existe es el unico que los tests no tocan.


def _fichero_db(d: Path) -> Path:
    nueva, vieja = d / "faros.db", d / "agenticos.db"
    if nueva.exists():
        return nueva
    if vieja.exists():
        return vieja      # instalacion anterior al rename: se usa la suya
    return nueva


DATA_DIR = _env.data_dir()
DEFAULT_DB = _fichero_db(DATA_DIR)

SCHEMA_VERSION = 5  # v5 (T1.1, 7-sep): tickets.weekday

# Definición v3 de las dos tablas que se RECONSTRUYEN al migrar desde v2
# (SQLite no permite quitar NOT NULL con ALTER). Se usan tanto en SCHEMA
# (base nueva) como en _rebuild (base vieja).
AGENT_JOBS_V3 = """
CREATE TABLE IF NOT EXISTS agent_jobs (
  id              INTEGER PRIMARY KEY,
  ticket_id       INTEGER UNIQUE REFERENCES tickets(id),
  project_id      INTEGER REFERENCES projects(id),
  name            TEXT NOT NULL DEFAULT '',
  status          TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','paused','archived')),
  harness         TEXT NOT NULL DEFAULT 'claude-cli',
  model           TEXT NOT NULL DEFAULT 'haiku',
  prompt          TEXT NOT NULL,
  cwd             TEXT,
  permission_mode TEXT NOT NULL DEFAULT 'dontAsk',
  allowed_tools   TEXT,
  mcp_config      TEXT,
  inherit_mcp     TEXT,
  mcp_servers     TEXT,
  strict_mcp      INTEGER NOT NULL DEFAULT 0,
  add_dirs        TEXT,
  effort          TEXT,
  fallback_model  TEXT,
  system_prompt   TEXT,
  json_schema     TEXT,
  max_budget_usd  REAL,
  billing_mode    TEXT,
  timeout_s       INTEGER NOT NULL DEFAULT 1800,
  schedule        TEXT NOT NULL DEFAULT '{"type":"manual"}',
  verify_level    TEXT NOT NULL DEFAULT 'auto',
  verify_criteria TEXT,
  enabled         INTEGER NOT NULL DEFAULT 1,
  created_at      TEXT NOT NULL,
  updated_at      TEXT
);
"""

RUNS_V3 = """
CREATE TABLE IF NOT EXISTS runs (
  id             INTEGER PRIMARY KEY,
  job_id         INTEGER NOT NULL REFERENCES agent_jobs(id),
  ticket_id      INTEGER REFERENCES tickets(id),
  started_at     TEXT NOT NULL,
  finished_at    TEXT,
  status         TEXT NOT NULL DEFAULT 'running'
                 CHECK (status IN ('running','ok','error','timeout','cancelled')),
  exit_code      INTEGER,
  cost_usd       REAL,
  cost_kind      TEXT,
  session_id     TEXT,
  output_path    TEXT,
  result_summary TEXT,
  verdict        TEXT CHECK (verdict IN ('pass','fail') OR verdict IS NULL),
  verdict_reason TEXT,
  verified_at    TEXT
);
"""

# v4 (4-sep, v3.1): workflows con cancelled/archived + archived_at + repo_root
# (el dueño, queja 4ª y §3). Se RECONSTRUYE al migrar (el CHECK de status no se altera con ALTER).
WORKFLOWS_V4 = """
CREATE TABLE IF NOT EXISTS workflows (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  project_id  INTEGER REFERENCES projects(id),
  kind        TEXT NOT NULL DEFAULT 'construccion',
  spec_path   TEXT,
  plan_path   TEXT,
  repo_root   TEXT,
  phases      TEXT NOT NULL DEFAULT '[]',
  coordinator TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'active'
              CHECK (status IN ('active','paused','closed','cancelled','archived')),
  created_at  TEXT NOT NULL,
  closed_at   TEXT,
  archived_at TEXT
);
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  color      TEXT,
  position   INTEGER NOT NULL DEFAULT 0,
  archived   INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

""" + WORKFLOWS_V4 + """
CREATE TABLE IF NOT EXISTS mcp_servers (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  transport  TEXT NOT NULL DEFAULT 'stdio' CHECK (transport IN ('stdio','http','sse')),
  command    TEXT,
  args       TEXT NOT NULL DEFAULT '[]',
  url        TEXT,
  env        TEXT NOT NULL DEFAULT '{}',
  headers    TEXT NOT NULL DEFAULT '{}',
  enabled    INTEGER NOT NULL DEFAULT 1,
  note       TEXT NOT NULL DEFAULT '',
  source     TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','local')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
  id                 INTEGER PRIMARY KEY,
  project_id         INTEGER REFERENCES projects(id),
  workflow_id        INTEGER REFERENCES workflows(id),
  phase              TEXT,
  plan_key           TEXT,
  plan_data          TEXT,
  suggested_agent    TEXT,
  dispatched_to      TEXT,
  dispatched_at      TEXT,
  kind               TEXT NOT NULL DEFAULT 'manual'
                     CHECK (kind IN ('manual','agentic')),
  title              TEXT NOT NULL,
  description        TEXT NOT NULL DEFAULT '',
  owner              TEXT,
  source             TEXT NOT NULL DEFAULT 'proposed',
  status             TEXT NOT NULL DEFAULT 'proposed',
  priority           TEXT NOT NULL DEFAULT 'media',
  due_at             TEXT,
  scheduled_at       TEXT,
  duration_min       INTEGER,
  all_day            INTEGER NOT NULL DEFAULT 0,
  preferred_time     TEXT,
  next_due_override  TEXT,
  weekday            INTEGER CHECK (weekday IS NULL OR weekday BETWEEN 0 AND 6),
  verify_criteria    TEXT,
  blocked_reason     TEXT,
  verification_level TEXT NOT NULL DEFAULT 'self',
  evidence_type      TEXT,
  evidence           TEXT,
  verified_by        TEXT,
  expires_at         TEXT,
  cadence_days       INTEGER,
  last_done_at       TEXT,
  created_by         TEXT NOT NULL,
  created_at         TEXT NOT NULL,
  closed_at          TEXT
);

CREATE TABLE IF NOT EXISTS ticket_history (
  id         INTEGER PRIMARY KEY,
  ticket_id  INTEGER NOT NULL REFERENCES tickets(id),
  old_status TEXT,
  new_status TEXT NOT NULL,
  changed_by TEXT NOT NULL,
  changed_at TEXT NOT NULL,
  note       TEXT
);
""" + AGENT_JOBS_V3 + RUNS_V3 + """
CREATE TABLE IF NOT EXISTS ticket_deps (
  ticket_id  INTEGER NOT NULL REFERENCES tickets(id),
  depends_on INTEGER NOT NULL REFERENCES tickets(id),
  PRIMARY KEY (ticket_id, depends_on)
);

CREATE TABLE IF NOT EXISTS findings (
  id                   INTEGER PRIMARY KEY,
  workflow_id          INTEGER NOT NULL REFERENCES workflows(id),
  ticket_id            INTEGER REFERENCES tickets(id),
  severity             TEXT NOT NULL
                       CHECK (severity IN ('critical','high','medium','low')),
  category             TEXT NOT NULL DEFAULT 'bug'
                       CHECK (category IN ('bug','gap','degradation','concern')),
  title                TEXT NOT NULL,
  detail               TEXT NOT NULL DEFAULT '',
  suggestion           TEXT NOT NULL DEFAULT '',
  found_by             TEXT NOT NULL,
  status               TEXT NOT NULL DEFAULT 'open'
                       CHECK (status IN ('open','dispatched','fixed','dismissed')),
  correction_ticket_id INTEGER REFERENCES tickets(id),
  created_at           TEXT NOT NULL,
  resolved_at          TEXT,
  resolved_note        TEXT
);

CREATE TABLE IF NOT EXISTS handoffs (
  id          INTEGER PRIMARY KEY,
  workflow_id INTEGER NOT NULL REFERENCES workflows(id),
  content     TEXT NOT NULL,
  created_by  TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS milestones (
  id          INTEGER PRIMARY KEY,
  workflow_id INTEGER NOT NULL REFERENCES workflows(id),
  kind        TEXT NOT NULL CHECK (kind IN ('phase_done','workflow_closed')),
  phase       TEXT,
  created_at  TEXT NOT NULL,
  ecodb_saved INTEGER NOT NULL DEFAULT 0,
  memory_id   TEXT
);

-- v3: ajustes de la app (secretos enmascarados al leer por la API).
CREATE TABLE IF NOT EXISTS settings (
  key        TEXT PRIMARY KEY,
  value      TEXT,
  secret     INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

-- v3: bisagras. Opciones LITERALES en JSON [{label, text}], nunca A/B/C.
CREATE TABLE IF NOT EXISTS decisions (
  id             INTEGER PRIMARY KEY,
  workflow_id    INTEGER REFERENCES workflows(id),
  project_id     INTEGER REFERENCES projects(id),
  title          TEXT NOT NULL,
  context        TEXT NOT NULL DEFAULT '',
  options        TEXT NOT NULL DEFAULT '[]',
  asked_by       TEXT NOT NULL,
  status         TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','decided','deferred')),
  decided_by     TEXT,
  decision       TEXT,
  rationale      TEXT,
  deferred_until TEXT,
  created_at     TEXT NOT NULL,
  decided_at     TEXT
);

CREATE TABLE IF NOT EXISTS decision_history (
  id          INTEGER PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decisions(id),
  event       TEXT NOT NULL,
  actor       TEXT NOT NULL,
  changed_at  TEXT NOT NULL,
  note        TEXT
);

CREATE TABLE IF NOT EXISTS schema_version (
  version    INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

-- Garantía a nivel de DB del anti-solape (review Prima F6): como mucho un run
-- 'running' por job, gane quien gane la carrera scheduler-vs-botón.
CREATE UNIQUE INDEX IF NOT EXISTS one_running_per_job
  ON runs(job_id) WHERE status = 'running';

-- T0.5: índices para evitar N+1 en board/workflow_status/calendar.

CREATE VIRTUAL TABLE IF NOT EXISTS tickets_fts USING fts5(title, description);

-- T3.9: búsqueda global. Se mantienen sincronizadas por triggers (jobs y decisiones
-- se escriben solo desde jobs.py/decisions.py, pero los triggers no dependen de eso).
CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5(name, prompt);
CREATE VIRTUAL TABLE IF NOT EXISTS decisions_fts USING fts5(title, context);
CREATE TRIGGER IF NOT EXISTS jobs_fts_ai AFTER INSERT ON agent_jobs BEGIN
  INSERT INTO jobs_fts (rowid, name, prompt) VALUES (new.id, new.name, new.prompt);
END;
CREATE TRIGGER IF NOT EXISTS jobs_fts_au AFTER UPDATE OF name, prompt ON agent_jobs BEGIN
  DELETE FROM jobs_fts WHERE rowid = old.id;
  INSERT INTO jobs_fts (rowid, name, prompt) VALUES (new.id, new.name, new.prompt);
END;
CREATE TRIGGER IF NOT EXISTS jobs_fts_ad AFTER DELETE ON agent_jobs BEGIN
  DELETE FROM jobs_fts WHERE rowid = old.id;
END;
CREATE TRIGGER IF NOT EXISTS decisions_fts_ai AFTER INSERT ON decisions BEGIN
  INSERT INTO decisions_fts (rowid, title, context) VALUES (new.id, new.title, new.context);
END;
CREATE TRIGGER IF NOT EXISTS decisions_fts_au AFTER UPDATE OF title, context ON decisions BEGIN
  DELETE FROM decisions_fts WHERE rowid = old.id;
  INSERT INTO decisions_fts (rowid, title, context) VALUES (new.id, new.title, new.context);
END;
"""


INDEXES = """
-- T0.5 (Prima): índices de lectura. Se crean DESPUÉS de migrar: en una base
-- v2 las columnas nuevas (scheduled_at...) aún no existen cuando corre SCHEMA.
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_project_id ON tickets(project_id);
CREATE INDEX IF NOT EXISTS idx_tickets_workflow_id ON tickets(workflow_id);
CREATE INDEX IF NOT EXISTS idx_tickets_owner ON tickets(owner);
CREATE INDEX IF NOT EXISTS idx_tickets_scheduled_at ON tickets(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_tickets_closed_at ON tickets(closed_at);
CREATE INDEX IF NOT EXISTS idx_th_ticket_id ON ticket_history(ticket_id, id);
CREATE INDEX IF NOT EXISTS idx_runs_job_id ON runs(job_id, id);
CREATE INDEX IF NOT EXISTS idx_findings_wf_status ON findings(workflow_id, status);
"""


def connect(db_path: str | os.PathLike | None = None) -> sqlite3.Connection:
    """Abre (creando si hace falta) la base y garantiza el esquema v3."""
    path = Path(db_path or _env.get("DB") or DEFAULT_DB)
    in_memory = str(path) == ":memory:"
    if not in_memory:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    if not in_memory:
        conn.execute("PRAGMA journal_mode = WAL")
    # foreign_keys se activa DESPUÉS de migrar: la reconstrucción de tablas
    # (agent_jobs/runs) exige FK off y el pragma no se puede cambiar dentro de
    # una transacción.
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(SCHEMA)
    _migrate(conn, None if in_memory else path)
    conn.executescript(INDEXES)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ------------------------------------------------------------------ migración

# Columnas añadidas sobre bases ya desplegadas. CREATE IF NOT EXISTS no altera
# tablas existentes; esto sí, de forma idempotente (tabla → {col: decl}).
_ADDED_COLUMNS = {
    "tickets": {
        # v2
        "workflow_id": "INTEGER REFERENCES workflows(id)",
        "phase": "TEXT",
        "plan_key": "TEXT",
        "plan_data": "TEXT",
        "suggested_agent": "TEXT",
        "dispatched_to": "TEXT",
        "dispatched_at": "TEXT",
        # v3 — capa temporal (T0.3)
        "scheduled_at": "TEXT",
        "duration_min": "INTEGER",
        "all_day": "INTEGER NOT NULL DEFAULT 0",
        "preferred_time": "TEXT",
        "next_due_override": "TEXT",
        # v5 (T1.1): día de la semana de una recurrente semanal (0=lunes … 6)
        "weekday": "INTEGER CHECK (weekday IS NULL OR weekday BETWEEN 0 AND 6)",
    },
    # v4 (T5.6): nombres del registro MCP que usa el job (JSON lista)
    "agent_jobs": {
        "mcp_servers": "TEXT",
    },
}

# Columnas que deben existir tras la reconstrucción (para detectar bases v2).
_REBUILD_MARKERS = {"agent_jobs": "name", "runs": "verdict", "workflows": "archived_at"}


def _columns(conn, table) -> dict[str, sqlite3.Row]:
    return {r["name"]: r for r in conn.execute(f"PRAGMA table_info({table})")}


def _needs_structural_migration(conn) -> bool:
    """True si hay que ALTERAR tablas (base v1/v2). Una base recién creada por
    SCHEMA ya es v3: solo le falta la fila de schema_version, sin backup."""
    for table, cols in _ADDED_COLUMNS.items():
        existing = _columns(conn, table)
        if any(c not in existing for c in cols):
            return True
    for table, marker in _REBUILD_MARKERS.items():
        if marker not in _columns(conn, table):
            return True
    return False


def backup_before_migration(conn, path: Path) -> Path | None:
    """Copia consistente de la base (sqlite backup API) antes de alterar nada.
    Devuelve la ruta de la copia, o None si la base aún no existe en disco."""
    if not path.exists():
        return None
    dest_dir = path.parent / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Nombre nuevo: este fichero solo lo escribe y lo lee un humano buscando
    # "la copia de antes de migrar", asi que no hay compatibilidad que romper.
    dest = dest_dir / f"faros_pre_migration_{ts}.db"
    out = sqlite3.connect(str(dest))
    try:
        conn.backup(out)
    finally:
        out.close()
    return dest


def _rebuild(conn, table: str, create_sql: str) -> None:
    """Recrea `table` con la definición v3 copiando las columnas comunes.
    Llamar con foreign_keys=OFF (fuera de transacción) — el DROP de la tabla
    vieja con FK on dispararía el borrado implícito de sus hijos."""
    old_cols = list(_columns(conn, table))
    tmp = f"{table}__v3"
    conn.execute(f"DROP TABLE IF EXISTS {tmp}")
    conn.execute(create_sql.replace(f"CREATE TABLE IF NOT EXISTS {table} ",
                                    f"CREATE TABLE {tmp} ", 1))
    new_cols = list(_columns(conn, tmp))
    common = [c for c in old_cols if c in new_cols]
    cols_sql = ", ".join(common)
    conn.execute(f"INSERT INTO {tmp} ({cols_sql}) SELECT {cols_sql} FROM {table}")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {tmp} RENAME TO {table}")


def _migrate(conn: sqlite3.Connection, path: Path | None) -> None:
    structural = _needs_structural_migration(conn)
    if not structural:
        if schema_version(conn) < SCHEMA_VERSION:
            conn.execute("INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                         (SCHEMA_VERSION, datetime.now().isoformat(timespec="seconds")))
            conn.commit()
        return
    if path is not None:
        backup_before_migration(conn, path)

    conn.execute("BEGIN")
    try:
        for table, cols in _ADDED_COLUMNS.items():
            existing = _columns(conn, table)
            for col, decl in cols.items():
                if col not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        if "name" not in _columns(conn, "agent_jobs"):
            _rebuild(conn, "agent_jobs", AGENT_JOBS_V3)
            # Los jobs v1/v2 vivían dentro de un ticket: heredan su título como nombre.
            conn.execute(
                "UPDATE agent_jobs SET name = COALESCE("
                " (SELECT title FROM tickets WHERE tickets.id = agent_jobs.ticket_id), ''),"
                " status = CASE WHEN enabled = 1 THEN 'active' ELSE 'paused' END"
                " WHERE name = ''")
        if "archived_at" not in _columns(conn, "workflows"):
            # v4: cancelled/archived en el CHECK, archived_at, repo_root.
            _rebuild(conn, "workflows", WORKFLOWS_V4)
            _fill_repo_root(conn)
        if "verdict" not in _columns(conn, "runs"):
            _rebuild(conn, "runs", RUNS_V3)
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_running_per_job"
                         " ON runs(job_id) WHERE status = 'running'")
        conn.execute("INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                     (SCHEMA_VERSION, datetime.now().isoformat(timespec="seconds")))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    # Comprobación de integridad referencial tras la reconstrucción.
    bad = conn.execute("PRAGMA foreign_key_check").fetchall()
    if bad:
        raise RuntimeError(f"migración v3: foreign_key_check devolvió {len(bad)} fila(s): "
                           f"{[tuple(b) for b in bad[:5]]}")


def _fill_repo_root(conn) -> None:
    """v4: los workflows importados antes no sabían su raíz de repo. Si el repo de
    desarrollo (este paquete) contiene su plan_path relativo, es esa; si no, queda
    NULL y documents() cae a settings.repo_root_default (PATCH /api/workflows lo fija)."""
    dev_root = Path(__file__).resolve().parent.parent
    for r in conn.execute("SELECT id, plan_path FROM workflows WHERE repo_root IS NULL").fetchall():
        pp = r["plan_path"]
        if pp and not Path(pp).is_absolute() and (dev_root / pp).exists():
            conn.execute("UPDATE workflows SET repo_root=? WHERE id=?", (str(dev_root), r["id"]))


def schema_version(conn) -> int:
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return int(row["v"] or 0)
