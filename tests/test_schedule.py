"""Tests de schedule v3 (T1.5): varias horas, días de la semana, catch-up L47."""

from datetime import datetime

import pytest

from faros import db, jobs, schedule
from faros.scheduler import due_jobs
from faros.service import TicketError

CREATED = "2026-09-01T10:00:00"  # martes


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def test_normalize_accepts_v1_string_and_lists():
    assert schedule.normalize({"type": "recurring", "every_days": 1, "at": "08:00"}) == \
        {"type": "recurring", "every_days": 1, "at": ["08:00"]}
    assert schedule.normalize({"type": "recurring", "every_days": 2, "at": ["20:00", "08:00", "08:00"]}) == \
        {"type": "recurring", "every_days": 2, "at": ["08:00", "20:00"]}
    assert schedule.normalize({"type": "weekly", "weekdays": [4, 0, 2], "at": "08:00"}) == \
        {"type": "weekly", "weekdays": [0, 2, 4], "at": ["08:00"]}
    assert schedule.normalize(None) == {"type": "manual"}


def test_normalize_rejects_garbage():
    with pytest.raises(TicketError, match="type"):
        schedule.normalize({"type": "hourly"})
    with pytest.raises(TicketError, match="hora inválida"):
        schedule.normalize({"type": "recurring", "every_days": 1, "at": ["8am"]})
    with pytest.raises(TicketError, match="weekdays"):
        schedule.normalize({"type": "weekly", "weekdays": [7], "at": ["08:00"]})
    with pytest.raises(TicketError, match="every_days"):
        schedule.normalize({"type": "recurring", "every_days": 0, "at": ["08:00"]})


def test_recurring_two_hours_fires_twice_a_day():
    sched = {"type": "recurring", "every_days": 1, "at": ["08:00", "20:00"]}
    assert schedule.last_fire(sched, CREATED, datetime(2026, 9, 2, 7, 59)) == datetime(2026, 9, 1, 20, 0)
    assert schedule.last_fire(sched, CREATED, datetime(2026, 9, 2, 8, 0)) == datetime(2026, 9, 2, 8, 0)
    assert schedule.last_fire(sched, CREATED, datetime(2026, 9, 2, 19, 59)) == datetime(2026, 9, 2, 8, 0)
    assert schedule.next_fire(sched, CREATED, datetime(2026, 9, 2, 8, 1)) == datetime(2026, 9, 2, 20, 0)
    assert schedule.next_fire(sched, CREATED, datetime(2026, 9, 2, 20, 1)) == datetime(2026, 9, 3, 8, 0)


def test_weekly_mon_wed_fri_skips_tuesday():
    sched = {"type": "weekly", "weekdays": [0, 2, 4], "at": ["08:00"]}
    # 1-sep-2026 es martes → el primer fire es el miércoles 2
    assert schedule.last_fire(sched, CREATED, datetime(2026, 9, 1, 12, 0)) is None
    assert schedule.next_fire(sched, CREATED, datetime(2026, 9, 1, 12, 0)) == datetime(2026, 9, 2, 8, 0)
    assert schedule.last_fire(sched, CREATED, datetime(2026, 9, 3, 12, 0)) == datetime(2026, 9, 2, 8, 0)  # jueves: no dispara
    assert schedule.next_fire(sched, CREATED, datetime(2026, 9, 3, 12, 0)) == datetime(2026, 9, 4, 8, 0)
    assert schedule.next_fire(sched, CREATED, datetime(2026, 9, 4, 9, 0)) == datetime(2026, 9, 7, 8, 0)  # lunes


def test_catch_up_after_gap_launches_once(conn):
    j = jobs.create_job(conn, "owner", "diario", "x",
                        schedule={"type": "recurring", "every_days": 1, "at": ["08:00", "20:00"]})
    conn.execute("UPDATE agent_jobs SET created_at=? WHERE id=?", (CREATED, j["id"])); conn.commit()
    # el daemon vuelve tras 3 días muerto: 6 disparos perdidos → UNA vez
    due = due_jobs(conn, datetime(2026, 9, 5, 9, 0))
    assert [x["id"] for x in due] == [j["id"]]
    run = jobs.create_run(conn, j["id"])
    jobs.finish_run(conn, run["id"], "ok")
    # el run real lleva la hora real; en el reloj falso "ocurrió" a las 09:01 del día 5
    conn.execute("UPDATE runs SET started_at='2026-09-05T09:01:00' WHERE id=?", (run["id"],)); conn.commit()
    assert due_jobs(conn, datetime(2026, 9, 5, 9, 5)) == []          # ya cubierto
    assert [x["id"] for x in due_jobs(conn, datetime(2026, 9, 5, 20, 1))] == [j["id"]]  # siguiente disparo real


def test_weekly_job_due_only_on_its_days(conn):
    j = jobs.create_job(conn, "owner", "semanal", "x",
                        schedule={"type": "weekly", "weekdays": [0, 2, 4], "at": ["08:00"]})
    conn.execute("UPDATE agent_jobs SET created_at=? WHERE id=?", (CREATED, j["id"])); conn.commit()
    assert due_jobs(conn, datetime(2026, 9, 1, 9, 0)) == []                      # martes
    assert [x["id"] for x in due_jobs(conn, datetime(2026, 9, 2, 8, 1))] == [j["id"]]  # miércoles
    assert jobs.get_job(conn, j["id"])["next_fire"].startswith("2026-09-")
    assert schedule.human(j["schedule"]) == "lunes, miércoles, viernes a las 08:00"
    assert schedule.human({"type": "recurring", "every_days": 1, "at": ["08:00", "20:00"]}) == "cada día a las 08:00 y 20:00"
