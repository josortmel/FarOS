"""#640 (Pepe, 19-oct: «necesito que podáis modificar y verificar las tareas a mi nombre, es importante»).

Mover, editar, aceptar y rechazar una tarea del dueño de la casa ya se podía. Lo que faltaba: empezarla,
completarla (y con ello sellar las de nivel «self») y pausarla. Ahora cualquiera de la casa puede hacerlo
sobre una tarea del dueño, y el historial dice quién lo hizo y en nombre de quién. Las tareas de otro
agente siguen siendo de su dueña.
"""

import pytest

from faros import db, service as s
from faros.service import HOUSE_OWNER, TicketError


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _tarea(conn, owner, nivel="self"):
    t = s.propose_ticket(conn, HOUSE_OWNER, "Lectura conjunta", owner=owner, verification_level=nivel,
                         scheduled_at="2031-03-05T10:00")["ticket"]
    s.accept_ticket(conn, HOUSE_OWNER, t["id"])
    return t["id"]


def _historial(conn, tid):
    return s.ticket_history(conn, tid)["history"]


def test_un_agente_empieza_y_sella_una_tarea_self_del_dueno(conn):
    tid = _tarea(conn, HOUSE_OWNER)
    s.start_ticket(conn, "alice", tid)
    t = s.complete_ticket(conn, "alice", tid, "file_path", "notas/lectura.md")
    assert t["status"] == "verified"
    assert t["verified_by"] == "alice"
    assert t["owner"] == HOUSE_OWNER  # sigue siendo suya
    for fila in _historial(conn, tid)[-2:]:
        assert fila["changed_by"] == "alice"
        assert f"en nombre de {HOUSE_OWNER}" in (fila["note"] or "")


def test_un_agente_pausa_una_tarea_del_dueno(conn):
    tid = _tarea(conn, HOUSE_OWNER)
    s.start_ticket(conn, HOUSE_OWNER, tid)
    t = s.pause_ticket(conn, "alice", tid, "no se ha empezado de verdad")
    assert t["status"] == "accepted"
    assert f"en nombre de {HOUSE_OWNER}" in _historial(conn, tid)[-1]["note"]


def test_un_agente_verifica_una_tarea_peer_del_dueno_que_hizo_otro(conn):
    tid = _tarea(conn, HOUSE_OWNER, nivel="peer")
    s.start_ticket(conn, "bob", tid)
    s.complete_ticket(conn, "bob", tid, "file_path", "notas/lectura.md")
    t = s.verify_ticket(conn, "alice", tid, "pass")
    assert t["status"] == "verified" and t["verified_by"] == "alice"


def test_las_tareas_de_otra_duena_siguen_siendo_suyas(conn):
    tid = _tarea(conn, "bob")
    with pytest.raises(TicketError) as e:
        s.start_ticket(conn, "alice", tid)
    assert e.value.code == "transition.not_owner"
    s.start_ticket(conn, "bob", tid)
    with pytest.raises(TicketError) as e:
        s.complete_ticket(conn, "alice", tid, "file_path", "x.md")
    assert e.value.code == "transition.not_owner"
    with pytest.raises(TicketError) as e:
        s.pause_ticket(conn, "alice", tid, "no se ha empezado de verdad")
    assert e.value.code == "pause.not_owner"


def test_el_dueno_no_lleva_nota_de_en_nombre_de_si_mismo(conn):
    tid = _tarea(conn, HOUSE_OWNER)
    s.start_ticket(conn, HOUSE_OWNER, tid)
    assert "en nombre de" not in (_historial(conn, tid)[-1]["note"] or "")
