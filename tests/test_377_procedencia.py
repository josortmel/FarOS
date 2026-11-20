"""#377 — cada verificación deja su procedencia en el historial.

Quinto laboratorio (#346, «Workflow Closure Is Not Scientific Closure»): en septiembre 95 de cada 100
verificaciones fueron Claude comprobando a Claude, y FarOS no lo distinguía de un test ni de la firma
del dueño. Forma mínima aprobada por Pepe (decisión 21, 8-oct; propuesta de Prima en
Prima/laboratorio/procedencia_377/septiembre.md):
- `external` y `same-family` se deducen de quién verifica (el dueño de la casa es externo; la dueña de
  la tarea que se la sella ella misma, `same-family:self`);
- solo `deterministic:<herramienta>` se declara.
"""

import sqlite3

import pytest

from faros import db
from faros import service as s
from faros.service import TicketError


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _hecho(conn, level="peer", by="alice"):
    t = s.propose_ticket(conn, "owner", "trabajo", owner=by, verification_level=level, scheduled_at="2031-03-05T10:00")[
        "ticket"
    ]
    s.accept_ticket(conn, by, t["id"])
    s.complete_ticket(conn, by, t["id"], "file_path", "informe.md")
    return t["id"]


def _ultima(conn, tid):
    return s.ticket_history(conn, tid)["history"][-1]


def test_un_par_de_la_familia_es_same_family(conn):
    tid = _hecho(conn)
    s.verify_ticket(conn, "bob", tid, "pass")
    assert _ultima(conn, tid)["provenance"] == "same-family"


def test_el_dueno_de_la_casa_es_external(conn):
    tid = _hecho(conn)
    s.verify_ticket(conn, s.HOUSE_OWNER, tid, "pass")
    assert _ultima(conn, tid)["provenance"] == "external"


def test_la_duena_que_se_la_sella_es_same_family_self(conn):
    tid = _hecho(conn, level="self")
    fila = _ultima(conn, tid)
    assert fila["new_status"] == "verified"
    assert fila["provenance"] == "same-family:self"


def test_determinista_declarado_se_guarda(conn):
    tid = _hecho(conn)
    s.verify_ticket(conn, "bob", tid, "pass", provenance="deterministic:pytest tests/test_x.py")
    assert _ultima(conn, tid)["provenance"] == "deterministic:pytest tests/test_x.py"


def test_determinista_tambien_al_sellarse_una_misma(conn):
    t = s.propose_ticket(
        conn, "owner", "trabajo", owner="alice", verification_level="self", scheduled_at="2031-03-05T10:00"
    )["ticket"]
    s.accept_ticket(conn, "alice", t["id"])
    s.complete_ticket(conn, "alice", t["id"], "command_output", "13/13", provenance="deterministic:gzip -t")
    assert _ultima(conn, t["id"])["provenance"] == "deterministic:gzip -t"


def test_el_fail_tambien_lleva_procedencia(conn):
    tid = _hecho(conn)
    s.verify_ticket(conn, "bob", tid, "fail", note="falta la mitad", provenance="deterministic:grep")
    fila = _ultima(conn, tid)
    assert (fila["new_status"], fila["provenance"]) == ("in_progress", "deterministic:grep")


@pytest.mark.parametrize(
    "mala",
    [
        "external",
        "same-family",
        "deterministic:",
        "deterministic:  ",
        "deterministic:a\nb",
        "deterministic:" + "x" * 200,
    ],
)
def test_lo_que_no_es_determinista_no_se_declara(conn, mala):
    tid = _hecho(conn)
    with pytest.raises(TicketError) as e:
        s.verify_ticket(conn, "bob", tid, "pass", provenance=mala)
    assert e.value.code == "verify.provenance_invalid"
    assert s._get(conn, tid)["status"] == "done"


def test_el_bot_verificador_es_same_family(conn):
    t = s.propose_ticket(
        conn, "owner", "auto", owner="alice", verification_level="auto", scheduled_at="2031-03-05T10:00"
    )["ticket"]
    s.accept_ticket(conn, "alice", t["id"])
    s.complete_ticket(conn, "alice", t["id"], "file_path", "x.md")
    s.verify_ticket(conn, "verifier-bot", t["id"], "pass")
    assert _ultima(conn, t["id"])["provenance"] == "same-family"


def test_las_transiciones_que_no_verifican_no_llevan_procedencia(conn):
    tid = _hecho(conn)
    filas = s.ticket_history(conn, tid)["history"]
    assert all(f["provenance"] is None for f in filas)


def test_una_base_v7_migra_y_gana_la_columna(tmp_path):
    path = tmp_path / "v7.db"
    c = db.connect(path)
    c.close()
    raw = sqlite3.connect(str(path))
    raw.execute("ALTER TABLE ticket_history DROP COLUMN provenance")
    raw.execute("DELETE FROM schema_version")
    raw.execute("INSERT INTO schema_version (version, applied_at) VALUES (7, '2026-10-08')")
    raw.commit()
    raw.close()
    c = db.connect(path)
    assert "provenance" in {r[1] for r in c.execute("PRAGMA table_info(ticket_history)")}
    assert db.schema_version(c) == db.SCHEMA_VERSION >= 8  # migra a la ACTUAL (9 desde #657)
    assert list((tmp_path / "backups").glob("faros_pre_migration_*.db"))
    c.close()


def test_por_la_api_se_declara_y_se_ve_en_el_historial(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from faros import api

    monkeypatch.setattr(api, "load_token", lambda: "t-377")
    db.connect(tmp_path / "t.db").close()
    c = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    cli, h = TestClient(api.create_app(c)), {"X-FarOS-Token": "t-377"}
    try:
        tid = cli.post(
            "/api/tickets",
            headers=h,
            json={
                "agent": "owner",
                "title": "por api",
                "owner": "alice",
                "verification_level": "peer",
                "scheduled_at": "2031-03-05T10:00",
            },
        ).json()["ticket"]["id"]
        for body in (
            {"agent": "alice", "action": "accept"},
            {"agent": "alice", "action": "complete", "evidence_type": "file_path", "evidence": "x.md"},
        ):
            assert cli.post(f"/api/tickets/{tid}/transition", headers=h, json=body).status_code == 200
        mala = cli.post(
            f"/api/tickets/{tid}/transition",
            headers=h,
            json={"agent": "bob", "action": "verify", "provenance": "external"},
        )
        # #722: es un error de entrada (nombra el campo) → 400 con field; el code no cambia (#270)
        assert mala.status_code == 400 and mala.json()["code"] == "verify.provenance_invalid"
        assert mala.json()["field"] == "provenance"
        ok = cli.post(
            f"/api/tickets/{tid}/transition",
            headers=h,
            json={"agent": "bob", "action": "verify", "provenance": "deterministic:pytest"},
        )
        assert ok.status_code == 200 and ok.json()["status"] == "verified"
        hist = cli.get(f"/api/tickets/{tid}/history", headers=h).json()["history"]
        assert hist[-1]["provenance"] == "deterministic:pytest"
    finally:
        c.close()
