"""#431: cambiar el calendario de un job lanzaba al momento un disparo «perdido» de antes del
cambio (la revista del 5-oct: de L-V 20:00 a L-V 11:00 a las 17:47 → run al momento, con el
prompt viejo). La recuperación L47 es para el daemon caído, no para un cambio de calendario."""

from datetime import datetime, timedelta

import pytest

from faros import db, jobs
from faros.scheduler import due_jobs


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _job(conn, at):
    return jobs.create_job(conn, agent="owner", name="Revista", prompt="haz la revista",
                           schedule={"type": "recurring", "every_days": 1, "at": at})


def _run(conn, job_id, when: datetime):
    conn.execute("INSERT INTO runs (job_id, started_at, status) VALUES (?, ?, 'ok')",
                 (job_id, when.isoformat(timespec="seconds")))
    conn.commit()


def _ayer_creado(conn, job_id):
    ayer = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
    conn.execute("UPDATE agent_jobs SET created_at=? WHERE id=?", (ayer, job_id))
    conn.commit()


def test_cambiar_el_calendario_a_una_hora_ya_pasada_no_lanza_nada(conn):
    hoy = datetime.now().replace(second=0, microsecond=0)
    luego = (hoy + timedelta(hours=1)).strftime("%H:%M")       # aún no toca hoy
    antes = (hoy - timedelta(hours=1)).strftime("%H:%M")       # ya pasó hoy
    if antes > hoy.strftime("%H:%M") or luego < hoy.strftime("%H:%M"):
        pytest.skip("cerca de medianoche: la hora de antes o la de después cae en otro día")
    j = _job(conn, luego)
    _ayer_creado(conn, j["id"])
    _run(conn, j["id"], hoy - timedelta(days=1) + timedelta(hours=1))  # el de ayer, hecho
    assert due_jobs(conn, datetime.now()) == []                 # control: nada pendiente
    jobs.update_job(conn, "owner", j["id"], schedule={"type": "recurring", "every_days": 1, "at": antes})
    assert due_jobs(conn, datetime.now()) == []


def test_daemon_caido_durante_un_disparo_sigue_recuperandolo(conn):
    hoy = datetime.now().replace(second=0, microsecond=0)
    antes = (hoy - timedelta(hours=1)).strftime("%H:%M")
    if antes > hoy.strftime("%H:%M"):
        pytest.skip("cerca de medianoche")
    j = _job(conn, antes)
    _ayer_creado(conn, j["id"])
    _run(conn, j["id"], hoy - timedelta(days=1) - timedelta(hours=1))  # ayer sí; hoy no hubo daemon
    assert [x["id"] for x in due_jobs(conn, datetime.now())] == [j["id"]]


def test_editar_solo_el_prompt_no_anula_la_recuperacion(conn):
    hoy = datetime.now().replace(second=0, microsecond=0)
    antes = (hoy - timedelta(hours=1)).strftime("%H:%M")
    if antes > hoy.strftime("%H:%M"):
        pytest.skip("cerca de medianoche")
    j = _job(conn, antes)
    _ayer_creado(conn, j["id"])
    _run(conn, j["id"], hoy - timedelta(days=1) - timedelta(hours=1))
    jobs.update_job(conn, "owner", j["id"], prompt="haz la revista, con fuentes")
    assert [x["id"] for x in due_jobs(conn, datetime.now())] == [j["id"]]
    # y volver a guardar el MISMO calendario tampoco cuenta como cambio
    jobs.update_job(conn, "owner", j["id"], schedule={"type": "recurring", "every_days": 1, "at": antes})
    assert [x["id"] for x in due_jobs(conn, datetime.now())] == [j["id"]]


def test_un_job_recien_creado_no_se_lanza_por_un_disparo_de_antes_de_existir(conn):
    # Lo vio Lienzo en la e2e del 16-oct: un diario de las 11:00 creado por la tarde se lanzaba al momento.
    hoy = datetime.now().replace(second=0, microsecond=0)
    antes = (hoy - timedelta(hours=1)).strftime("%H:%M")
    if antes > hoy.strftime("%H:%M"):
        pytest.skip("cerca de medianoche")
    _job(conn, antes)
    assert due_jobs(conn, datetime.now()) == []
    assert [x["id"] for x in due_jobs(conn, datetime.now() + timedelta(days=1))] != []   # mañana sí
