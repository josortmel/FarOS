"""T4.1/T4.2 (v3.1): archivo/cancelación de workflows, búsqueda por nombre, repo_root."""
from pathlib import Path

import pytest

from faros import db, service as s, workflows as w
from faros.service import TicketError


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _plan(name):
    return {"workflow": {"name": name, "kind": "construccion", "spec_path": "docs/SPEC.md",
                         "plan_path": f"plans/{name}.plan.json", "project": "Demo",
                         "coordinator": "alice"},
            "phases": ["core"],
            "tasks": [{"key": "T1", "phase": "core", "title": "x", "criterio": "y"}]}


def test_archive_only_from_closed_or_cancelled(conn):
    wf = w.import_plan(conn, "alice", _plan("alpha-v1"))
    with pytest.raises(TicketError, match="solo se archiva"):
        w.update_workflow(conn, "alice", wf["id"], status="archived")
    w.update_workflow(conn, "alice", wf["id"], status="closed")
    res = w.update_workflow(conn, "alice", wf["id"], status="archived")
    assert res["status"] == "archived"
    row = w.get_workflow(conn, wf["id"])
    assert row["archived_at"] is not None and row["closed_at"] is not None
    # desarchivar = closed
    res = w.update_workflow(conn, "alice", wf["id"], status="closed")
    assert res["status"] == "closed" and w.get_workflow(conn, wf["id"])["archived_at"] is None
    w.update_workflow(conn, "alice", wf["id"], status="archived")
    with pytest.raises(TicketError, match="archivado solo vuelve"):
        w.update_workflow(conn, "alice", wf["id"], status="paused")


def test_cancel_requires_reason_and_rejects_open_tickets(conn):
    wf = w.import_plan(conn, "alice", _plan("beta-v1"))
    with pytest.raises(TicketError, match="requiere reason"):
        w.update_workflow(conn, "alice", wf["id"], status="cancelled")
    res = w.update_workflow(conn, "alice", wf["id"], status="cancelled", reason="cambió el producto")
    assert res["status"] == "cancelled"
    t = conn.execute("SELECT status FROM tickets WHERE workflow_id=?", (wf["id"],)).fetchone()
    assert t["status"] == "rejected"
    h = conn.execute("SELECT note FROM ticket_history WHERE ticket_id="
                     "(SELECT id FROM tickets WHERE workflow_id=?) ORDER BY id DESC LIMIT 1",
                     (wf["id"],)).fetchone()
    assert "[workflow cancelado] cambió el producto" == h["note"]
    assert w.update_workflow(conn, "alice", wf["id"], status="archived")["status"] == "archived"


def test_list_filters_and_search(conn):
    a = w.import_plan(conn, "alice", _plan("agenticos-v3"))
    b = w.import_plan(conn, "alice", _plan("knowtwin-v2"))
    w.update_workflow(conn, "alice", a["id"], status="closed")
    w.update_workflow(conn, "alice", a["id"], status="archived")
    assert [x["name"] for x in w.list_workflows(conn, status="archived")] == ["agenticos-v3"]
    assert [x["name"] for x in w.list_workflows(conn, status="open")] == ["knowtwin-v2"]
    assert [x["name"] for x in w.list_workflows(conn, q="AGENT")] == ["agenticos-v3"]
    assert [x["name"] for x in w.list_workflows(conn, status="archived", q="know")] == []
    assert len(w.list_workflows(conn)) == 2


def test_peer_cannot_archive(conn):
    wf = w.import_plan(conn, "alice", _plan("gamma-v1"))
    w.update_workflow(conn, "alice", wf["id"], status="closed")
    with pytest.raises(TicketError, match="coordinador"):
        w.update_workflow(conn, "code", wf["id"], status="archived")


def test_repo_root_on_import_and_patch_and_documents(conn, tmp_path):
    root = tmp_path / "repo"
    (root / "plans").mkdir(parents=True)
    (root / "docs" / "mediciones").mkdir(parents=True)
    (root / "reviews").mkdir()
    (root / "docs" / "SPEC.md").write_text("# spec", encoding="utf-8")
    (root / "plans" / "delta-v1.plan.json").write_text("{}", encoding="utf-8")
    (root / "reviews" / "r1.md").write_text("ok", encoding="utf-8")
    with pytest.raises(TicketError, match="repo_root"):
        w.import_plan(conn, "alice", _plan("delta-v0"), repo_root="relativo/no")
    wf = w.import_plan(conn, "alice", _plan("delta-v1"), repo_root=str(root))
    assert w.get_workflow(conn, wf["id"])["repo_root"] == str(root.resolve())
    kinds = {(d["kind"], d["name"]) for d in w.documents(conn, wf["id"])}
    assert ("spec", "SPEC.md") in kinds and ("plan", "delta-v1.plan.json") in kinds
    assert ("review", "r1.md") in kinds
    # sin repo_root: la lista no encuentra los ficheros del paquete (no es el repo)
    wf2 = w.import_plan(conn, "alice", _plan("delta-v2"))
    assert all(Path(d["path"]).is_absolute() for d in w.documents(conn, wf2["id"]))
    # PATCH corrige la raíz
    w.update_workflow(conn, "alice", wf2["id"], repo_root=str(root))
    names = {d["name"] for d in w.documents(conn, wf2["id"])}
    assert "SPEC.md" in names and "r1.md" in names


def test_settings_repo_root_default_used(conn, tmp_path):
    from faros import settings
    root = tmp_path / "repo2"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "SPEC.md").write_text("# spec", encoding="utf-8")
    wf = w.import_plan(conn, "alice", _plan("eps-v1"))
    settings.patch(conn, "alice", {"repo_root_default": str(root)})
    assert "SPEC.md" in {d["name"] for d in w.documents(conn, wf["id"])}


def test_archive_active_in_one_step_and_reactivate(conn):
    wf = w.import_plan(conn, "alice", _plan("zeta-v1"))
    res = w.update_workflow(conn, "alice", wf["id"], status="archived", close=True)
    assert res["status"] == "archived"
    row = w.get_workflow(conn, wf["id"])
    assert row["closed_at"] and row["archived_at"]
    res = w.update_workflow(conn, "alice", wf["id"], status="active")
    assert res["status"] == "active"
    row = w.get_workflow(conn, wf["id"])
    assert row["closed_at"] is None and row["archived_at"] is None
