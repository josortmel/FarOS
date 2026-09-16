"""Búsqueda global (T3.9): tickets, jobs y decisiones en un cuadro.

FTS5 para tickets (ya existía), jobs (name + prompt) y decisiones (title +
context). Cada resultado lleva `section` (tablero|oficina|agentes) y lo justo
para pintar y navegar. Máximo 10 por grupo.
"""

from __future__ import annotations

from .service import TicketError

LIMIT = 10


def _terms(q: str) -> str:
    words = [w.strip('"') for w in q.split() if len(w.strip('"')) > 1]
    if not words:
        raise TicketError("búsqueda vacía (mínimo una palabra de 2+ letras)")
    return " OR ".join(f'"{w}"*' for w in words[:8])


def search(conn, q: str) -> dict:
    terms = _terms(q or "")
    tickets = [dict(r) for r in conn.execute(
        "SELECT t.id, t.title, t.status, t.owner, t.project_id, t.workflow_id, t.plan_key,"
        " t.cadence_days, bm25(tickets_fts) AS score"
        " FROM tickets_fts JOIN tickets t ON t.id = tickets_fts.rowid"
        " WHERE tickets_fts MATCH ? ORDER BY score LIMIT ?", (terms, LIMIT))]
    for t in tickets:
        t["section"] = "oficina" if t["workflow_id"] else "tablero"
    jobs = [dict(r) for r in conn.execute(
        "SELECT j.id, j.name, j.status, j.harness, j.model, j.ticket_id, bm25(jobs_fts) AS score"
        " FROM jobs_fts JOIN agent_jobs j ON j.id = jobs_fts.rowid"
        " WHERE jobs_fts MATCH ? AND j.status != 'archived' ORDER BY score LIMIT ?", (terms, LIMIT))]
    for j in jobs:
        j["section"] = "agentes"
    decisions = [dict(r) for r in conn.execute(
        "SELECT d.id, d.title, d.status, d.workflow_id, d.decided_by, bm25(decisions_fts) AS score"
        " FROM decisions_fts JOIN decisions d ON d.id = decisions_fts.rowid"
        " WHERE decisions_fts MATCH ? ORDER BY score LIMIT ?", (terms, LIMIT))]
    for d in decisions:
        d["section"] = "oficina"
    return {"q": q, "tickets": tickets, "jobs": jobs, "decisions": decisions,
            "total": len(tickets) + len(jobs) + len(decisions)}


def reindex(conn) -> dict:
    """Reconstruye jobs_fts y decisions_fts desde las tablas (idempotente)."""
    conn.execute("DELETE FROM jobs_fts")
    conn.execute("INSERT INTO jobs_fts (rowid, name, prompt) SELECT id, name, prompt FROM agent_jobs")
    conn.execute("DELETE FROM decisions_fts")
    conn.execute("INSERT INTO decisions_fts (rowid, title, context) SELECT id, title, context FROM decisions")
    conn.commit()
    return {"jobs": conn.execute("SELECT COUNT(*) FROM jobs_fts").fetchone()[0],
            "decisions": conn.execute("SELECT COUNT(*) FROM decisions_fts").fetchone()[0]}
