"""Tests de AgenticOS v2 — workflows, deps, despacho, findings, hitos (SPEC v2)."""

import pytest

from faros import db, service as s, workflows as w
from faros.service import TicketError


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


PLAN = {
    "workflow": {"name": "demo-build", "kind": "construccion",
                 "spec_path": "docs/SPEC.md", "project": "Demo",
                 "coordinator": "alice"},
    "phases": ["fundacion", "core"],
    "tasks": [
        {"key": "T1", "phase": "fundacion", "title": "Esquema de datos",
         "objetivo": "crear schema", "criterio": "pytest del schema en verde",
         "verification_level": "peer"},
        {"key": "T2", "phase": "fundacion", "title": "Service layer",
         "criterio": "tests service verdes", "depende_de": ["T1"]},
        {"key": "T3", "phase": "core", "title": "API",
         "criterio": "smoke curl ok", "depende_de": ["T2"]},
    ],
}


def _imported(conn):
    return w.import_plan(conn, "alice", PLAN)


def _tid(conn, wf, key):
    for phase in wf["tickets_by_phase"].values():
        for t in phase:
            if t["plan_key"] == key:
                return t["id"]
    raise AssertionError(key)


# ------------------------------------------------------------ import + contrato

def test_import_plan_creates_workflow_tasks_deps(conn):
    wf = _imported(conn)
    assert wf["name"] == "demo-build" and wf["status"] == "active"
    assert wf["phases"] == ["fundacion", "core"]
    assert wf["pct_global"] == 0
    t1, t2 = wf["tickets_by_phase"]["fundacion"]
    assert t1["plan_key"] == "T1" and t1["ready"] is True
    assert t2["plan_key"] == "T2" and t2["ready"] is False
    assert t2["unmet_deps"][0]["plan_key"] == "T1"
    assert t1["status"] == "accepted"  # nacen aceptadas: el gate fue el diseño
    assert t1["verify_criteria"] == "pytest del schema en verde"


def test_import_plan_all_or_nothing(conn):
    bad = {**PLAN, "tasks": PLAN["tasks"] + [
        {"key": "T9", "phase": "inexistente", "title": "x", "criterio": "y"}]}
    with pytest.raises(TicketError, match="fase 'inexistente'"):
        w.import_plan(conn, "alice", bad)
    assert conn.execute("SELECT COUNT(*) c FROM workflows").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM tickets").fetchone()["c"] == 0


def test_import_plan_rejects_cycles_dups_unknown_deps(conn):
    cyc = {**PLAN, "tasks": [
        {"key": "A", "phase": "core", "title": "a", "criterio": "c", "depende_de": ["B"]},
        {"key": "B", "phase": "core", "title": "b", "criterio": "c", "depende_de": ["A"]}]}
    with pytest.raises(TicketError, match="ciclo"):
        w.import_plan(conn, "alice", cyc)
    dup = {**PLAN, "tasks": PLAN["tasks"] + [dict(PLAN["tasks"][0])]}
    with pytest.raises(TicketError, match="duplicadas"):
        w.import_plan(conn, "alice", dup)
    unk = {**PLAN, "tasks": [{"key": "A", "phase": "core", "title": "a",
                              "criterio": "c", "depende_de": ["NOPE"]}]}
    with pytest.raises(TicketError, match="NOPE"):
        w.import_plan(conn, "alice", unk)


def test_reimport_same_name_rejected(conn):
    _imported(conn)
    with pytest.raises(TicketError, match="ya existe"):
        w.import_plan(conn, "alice", PLAN)


def test_workflow_agents_cannot_import(conn):
    with pytest.raises(TicketError, match="sin permiso"):
        w.import_plan(conn, "code", PLAN)


# ------------------------------------------------------------ deps + despacho

