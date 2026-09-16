"""Tests del calendario (T2.1/T2.2)."""

from datetime import date, timedelta

import pytest

from faros import db, service as s, agenda as cal
from faros.service import TicketError


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _recurrente(conn, last_done, cadence=7, **kw):
    t = s.propose_ticket(conn, "owner", kw.pop("title", "Backup EcoDB"), owner="alice",
                         cadence_days=cadence, auto_accept=True, **kw)["ticket"]
    conn.execute("UPDATE tickets SET last_done_at=? WHERE id=?", (last_done, t["id"])); conn.commit()
    return t


def _dates(items, tid):
    return [i["date"] for i in items if i["ticket_id"] == tid]


def test_recurrente_projects_next_and_ghosts(conn):
    t = _recurrente(conn, "2026-09-01T10:00:00", 7, preferred_time="09:00", duration_min=30)
    res = cal.occurrences(conn, "2026-09-01", "2026-09-30")
    mine = [i for i in res["items"] if i["ticket_id"] == t["id"]]
    assert [i["date"] for i in mine] == ["2026-09-08", "2026-09-15", "2026-09-22", "2026-09-29"]
    assert [i["ghost"] for i in mine] == [False, True, True, True]
    assert mine[0]["kind"] == "recurrence" and mine[0]["start"] == "09:00" and mine[0]["end"] == "09:30"
    # el orden es ALFABETICO, no el del roster: con los nombres viejos
    # (Eco, Hilo, Lienzo, el dueño, Prima) tambien lo era y no se notaba.
    assert res["lanes"] == ["alice", "bob", "carol", "dave", "owner"]
    assert res["slots"] == [["09:00", "12:00"], ["14:00", "18:00"]]


def test_override_moves_only_next(conn):
    t = _recurrente(conn, "2026-09-01T10:00:00", 7)
    s.update_ticket(conn, "alice", t["id"], next_due_override="2026-09-10")
    res = cal.occurrences(conn, "2026-09-01", "2026-09-30")
    assert _dates(res["items"], t["id"]) == ["2026-09-10", "2026-09-17", "2026-09-24"]


def test_overdue_recurrente_appears_on_range_start(conn):
    t = _recurrente(conn, "2020-01-01T10:00:00", 7)  # vencidísima
    today = date.today()
    res = cal.occurrences(conn, today.isoformat(), (today + timedelta(days=6)).isoformat())
    mine = [i for i in res["items"] if i["ticket_id"] == t["id"]]
    assert mine[0]["date"] == today.isoformat() and mine[0]["ghost"] is False
    assert mine[0]["overdue_since"] == "2020-01-08" and mine[0]["due_state"] == "overdue"


def test_scheduled_and_due_items(conn):
    a = s.propose_ticket(conn, "owner", "Reunión", owner="owner", auto_accept=True,
                         scheduled_at="2026-09-03T09:00", duration_min=60)["ticket"]
    b = s.propose_ticket(conn, "owner", "Entregar informe", owner="bob", auto_accept=True,
                         due_at="2026-09-04")["ticket"]
    c = s.propose_ticket(conn, "owner", "Todo el día", owner="carol", auto_accept=True,
                         scheduled_at="2026-09-05T00:00", all_day=1)["ticket"]
    res = cal.occurrences(conn, "2026-09-01", "2026-09-07")
    by = {i["ticket_id"]: i for i in res["items"]}
    assert by[a["id"]]["kind"] == "scheduled" and by[a["id"]]["start"] == "09:00" and by[a["id"]]["end"] == "10:00"
    assert by[b["id"]]["kind"] == "due" and by[b["id"]]["start"] is None
    assert by[c["id"]]["all_day"] is True and by[c["id"]]["start"] is None
    only_prima = cal.occurrences(conn, "2026-09-01", "2026-09-07", agent="bob")
    assert [i["ticket_id"] for i in only_prima["items"]] == [b["id"]]


