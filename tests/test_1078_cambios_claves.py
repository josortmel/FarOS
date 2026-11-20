"""#1078 — historial de cambios de las claves (sale de #1068, 9-nov: no se pudo reconstruir qué pasó con
una clave el 7-nov porque nada guardaba quién la marcó entregable). Cada alta, cambio de nombre,
origen, valor, en uso, entregable y borrado deja una fila con quién y cuándo. El VALOR no aparece nunca:
de un cambio de valor solo queda que cambió."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from faros import api, db
from faros import credentials as cr
from faros import service as s

SECRETO = "otra-" + "f00dfeed" * 4
OTRO = "otra-" + "c0ffee00" * 4
TOKEN = "t" * 32
H = {"X-FarOS-Token": TOKEN}


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _historial(conn):
    return [(r["name"], r["field"], r["antes"], r["despues"], r["changed_by"]) for r in reversed(cr.changes(conn))]


def test_alta_entregable_valor_nombre_y_borrado_quedan_con_quien(conn):
    c = cr.create(conn, "alice", "Contraseña del router", "otra", SECRETO)
    cr.update(conn, s.HOUSE_OWNER, c["id"], {"deliverable": True})
    cr.update(conn, "bob", c["id"], {"value": OTRO, "origin": "la del cliente"})
    cr.update(conn, "carol", c["id"], {"name": "Router"})
    cr.update(conn, s.HOUSE_OWNER, c["id"], {"deliverable": False})
    cr.delete(conn, "dave", c["id"])
    assert _historial(conn) == [
        ("Contraseña del router", "alta", None, "otra", "alice"),
        ("Contraseña del router", "entregable", "no", "sí", s.HOUSE_OWNER),
        ("Contraseña del router", "origen", "", "la del cliente", "bob"),
        ("Contraseña del router", "valor", None, "cambiado", "bob"),
        ("Router", "nombre", "Contraseña del router", "Router", "carol"),
        ("Router", "entregable", "sí", "no", s.HOUSE_OWNER),
        ("Router", "borrada", None, None, "dave"),
    ]


def test_el_valor_no_sale_nunca_en_el_historial(conn):
    c = cr.create(conn, "alice", "X", "otra", SECRETO)
    cr.update(conn, "alice", c["id"], {"value": OTRO})
    filas = conn.execute("SELECT * FROM credential_changes").fetchall()
    texto = repr([tuple(f) for f in filas])
    assert SECRETO not in texto and OTRO not in texto


def test_lo_que_no_cambia_no_deja_fila(conn):
    c = cr.create(conn, "alice", "X", "otra", SECRETO)
    cr.update(conn, "alice", c["id"], {"name": "X", "value": cr.MASK})
    cr.update(conn, s.HOUSE_OWNER, c["id"], {"deliverable": False})
    assert [f for _, f, *_ in _historial(conn)] == ["alta"]


def test_marcar_en_uso_apunta_la_que_entra_y_la_que_sale(conn):
    a = cr.create(conn, "alice", "DeepSeek A", "deepseek", "sk-a" * 8)  # primera de su tipo: en uso
    b = cr.create(conn, "alice", "DeepSeek B", "deepseek", "sk-b" * 8)
    cr.update(conn, "bob", b["id"], {"in_use": True})
    cr.update(conn, "bob", b["id"], {"in_use": False})
    assert [h for h in _historial(conn) if h[1] == "en uso"] == [
        ("DeepSeek A", "en uso", "no", "sí", "alice"),
        ("DeepSeek A", "en uso", "sí", "no", "bob"),
        ("DeepSeek B", "en uso", "no", "sí", "bob"),
        ("DeepSeek B", "en uso", "sí", "no", "bob"),
    ]
    assert a["id"] != b["id"]


def test_el_rastro_sobrevive_al_borrado_y_a_que_se_reutilice_el_id(conn):
    c = cr.create(conn, "alice", "X", "otra", SECRETO)
    cr.delete(conn, "alice", c["id"])
    d = cr.create(conn, "bob", "Y", "otra", OTRO)
    assert [(n, f) for n, f, *_ in _historial(conn)] == [("X", "alta"), ("X", "borrada"), ("Y", "alta")]
    assert d["id"] == c["id"]  # los ids se reutilizan (#1068): por eso cada fila lleva el nombre


def test_los_ajustes_tambien_dejan_rastro(conn):
    cr.desde_ajuste(conn, "bob", "deepseek_api_key", "sk-x" * 8)
    cr.desde_ajuste(conn, "bob", "deepseek_api_key", "sk-y" * 8)
    cr.desde_ajuste(conn, "dave", "deepseek_api_key", "")
    assert [(f, a, d, by) for _, f, a, d, by in _historial(conn)] == [
        ("alta", None, "deepseek", "bob"),
        ("valor", None, "cambiado", "bob"),
        ("en uso", "sí", "no", "dave"),
    ]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "load_token", lambda: TOKEN)
    db.connect(tmp_path / "t.db").close()
    c = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    yield TestClient(api.create_app(c)), c
    c.close()


def test_api_lista_los_cambios_sin_valores_y_pide_token(client):
    cli, c = client
    k = cr.create(c, "alice", "X", "otra", SECRETO)
    cr.update(c, "alice", k["id"], {"value": OTRO})
    r = cli.get("/api/credentials/changes", headers=H)
    assert r.status_code == 200 and [f["field"] for f in r.json()] == ["valor", "alta"]
    assert SECRETO not in r.text and OTRO not in r.text
    assert cli.get("/api/credentials/changes").status_code == 401


def test_una_base_v12_recibe_la_tabla_al_migrar_y_hace_copia_antes(tmp_path):
    ruta = tmp_path / "v12.db"
    c = db.connect(str(ruta))
    k = cr.create(c, "alice", "X", "otra", SECRETO)
    c.execute("DROP TABLE credential_changes")
    c.execute("DELETE FROM schema_version")
    c.execute("INSERT INTO schema_version (version, applied_at) VALUES (12, '2026-11-10T10:00:00')")
    c.commit()
    c.close()
    c = db.connect(str(ruta))
    assert db.schema_version(c) == db.SCHEMA_VERSION >= 13  # >=: la v14 (#1279) va después
    assert c.execute("SELECT name FROM credentials WHERE id = ?", (k["id"],)).fetchone()["name"] == "X"
    cr.update(c, "alice", k["id"], {"name": "Y"})
    assert [r["field"] for r in cr.changes(c)] == ["nombre"]
    c.close()
    assert list(tmp_path.glob("**/*pre_migration*")), "sin copia previa a la migración"