def test_deps_gate_start_and_dispatch(conn):
    wf = _imported(conn)
    t1, t2 = _tid(conn, wf, "T1"), _tid(conn, wf, "T2")
    with pytest.raises(TicketError, match="dependencias sin verificar"):
        w.dispatch_ticket(conn, "alice", t2, "code")
    with pytest.raises(TicketError, match="dependencias sin verificar"):
        s.start_ticket(conn, "code", t2)
    # ciclo de vida de T1 por el peer despachado
    w.dispatch_ticket(conn, "alice", t1, "code", note="lote 1")
    fresh = s.list_tickets(conn)[0]
    s.start_ticket(conn, "code", t1)
    s.complete_ticket(conn, "code", t1, "command_output", "pytest 10 passed")
    s.verify_ticket(conn, "verificador", t1, "pass", "comprobado")
    # ahora T2 está ready y despachable
    st = w.workflow_status(conn, wf["id"])
    t2_card = [t for t in st["tickets_by_phase"]["fundacion"] if t["id"] == t2][0]
    assert t2_card["ready"] is True
    d = w.dispatch_ticket(conn, "alice", t2, "code")
    assert d["dispatched_to"] == "code" and d["owner"] == "code"


def test_dispatch_only_coordinator_or_family(conn):
    wf = _imported(conn)
    t1 = _tid(conn, wf, "T1")
    with pytest.raises(TicketError, match="coordinador"):
        w.dispatch_ticket(conn, "code", t1, "code")
    w.dispatch_ticket(conn, "owner", t1, "code")  # familia sí


def test_add_dep_rejects_cycle_and_cross_workflow(conn):
    wf = _imported(conn)
    t1, t2 = _tid(conn, wf, "T1"), _tid(conn, wf, "T2")
    with pytest.raises(TicketError, match="ciclo"):
        w.add_dep(conn, "alice", t1, t2)  # T2 ya depende de T1
    solo = s.propose_ticket(conn, "owner", "fuera de workflow")["ticket"]
    with pytest.raises(TicketError, match="MISMO workflow"):
        w.add_dep(conn, "alice", t1, solo["id"])
    with pytest.raises(TicketError, match="sí misma"):
        w.add_dep(conn, "alice", t1, t1)


# ------------------------------------------------------------ findings

def _verify_t1(conn, wf):
    t1 = _tid(conn, wf, "T1")
    w.dispatch_ticket(conn, "alice", t1, "code")
    s.start_ticket(conn, "code", t1)
    s.complete_ticket(conn, "code", t1, "command_output", "ok")
    s.verify_ticket(conn, "verificador", t1, "pass")
    return t1


def test_finding_full_cycle_open_dispatched_fixed(conn):
    wf = _imported(conn)
    t1 = _verify_t1(conn, wf)
    f = w.add_finding(conn, "adv-code", wf["id"], "high",
                      "SQL injection en el filtro", "detalle...", ticket_id=t1)
    assert f["status"] == "open" and f["found_by"] == "adv-code"
    fd = w.dispatch_finding(conn, "alice", f["id"], note="prioridad")
    assert fd["status"] == "dispatched"
    fix_id = fd["correction_ticket_id"]
    fix = s._as_dict(conn, s._get(conn, fix_id))
    assert fix["title"].startswith("FIX:") and fix["workflow_id"] == wf["id"]
    assert fix["priority"] == "alta" and fix["verification_level"] == "peer"
    # corrección verificada → finding fixed AUTOMÁTICO
    s.start_ticket(conn, "code", fix_id)
    s.complete_ticket(conn, "code", fix_id, "file_path", "fixed.py")
    s.verify_ticket(conn, "adv-code", fix_id, "pass", "ya no reproduce")
    assert w.get_finding(conn, f["id"])["status"] == "fixed"


def test_finding_dismiss_requires_note_and_coordinator(conn):
    wf = _imported(conn)
    f = w.add_finding(conn, "adv-seg", wf["id"], "low", "nit de estilo")
    with pytest.raises(TicketError, match="coordinador"):
        w.dismiss_finding(conn, "adv-seg", f["id"], "no procede")
    with pytest.raises(TicketError, match="note"):
        w.dismiss_finding(conn, "alice", f["id"], "")
    d = w.dismiss_finding(conn, "alice", f["id"], "estilo, no bug")
    assert d["status"] == "dismissed"


