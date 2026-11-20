"""#482 (lo encontró Prima el 9-oct; Pepe dijo «las dos» el 16-oct): que el tablero diga la verdad.

(1) El motivo de un bloqueo que ya no es cierto se reescribe sin desbloquear la tarea: `block` sobre una
    bloqueada cambia el motivo, y el viejo queda en el historial.
(2) Una tarea que se pasó a «en curso» y no se ha empezado de verdad vuelve a «aceptada» con `pause`
    y nota obligatoria. La pausa la hace la dueña de la tarea o el dueño de la casa.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from faros import api, db
from faros import service as s
from faros.service import TicketError


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _tarea(conn, owner="alice"):
    t = s.propose_ticket(
        conn, "owner", "Manual v2", owner=owner, verification_level="peer", scheduled_at="2031-03-05T10:00"
    )["ticket"]
    s.accept_ticket(conn, owner, t["id"])
    return t["id"]


def _ultima(conn, tid):
    return s.ticket_history(conn, tid)["history"][-1]


# ------------------------------------------------------------------ (1) el motivo del bloqueo


def test_reescribir_el_motivo_no_desbloquea(conn):
    tid = _tarea(conn)
    s.block_ticket(conn, "alice", tid, "espera a que Pepe decida")
    t = s.block_ticket(conn, "alice", tid, "espera al #471 verificado")
    assert t["status"] == "blocked" and t["blocked_reason"] == "espera al #471 verificado"
    fila = _ultima(conn, tid)
    assert (fila["old_status"], fila["new_status"]) == ("blocked", "blocked")
    assert "espera a que Pepe decida" in fila["note"] and "espera al #471 verificado" in fila["note"]


def test_el_mismo_motivo_no_ensucia_el_historial(conn):
    tid = _tarea(conn)
    s.block_ticket(conn, "alice", tid, "espera a Pepe")
    n = len(s.ticket_history(conn, tid)["history"])
    s.block_ticket(conn, "alice", tid, "espera a Pepe")
    assert len(s.ticket_history(conn, tid)["history"]) == n


def test_sin_motivo_sigue_sin_poderse(conn):
    tid = _tarea(conn)
    s.block_ticket(conn, "alice", tid, "x")
    with pytest.raises(TicketError) as e:
        s.block_ticket(conn, "alice", tid, "  ")
    assert e.value.code == "block.reason_required"


# ------------------------------------------------------------------ (2) en curso → aceptada


def test_pausar_devuelve_a_aceptada_con_nota(conn):
    tid = _tarea(conn)
    s.start_ticket(conn, "alice", tid)
    t = s.pause_ticket(conn, "alice", tid, "la marqué en curso y no la he empezado")
    assert t["status"] == "accepted"
    fila = _ultima(conn, tid)
    assert (fila["old_status"], fila["new_status"]) == ("in_progress", "accepted")
    assert "no la he empezado" in fila["note"]


def test_pausar_exige_nota(conn):
    tid = _tarea(conn)
    s.start_ticket(conn, "alice", tid)
    with pytest.raises(TicketError) as e:
        s.pause_ticket(conn, "alice", tid, "corta")
    assert e.value.code == "pause.note_required"
    assert s._get(conn, tid)["status"] == "in_progress"


def test_solo_pausa_la_duena_o_el_dueno_de_la_casa(conn):
    tid = _tarea(conn)
    s.start_ticket(conn, "alice", tid)
    with pytest.raises(TicketError) as e:
        s.pause_ticket(conn, "bob", tid, "no es mía pero la paro")
    assert e.value.code == "pause.not_owner"
    assert s.pause_ticket(conn, s.HOUSE_OWNER, tid, "el dueño la devuelve a aceptadas")["status"] == "accepted"


def test_solo_se_pausa_lo_que_esta_en_curso(conn):
    tid = _tarea(conn)
    with pytest.raises(TicketError) as e:
        s.pause_ticket(conn, "alice", tid, "todavía está aceptada")
    assert e.value.code == "transition.illegal"


def test_en_el_tablero_sale_en_aceptadas(conn):
    tid = _tarea(conn)
    s.start_ticket(conn, "alice", tid)
    s.pause_ticket(conn, "alice", tid, "no la he empezado de verdad")
    cols = s.board(conn)["columns"]
    ids = lambda col: [t["id"] for t in cols.get(col, [])]
    assert tid in ids("accepted") and tid not in ids("in_progress")


# ------------------------------------------------------------------ por la API (como lo usa el MCP)


def test_por_la_api(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "load_token", lambda: "t-482")
    db.connect(tmp_path / "t.db").close()
    c = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    cli, h = TestClient(api.create_app(c)), {"X-FarOS-Token": "t-482"}
    try:
        tid = _tarea(c)
        post = lambda body: cli.post(f"/api/tickets/{tid}/transition", headers=h, json=body)
        assert post({"agent": "alice", "action": "start"}).status_code == 200
        r = post({"agent": "alice", "action": "pause", "note": "marcada en curso por error"})
        assert r.status_code == 200 and r.json()["status"] == "accepted"
        post({"agent": "alice", "action": "block", "reason": "motivo viejo"})
        r = post({"agent": "alice", "action": "block", "reason": "motivo nuevo"})
        assert r.status_code == 200 and r.json()["blocked_reason"] == "motivo nuevo"
    finally:
        c.close()


# ------------------------------------------------------------------ backlog no es una puerta trasera


def test_backlog_tiene_las_mismas_reglas_que_pausar(conn):
    """Lo vio Lienzo el 16-oct: `backlog` hacía lo mismo que `pause` (en curso → aceptada) sin nota y a
    nombre de cualquiera. Ahora es un alias de `pause`: mismas reglas, mismo historial."""
    tid = _tarea(conn)
    s.start_ticket(conn, "alice", tid)
    with pytest.raises(TicketError) as e:
        s.backlog_ticket(conn, "alice", tid)
    assert e.value.code == "pause.note_required"
    with pytest.raises(TicketError) as e:
        s.backlog_ticket(conn, "bob", tid, "no es mía pero la devuelvo")
    assert e.value.code == "pause.not_owner"
    assert s.backlog_ticket(conn, "alice", tid, "devuelta a la cola sin empezar")["status"] == "accepted"
    assert _ultima(conn, tid)["note"].startswith("[pausa]")
