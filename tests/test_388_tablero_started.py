"""#388 — el tablero distingue «acabas de llegar» de «esta vista está vacía».

El vacío del #285 mira solo la VISTA y dice siempre «cambia de vista», también a quien
abre FarOS por primera vez y no tiene ninguna vista a la que cambiar. board() devuelve
`started`: si la base ha tenido alguna vez una tarea del tablero. Igual que Excalidraw
(referente del 28-sep): la bienvenida no vuelve porque vacíes la vista o rechaces lo
único que había. Las agénticas no cuentan: viven en Agentes, no en el tablero.
"""

import pytest

from faros import db
from faros import service as s


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _propose(conn, **kw):
    args = dict(agent="alice", title="Arreglar el widget", description="el widget está roto")
    args.update(kw)
    return s.propose_ticket(conn, **args)["ticket"]


def test_388_base_limpia_no_ha_empezado(conn):
    assert s.board(conn)["started"] is False


def test_388_una_tarea_en_otro_proyecto_ya_cuenta(conn):
    lleno = s.create_project(conn, "owner", "Con tareas")
    vacio = s.create_project(conn, "owner", "Sin tareas")
    _propose(conn, project_id=lleno["id"])
    b = s.board(conn, project_id=vacio["id"])
    assert sum(len(v) for v in b["columns"].values()) == 0, "la vista no está vacía: el test no probaría nada"
    assert b["started"] is True


def test_388_rechazar_lo_unico_que_habia_no_devuelve_la_bienvenida(conn):
    t = _propose(conn)
    s.reject_ticket(conn, "owner", t["id"], "no era esto")
    b = s.board(conn)
    assert sum(len(v) for v in b["columns"].values()) == 0
    assert b["started"] is True


def test_388_las_agenticas_no_cuentan_como_empezar_el_tablero(conn):
    _propose(
        conn,
        title="Resumen de correo",
        kind="agentic",
        verification_level="auto",
        job={"prompt": "di hola", "model": "haiku"},
    )
    assert s.board(conn)["started"] is False
