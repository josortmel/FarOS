"""#518: un job enlazado a una tarea CERRADA dejaba de dispararse en silencio y next_fire
seguía anunciándolo (la revista del 15-oct: job 13 colgado del #321 ya verificado)."""

from datetime import datetime, timedelta

import pytest

from faros import db, service as s, jobs
from faros.scheduler import due_jobs, skipped_jobs


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _job_colgado_de_tarea(conn):
    t = s.propose_ticket(conn, "owner", "Racha de la revista", auto_accept=True,
                         verification_level="peer")["ticket"]
    j = jobs.create_job(conn, agent="owner", name="Revista diaria", prompt="haz la revista",
                        schedule={"type": "recurring", "every_days": 1, "at": "11:00"},
                        ticket_id=t["id"])
    return t, j


def test_get_job_dice_que_no_se_dispara_si_su_tarea_esta_cerrada(conn):
    t, j = _job_colgado_de_tarea(conn)
    assert jobs.get_job(conn, j["id"])["next_fire"] is not None          # control: abierta, anuncia
    s.reject_ticket(conn, "owner", t["id"], "ya no hace falta")
    d = jobs.get_job(conn, j["id"])
    assert d["next_fire"] is None
    assert f"#{t['id']}" in d["not_firing"] and "cerrada" in d["not_firing"]


def test_el_scheduler_no_lo_lanza_pero_lo_cuenta_como_saltado(conn):
    t, j = _job_colgado_de_tarea(conn)
    s.reject_ticket(conn, "owner", t["id"], "ya no hace falta")
    ahora = datetime.now().replace(hour=11, minute=5, second=0, microsecond=0) + timedelta(days=1)
    assert [x["id"] for x in due_jobs(conn, ahora)] == []
    saltados = skipped_jobs(conn, ahora)
    assert [x["id"] for x in saltados] == [j["id"]]
    assert saltados[0]["fire"] and saltados[0]["ticket_id"] == t["id"]


def test_al_cerrar_la_tarea_avisa_del_job_que_cuelga(conn):
    t, j = _job_colgado_de_tarea(conn)
    r = s.reject_ticket(conn, "owner", t["id"], "ya no hace falta")
    assert f"job #{j['id']}" in r["job_warning"] and "no se dispara" in r["job_warning"]
    # una tarea abierta no lleva aviso
    t2 = s.propose_ticket(conn, "owner", "Otra", auto_accept=True, verification_level="peer")["ticket"]
    assert "job_warning" not in s._as_dict(conn, s._get(conn, t2["id"]))


def test_desenlazar_el_job_lo_devuelve_al_calendario(conn):
    t, j = _job_colgado_de_tarea(conn)
    s.reject_ticket(conn, "owner", t["id"], "ya no hace falta")
    d = jobs.update_job(conn, "owner", j["id"], ticket_id=None)
    assert d["ticket_id"] is None and d["next_fire"] is not None and "not_firing" not in d
    assert "job_warning" not in s._as_dict(conn, s._get(conn, t["id"]))


def test_mcp_update_job_clear_ticket_id_manda_null(monkeypatch):
    from faros import mcp_server as m
    sent = {}
    monkeypatch.setattr(m, "_call", lambda method, path, **kw: sent.update(method=method, path=path, **kw) or {"ok": 1})
    m.update_job("carol", 13, clear=["ticket_id"])
    assert sent["method"] == "PATCH" and sent["path"] == "/api/jobs/13"
    assert sent["json"] == {"agent": "carol", "ticket_id": None}
    sent.clear()
    r = m.update_job("carol", 13, clear=["prompt"])
    assert r["error"] and not sent
