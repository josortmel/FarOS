"""#919 (decisiones vigentes): decide(..., supersedes=[n]) marca «superada por» en las decisiones anteriores.

Se valida todo ANTES de escribir: si una de las sustituidas no vale, la decisión nueva no se decide.
"""

import pytest

from faros import db
from faros import decisions as dec
from faros.service import HOUSE_OWNER, TicketError

OPTS = [{"label": "sí", "text": "sí, hacerlo"}, {"label": "no", "text": "no hacerlo"}]


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _pendiente(conn, titulo="¿lo hago?"):
    return dec.ask(conn, "alice", titulo, OPTS)["id"]


def _decidida(conn, **kw):
    return dec.decide(conn, HOUSE_OWNER, _pendiente(conn), option="sí", rationale="porque sí", **kw)


def test_la_nueva_marca_la_vieja_con_quien_cuando_y_por_que(conn):
    vieja = _decidida(conn)["id"]
    nueva = _decidida(conn, supersedes=[vieja], supersede_reason="cambió el horario")["id"]
    v = dec.get_decision(conn, vieja)
    assert v["superseded_by"] == nueva
    assert v["supersede_kind"] == "total"
    assert v["supersede_reason"] == "cambió el horario"
    assert v["marked_by"]["by"] == HOUSE_OWNER and v["marked_by"]["at"] and v["marked_by"]["via"] == f"decide #{nueva}"
    assert any(h["event"] == "superseded" and f"#{nueva}" in h["note"] for h in v["history"])


def test_decidido_por_y_valid_until_quedan_en_la_nueva(conn):
    d = _decidida(conn, decidido_por={"tipo": "humano", "quien": "Pepe"}, valid_until="2026-12-31")
    assert d["decidido_por"] == {"tipo": "humano", "quien": "Pepe"}
    assert d["valid_until"] == "2026-12-31"


@pytest.mark.parametrize("caso", ["no_existe", "pendiente", "ya_superada", "a_si_misma", "repetida"])
def test_una_sustituida_que_no_vale_deja_la_nueva_sin_decidir(conn, caso):
    nueva = _pendiente(conn, "¿la nueva?")
    if caso == "no_existe":
        objetivo = [9999]
    elif caso == "pendiente":
        objetivo = [_pendiente(conn)]
    elif caso == "ya_superada":
        vieja = _decidida(conn)["id"]
        _decidida(conn, supersedes=[vieja])
        objetivo = [vieja]
    elif caso == "a_si_misma":
        objetivo = [nueva]
    else:
        vieja = _decidida(conn)["id"]
        objetivo = [vieja, vieja]
    with pytest.raises(TicketError) as e:
        dec.decide(conn, HOUSE_OWNER, nueva, option="sí", rationale="r", supersedes=objetivo)
    assert e.value.field == "supersedes"
    assert dec.get_decision(conn, nueva)["status"] == "pending"


def test_nunca_valida_solo_el_dueno(conn):
    vieja = _decidida(conn)["id"]
    otra = _pendiente(conn)
    with pytest.raises(TicketError) as e:
        dec.decide(conn, "alice", otra, option="sí", rationale="r", supersedes=[vieja], supersede_kind="nunca_valida")
    assert e.value.field == "supersede_kind"
    assert dec.get_decision(conn, vieja)["superseded_by"] is None
    d = dec.decide(
        conn, HOUSE_OWNER, otra, option="sí", rationale="r", supersedes=[vieja], supersede_kind="nunca_valida"
    )
    assert dec.get_decision(conn, vieja)["supersede_kind"] == "nunca_valida" and d["status"] == "decided"


