"""#604 (Pepe, 16-oct): una propuesta pendiente de decisión sale en el tablero en TODAS las vistas,
caiga la fecha que caiga. Antes, en Hoy y en Semana se filtraba por fecha como cualquier tarea: las
lecturas conjuntas #582-#585 y el piloto #502, de la semana siguiente, no le salían a Pepe para
aceptarlas hasta que navegaba a su semana."""

from datetime import date, timedelta

import pytest

from faros import db, service as s


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _ids(board, col="proposed"):
    return {t["id"] for t in board["columns"][col]}


def test_una_propuesta_de_dentro_de_dos_semanas_sale_en_todas_las_vistas(conn):
    lejos = (date.today() + timedelta(days=14)).isoformat() + "T16:00"
    p = s.propose_ticket(conn, "alice", "Lectura conjunta", scheduled_at=lejos)["ticket"]
    for view in ("today", "week", "future", "all"):
        assert p["id"] in _ids(s.board(conn, view=view)), view


def test_una_propuesta_sin_fecha_y_una_de_ayer_tambien(conn):
    sin = s.propose_ticket(conn, "alice", "Sin fecha")["ticket"]
    ayer = s.propose_ticket(conn, "alice", "De ayer",
                            scheduled_at=(date.today() - timedelta(days=1)).isoformat() + "T10:00")["ticket"]
    for view in ("today", "week", "future"):
        assert {sin["id"], ayer["id"]} <= _ids(s.board(conn, view=view)), view


def test_aceptada_deja_de_salir_y_vuelve_a_su_fecha(conn):
    """Control: la regla es para lo que espera decisión. Aceptada, vuelve a mandar la fecha."""
    lejos = (date.today() + timedelta(days=14)).isoformat() + "T16:00"
    p = s.propose_ticket(conn, "alice", "Lectura conjunta", scheduled_at=lejos)["ticket"]
    s.accept_ticket(conn, "owner", p["id"])
    assert p["id"] not in _ids(s.board(conn, view="week"), "accepted")
    assert p["id"] in _ids(s.board(conn, view="future"), "accepted")
