"""Tests de búsqueda global (T3.9)."""

import pytest

from faros import db, service as s, jobs, decisions as dec, search
from faros.service import TicketError


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def test_search_across_tickets_jobs_decisions(conn):
    t = s.propose_ticket(conn, "owner", "Revisar el correo de clientes", auto_accept=True)["ticket"]
    j = jobs.create_job(conn, "owner", "Resumen de correo", "lee el correo cada mañana")
    d = dec.ask(conn, "alice", "¿Qué buzón de correo usamos?", ["el compartido", "el personal"])
    res = search.search(conn, "correo")
    assert [x["id"] for x in res["tickets"]] == [t["id"]] and res["tickets"][0]["section"] == "tablero"
    assert [x["id"] for x in res["jobs"]] == [j["id"]] and res["jobs"][0]["section"] == "agentes"
    assert [x["id"] for x in res["decisions"]] == [d["id"]] and res["decisions"][0]["section"] == "oficina"
    assert res["total"] == 3
    jobs.update_job(conn, "owner", j["id"], name="Resumen de facturas")
    assert search.search(conn, "facturas")["jobs"][0]["id"] == j["id"]
    jobs.set_status(conn, "owner", j["id"], "archived")
    assert search.search(conn, "facturas")["jobs"] == []
    assert search.search(conn, "corr")["tickets"]  # prefijo
    with pytest.raises(TicketError, match="vacía"):
        search.search(conn, " ")


def test_reindex_rebuilds(conn):
    jobs.create_job(conn, "owner", "Uno", "x")
    conn.execute("DELETE FROM jobs_fts"); conn.commit()
    assert search.search(conn, "Uno")["jobs"] == []
    assert search.reindex(conn) == {"jobs": 1, "decisions": 0}
    assert search.search(conn, "Uno")["jobs"]
