"""#376 — una recurrente semanal no cambia de día (Pepe, 25-sep).

Dos huecos preventivos, con la regla de hoy (#364: nada se ancla con un valor por
defecto; lo coloca su dueño):
1. Una semanal sin weekday toma su día DE LA FECHA QUE LE DIO SU DUEÑO (scheduled_at,
   due_at u override). Sin ninguna fecha se queda sin día, «por ubicar».
2. Mover una ocurrencia (next_due_override) mueve SOLO esa: las previstas siguientes
   se proyectan desde el día ancla, no desde el día movido.
"""

from datetime import date, timedelta

import pytest

from faros import agenda, db
from faros import service as s


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _proximo(wd: int) -> date:
    hoy = date.today()
    return hoy + timedelta(days=(wd - hoy.weekday()) % 7 or 7)


def test_weekly_takes_its_day_from_the_date_the_owner_gave(conn):
    jueves = _proximo(3)
    t = s.propose_ticket(
        conn,
        "owner",
        "semanal",
        owner="alice",
        cadence_days=7,
        preferred_time="09:00",
        scheduled_at=f"{jueves.isoformat()}T09:00",
        auto_accept=True,
    )["ticket"]
    assert t["weekday"] == 3


def test_weekly_without_any_date_stays_unplaced_not_defaulted(conn):
    t = s.propose_ticket(conn, "owner", "semanal sin fecha", owner="alice", cadence_days=7, auto_accept=True)["ticket"]
    assert t["weekday"] is None
    assert t["id"] in {x["id"] for x in s.my_board(conn, "alice")["por_ubicar"]}


def test_editing_a_date_on_a_dayless_weekly_anchors_it(conn):
    t = s.propose_ticket(conn, "owner", "semanal", owner="alice", cadence_days=7, auto_accept=True)["ticket"]
    martes = _proximo(1)
    u = s.update_ticket(conn, "alice", t["id"], scheduled_at=f"{martes.isoformat()}T10:00", preferred_time="10:00")
    assert u["weekday"] == 1


def test_moving_one_occurrence_does_not_shift_the_series(conn):
    lunes = _proximo(0)
    t = s.propose_ticket(
        conn, "owner", "backup", owner="alice", cadence_days=7, weekday=0, preferred_time="08:00", auto_accept=True
    )["ticket"]
    movida = lunes + timedelta(days=2)  # esta semana se hace el miércoles
    s.update_ticket(conn, "alice", t["id"], next_due_override=movida.isoformat())
    cal = agenda.occurrences(conn, (lunes - timedelta(days=1)).isoformat(), (lunes + timedelta(days=22)).isoformat())
    dias = sorted(i["date"] for i in cal["items"] if i.get("ticket_id") == t["id"])
    assert movida.isoformat() in dias
    siguientes = [d for d in dias if d > movida.isoformat()]
    assert siguientes, dias
    assert all(date.fromisoformat(d).weekday() == 0 for d in siguientes), (
        f"las previstas se han ido con la movida: {dias}"
    )
