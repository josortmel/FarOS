"""T0.5: query-count tests for board() and workflow_status().

Uses sqlite3.set_trace_callback to count every SQL statement executed.
Target: board() <= 8 queries on 200 tickets; workflow_status() <= 10.
"""

import sqlite3
import threading
from datetime import datetime

import pytest

from faros import db
from faros import service
from faros import workflows


class QueryCounter:
    """Thread-safe SQL query counter via set_trace_callback."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._count = 0
        self._statements: list[str] = []
        self._active = False

    def start(self):
        self._count = 0
        self._statements = []
        self._active = True
        self._conn.set_trace_callback(self._trace)

    def stop(self) -> int:
        self._active = False
        self._conn.set_trace_callback(None)
        return self._count

    def _trace(self, sql: str):
        if self._active:
            self._count += 1
            self._statements.append(sql[:120])

    @property
    def count(self):
        return self._count

    @property
    def statements(self):
        return list(self._statements)


def _seed_tickets(conn, n: int, project_id: int, workflow_id: int | None = None,
                  phase: str | None = None):
    """Seed n tickets quickly (bypassing service.propose_ticket for speed)."""
    now = datetime.now().isoformat(timespec="seconds")
    for i in range(n):
        status = ["proposed", "accepted", "in_progress", "done", "verified"][i % 5]
        closed = now if status == "verified" else None
        conn.execute(
            "INSERT INTO tickets (project_id, workflow_id, phase, kind, title,"
            " description, owner, source, status, priority, created_by, created_at,"
            " closed_at, verification_level)"
            " VALUES (?, ?, ?, 'manual', ?, '', 'alice', 'proposed', ?, 'media',"
            " 'alice', ?, ?, 'self')",
            (project_id, workflow_id, phase, f"Test ticket {i}", status, now, closed))
        tid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO ticket_history (ticket_id, old_status, new_status,"
            " changed_by, changed_at) VALUES (?, NULL, 'proposed', 'alice', ?)",
            (tid, now))
        if status != "proposed":
            conn.execute(
                "INSERT INTO ticket_history (ticket_id, old_status, new_status,"
                " changed_by, changed_at) VALUES (?, 'proposed', ?, 'alice', ?)",
                (tid, status, now))
        conn.execute(
            "INSERT INTO tickets_fts (rowid, title, description) VALUES (?, ?, '')",
            (tid, f"Test ticket {i}"))
    conn.commit()


@pytest.fixture
def big_db():
    """In-memory DB with 200 tickets across 2 projects, 1 workflow with 100."""
    conn = db.connect(":memory:")
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute("INSERT INTO projects (name, position, created_at) VALUES ('P1', 0, ?)", (now,))
    conn.execute("INSERT INTO projects (name, position, created_at) VALUES ('P2', 1, ?)", (now,))
    conn.execute(
        "INSERT INTO workflows (name, project_id, kind, phases, coordinator, created_at)"
        " VALUES ('wf-test', 1, 'construccion', '[\"fase-1\",\"fase-2\"]', 'alice', ?)", (now,))
    conn.commit()
    _seed_tickets(conn, 100, project_id=1, workflow_id=1, phase="fase-1")
    _seed_tickets(conn, 100, project_id=2)
    return conn


def test_board_query_count(big_db):
    """board() on 200 tickets must use <= 8 queries."""
    counter = QueryCounter(big_db)
    counter.start()
    result = service.board(big_db)
    n = counter.stop()
    total_tickets = sum(result["counts"].values())
    assert total_tickets > 0, "board returned no tickets"
    assert n <= 8, (
        f"board() used {n} queries (limit 8) on {total_tickets} tickets.\n"
        f"Statements:\n" + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(counter.statements))
    )


def test_workflow_status_query_count(big_db):
    """workflow_status() on 100 tickets must use <= 11 queries.
    (10 medidos por bob en T0.5 + 1 consulta de bisagras pendientes, T3.3.)"""
    counter = QueryCounter(big_db)
    counter.start()
    result = workflows.workflow_status(big_db, 1)
    n = counter.stop()
    total = sum(ps["total"] for ps in result["phases_summary"])
    assert total == 100, f"expected 100 tickets, got {total}"
    assert n <= 11, (
        f"workflow_status() used {n} queries (limit 10) on {total} tickets.\n"
        f"Statements:\n" + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(counter.statements))
    )


def test_board_returns_correct_data(big_db):
    """Sanity: batch enrichment produces correct last_change and due_state."""
    result = service.board(big_db)
    for col_name, tickets in result["columns"].items():
        for t in tickets:
            assert "last_change" in t, f"ticket #{t['id']} missing last_change"
            assert "due_state" in t, f"ticket #{t['id']} missing due_state"
            if t["status"] not in ("proposed",):
                assert t["last_change"] is not None, (
                    f"ticket #{t['id']} status={t['status']} should have last_change")
