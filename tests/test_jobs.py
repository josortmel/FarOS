"""Tests de jobs desligados (T1.1): entidad propia, veredicto por run, brazo automático."""

from datetime import datetime

import pytest

from faros import db, service as s, jobs
from faros.service import TicketError
from faros.scheduler import due_jobs


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _job(conn, **kw):
    args = dict(agent="owner", name="Resumen de correo", prompt="resume mis correos")
    args.update(kw)
    return jobs.create_job(conn, **args)


def _manual(conn, **kw):
    args = dict(agent="owner", title="Leer el correo de la mañana", auto_accept=True,
                verification_level="peer")
    args.update(kw)
    return s.propose_ticket(conn, **args)["ticket"]


def test_job_without_ticket_runs_and_touches_no_ticket(conn):
    before = conn.execute("SELECT COUNT(*) FROM ticket_history").fetchone()[0]
    j = _job(conn)
    assert j["status"] == "active" and j["ticket_id"] is None and j["billing_mode"] == "subscription"
    run = jobs.create_run(conn, j["id"])
    assert run["status"] == "running" and run["ticket_id"] is None
    done = jobs.finish_run(conn, run["id"], "ok", cost_usd=0.02, result_summary="3 correos")
    assert done["status"] == "ok" and done["cost_kind"] == "equivalent" and done["verdict"] is None
    v = jobs.set_verdict(conn, run["id"], "pass", "resumen coherente")
    assert v["verdict"] == "pass" and v["verified_at"]
    assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ticket_history").fetchone()[0] == before
    j2 = jobs.get_job(conn, j["id"])
    assert j2["last_run"]["verdict"] == "pass" and j2["total_equivalent_usd"] == 0.02
    assert j2["total_cost_usd"] == 0.0  # suscripción: nada facturado


def test_linked_ticket_completed_on_pass_not_on_fail(conn):
    t = _manual(conn)
    j = _job(conn, ticket_id=t["id"])
    assert j["ticket"]["id"] == t["id"]
    run = jobs.create_run(conn, j["id"])
    assert s._as_dict(conn, s._get(conn, t["id"]))["status"] == "in_progress"
    jobs.finish_run(conn, run["id"], "ok", result_summary="hecho")
    assert s._as_dict(conn, s._get(conn, t["id"]))["status"] == "in_progress"  # espera veredicto
    jobs.set_verdict(conn, run["id"], "fail", "el resumen inventa un remitente")
    fresh = s._as_dict(conn, s._get(conn, t["id"]))
    assert fresh["status"] == "in_progress"
    assert any("veredicto fail" in (h["note"] or "") for h in s.ticket_history(conn, t["id"])["history"])
    run2 = jobs.create_run(conn, j["id"])
    jobs.finish_run(conn, run2["id"], "ok", result_summary="hecho bien")
    jobs.set_verdict(conn, run2["id"], "pass", "coincide con el buzón")
    fresh = s._as_dict(conn, s._get(conn, t["id"]))
    assert fresh["status"] == "done" and fresh["evidence_type"] == "run_output"
    assert fresh["evidence"] == str(run2["id"])  # nivel peer: queda en cola de verificación humana


def test_linked_auto_ticket_verified_once_on_pass(conn):
    t = _manual(conn, verification_level="auto")
    j = _job(conn, ticket_id=t["id"])
    run = jobs.create_run(conn, j["id"])
    jobs.finish_run(conn, run["id"], "ok", result_summary="x")
    jobs.set_verdict(conn, run["id"], "pass", "ok")
    fresh = s._as_dict(conn, s._get(conn, t["id"]))
    assert fresh["status"] == "verified" and fresh["verified_by"] == "verifier-bot"


def test_run_error_blocks_linked_ticket(conn):
    t = _manual(conn)
    j = _job(conn, ticket_id=t["id"])
    run = jobs.create_run(conn, j["id"])
    jobs.finish_run(conn, run["id"], "timeout")
    fresh = s._as_dict(conn, s._get(conn, t["id"]))
    assert fresh["status"] == "blocked" and "run #" in fresh["blocked_reason"]


