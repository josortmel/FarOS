"""#414 y #481 (decisiones 47 y 48 de Pepe, 16-oct).
#414: una tarea cuyo criterio pide a otra persona nacía «self» y se sellaba sola. Ahora el nivel
se puede cambiar (subirlo cualquiera; bajarlo a self solo el dueño de la casa) y FarOS avisa al
crearla y al completarla si el criterio pide a otro y el nivel es self.
#481: apuntarse a verificar EN la tarea; el segundo recibe «ya la tiene X»."""

import threading

import pytest

from faros import db
from faros import service as s
from faros.service import TicketError

CRITERIO_OTRO = "Lo verifica otra persona, no quien lo hace."


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _tarea(conn, owner="alice", nivel="self", criterio=None):
    return s.propose_ticket(
        conn,
        owner,
        "Arreglar el scheduler",
        owner=owner,
        auto_accept=True,
        verification_level=nivel,
        verify_criteria=criterio,
    )


# ------------------------------------------------------------------ #414


def test_subir_el_nivel_de_self_a_peer_lo_puede_hacer_la_duena(conn):
    t = _tarea(conn)["ticket"]
    u = s.update_ticket(conn, "alice", t["id"], verification_level="peer")
    assert u["verification_level"] == "peer"


def test_bajar_a_self_solo_el_dueno_de_la_casa(conn):
    t = _tarea(conn, nivel="peer")["ticket"]
    with pytest.raises(TicketError) as e:
        s.update_ticket(conn, "alice", t["id"], verification_level="self")
    assert e.value.code == "ticket.level_to_self_owner_only"
    assert s.update_ticket(conn, s.HOUSE_OWNER, t["id"], verification_level="self")["verification_level"] == "self"


def test_nivel_invalido_cerrada_o_recurrente_no(conn):
    t = _tarea(conn)["ticket"]
    with pytest.raises(TicketError):
        s.update_ticket(conn, "alice", t["id"], verification_level="mucho")
    r = s.propose_ticket(conn, "alice", "Backup semanal", owner="alice", auto_accept=True, cadence_days=7)["ticket"]
    with pytest.raises(TicketError):
        s.update_ticket(conn, "alice", r["id"], verification_level="peer")
    s.reject_ticket(conn, "alice", t["id"], "ya no hace falta")
    with pytest.raises(TicketError):
        s.update_ticket(conn, "alice", t["id"], verification_level="peer")


def test_avisa_al_crear_una_self_cuyo_criterio_pide_a_otro(conn):
    assert "self" in _tarea(conn, criterio=CRITERIO_OTRO)["warning"]
    assert "warning" not in _tarea(conn, criterio="Pasan los tests.")
    assert "warning" not in _tarea(conn, nivel="peer", criterio=CRITERIO_OTRO)


def test_avisa_al_completar_una_self_cuyo_criterio_pide_a_otro(conn):
    t = _tarea(conn, criterio="Lo verifica Prima reproduciéndolo.")["ticket"]
    r = s.complete_ticket(conn, "alice", t["id"], "command_output", "tests verdes")
    assert r["status"] == "verified" and "otra persona" in r["warning"]
    notas = [h["note"] or "" for h in s.ticket_history(conn, t["id"])["history"]]
    assert any("AVISO" in n for n in notas)


def test_mcp_update_ticket_lleva_el_nivel(monkeypatch):
    from faros import mcp_server as m

    sent = {}
    monkeypatch.setattr(m, "_call", lambda method, path, **kw: sent.update(method=method, path=path, **kw) or {"ok": 1})
    m.update_ticket("carol", 7, verification_level="peer")
    assert sent["json"] == {"agent": "carol", "verification_level": "peer"}


# ------------------------------------------------------------------ #481


def _hecha_peer(conn, owner="alice"):
    t = _tarea(conn, owner=owner, nivel="peer")["ticket"]
    s.complete_ticket(conn, owner, t["id"], "command_output", "hecho")
    return t["id"]


