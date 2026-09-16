"""T2.1 (v3.1): owner mueve cualquier ticket; rework devuelve done/verified con motivo."""
import pytest

from faros import db, service as s, workflows as w
from faros.service import TicketError


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _peer_ticket(conn, owner="carol"):
    t = s.propose_ticket(conn, "owner", "tarea de carol", owner=owner, verification_level="peer",
                         auto_accept=True)
    return t["ticket"]["id"] if "ticket" in t else t["id"]


def _done(conn, tid, owner="carol"):
    s.start_ticket(conn, owner, tid)
    s.complete_ticket(conn, owner, tid, "file_path", "x.md")
    return s._as_dict(conn, s._get(conn, tid))


def test_owner_can_start_any_ticket(conn):
    tid = _peer_ticket(conn)
    t = s.start_ticket(conn, "owner", tid)
    assert t["status"] == "in_progress" and t["owner"] == "carol"
    h = s.ticket_history(conn, tid)["history"]
    assert h[-1]["changed_by"] == "owner"


def test_other_agent_cannot_start_foreign_ticket(conn):
    tid = _peer_ticket(conn)
    with pytest.raises(TicketError, match="solo puede hacerlo el owner"):
        s.start_ticket(conn, "bob", tid)


def test_rework_requires_reason(conn):
    tid = _peer_ticket(conn)
    _done(conn, tid)
    with pytest.raises(TicketError, match="motivo"):
        s.rework_ticket(conn, "owner", tid, "accepted", "")
    with pytest.raises(TicketError, match="motivo"):
        s.rework_ticket(conn, "owner", tid, "accepted", "corto")


def test_rework_from_done_keeps_history_and_evidence(conn):
    tid = _peer_ticket(conn)
    _done(conn, tid)
    t = s.rework_ticket(conn, "owner", tid, "accepted", "la evidencia no cubre el criterio, falta el test")
    assert t["status"] == "accepted" and t["closed_at"] is None
    assert t["evidence"] == "x.md"  # la evidencia se conserva
    h = s.ticket_history(conn, tid)["history"]
    statuses = [(r["old_status"], r["new_status"]) for r in h]
    assert ("in_progress", "done") in statuses
    assert statuses[-1] == ("done", "accepted")
    assert h[-1]["note"].startswith("[rechazo] ")


def test_rework_from_verified_by_owner_and_by_verifier(conn):
    tid = _peer_ticket(conn)
    _done(conn, tid)
    s.verify_ticket(conn, "bob", tid, "pass", "ok")
    assert s._get(conn, tid)["status"] == "verified"
    with pytest.raises(TicketError, match="rework solo puede hacerlo"):
        s.rework_ticket(conn, "dave", tid, "in_progress", "no soy nadie aquí para devolverla")
    t = s.rework_ticket(conn, "bob", tid, "in_progress", "verifiqué mal: el criterio pedía dos casos")
    assert t["status"] == "in_progress" and t["verified_by"] is None and t["closed_at"] is None
    h = s.ticket_history(conn, tid)["history"]
    assert [(r["old_status"], r["new_status"]) for r in h][-2:] == [("done", "verified"), ("verified", "in_progress")]
    # segunda vuelta completa: se verifica otra vez y el history lo cuenta entero
    s.complete_ticket(conn, "carol", tid, "file_path", "x2.md")
    s.verify_ticket(conn, "bob", tid, "pass", "ahora sí")
    assert len(s.ticket_history(conn, tid)["history"]) >= 7


def test_rework_illegal_from_open_states_and_recurrentes(conn):
    tid = _peer_ticket(conn)
    with pytest.raises(TicketError, match="rework ilegal desde"):
        s.rework_ticket(conn, "owner", tid, "accepted", "motivo suficientemente largo")
    rec = s.propose_ticket(conn, "owner", "recurrente", owner="alice", cadence_days=7, auto_accept=True)
    rid = rec["ticket"]["id"] if "ticket" in rec else rec["id"]
    s.complete_ticket(conn, "alice", rid, "command_output", "estampada")
    with pytest.raises(TicketError, match="recurrentes|rework ilegal"):
        s.rework_ticket(conn, "owner", rid, "accepted", "motivo suficientemente largo")


def test_rework_verified_fix_reopens_finding_as_dispatched(conn):
    plan = {"workflow": {"name": "wf", "kind": "construccion", "spec_path": "docs/SPEC.md",
                         "project": "Demo", "coordinator": "alice"},
            "phases": ["core"],
            "tasks": [{"key": "T1", "phase": "core", "title": "x", "criterio": "y",
                       "verification_level": "peer"}]}
    wf = w.import_plan(conn, "alice", plan)
    t1 = wf["tickets_by_phase"]["core"][0]["id"]
    s.start_ticket(conn, "code", t1)
    s.complete_ticket(conn, "code", t1, "file_path", "a.py")
    s.verify_ticket(conn, "bob", t1, "pass", "ok")
    f = w.add_finding(conn, "adv-code", wf["id"], "high", "bug", ticket_id=t1)
    fix_id = w.dispatch_finding(conn, "alice", f["id"])["correction_ticket_id"]
    s.start_ticket(conn, "code", fix_id)
    s.complete_ticket(conn, "code", fix_id, "file_path", "fix.py")
    s.verify_ticket(conn, "bob", fix_id, "pass", "ya no reproduce")
    assert w.get_finding(conn, f["id"])["status"] == "fixed"
    s.rework_ticket(conn, "owner", fix_id, "in_progress", "sigue reproduciendo en la app instalada")
    fnd = w.get_finding(conn, f["id"])
    assert fnd["status"] == "dispatched" and fnd["correction_ticket_id"] == fix_id


def test_manual_ticket_without_time_gets_auto_anchor(conn):
    t = s.propose_ticket(conn, "owner", "sin hora", owner="alice", auto_accept=True)["ticket"]
    assert t["scheduled_at"] and t["scheduled_at"][11:16] == "14:00" and t["duration_min"] == 60
    h = s.ticket_history(conn, t["id"])["history"]
    assert any("[ancla automática]" in (r["note"] or "") for r in h)
    r = s.propose_ticket(conn, "owner", "recurrente sin hora", owner="alice", cadence_days=7, auto_accept=True)["ticket"]
    assert r["preferred_time"] == "14:00"
    e = s.propose_ticket(conn, "owner", "con hora", owner="alice", scheduled_at="2026-09-10T09:00")["ticket"]
    assert e["scheduled_at"] == "2026-09-10T09:00"
