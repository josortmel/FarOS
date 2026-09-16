"""Tests de decisiones/bisagras (T3.2)."""

import pytest

from faros import db, decisions as dec, workflows as w
from faros.service import TicketError


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


OPTS = [{"label": "opencode", "text": "opencode (deepseek por API; valida el presupuesto real)"},
        {"label": "ninguno", "text": "ninguno, solo el contrato"},
        {"label": "copilot", "text": "copilot"}]


def test_ask_decide_once(conn):
    d = dec.ask(conn, "alice", "Segundo harness en v3", OPTS, context="decisión 2 del plan")
    assert d["status"] == "pending" and d["options"][0]["label"] == "opencode"
    assert [h["event"] for h in d["history"]] == ["asked"]
    d = dec.decide(conn, "owner", d["id"], option="opencode", rationale="valida el presupuesto real")
    assert d["status"] == "decided" and d["decided_by"] == "owner" and d["decision"] == "opencode"
    assert d["decided_at"] and d["history"][-1]["event"] == "decided"
    with pytest.raises(TicketError, match="no se decide dos veces"):
        dec.decide(conn, "owner", d["id"], option="copilot", rationale="cambio de idea")
    with pytest.raises(TicketError, match="ya está decidida"):
        dec.defer(conn, "owner", d["id"])


def test_free_text_decision_and_required_rationale(conn):
    d = dec.ask(conn, "alice", "Excel", OPTS)
    with pytest.raises(TicketError, match="rationale"):
        dec.decide(conn, "owner", d["id"], option="ninguno")
    with pytest.raises(TicketError, match="opción desconocida"):
        dec.decide(conn, "owner", d["id"], option="D", rationale="x")
    d = dec.decide(conn, "owner", d["id"], decision="congelado de referencia hasta betatestear", rationale="mi criterio")
    assert d["decision"].startswith("congelado")


def test_options_must_be_literal(conn):
    with pytest.raises(TicketError, match="nunca A/B/C"):
        dec.ask(conn, "alice", "x", [{"label": "A", "text": "opción a"}, {"label": "B", "text": "b"}])
    with pytest.raises(TicketError, match="no vacía"):
        dec.ask(conn, "alice", "x", [])
    with pytest.raises(TicketError, match="duplicada"):
        dec.ask(conn, "alice", "x", ["misma", "misma"])
    d = dec.ask(conn, "alice", "strings", ["sí y sustituye los .md", "sí pero los .md siguen"])
    assert d["options"][0]["label"] == "sí y sustituye los .md"


def test_defer_reopen_list_and_workflow_link(conn):
    wf = w.import_plan(conn, "alice", {
        "workflow": {"name": "demo", "coordinator": "alice"}, "phases": ["f"],
        "tasks": [{"key": "T1", "phase": "f", "title": "t", "criterio": "c"}]})
    d = dec.ask(conn, "alice", "bisagra del workflow", OPTS, workflow_id=wf["id"])
    d = dec.defer(conn, "owner", d["id"], until="2026-09-10", note="cuando vuelva")
    assert d["status"] == "deferred" and d["deferred_until"] == "2026-09-10"
    assert dec.list_decisions(conn, status="deferred")[0]["id"] == d["id"]
    d = dec.reopen(conn, "alice", d["id"])
    assert d["status"] == "pending" and d["workflow_name"] == "demo"
    assert [x["id"] for x in dec.pending_for_workflow(conn, wf["id"])] == [d["id"]]
    with pytest.raises(TicketError, match="permiso"):
        dec.decide(conn, "code", d["id"], option="opencode", rationale="x")
    with pytest.raises(TicketError, match="no existe"):
        dec.ask(conn, "alice", "x", OPTS, workflow_id=999)


def test_workflow_status_and_handoff_draft_show_pending_decisions(conn, tmp_path):
    wf = w.import_plan(conn, "alice", {
        "workflow": {"name": "demo2", "coordinator": "alice", "plan_path": "plans/agenticos-v3.plan.json",
                     "spec_path": "docs/SPEC.md"},
        "phases": ["f"], "tasks": [{"key": "T1", "phase": "f", "title": "t", "criterio": "c"}]})
    d = dec.ask(conn, "alice", "¿Segundo harness?", OPTS, workflow_id=wf["id"])
    st = w.workflow_status(conn, wf["id"])
    assert st["decisions_pending_count"] == 1 and st["decisions_pending"][0]["id"] == d["id"]
    draft = w.handoff_draft(conn, wf["id"])["content"]
    assert "Bisagras PENDIENTES de owner (1)" in draft and "opencode / ninguno / copilot" in draft
    dec.decide(conn, "owner", d["id"], option="opencode", rationale="ok")
    assert w.workflow_status(conn, wf["id"], embed_tickets=False)["decisions_pending_count"] == 0
    # documents() contra una raíz que construye el test, no contra el checkout
    # (16-sep): antes resolvía spec_path/plan_path relativos a la carpeta del
    # paquete, así que el test pasaba en el repo de desarrollo —donde existen
    # docs/SPEC.md y plans/— y salía ROJO en el repo público, que no los lleva.
    # No era un defecto: era el test mirando lo que tenía al lado.
    fake_root = tmp_path / "repo"
    (fake_root / "docs").mkdir(parents=True)
    (fake_root / "plans").mkdir(parents=True)
    (fake_root / "docs" / "SPEC.md").write_text("# spec", encoding="utf-8")
    (fake_root / "plans" / "agenticos-v3.plan.json").write_text("{}", encoding="utf-8")
    docs = w.documents(conn, wf["id"], repo_root=str(fake_root))
    kinds = {x["kind"] for x in docs}
    assert {"spec", "plan"} <= kinds and all(x["path"] for x in docs)