def test_finding_validation(conn):
    wf = _imported(conn)
    with pytest.raises(TicketError, match="severity"):
        w.add_finding(conn, "adv-code", wf["id"], "urgente", "x")
    with pytest.raises(TicketError, match="title"):
        w.add_finding(conn, "adv-code", wf["id"], "high", "  ")
    solo = s.propose_ticket(conn, "owner", "ticket suelto")["ticket"]
    with pytest.raises(TicketError, match="no es del workflow"):
        w.add_finding(conn, "adv-code", wf["id"], "high", "x", ticket_id=solo["id"])


# ------------------------------------------------------------ hitos + estado

def test_phase_milestone_and_memory_draft(conn):
    wf = _imported(conn)
    t1, t2 = _tid(conn, wf, "T1"), _tid(conn, wf, "T2")
    _verify_t1(conn, wf)
    assert w.workflow_status(conn, wf["id"])["milestones"] == []  # T2 abierta aún
    w.dispatch_ticket(conn, "alice", t2, "code")
    s.start_ticket(conn, "code", t2)
    s.complete_ticket(conn, "code", t2, "command_output", "verde")
    s.verify_ticket(conn, "verificador", t2, "pass")
    st = w.workflow_status(conn, wf["id"])
    ms = st["milestones"]
    assert len(ms) == 1 and ms[0]["kind"] == "phase_done" and ms[0]["phase"] == "fundacion"
    assert st["milestones_pending_ecodb"]
    draft = w.memory_draft(conn, wf["id"], ms[0]["id"])
    assert "fase 'fundacion'" in draft["content"] and "T1" in draft["content"]
    assert draft["suggested_type"] == "tecnico"
    saved = w.mark_milestone_saved(conn, "alice", wf["id"], ms[0]["id"], "mem-uuid-123")
    assert saved["ecodb_saved"] == 1 and saved["memory_id"] == "mem-uuid-123"
    assert w.workflow_status(conn, wf["id"])["milestones_pending_ecodb"] == []


def test_workflow_close_creates_milestone_and_blocks_dispatch(conn):
    wf = _imported(conn)
    t1 = _tid(conn, wf, "T1")
    w.update_workflow(conn, "alice", wf["id"], status="closed")
    st = w.workflow_status(conn, wf["id"])
    assert st["status"] == "closed"
    assert any(m["kind"] == "workflow_closed" for m in st["milestones"])
    with pytest.raises(TicketError, match="closed"):
        w.dispatch_ticket(conn, "alice", t1, "code")


def test_progress_counters(conn):
    wf = _imported(conn)
    _verify_t1(conn, wf)
    st = w.workflow_status(conn, wf["id"])
    fnd = [p for p in st["phases_summary"] if p["phase"] == "fundacion"][0]
    assert fnd["total"] == 2 and fnd["verified"] == 1 and fnd["ready"] == 1
    assert st["pct_global"] == 33  # 1 de 3


# ------------------------------------------------------------ ronda adversarial v2

def test_fix_rejected_reopens_finding(conn):
    # bob F1: la corrección muere → el finding no se queda dispatched eterno
    wf = _imported(conn)
    t1 = _verify_t1(conn, wf)
    f = w.add_finding(conn, "adv-code", wf["id"], "high", "bug X", ticket_id=t1)
    fd = w.dispatch_finding(conn, "alice", f["id"])
    s.reject_ticket(conn, "alice", fd["correction_ticket_id"], "enfoque equivocado")
    fresh = w.get_finding(conn, f["id"])
    assert fresh["status"] == "open" and fresh["correction_ticket_id"] is None
    assert "reabierto" in (fresh["resolved_note"] or "")


