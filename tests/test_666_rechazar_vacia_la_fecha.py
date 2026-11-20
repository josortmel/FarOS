"""#666 — rechazar una tarea le vacía la fecha.

Rechazar no vaciaba `scheduled_at`: el 20-oct había cuatro tareas rechazadas de Eco con hora del
jueves 22, una solapada con su versión viva, y el MCP se negaba a reprogramarlas porque estaban
cerradas. El estado decía una cosa y la fecha otra. Ahora rechazar deja `scheduled_at`,
`duration_min` y `preferred_time` en null, y lo que tenían queda escrito en el historial. Las
rechazadas que ya estaban en la base (58 en la casa el 29-oct) las limpia la migración del esquema 10.
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
    args = dict(agent="alice", title="Tarea del jueves", description="d")
    args.update(kw)
    return s.propose_ticket(conn, **args)["ticket"]


def test_666_rechazar_vacia_la_fecha_y_la_deja_en_el_historial(conn):
    t = _propose(conn, scheduled_at="2026-10-22T11:00", duration_min=180)
    r = s.reject_ticket(conn, "alice", t["id"], "era la versión anterior de otra")
    assert r["status"] == "rejected"
    assert r["scheduled_at"] is None and r["duration_min"] is None and r["preferred_time"] is None
    nota = conn.execute(
        "SELECT note FROM ticket_history WHERE ticket_id=? AND new_status='rejected'", (t["id"],)
    ).fetchone()["note"]
    assert "era la versión anterior de otra" in nota
    assert "scheduled_at=2026-10-22T11:00" in nota and "duration_min=180" in nota


def test_666_rechazar_sin_fecha_no_ensucia_el_historial(conn):
    t = _propose(conn)
    s.reject_ticket(conn, "alice", t["id"], "no hace falta")
    nota = conn.execute(
        "SELECT note FROM ticket_history WHERE ticket_id=? AND new_status='rejected'", (t["id"],)
    ).fetchone()["note"]
    assert nota == "no hace falta"


def test_666_la_migracion_limpia_las_rechazadas_que_ya_tenian_fecha(conn):
    viva = _propose(conn, scheduled_at="2026-10-22T11:00", duration_min=60)
    vieja = _propose(conn, scheduled_at="2026-10-22T09:00", duration_min=120)
    # como estaban antes del arreglo: rechazada y con su fecha
    conn.execute("UPDATE tickets SET status='rejected' WHERE id=?", (vieja["id"],))
    conn.commit()
    db._v10_rechazadas_sin_fecha(conn)
    v = conn.execute("SELECT scheduled_at, duration_min FROM tickets WHERE id=?", (vieja["id"],)).fetchone()
    assert v["scheduled_at"] is None and v["duration_min"] is None
    h = conn.execute(
        "SELECT note, changed_by FROM ticket_history WHERE ticket_id=? ORDER BY id DESC", (vieja["id"],)
    ).fetchone()
    assert "scheduled_at=2026-10-22T09:00" in h["note"] and h["changed_by"] == "faros"
    # la viva no se toca, y una segunda pasada no escribe otra fila
    assert (
        conn.execute("SELECT scheduled_at FROM tickets WHERE id=?", (viva["id"],)).fetchone()[0] == "2026-10-22T11:00"
    )
    antes = conn.execute("SELECT count(*) FROM ticket_history").fetchone()[0]
    db._v10_rechazadas_sin_fecha(conn)
    assert conn.execute("SELECT count(*) FROM ticket_history").fetchone()[0] == antes