def test_agentic_tickets_never_in_calendar(conn):
    t = s.propose_ticket(conn, "alice", "Resumen correo", kind="agentic", verification_level="auto",
                         auto_accept=True, due_at="2026-09-03",
                         job={"prompt": "x"})["ticket"]
    res = cal.occurrences(conn, "2026-09-01", "2026-09-07")
    assert t["id"] not in [i["ticket_id"] for i in res["items"]]


def test_range_validation(conn):
    with pytest.raises(TicketError, match="from"):
        cal.occurrences(conn, "ayer", "2026-09-07")
    with pytest.raises(TicketError, match="to < from"):
        cal.occurrences(conn, "2026-09-07", "2026-09-01")


def test_schedule_and_shift_operations(conn):
    a = s.propose_ticket(conn, "owner", "Reunión", owner="owner", auto_accept=True)["ticket"]
    r = cal.schedule_ticket(conn, "owner", a["id"], scheduled_at="2026-09-03T09:00", duration_min=45)
    assert r["scheduled_at"] == "2026-09-03T09:00" and r["duration_min"] == 45
    r = cal.schedule_ticket(conn, "owner", a["id"], scheduled_at="2026-09-04T10:00")
    hist = s.ticket_history(conn, a["id"])["history"]
    assert any("programada: 2026-09-03T09:00 (45 min) → 2026-09-04T10:00 (45 min)" in (h["note"] or "") for h in hist)
    with pytest.raises(TicketError, match="nada que cambiar"):
        cal.schedule_ticket(conn, "owner", a["id"])
    t = _recurrente(conn, "2026-09-01T10:00:00", 7)
    # fut debe caer en laborable: el motor snapa sábado/domingo a lunes (weekend-skip
    # intencional), y este test comprueba el shift + la cadencia, no el snap. Sin esto,
    # el test falla los días en que today+3 cae en fin de semana (p. ej. un miércoles).
    fut_d = date.today() + timedelta(days=3)
    while fut_d.weekday() >= 5:
        fut_d += timedelta(days=1)
    fut = fut_d.isoformat()
    r = cal.shift_next_occurrence(conn, "alice", t["id"], fut)
    assert r["next_due"] == fut and r["cadence_days"] == 7
    with pytest.raises(TicketError, match="pasado"):
        cal.shift_next_occurrence(conn, "alice", t["id"], "2020-01-01")
    r = cal.shift_next_occurrence(conn, "alice", t["id"], "")
    assert r["next_due"] == "2026-09-08"
    with pytest.raises(TicketError, match="no es recurrente"):
        cal.shift_next_occurrence(conn, "alice", a["id"], fut)


def test_closed_ticket_shows_on_closing_day_only(conn):
    """Lo hecho forma parte del trabajo del dia: una cerrada se ve el dia de closed_at,
    kind='closed', y no se proyecta a ningun otro dia (T2.6, regla del coordinador)."""
    t = s.propose_ticket(conn, "owner", "Informe semanal", owner="alice", auto_accept=True)["ticket"]
    s.start_ticket(conn, "alice", t["id"])
    s.complete_ticket(conn, "alice", t["id"], "file_path", "docs/x.md")
    conn.execute("UPDATE tickets SET closed_at='2026-09-03T10:15:00', scheduled_at='2026-09-03T09:00:00' WHERE id=?", (t["id"],)); conn.commit()
    res = cal.occurrences(conn, "2026-09-01", "2026-09-30")
    mine = [i for i in res["items"] if i["ticket_id"] == t["id"]]
    assert [i["date"] for i in mine] == ["2026-09-03"]
    assert mine[0]["kind"] == "closed" and mine[0]["status"] in ("done", "verified")
    assert mine[0]["start"] == "09:00" and mine[0]["closed_at"] == "2026-09-03T10:15:00"
    assert cal.occurrences(conn, "2026-09-04", "2026-09-30")["items"] == [
        i for i in cal.occurrences(conn, "2026-09-04", "2026-09-30")["items"] if i["ticket_id"] != t["id"]]
