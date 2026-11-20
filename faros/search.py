"""Búsqueda global (T3.9): tickets, jobs, decisiones y workflows (#1320) en un cuadro.

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
    tickets = [
        dict(r)
        for r in conn.execute(
            "SELECT t.id, t.title, t.status, t.owner, t.project_id, t.workflow_id, t.plan_key,"
            " t.cadence_days, bm25(tickets_fts) AS score"
            " FROM tickets_fts JOIN tickets t ON t.id = tickets_fts.rowid"
            " WHERE tickets_fts MATCH ? ORDER BY score LIMIT ?",
            (terms, LIMIT),
        )
    ]
    for t in tickets:
        t["section"] = "oficina" if t["workflow_id"] else "tablero"
    jobs = [
        dict(r)
        for r in conn.execute(
            "SELECT j.id, j.name, j.status, j.harness, j.model, j.ticket_id, bm25(jobs_fts) AS score"
            " FROM jobs_fts JOIN agent_jobs j ON j.id = jobs_fts.rowid"
            " WHERE jobs_fts MATCH ? AND j.status != 'archived' ORDER BY score LIMIT ?",
            (terms, LIMIT),
        )
    ]
    for j in jobs:
        j["section"] = "agentes"
    decisions = [
        dict(r)
        for r in conn.execute(
            "SELECT d.id, d.title, d.status, d.workflow_id, d.decided_by, bm25(decisions_fts2) AS score"
            " FROM decisions_fts2 JOIN decisions d ON d.id = decisions_fts2.rowid"
            " WHERE decisions_fts2 MATCH ? ORDER BY score LIMIT ?",
            (terms, LIMIT),
        )
    ]
    for d in decisions:
        d["section"] = "oficina"
    # #1320 (la lupa, orden de Pepe del 18-nov): también los workflows. Son pocos (6 en la casa el 19-nov), así que
    # basta un LIKE por palabra sobre el nombre, sin tabla FTS ni subir el esquema. Cualquier palabra, como el resto.
    palabras = [w.strip('"').lower() for w in (q or "").split() if len(w.strip('"')) > 1][:8]
    workflows = [
        dict(r)
        for r in conn.execute(
            "SELECT w.id, w.name, w.status, w.kind, w.project_id, w.coordinator FROM workflows w WHERE "
            + " OR ".join(["lower(w.name) LIKE ?"] * len(palabras))
            + " ORDER BY w.id DESC LIMIT ?",
            (*[f"%{p}%" for p in palabras], LIMIT),
        )
    ]
    for w in workflows:
        w["section"] = "oficina"
    return {
        "q": q,
        "tickets": tickets,
        "jobs": jobs,
        "decisions": decisions,
        "workflows": workflows,
        "total": len(tickets) + len(jobs) + len(decisions) + len(workflows),
    }


def reindex(conn) -> dict:
    """Reconstruye jobs_fts y decisions_fts2 desde las tablas (idempotente)."""
    conn.execute("DELETE FROM jobs_fts")
    conn.execute("INSERT INTO jobs_fts (rowid, name, prompt) SELECT id, name, prompt FROM agent_jobs")
    conn.execute("DELETE FROM decisions_fts2")
    conn.execute(
        "INSERT INTO decisions_fts2 (rowid, title, context, decision, rationale) SELECT id, title,"
        " context, coalesce(decision, ''), coalesce(rationale, '') FROM decisions"
    )
    conn.commit()
    return {
        "jobs": conn.execute("SELECT COUNT(*) FROM jobs_fts").fetchone()[0],
        "decisions": conn.execute("SELECT COUNT(*) FROM decisions_fts2").fetchone()[0],
    }
