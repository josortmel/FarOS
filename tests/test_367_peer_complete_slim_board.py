"""#367 — los dos puntos que aceptó Pepe el 25-sep.

1. Un ticket AJENO ejecutado no tenía dónde dejar el resultado: accept sí, start/complete
   no («solo el owner»). Salida A elegida por Pepe: con verification_level=peer, un agente
   que no es el owner puede arrancarlo y completarlo con evidencia; el sello sigue siendo
   de un tercero — el que completa NO puede verificar (canario obligatorio).
2. my_board devolvía cada ticket entero (170.229 caracteres para Lienzo) y no cabía en una
   respuesta. Ahora sirve lo necesario para decidir qué toca + un recorte del cuerpo;
   full=True devuelve el cuerpo entero.
"""

import json

import pytest

from faros import db, service as s
from faros.service import TicketError


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _ajeno(conn, level="peer"):
    t = s.propose_ticket(conn, "owner", "decide el dueño, ejecuta otro", owner="owner",
                         verification_level=level, scheduled_at="2031-03-05T10:00")["ticket"]
    s.accept_ticket(conn, "alice", t["id"])
    return t["id"]


def test_non_owner_can_start_and_complete_a_peer_ticket(conn):
    tid = _ajeno(conn)
    s.start_ticket(conn, "alice", tid)
    t = s.complete_ticket(conn, "alice", tid, "file_path", "informe.md")
    assert t["status"] == "done" and t["evidence"] == "informe.md"
    assert t["owner"] == "owner", "completar no le quita el ticket a su dueño"


def test_the_one_who_completed_cannot_verify_but_a_third_can(conn):
    tid = _ajeno(conn)
    s.complete_ticket(conn, "alice", tid, "file_path", "informe.md")
    with pytest.raises(TicketError) as e:
        s.verify_ticket(conn, "alice", tid, "pass")
    assert e.value.code == "verify.completer_cannot_verify"
    with pytest.raises(TicketError):
        s.verify_ticket(conn, "owner", tid, "pass")  # el owner sigue sin poder verificar
    assert s.verify_ticket(conn, "bob", tid, "pass")["status"] == "verified"


def test_self_level_tickets_stay_owner_only(conn):
    # #640 (Pepe, 19-oct): las tareas DEL dueño de la casa las puede cerrar cualquiera de la casa
    # en su nombre (test_640_tareas_del_dueno.py). La regla sigue para la tarea self de otro agente.
    t = s.propose_ticket(conn, "owner", "la tarea self de bob", owner="bob",
                         verification_level="self", scheduled_at="2031-03-05T10:00")["ticket"]
    s.accept_ticket(conn, "bob", t["id"])
    tid = t["id"]
    with pytest.raises(TicketError) as e:
        s.complete_ticket(conn, "alice", tid, "file_path", "informe.md")
    assert e.value.code == "transition.not_owner"
    with pytest.raises(TicketError):
        s.start_ticket(conn, "alice", tid)


def test_my_board_is_slim_by_default_and_full_on_request(conn):
    cuerpo = "x" * 11_000
    for i in range(12):
        s.propose_ticket(conn, "owner", f"tarea {i}", description=cuerpo, verify_criteria=cuerpo,
                         owner="alice", scheduled_at="2031-03-05T10:00", auto_accept=True)
    slim = s.my_board(conn, "alice")
    t0 = slim["mine_open"][0]
    assert "description" not in t0 and "verify_criteria" not in t0
    assert len(t0["resumen"]) <= 201 and t0["title"] and t0["status"] and "id" in t0
    assert len(json.dumps(slim)) < 20_000, len(json.dumps(slim))
    full = s.my_board(conn, "alice", full=True)
    assert full["mine_open"][0]["description"] == cuerpo
