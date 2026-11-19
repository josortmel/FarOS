"""#662, decisión 74 de Pepe (26-oct): «Uno por racha».

Un job que falla avisa por Telegram en el PRIMER fallo y no vuelve a avisar de ESE job hasta
que tenga un run bueno. Fallo = el run termina error/timeout/cancelled, o termina ok y el
verificador dice fail. Antes, un job que fallaba cada hora mandaba un Telegram cada hora.
"""

import pytest

from faros import db, jobs


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def sent(monkeypatch):
    """Lo que se habría mandado a Telegram, sin red."""
    out = []
    monkeypatch.setattr("faros.notify.telegram", lambda conn, text: out.append(text) or True)
    return out


def _run(conn, job_id, status, verdict=None):
    r = jobs.create_run(conn, job_id)
    jobs.finish_run(conn, r["id"], status, result_summary="salida")
    if verdict:
        jobs.set_verdict(conn, r["id"], verdict, "motivo")
    return r["id"]


def test_dos_fallos_seguidos_del_mismo_job_un_solo_aviso(conn, sent):
    j = jobs.create_job(conn, "owner", "Revista", "x")
    _run(conn, j["id"], "error")
    _run(conn, j["id"], "error")
    _run(conn, j["id"], "timeout")
    assert len(sent) == 1 and "Revista" in sent[0]


def test_tras_un_run_bueno_vuelve_a_avisar(conn, sent):
    j = jobs.create_job(conn, "owner", "Revista", "x")
    _run(conn, j["id"], "error")
    _run(conn, j["id"], "ok", verdict="pass")
    _run(conn, j["id"], "error")
    assert len(sent) == 2


def test_un_fail_del_verificador_tambien_es_racha(conn, sent):
    j = jobs.create_job(conn, "owner", "Revista", "x")
    _run(conn, j["id"], "ok", verdict="fail")   # aviso: no pasó la verificación
    _run(conn, j["id"], "error")                # sigue la racha: sin aviso
    _run(conn, j["id"], "ok", verdict="fail")   # sigue: sin aviso
    assert len(sent) == 1 and "verificación" in sent[0]


def test_la_racha_es_por_job(conn, sent):
    a = jobs.create_job(conn, "owner", "Revista", "x")
    b = jobs.create_job(conn, "owner", "Semanal", "y")
    _run(conn, a["id"], "error")
    _run(conn, b["id"], "error")
    _run(conn, a["id"], "error")
    assert len(sent) == 2 and "Revista" in sent[0] and "Semanal" in sent[1]


def test_un_job_sano_no_avisa(conn, sent):
    j = jobs.create_job(conn, "owner", "Revista", "x")
    _run(conn, j["id"], "ok", verdict="pass")
    _run(conn, j["id"], "ok")
    assert sent == []