def test_dismiss_dispatched_rejects_fix_not_reopen(conn):
    # bob F2 + orden: dismiss mata al FIX y el finding queda dismissed (no open)
    wf = _imported(conn)
    f = w.add_finding(conn, "adv-seg", wf["id"], "medium", "concern Y")
    fd = w.dispatch_finding(conn, "alice", f["id"])
    d = w.dismiss_finding(conn, "alice", f["id"], "falso positivo tras revisar")
    assert d["status"] == "dismissed"
    fix = s._as_dict(conn, s._get(conn, fd["correction_ticket_id"]))
    assert fix["status"] == "rejected"


def test_add_dep_rejected_on_running_task(conn):
    # bob F5
    wf = _imported(conn)
    t1, t3 = _tid(conn, wf, "T1"), _tid(conn, wf, "T3")
    s.start_ticket(conn, "alice", t1)
    with pytest.raises(TicketError, match="en 'in_progress'"):
        w.add_dep(conn, "alice", t1, t3)


def test_finding_category_and_suggestion_travel_to_fix(conn):
    # carol: category + la sugerencia viaja al ticket FIX
    wf = _imported(conn)
    with pytest.raises(TicketError, match="category"):
        w.add_finding(conn, "adv-code", wf["id"], "high", "x", category="tipo-raro")
    f = w.add_finding(conn, "adv-code", wf["id"], "high", "N+1 en board",
                      detail="la query se repite", category="degradation",
                      suggestion="cachear projects en el loop")
    fd = w.dispatch_finding(conn, "alice", f["id"])
    fix = s._as_dict(conn, s._get(conn, fd["correction_ticket_id"]))
    assert "Sugerencia de adv-code: cachear projects" in fix["description"]
    assert "degradation" in fix["description"]


def test_plan_hints_imported(conn):
    # bob P1/P3: agente_sugerido + plan_data
    plan = {**PLAN, "workflow": {**PLAN["workflow"], "name": "demo-hints"},
            "tasks": [{**PLAN["tasks"][0], "agente_sugerido": "code",
                       "estimacion": "M"}] + PLAN["tasks"][1:]}
    wf = w.import_plan(conn, "alice", plan)
    t1 = wf["tickets_by_phase"]["fundacion"][0]
    assert t1["suggested_agent"] == "code"
    import json as _json
    assert _json.loads(t1["plan_data"])["estimacion"] == "M"


def test_recent_activity_and_handoff(conn):
    # carol SNAG-5 + encargo de owner: handoff en un solo sitio
    wf = _imported(conn)
    _verify_t1(conn, wf)
    w.add_finding(conn, "adv-seg", wf["id"], "low", "nit")
    acts = w.recent_activity(conn, wf["id"])
    assert any(a["kind"] == "finding" for a in acts)
    assert any(a["kind"] == "ticket" and a["new_status"] == "verified" for a in acts)
    # draft auto-compuesto → editar → guardar → status lo embebe
    draft = w.handoff_draft(conn, wf["id"])
    assert "Progreso global" in draft["content"] and "T1" in draft["content"]
    with pytest.raises(TicketError, match="coordinador"):
        w.write_handoff(conn, "code", wf["id"], draft["content"])
    h = w.write_handoff(conn, "alice", wf["id"], draft["content"] + "\nNota mía.")
    st = w.workflow_status(conn, wf["id"])
    assert st["latest_handoff"]["id"] == h["id"]
    assert st["recent_activity"]