def test_one_job_per_ticket_and_only_manual_open(conn):
    t = _manual(conn)
    _job(conn, ticket_id=t["id"])
    with pytest.raises(TicketError, match="ya tiene brazo"):
        _job(conn, ticket_id=t["id"], name="otro")
    s.reject_ticket(conn, "owner", t["id"], "no")
    with pytest.raises(TicketError, match="cerrado"):
        _job(conn, ticket_id=t["id"], name="otro2")


def test_pause_resume_archive(conn):
    j = _job(conn, schedule={"type": "once", "run_at": "2026-09-01T08:00:00"})
    assert [x["id"] for x in due_jobs(conn, datetime(2026, 9, 1, 8, 1))] == [j["id"]]
    jobs.set_status(conn, "owner", j["id"], "paused")
    assert due_jobs(conn, datetime(2026, 9, 1, 8, 1)) == []
    with pytest.raises(TicketError, match="paused"):
        jobs.create_run(conn, j["id"])
    jobs.set_status(conn, "owner", j["id"], "active")
    run = jobs.create_run(conn, j["id"])
    with pytest.raises(TicketError, match="run en curso"):
        jobs.set_status(conn, "owner", j["id"], "archived")
    jobs.finish_run(conn, run["id"], "cancelled")
    jobs.set_status(conn, "owner", j["id"], "archived")
    with pytest.raises(TicketError, match="archived"):
        jobs.create_run(conn, j["id"])
    with pytest.raises(TicketError, match="archivado"):
        jobs.set_status(conn, "owner", j["id"], "active")
    assert [x["id"] for x in jobs.list_jobs(conn)] == []
    assert [x["id"] for x in jobs.list_jobs(conn, include_archived=True)] == [j["id"]]


def test_duplicate_copies_config_not_runs(conn):
    t = _manual(conn)
    j = _job(conn, ticket_id=t["id"], model="sonnet", inherit_mcp=["agenticos"],
             add_dirs=["C:/data"], effort="high", json_schema={"type": "object"})
    run = jobs.create_run(conn, j["id"])
    jobs.finish_run(conn, run["id"], "ok")
    d = jobs.duplicate_job(conn, "owner", j["id"])
    assert d["id"] != j["id"] and d["name"].endswith("(copia)") and d["status"] == "active"
    assert d["model"] == "sonnet" and d["inherit_mcp"] == ["agenticos"] and d["add_dirs"] == ["C:/data"]
    assert d["effort"] == "high" and d["json_schema"] == '{"type": "object"}'
    assert d["ticket_id"] is None and d["runs_count"] == 0 and d["last_run"] is None


def test_budget_ignored_on_subscription_with_warning(conn):
    j = _job(conn, max_budget_usd=5)
    assert j["max_budget_usd"] is None and "ignorado" in j["warning"]


def test_update_and_next_fire(conn):
    j = _job(conn)
    assert j["next_fire"] is None
    u = jobs.update_job(conn, "owner", j["id"],
                        schedule={"type": "recurring", "every_days": 1, "at": "23:59"})
    assert u["next_fire"] is not None and u["next_fire"].endswith("23:59")
    with pytest.raises(TicketError, match="no editables"):
        jobs.update_job(conn, "owner", j["id"], billing_mode="api")
    with pytest.raises(TicketError, match="verify_level"):
        jobs.update_job(conn, "owner", j["id"], verify_level="peer")


def test_pending_verdicts_and_results(conn):
    j = _job(conn)
    run = jobs.create_run(conn, j["id"])
    jobs.finish_run(conn, run["id"], "ok")
    assert [r["id"] for r in jobs.runs_pending_verdict(conn)] == [run["id"]]
    j2 = _job(conn, name="sin verificar", verify_level="none")
    run2 = jobs.create_run(conn, j2["id"])
    jobs.finish_run(conn, run2["id"], "ok")
    assert [r["id"] for r in jobs.runs_pending_verdict(conn)] == [run["id"]]
    with pytest.raises(TicketError, match="no terminó ok"):
        jobs.set_verdict(conn, jobs.create_run(conn, j["id"])["id"], "pass", "x")
    res = jobs.results(conn)
    assert {r["job"]["id"] for r in res} == {j["id"], j2["id"]}
