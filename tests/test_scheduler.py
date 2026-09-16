"""Tests del scheduler con reloj inyectado (SPEC §7). Sin subprocess: solo la
decisión due_jobs, que es pura sobre la DB."""

import json
from datetime import datetime

import pytest

from faros import db, service as s
from faros.scheduler import due_jobs, last_theoretical_fire


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _agentic(conn, schedule, **kw):
    t = s.propose_ticket(conn, "owner", kw.pop("title", "t"), kind="agentic",
                         verification_level=kw.pop("vl", "self" if schedule.get("type") == "recurring" else "auto"),
                         auto_accept=True,
                         job={"prompt": "x", "model": "haiku", "schedule": schedule})
    return t["ticket"]


def test_manual_never_due(conn):
    _agentic(conn, {"type": "manual"})
    assert due_jobs(conn, datetime(2026, 9, 1, 8, 0)) == []


def test_once_due_after_run_at(conn):
    t = _agentic(conn, {"type": "once", "run_at": "2026-09-01T08:00:00"})
    assert due_jobs(conn, datetime(2026, 9, 1, 7, 59)) == []          # antes: no
    due = due_jobs(conn, datetime(2026, 9, 1, 8, 1))                  # después: sí
    assert [j["ticket_id"] for j in due] == [t["id"]]


def test_once_not_relaunched_after_run(conn):
    t = _agentic(conn, {"type": "once", "run_at": "2026-09-01T08:00:00"})
    job_id = t["job"]["id"]
    s.create_run(conn, job_id)  # ya se lanzó
    assert due_jobs(conn, datetime(2026, 9, 1, 8, 5)) == []


def test_once_missed_beyond_grace_disables(conn):
    t = _agentic(conn, {"type": "once", "run_at": "2026-09-01T08:00:00"})
    # 25h después sin run: se marca missed y se deshabilita, no se lanza
    assert due_jobs(conn, datetime(2026, 9, 2, 9, 1)) == []
    job = s.get_job(conn, t["job"]["id"])
    assert job["enabled"] == 0
    fresh = s.list_tickets(conn, include_closed=True)[0]
    assert fresh["status"] == "blocked"


def test_recurring_due_at_first_fire(conn):
    t = _agentic(conn, {"type": "recurring", "every_days": 1, "at": "08:00"})
    # creado hoy; a las 08:00 del mismo día debe dispararse (nunca corrido)
    created = t["job"]["created_at"]
    fire_day = datetime.fromisoformat(created).replace(hour=8, minute=1, second=0)
    due = due_jobs(conn, fire_day)
    assert [j["ticket_id"] for j in due] == [t["id"]]


def test_recurring_not_due_before_at(conn):
    t = _agentic(conn, {"type": "recurring", "every_days": 1, "at": "08:00"})
    created = datetime.fromisoformat(t["job"]["created_at"])
    before = created.replace(hour=7, minute=0)
    # si se creó después de las 07:00 el fire teórico de hoy aún no llegó
    if created.hour < 7:
        assert due_jobs(conn, before) == []


def test_recurring_catchup_after_downtime(conn):
    """L47: máquina apagada varios días → al volver se lanza UNA vez, no una por
    ventana perdida. Anclado al created_at real (service usa now())."""
    from datetime import timedelta
    t = _agentic(conn, {"type": "recurring", "every_days": 1, "at": "08:00"})
    job_id = t["job"]["id"]
    created = datetime.fromisoformat(t["job"]["created_at"])
    day0 = created.date()
    # corrió su fire del día de creación, luego 3 días de downtime
    s.create_run(conn, job_id)
    ran_at = datetime.combine(day0, datetime.min.time()).replace(hour=8, minute=0, second=5)
    conn.execute("UPDATE runs SET started_at=?, status='ok', finished_at=? WHERE job_id=?",
                 (ran_at.isoformat(), (ran_at + timedelta(minutes=1)).isoformat(), job_id))
    conn.commit()
    # tres días después a las 08:05: due UNA sola vez (no 3)
    now = datetime.combine(day0 + timedelta(days=3), datetime.min.time()).replace(hour=8, minute=5)
    due = due_jobs(conn, now)
    assert len(due) == 1 and due[0]["ticket_id"] == t["id"]


def test_running_blocks_scheduler(conn):
    t = _agentic(conn, {"type": "recurring", "every_days": 1, "at": "08:00"})
    s.create_run(conn, t["job"]["id"])  # queda running
    created = datetime.fromisoformat(t["job"]["created_at"]).replace(hour=8, minute=1)
    assert due_jobs(conn, created) == []  # anti-solape


def test_disabled_job_not_due(conn):
    t = _agentic(conn, {"type": "once", "run_at": "2026-09-01T08:00:00"})
    s.update_job(conn, "owner", t["job"]["id"], enabled=False)
    assert due_jobs(conn, datetime(2026, 9, 1, 8, 1)) == []


def test_closed_ticket_job_not_due(conn):
    t = _agentic(conn, {"type": "once", "run_at": "2026-09-01T08:00:00"})
    s.reject_ticket(conn, "owner", t["id"], "no procede")
    assert due_jobs(conn, datetime(2026, 9, 1, 8, 1)) == []


def test_last_theoretical_fire_every_2_days(conn):
    fire = last_theoretical_fire({"every_days": 2, "at": "09:00"},
                                 "2026-09-01T10:00:00", datetime(2026, 9, 5, 9, 30))
    # ancla 01, cada 2 días: 01,03,05 → el último <= now(05 09:30) es 05 09:00
    assert fire == datetime(2026, 9, 5, 9, 0)
    fire2 = last_theoretical_fire({"every_days": 2, "at": "09:00"},
                                  "2026-09-01T10:00:00", datetime(2026, 9, 4, 12, 0))
    # 04 no es día de fire (01,03,05); el último <= now es 03 09:00
    assert fire2 == datetime(2026, 9, 3, 9, 0)
