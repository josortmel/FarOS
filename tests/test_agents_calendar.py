"""T5.1 (v3.1): calendario propio de Agentes — jobs once/recurring/weekly + runs."""
from datetime import datetime

import pytest

from faros import db, jobs as jm, agents_agenda as ag
from faros.service import TicketError


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


NOW = datetime(2026, 9, 4, 10, 0)  # viernes


def test_weekly_projects_only_future_fires_in_range(conn):
    j = jm.create_job(conn, "alice", "correos", "lee", model="haiku",
                      schedule={"type": "weekly", "weekdays": [0, 2, 4], "at": ["08:00"]})
    # El job no proyecta ocurrencias anteriores a su created_at (fires_on: day >= anchor).
    # create_job lo ancla a hoy real, así que sin fijarlo el rango fijo de septiembre queda
    # por detrás del anchor y solo sobrevive el día real. Anclar antes del rango hace el
    # test determinista con el reloj inyectado NOW, cualquier día en que se ejecute.
    conn.execute("UPDATE agent_jobs SET created_at='2026-09-01T00:00:00' WHERE id=?", (j["id"],))
    conn.commit()
    res = ag.occurrences(conn, "2026-09-04", "2026-09-17", now=NOW)
    fires = [i for i in res["items"] if i["kind"] == "job_recurring"]
    # hoy viernes 08:00 ya pasó (now=10:00) → L7, X9, V11, L14, X16 = 5
    assert [i["date"] for i in fires] == ["2026-09-07", "2026-09-09", "2026-09-11",
                                          "2026-09-14", "2026-09-16"]
    assert fires[0]["start"] == "08:00" and fires[0]["title"] == "correos"
    assert fires[0]["schedule_human"]


def test_once_in_and_out_of_range(conn):
    jm.create_job(conn, "alice", "una vez", "x", schedule={"type": "once", "run_at": "2026-09-10T15:30"})
    jm.create_job(conn, "alice", "fuera", "x", schedule={"type": "once", "run_at": "2026-12-01T09:00"})
    res = ag.occurrences(conn, "2026-09-04", "2026-09-30", now=NOW)
    once = [i for i in res["items"] if i["kind"] == "job_once"]
    assert [(i["title"], i["date"], i["start"]) for i in once] == [("una vez", "2026-09-10", "15:30")]


def test_paused_and_manual_do_not_project(conn):
    j = jm.create_job(conn, "alice", "pausado", "x",
                      schedule={"type": "recurring", "every_days": 1, "at": ["12:00"]})
    jm.set_status(conn, "alice", j["id"], "paused")
    jm.create_job(conn, "alice", "manual", "x")
    res = ag.occurrences(conn, "2026-09-04", "2026-09-10", now=NOW)
    assert res["items"] == []


def test_runs_appear_with_verdict(conn):
    j = jm.create_job(conn, "alice", "demo", "x")
    conn.execute("INSERT INTO runs (job_id, started_at, finished_at, status, verdict, cost_usd)"
                 " VALUES (?, '2026-09-03T09:05:00', '2026-09-03T09:07:10', 'ok', 'pass', 0.03)",
                 (j["id"],))
    conn.commit()
    res = ag.occurrences(conn, "2026-09-01", "2026-09-04", now=NOW)
    runs = [i for i in res["items"] if i["kind"] == "run"]
    assert len(runs) == 1
    r = runs[0]
    assert r["date"] == "2026-09-03" and r["start"] == "09:05" and r["end"] == "09:07"
    assert r["verdict"] == "pass" and r["status"] == "ok" and r["title"] == "demo"


def test_range_validation(conn):
    with pytest.raises(TicketError):
        ag.occurrences(conn, "2026-09-10", "2026-09-01")
    with pytest.raises(TicketError):
        ag.occurrences(conn, "2026-01-01", "2027-12-31")
    with pytest.raises(TicketError):
        ag.occurrences(conn, "hoy", "2026-09-01")