@pytest.mark.parametrize(
    "kw,campo",
    [
        ({"decidido_por": {"tipo": "jefe", "quien": "x"}}, "decidido_por"),
        ({"decidido_por": {"tipo": "humano"}}, "decidido_por"),
        ({"valid_until": "mañana"}, "valid_until"),
        ({"supersede_reason": "sin a quién"}, "supersede_reason"),
    ],
)
def test_valores_malos_se_rechazan_con_su_campo(conn, kw, campo):
    nueva = _pendiente(conn)
    with pytest.raises(TicketError) as e:
        dec.decide(conn, HOUSE_OWNER, nueva, option="sí", rationale="r", **kw)
    assert e.value.field == campo
    assert dec.get_decision(conn, nueva)["status"] == "pending"


def test_una_base_v11_recibe_las_columnas_al_migrar(tmp_path):
    """Una base de antes (sin las columnas de #919) migra a la v12 sin perder sus decisiones."""
    ruta = tmp_path / "vieja.db"
    c = db.connect(str(ruta))
    d = dec.ask(c, "alice", "¿vieja?", OPTS)
    for col in ("superseded_by", "supersede_kind", "supersede_reason", "marked_by", "decidido_por", "valid_until"):
        c.execute(f"ALTER TABLE decisions DROP COLUMN {col}")
    c.execute("DELETE FROM schema_version WHERE version >= 12")
    c.commit()
    c.close()
    c = db.connect(str(ruta))
    cols = {r[1] for r in c.execute("PRAGMA table_info(decisions)")}
    assert {"superseded_by", "supersede_kind", "marked_by", "decidido_por", "valid_until"} <= cols
    assert dec.get_decision(c, d["id"])["title"] == "¿vieja?"
    assert db.schema_version(c) == db.SCHEMA_VERSION
    c.close()


# ---- por HTTP y por MCP (#919): los campos llegan a decide() ----

import sqlite3  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from faros import api, mcp_server  # noqa: E402

TOKEN = "t-919"
H = {"X-FarOS-Token": TOKEN}


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "load_token", lambda: TOKEN)
    db.connect(tmp_path / "t.db").close()
    c = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    yield TestClient(api.create_app(c)), c
    c.close()


def test_por_http_supersedes_marca_la_vieja(cliente):
    client, c = cliente
    vieja = _decidida(c)["id"]
    nueva = _pendiente(c, "¿la nueva?")
    r = client.post(
        f"/api/decisions/{nueva}/decide",
        headers=H,
        json={
            "agent": HOUSE_OWNER,
            "option": "sí",
            "rationale": "r",
            "supersedes": [vieja],
            "supersede_reason": "cambia",
            "decidido_por": {"tipo": "humano", "quien": "Pepe"},
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["decidido_por"] == {"tipo": "humano", "quien": "Pepe"}
    assert dec.get_decision(c, vieja)["superseded_by"] == nueva


def test_por_http_una_sustituida_mala_es_400_con_su_campo(cliente):
    client, c = cliente
    nueva = _pendiente(c)
    r = client.post(
        f"/api/decisions/{nueva}/decide",
        headers=H,
        json={"agent": HOUSE_OWNER, "option": "sí", "rationale": "r", "supersedes": [9999]},
    )
    assert r.status_code == 400, r.text
    assert r.json()["field"] == "supersedes"
    assert dec.get_decision(c, nueva)["status"] == "pending"


def test_el_mcp_reenvia_los_campos(monkeypatch):
    enviados = []
    monkeypatch.setattr(mcp_server, "_call", lambda m, path, **kw: enviados.append((path, kw.get("json"))) or {})
    fn = getattr(mcp_server.decide, "fn", mcp_server.decide)
    fn(
        agent=HOUSE_OWNER,
        decision_id=7,
        rationale="r",
        option="sí",
        supersedes=[3],
        supersede_kind="parcial",
        supersede_reason="x",
        decidido_por={"tipo": "equipo", "quien": "casa"},
        valid_until="2026-12-01",
    )
    path, body = enviados[-1]
    assert path == "/api/decisions/7/decide"
    assert body["supersedes"] == [3] and body["supersede_kind"] == "parcial"
    assert body["decidido_por"] == {"tipo": "equipo", "quien": "casa"} and body["valid_until"] == "2026-12-01"
