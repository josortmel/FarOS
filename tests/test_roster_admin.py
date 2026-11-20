"""#245 (Hilo) + #252 (Lienzo): el roster editable desde la app, en caliente.

El perfil ES el agente del roster (Pepe, 25-sep). Contrato cerrado con Lienzo el
30-sep; política de tareas decidida por Pepe el mismo día:
  · renombrar se lleva sus tareas (abiertas y cerradas) y la coordinación de sus workflows;
  · suspender (no borrar: «prefiero suspenderlo») exige a quién pasan las abiertas;
    las cerradas conservan el nombre, son historia;
  · borrar solo vale para un nombre sin ninguna tarea;
  · el dueño no se renombra, no se suspende y no se borra.
Recarga en caliente: AGENTS es el MISMO objeto antes y después (agenda, decisions y
workflows lo copian al importar), así que se muta en su sitio.
"""

import json

import pytest

from faros import db, decisions, roster
from faros import roster_admin as ra
from faros import service as s
from faros import workflows as w


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Roster desde FICHERO (la suite lo fija por env: aquí se quita) en una casa aparte."""
    for k in ("FAROS_OWNER", "FAROS_AGENTS", "AGENTICOS_OWNER", "AGENTICOS_AGENTS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FAROS_HOME", str(tmp_path))
    (tmp_path / "roster.json").write_text(
        json.dumps({"owner": "owner", "agents": ["owner", "alice", "bob", "carol", "dave"]}), encoding="utf-8"
    )
    roster.reload()
    yield tmp_path
    monkeypatch.undo()
    roster.reload()  # vuelve al roster por env de la suite


@pytest.fixture
def conn(home):
    c = db.connect(":memory:")
    yield c
    c.close()


def _ticket(conn, owner, title="t"):
    return s.propose_ticket(conn, "owner", title, owner=owner)["ticket"]


def _owner_of(conn, tid):
    return s.ticket_history(conn, tid)["ticket"]["owner"]


def _names(conn):
    return {a["name"]: a for a in ra.listing(conn)["agents"]}


def _code(fn, *a, **k):
    with pytest.raises(ra.RosterError) as e:
        fn(*a, **k)
    return e.value


# ---------------------------------------------------------------- lectura


def test_listing_says_source_owner_and_counts(conn):
    t = _ticket(conn, "alice")
    _ticket(conn, "alice")
    s.reject_ticket(conn, "owner", t["id"], "no hace falta ya")
    r = ra.listing(conn)
    assert r["owner"] == "owner" and r["source"] == "file" and r["editable"] is True
    assert "env_var" not in r
    a = _names(conn)["alice"]
    assert (a["open_tickets"], a["total_tickets"], a["suspended"]) == (1, 2, False)


def test_env_roster_is_read_only_and_names_the_variable(conn, monkeypatch):
    monkeypatch.setenv("FAROS_OWNER", "owner")
    monkeypatch.setenv("FAROS_AGENTS", "owner,alice")
    roster.reload()
    r = ra.listing(conn)
    assert r["source"] == "env" and r["editable"] is False and r["env_var"] == "FAROS_AGENTS"
    e = _code(ra.create, conn, "zoe")
    assert e.status == 409 and "FAROS_AGENTS" in str(e)


# ---------------------------------------------------------------- alta


def test_create_is_live_everywhere_without_restart(conn, home):
    before = s.AGENTS
    ra.create(conn, "  zoe ")
    assert s.AGENTS is before, "AGENTS se muta en su sitio: los módulos que lo importaron lo ven"
    assert "zoe" in s.AGENTS and "zoe" in decisions.DECIDERS
    assert _ticket(conn, "zoe")["owner"] == "zoe"
    on_disk = json.loads((home / "roster.json").read_text(encoding="utf-8"))
    assert "zoe" in on_disk["agents"] and on_disk["owner"] == "owner"


@pytest.mark.parametrize(
    "bad, status",
    [
        ("", 422),
        ("   ", 422),
        ("x" * 41, 422),
        ("verificador", 422),
        ("Runner-Bot", 422),
        ("ALICE", 409),
    ],
)
def test_create_rejects(conn, bad, status):
    e = _code(ra.create, conn, bad)
    assert e.status == status and e.field == "name"


def test_default_roster_first_write_creates_the_file(tmp_path, monkeypatch):
    for k in ("FAROS_OWNER", "FAROS_AGENTS", "AGENTICOS_OWNER", "AGENTICOS_AGENTS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FAROS_HOME", str(tmp_path))
    roster.reload()
    c = db.connect(":memory:")
    try:
        assert ra.listing(c)["source"] == "default"
        ra.create(c, "zoe")
        data = json.loads((tmp_path / "roster.json").read_text(encoding="utf-8"))
        assert data["owner"] == roster.DEFAULT_OWNER
        assert set(roster.DEFAULT_AGENTS) | {"zoe"} <= set(data["agents"])
        assert ra.listing(c)["source"] == "file"
    finally:
        c.close()
        monkeypatch.undo()
        roster.reload()


# ---------------------------------------------------------------- renombrar


def test_rename_moves_open_and_closed_tickets_and_coordination(conn):
    open_t = _ticket(conn, "alice")
    closed = _ticket(conn, "alice")
    s.reject_ticket(conn, "owner", closed["id"], "no hace falta ya")
    wf = w.import_plan(
        conn,
        "alice",
        {
            "workflow": {"name": "wf-ren", "kind": "construccion", "coordinator": "alice"},
            "phases": ["p"],
            "tasks": [{"key": "T1", "phase": "p", "title": "x", "criterio": "y"}],
        },
    )
    ra.rename(conn, "alice", "Alicia")
    assert _owner_of(conn, open_t["id"]) == "Alicia"
    assert _owner_of(conn, closed["id"]) == "Alicia"
    assert w.get_workflow(conn, wf["id"])["coordinator"] == "Alicia"
    assert "alice" not in s.AGENTS and "Alicia" in s.AGENTS
    # la historia conserva quién hizo qué con el nombre de entonces
    hist = s.ticket_history(conn, open_t["id"])["history"]
    assert any("alice" in (h["note"] or "") and "Alicia" in (h["note"] or "") for h in hist)


def test_rename_rules(conn):
    assert _code(ra.rename, conn, "owner", "boss").status == 403
    assert _code(ra.rename, conn, "nobody", "x").status == 404
    e = _code(ra.rename, conn, "alice", "BOB")
    assert e.status == 409 and e.field == "name"
    ra.rename(conn, "alice", "Alice")  # solo mayúsculas: vale, es el mismo agente
    assert "Alice" in s.AGENTS


# ---------------------------------------------------------------- suspender


def test_suspend_with_open_tickets_requires_reassign(conn):
    t1, t2 = _ticket(conn, "carol"), _ticket(conn, "carol")
    closed = _ticket(conn, "carol")
    s.reject_ticket(conn, "owner", closed["id"], "no hace falta ya")
    e = _code(ra.set_suspended, conn, "carol", True)
    assert e.status == 409 and e.extra["open_tickets"] == 2
    assert _code(ra.set_suspended, conn, "carol", True, reassign_to="carol").status == 422
    assert _code(ra.set_suspended, conn, "carol", True, reassign_to="nobody").status == 422
    res = ra.set_suspended(conn, "carol", True, reassign_to="dave")
    assert res["reassigned"] == 2
    assert {_owner_of(conn, i) for i in (t1["id"], t2["id"])} == {"dave"}
    assert _owner_of(conn, closed["id"]) == "carol", "las cerradas son historia"
    assert "carol" not in s.AGENTS and "carol" in roster.SUSPENDED
    a = _names(conn)["carol"]
    assert a["suspended"] is True and a["total_tickets"] == 1
    with pytest.raises(s.TicketError):
        _ticket(conn, "carol")  # no es dueño válido para trabajo nuevo


def test_suspend_without_open_tickets_and_reactivate(conn):
    ra.set_suspended(conn, "bob", True)
    assert "bob" not in s.AGENTS
    assert _code(ra.set_suspended, conn, "carol", True, reassign_to="bob").status == 422, (
        "no se reasigna a un suspendido"
    )
    res = ra.set_suspended(conn, "bob", False)
    assert res["reassigned"] == 0 and "bob" in s.AGENTS and "bob" not in roster.SUSPENDED


def test_owner_cannot_be_suspended(conn):
    assert _code(ra.set_suspended, conn, "owner", True).status == 403


def test_rename_a_suspended_agent_keeps_it_suspended(conn):
    ra.set_suspended(conn, "bob", True)
    ra.rename(conn, "bob", "roberto")
    assert "roberto" in roster.SUSPENDED and "roberto" not in s.AGENTS


# ---------------------------------------------------------------- borrar


def test_delete_only_without_any_ticket(conn):
    _ticket(conn, "alice")
    e = _code(ra.delete, conn, "alice")
    assert e.status == 409 and "suspénd" in str(e).lower()
    ra.create(conn, "typo")
    ra.delete(conn, "typo")
    assert "typo" not in s.AGENTS and "typo" not in _names(conn)
    assert _code(ra.delete, conn, "owner").status == 403
    assert _code(ra.delete, conn, "nobody").status == 404


# ---------------------------------------------------------------- HTTP (contrato con la interfaz)


def test_http_contract(home, tmp_path, monkeypatch):
    import sqlite3

    from fastapi.testclient import TestClient

    from faros import api

    monkeypatch.setattr(api, "load_token", lambda: "t-test")
    db.connect(tmp_path / "t.db").close()
    c = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    client = TestClient(api.create_app(c))
    h = {"X-FarOS-Token": "t-test"}
    try:
        r = client.get("/api/roster", headers=h)
        assert r.status_code == 200 and r.json()["source"] == "file"
        r = client.post("/api/roster/agents", json={"name": "zoe"}, headers=h)
        assert r.status_code == 201 and r.json() == {"agent": "zoe"}
        assert "zoe" in client.get("/api/meta", headers=h).json()["agents"]
        r = client.post("/api/roster/agents", json={"name": "ZOE"}, headers=h)
        assert r.status_code == 409 and r.json()["error"] == "conflict" and r.json()["field"] == "name"
        r = client.post("/api/roster/agents", json={"name": "code"}, headers=h)
        assert r.status_code == 422 and r.json()["field"] == "name"
        client.post("/api/tickets", json={"agent": "owner", "title": "a", "owner": "zoe"}, headers=h)
        r = client.patch("/api/roster/agents/zoe", json={"suspended": True}, headers=h)
        assert r.status_code == 409 and r.json()["open_tickets"] == 1
        r = client.patch("/api/roster/agents/zoe", json={"suspended": True, "reassign_to": "alice"}, headers=h)
        assert r.status_code == 200 and r.json()["reassigned"] == 1
        assert "zoe" not in client.get("/api/meta", headers=h).json()["agents"]
        z = next(a for a in client.get("/api/roster", headers=h).json()["agents"] if a["name"] == "zoe")
        assert z["suspended"] is True
        r = client.patch("/api/roster/agents/owner", json={"name": "boss"}, headers=h)
        assert r.status_code == 403 and r.json()["error"] == "forbidden"
        r = client.delete("/api/roster/agents/nobody", headers=h)
        assert r.status_code == 404 and r.json()["error"] == "not_found"
        r = client.delete("/api/roster/agents/bob", headers=h)
        assert r.status_code == 200 and r.json()["removed"] is True
        r = client.patch("/api/roster/agents/alice", json={}, headers=h)
        assert r.status_code == 400
    finally:
        c.close()