def test_apuntarse_y_el_segundo_recibe_quien_la_tiene(conn):
    tid = _hecha_peer(conn)
    assert s.claim_verification(conn, "bob", tid)["verify_claimed_by"] == "bob"
    assert s.claim_verification(conn, "bob", tid)["verify_claimed_by"] == "bob"  # idempotente
    with pytest.raises(TicketError) as e:
        s.claim_verification(conn, "carol", tid)
    assert e.value.code == "verify.already_claimed" and "bob" in str(e.value)


def test_la_duena_no_se_apunta_a_verificar_lo_suyo(conn):
    tid = _hecha_peer(conn)
    with pytest.raises(TicketError) as e:
        s.claim_verification(conn, "alice", tid)
    assert e.value.code == "verify.owner_cannot_verify"


def test_verificar_una_cogida_por_otro_no_se_puede_y_soltarla_si(conn):
    tid = _hecha_peer(conn)
    s.claim_verification(conn, "bob", tid)
    with pytest.raises(TicketError) as e:
        s.verify_ticket(conn, "carol", tid, "pass")
    assert e.value.code == "verify.claimed_by_other"
    with pytest.raises(TicketError):
        s.release_verification(conn, "carol", tid)  # no es suya
    assert s.release_verification(conn, "bob", tid)["verify_claimed_by"] is None
    assert s.claim_verification(conn, "carol", tid)["verify_claimed_by"] == "carol"
    assert s.verify_ticket(conn, "carol", tid, "pass")["status"] == "verified"


def test_my_board_ensena_las_cogidas_y_no_ofrece_las_de_otro(conn):
    tid = _hecha_peer(conn)
    otra = _hecha_peer(conn)
    s.claim_verification(conn, "bob", tid)
    bob = s.my_board(conn, "bob")
    carol = s.my_board(conn, "carol")
    assert [t["id"] for t in bob["verifications_i_claimed"]] == [tid]
    assert tid not in [t["id"] for t in carol["needs_my_verification"]]
    assert otra in [t["id"] for t in carol["needs_my_verification"]]


def test_dos_a_la_vez_solo_uno_la_consigue(tmp_path):
    path = tmp_path / "casa.db"
    c0 = db.connect(str(path))
    tid = _hecha_peer(c0)
    c0.close()
    resultados, barrera = {}, threading.Barrier(2)

    def coger(quien):
        c = db.connect(str(path))
        barrera.wait()
        try:
            resultados[quien] = s.claim_verification(c, quien, tid)["verify_claimed_by"]
        except TicketError as e:
            resultados[quien] = e.code
        finally:
            c.close()

    hilos = [threading.Thread(target=coger, args=(q,)) for q in ("bob", "carol")]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert sorted(resultados.values()) in (["bob", "verify.already_claimed"], ["carol", "verify.already_claimed"]), (
        resultados
    )


def test_por_la_api_el_segundo_recibe_409_con_quien_la_tiene(tmp_path, monkeypatch):
    import sqlite3

    from fastapi.testclient import TestClient

    from faros import api

    monkeypatch.setattr(api, "load_token", lambda: "t-481")
    db.connect(tmp_path / "t.db").close()
    c = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    cli, h = TestClient(api.create_app(c)), {"X-FarOS-Token": "t-481"}
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
        r = cli.post(f"/api/tickets/{tid}/transition", headers=h, json={"agent": "bob", "action": "claim"})
        assert r.status_code == 200 and r.json()["verify_claimed_by"] == "bob"
        r = cli.post(f"/api/tickets/{tid}/transition", headers=h, json={"agent": "carol", "action": "claim"})
        assert r.status_code == 409 and "bob" in r.json()["reason"]
        r = cli.post(f"/api/tickets/{tid}/transition", headers=h, json={"agent": "bob", "action": "unclaim"})
        assert r.status_code == 200 and r.json()["verify_claimed_by"] is None
    finally:
        c.close()