def test_memory_draft_prose(conn):
    # carol: prosa con decisiones, descartados y abiertos
    wf = _imported(conn)
    t1, t2 = _tid(conn, wf, "T1"), _tid(conn, wf, "T2")
    _verify_t1(conn, wf)
    f = w.add_finding(conn, "adv-code", wf["id"], "low", "estilo raro")
    w.dismiss_finding(conn, "alice", f["id"], "no es bug, es convención del repo")
    w.dispatch_ticket(conn, "alice", t2, "code")
    s.start_ticket(conn, "code", t2)
    s.complete_ticket(conn, "code", t2, "command_output", "verde")
    s.verify_ticket(conn, "verificador", t2, "pass", "decisión: usamos WAL")
    ms = w.workflow_status(conn, wf["id"])["milestones"]
    draft = w.memory_draft(conn, wf["id"], ms[0]["id"])
    c = draft["content"]
    assert "completado el" in c                      # frase de apertura, prosa
    assert "conocimiento negativo" in c and "no es bug" in c
    assert "decisión: usamos WAL" in c               # notas del history


# ------------------------------------------------------------ batch dispatch

def test_dispatch_batch_all_or_nothing(conn):
    """5 valid → 5 dispatched; 1 invalid in the batch → 0 and reason names culprit."""
    wf = _imported(conn)
    t1 = _tid(conn, wf, "T1")
    t2 = _tid(conn, wf, "T2")  # has unmet dep on T1

    # batch with one invalid (t2 has unmet deps) → 0 dispatched
    with pytest.raises(TicketError, match="items\\[1\\].*dependencias sin verificar"):
        w.dispatch_batch(conn, "alice", wf["id"], [
            {"ticket_id": t1, "to": "code", "note": "lote"},
            {"ticket_id": t2, "to": "code"},
        ])
    # verify t1 was NOT dispatched (all-or-nothing)
    fresh = dict(conn.execute("SELECT * FROM tickets WHERE id=?", (t1,)).fetchone())
    assert fresh["dispatched_to"] is None


def test_dispatch_batch_success(conn):
    """All valid → all dispatched in one commit."""
    wf = _imported(conn)
    t1 = _tid(conn, wf, "T1")
    result = w.dispatch_batch(conn, "alice", wf["id"], [
        {"ticket_id": t1, "to": "code", "note": "batch test"},
    ])
    assert result["count"] == 1
    assert result["dispatched"][0]["to"] == "code"
    assert result["dispatched"][0]["ticket_id"] == t1
    fresh = dict(conn.execute("SELECT * FROM tickets WHERE id=?", (t1,)).fetchone())
    assert fresh["dispatched_to"] == "code"


def test_dispatch_batch_rejects_empty(conn):
    wf = _imported(conn)
    with pytest.raises(TicketError, match="items vacío"):
        w.dispatch_batch(conn, "alice", wf["id"], [])


def test_dispatch_batch_rejects_duplicate_ticket(conn):
    wf = _imported(conn)
    t1 = _tid(conn, wf, "T1")
    with pytest.raises(TicketError, match="duplicado"):
        w.dispatch_batch(conn, "alice", wf["id"], [
            {"ticket_id": t1, "to": "code"},
            {"ticket_id": t1, "to": "bob"},
        ])


def test_dispatch_batch_rejects_wrong_workflow(conn):
    wf = _imported(conn)
    solo = s.propose_ticket(conn, "owner", "outside", auto_accept=True)["ticket"]
    with pytest.raises(TicketError, match="no pertenece al workflow"):
        w.dispatch_batch(conn, "alice", wf["id"], [
            {"ticket_id": solo["id"], "to": "code"},
        ])


def test_dispatch_batch_rejects_non_coordinator(conn):
    wf = _imported(conn)
    t1 = _tid(conn, wf, "T1")
    with pytest.raises(TicketError, match="coordinador"):
        w.dispatch_batch(conn, "code", wf["id"], [
            {"ticket_id": t1, "to": "bob"},
        ])


def test_v1_regression_intact(conn):
    # sanity: lo de v1 sigue funcionando con el schema migrado
    t = s.propose_ticket(conn, "owner", "tarea normal", auto_accept=True)["ticket"]
    s.start_ticket(conn, "alice", t["id"])
    r = s.complete_ticket(conn, "alice", t["id"], "command_output", "ok")
    assert r["status"] == "verified"
