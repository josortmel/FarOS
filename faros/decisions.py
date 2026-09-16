"""Decisiones (bisagras) como entidad (T3.2, decisión 7 del dueño): sustituyen a
los bisagra_*.md. Opciones LITERALES enteras — nunca A/B/C. Append-only: una
decisión decidida no se decide dos veces; se abre otra.

Estados: pending → decided | deferred (→ pending al retomar). Cada evento deja
fila en decision_history (event, actor, note).
"""

from __future__ import annotations

import json
from datetime import date

from .service import TicketError, _now, _validate_agent, _validate_project, AGENTS

STATUSES = {"pending", "decided", "deferred"}
DECIDERS = AGENTS  # el dueño decide; la familia puede registrar SU decisión (con quién/por qué)


def _hist(conn, decision_id, event, actor, note=None):
    conn.execute(
        "INSERT INTO decision_history (decision_id, event, actor, changed_at, note)"
        " VALUES (?, ?, ?, ?, ?)", (decision_id, event, actor, _now(), note))


def _normalize_options(options) -> list[dict]:
    if not isinstance(options, list) or not options:
        raise TicketError("options: lista no vacía de opciones literales [{label, text}]")
    out, labels = [], set()
    for o in options:
        if isinstance(o, str):
            o = {"label": o[:60], "text": o}
        if not isinstance(o, dict) or not (o.get("text") or "").strip():
            raise TicketError(f"opción inválida: {o!r} (necesita text)")
        label = (o.get("label") or o["text"][:60]).strip()
        if len(label) <= 2 and label.upper() in {"A", "B", "C", "D", "1", "2", "3"}:
            raise TicketError(f"opción {label!r}: las opciones son literales enteras, nunca A/B/C")
        if label in labels:
            raise TicketError(f"opción duplicada: {label!r}")
        labels.add(label)
        out.append({"label": label, "text": o["text"].strip()})
    return out


def get_decision(conn, decision_id) -> dict:
    row = conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
    if row is None:
        raise TicketError(f"decisión #{decision_id} no existe")
    d = dict(row)
    d["options"] = json.loads(d["options"] or "[]")
    d["history"] = [dict(r) for r in conn.execute(
        "SELECT event, actor, changed_at, note FROM decision_history WHERE decision_id=? ORDER BY id",
        (decision_id,))]
    if d["workflow_id"]:
        w = conn.execute("SELECT name FROM workflows WHERE id=?", (d["workflow_id"],)).fetchone()
        d["workflow_name"] = w["name"] if w else None
    return d


def ask(conn, agent, title, options, context="", workflow_id=None, project_id=None) -> dict:
    _validate_agent(agent)
    if not (title or "").strip():
        raise TicketError("title vacío")
    opts = _normalize_options(options)
    if workflow_id is not None and conn.execute(
            "SELECT id FROM workflows WHERE id=?", (workflow_id,)).fetchone() is None:
        raise TicketError(f"workflow #{workflow_id} no existe")
    _validate_project(conn, project_id)
    cur = conn.execute(
        "INSERT INTO decisions (workflow_id, project_id, title, context, options, asked_by, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (workflow_id, project_id, title.strip(), context or "", json.dumps(opts, ensure_ascii=False),
         agent, _now()))
    _hist(conn, cur.lastrowid, "asked", agent)
    conn.commit()
    return get_decision(conn, cur.lastrowid)


