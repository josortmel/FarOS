"""#1349: la ficha necesita leer TODAS las dependencias de una tarea, en las dos direcciones.

GET /api/tickets/{id} trae `deps` (las tareas a las que espera, hechas o no) y `blocks` (las tareas
que la esperan), cada una con id, plan_key, title y status. Solo en el GET de UNA tarea (acuerdo con
Hilo, 23-nov): las listas siguen con unmet_deps y no cargan esto.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from faros import api, db
from faros import service as s
from faros import workflows as w

TOKEN = "t-1349"
H = {"X-FarOS-Token": TOKEN}

PLAN = {
    "workflow": {"name": "deps-1349", "coordinator": "alice"},
    "phases": ["F"],
    "tasks": [
        {"key": "T1", "phase": "F", "title": "Uno", "criterio": "c"},
        {"key": "T2", "phase": "F", "title": "Dos", "criterio": "c", "depende_de": ["T1"]},
        {"key": "T3", "phase": "F", "title": "Tres", "criterio": "c", "depende_de": ["T1", "T2"]},
    ],
}


@pytest.fixture
def cl(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "load_token", lambda: TOKEN)
    c0 = db.connect(tmp_path / "f.db")
    wf = w.import_plan(c0, "alice", PLAN)
    ids = {t["plan_key"]: t["id"] for ph in wf["tickets_by_phase"].values() for t in ph}
    c0.close()
    conn = sqlite3.connect(str(tmp_path / "f.db"), check_same_thread=False)  # TestClient corre en otro hilo
    conn.row_factory = sqlite3.Row
    c = TestClient(api.create_app(conn))
    c.conn, c.ids = conn, ids
    yield c
    conn.close()


def _get(cl, key):
    r = cl.get(f"/api/tickets/{cl.ids[key]}", headers=H)
    assert r.status_code == 200
    return r.json()


def test_deps_y_blocks_en_las_dos_direcciones(cl):
    t1, t2, t3 = (_get(cl, k) for k in ("T1", "T2", "T3"))
    assert [d["plan_key"] for d in t1["deps"]] == []
    assert sorted(d["plan_key"] for d in t1["blocks"]) == ["T2", "T3"]
    assert [d["plan_key"] for d in t2["deps"]] == ["T1"]
    assert [d["plan_key"] for d in t2["blocks"]] == ["T3"]
    assert sorted(d["plan_key"] for d in t3["deps"]) == ["T1", "T2"]
    assert t3["blocks"] == []
    assert set(t3["deps"][0]) >= {"id", "plan_key", "title", "status"}


def test_una_dependencia_hecha_sigue_saliendo(cl):
    """Lo que unmet_deps no puede dar: la dependencia ya cumplida se ve, para poder quitarla."""
    t1 = cl.ids["T1"]
    cl.conn.execute("UPDATE tickets SET status='verified' WHERE id=?", (t1,))
    cl.conn.commit()
    deps = _get(cl, "T2")["deps"]
    assert [(d["plan_key"], d["status"]) for d in deps] == [("T1", "verified")]
    assert s._unmet_deps(cl.conn, cl.ids["T2"]) == []  # la lista de las que faltan ya no la tiene


def test_anadir_y_quitar_se_ve_en_el_get(cl):
    r = cl.post(f"/api/tickets/{cl.ids['T2']}/deps", json={"agent": "alice", "depends_on": cl.ids["T1"]}, headers=H)
    assert r.status_code == 200
    r = cl.delete(f"/api/tickets/{cl.ids['T3']}/deps/{cl.ids['T1']}?agent=alice", headers=H)
    assert r.status_code == 200
    assert [d["plan_key"] for d in _get(cl, "T3")["deps"]] == ["T2"]
    assert sorted(d["plan_key"] for d in _get(cl, "T1")["blocks"]) == ["T2"]


def test_las_listas_no_cargan_deps(cl):
    r = cl.get("/api/board", headers=H)
    assert r.status_code == 200
    tareas = []

    def recoger(x):
        if isinstance(x, dict):
            if "plan_key" in x and "status" in x:
                tareas.append(x)
            for v in x.values():
                recoger(v)
        elif isinstance(x, list):
            for v in x:
                recoger(v)

    recoger(r.json())
    assert tareas, "el tablero no trae tareas: la prueba no estaría mirando nada"
    assert all("blocks" not in t and "deps" not in t for t in tareas)
