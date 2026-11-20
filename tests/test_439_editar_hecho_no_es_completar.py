"""#439 — editar un ticket que ya está en done no te convierte en «quien lo completó».

Visto el 5-oct verificando el #308: «quien completó» (#367) se tomaba de la ÚLTIMA fila del
historial con new_status='done', y una edición de un ticket hecho (reprogramarlo para
verificarlo luego) deja una fila done→done a nombre de quien edita. Resultado: quien
reprogramaba para verificar quedaba bloqueado, y la regla dejaba de mirar a quien de verdad
completó. Solo cuenta la transición HACIA done.
"""

import pytest

from faros import db
from faros import service as s
from faros.service import TicketError


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _hecho_por_alice(conn):
    t = s.propose_ticket(
        conn, "owner", "trabajo de alice", owner="owner", verification_level="peer", scheduled_at="2031-03-05T10:00"
    )["ticket"]
    s.accept_ticket(conn, "alice", t["id"])
    s.complete_ticket(conn, "alice", t["id"], "file_path", "informe.md")
    return t["id"]


def test_reprogramar_un_hecho_no_bloquea_a_quien_lo_reprograma(conn):
    tid = _hecho_por_alice(conn)
    s.update_ticket(conn, "bob", tid, scheduled_at="2031-03-06T19:00")
    assert s.verify_ticket(conn, "bob", tid, "pass")["status"] == "verified"


def test_y_quien_lo_completo_sigue_sin_poder_aunque_otro_lo_edite(conn):
    tid = _hecho_por_alice(conn)
    s.update_ticket(conn, "bob", tid, scheduled_at="2031-03-06T19:00")
    with pytest.raises(TicketError) as e:
        s.verify_ticket(conn, "alice", tid, "pass")
    assert e.value.code == "verify.completer_cannot_verify"


def test_si_se_devuelve_y_se_vuelve_a_completar_manda_el_ultimo_que_completo(conn):
    tid = _hecho_por_alice(conn)
    s.rework_ticket(conn, "owner", tid, "accepted", "falta la segunda mitad del criterio")
    s.complete_ticket(conn, "carol", tid, "file_path", "informe2.md")
    with pytest.raises(TicketError):
        s.verify_ticket(conn, "carol", tid, "pass")
    assert s.verify_ticket(conn, "alice", tid, "pass")["status"] == "verified"