def decide(conn, agent, decision_id, option=None, decision=None, rationale="") -> dict:
    """option = label de una opción literal; decision = texto libre (el dueño puede
    decidir fuera de las opciones — se registra tal cual)."""
    _validate_agent(agent, family_only=True)
    d = get_decision(conn, decision_id)
    if d["status"] == "decided":
        raise TicketError(f"decisión #{decision_id} ya está decidida por {d['decided_by']} "
                          f"({d['decided_at']}): {d['decision']!r} — no se decide dos veces, abre otra")
    if option:
        match = next((o for o in d["options"] if o["label"] == option), None)
        if match is None:
            raise TicketError(f"opción desconocida: {option!r} (válidas: {[o['label'] for o in d['options']]})")
        chosen = match["label"]
    elif (decision or "").strip():
        chosen = decision.strip()
    else:
        raise TicketError("decide requiere option (label) o decision (texto libre)")
    if not (rationale or "").strip():
        raise TicketError("decide requiere rationale (por qué)")
    conn.execute(
        "UPDATE decisions SET status='decided', decided_by=?, decision=?, rationale=?, decided_at=?"
        " WHERE id=?", (agent, chosen, rationale.strip(), _now(), decision_id))
    _hist(conn, decision_id, "decided", agent, f"{chosen} — {rationale.strip()}")
    conn.commit()
    return get_decision(conn, decision_id)


def defer(conn, agent, decision_id, until=None, note="") -> dict:
    _validate_agent(agent, family_only=True)
    d = get_decision(conn, decision_id)
    if d["status"] == "decided":
        raise TicketError(f"decisión #{decision_id} ya está decidida")
    until_iso = None
    if until:
        try:
            until_iso = date.fromisoformat(str(until)[:10]).isoformat()
        except ValueError:
            raise TicketError(f"until inválido: {until!r} (YYYY-MM-DD)")
    conn.execute("UPDATE decisions SET status='deferred', deferred_until=? WHERE id=?",
                 (until_iso, decision_id))
    _hist(conn, decision_id, "deferred", agent, (f"hasta {until_iso}. " if until_iso else "") + (note or ""))
    conn.commit()
    return get_decision(conn, decision_id)


def reopen(conn, agent, decision_id, note="") -> dict:
    _validate_agent(agent, family_only=True)
    d = get_decision(conn, decision_id)
    if d["status"] != "deferred":
        raise TicketError(f"decisión #{decision_id} está {d['status']} — solo se retoman las aplazadas")
    conn.execute("UPDATE decisions SET status='pending', deferred_until=NULL WHERE id=?", (decision_id,))
    _hist(conn, decision_id, "reopened", agent, note or None)
    conn.commit()
    return get_decision(conn, decision_id)


def list_decisions(conn, status=None, workflow_id=None, project_id=None) -> list[dict]:
    where, params = [], []
    if status:
        if status not in STATUSES:
            raise TicketError(f"status inválido: {status!r}")
        where.append("d.status=?"); params.append(status)
    if workflow_id is not None:
        where.append("d.workflow_id=?"); params.append(workflow_id)
    if project_id is not None:
        where.append("d.project_id=?"); params.append(project_id)
    sql = ("SELECT d.*, w.name AS wf_name FROM decisions d"
           " LEFT JOIN workflows w ON w.id = d.workflow_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE d.status WHEN 'pending' THEN 0 WHEN 'deferred' THEN 1 ELSE 2 END, d.id DESC"
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    ph = ",".join("?" * len(ids))
    hist_rows = conn.execute(
        f"SELECT decision_id, event, actor, changed_at, note FROM decision_history"
        f" WHERE decision_id IN ({ph}) ORDER BY id", ids).fetchall()
    hist_map: dict[int, list[dict]] = {did: [] for did in ids}
    for h in hist_rows:
        hist_map[h["decision_id"]].append(
            {"event": h["event"], "actor": h["actor"],
             "changed_at": h["changed_at"], "note": h["note"]})
    out = []
    for r in rows:
        d = dict(r)
        d["options"] = json.loads(d["options"] or "[]")
        d["history"] = hist_map.get(d["id"], [])
        d["workflow_name"] = d.pop("wf_name", None)
        out.append(d)
    return out


def pending_for_workflow(conn, workflow_id) -> list[dict]:
    return [d for d in list_decisions(conn, workflow_id=workflow_id) if d["status"] != "decided"]
