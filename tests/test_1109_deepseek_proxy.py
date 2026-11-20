"""#1109 — la clave de DeepSeek no entra en el entorno del agente: el run habla con un reenvío del daemon
con un token que solo vale mientras dura el run, y el daemon pone la clave al salir. Sin red: la API de
DeepSeek es un httpx.MockTransport."""

import asyncio
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from faros import api, db, harness, runner, settings
from faros import deepseek_proxy as dsp

CLAVE = "sk-prueba-1109-" + "9" * 20
TOKEN_CASA = "t" * 32


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "load_token", lambda: TOKEN_CASA)
    db.connect(tmp_path / "t.db").close()
    c = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    settings._guardar(c, "deepseek_api_key", CLAVE)
    c.commit()
    vistas = []

    def deepseek(req: httpx.Request):
        vistas.append(req)
        cuerpo = b'event: message_start\ndata: {"type":"message_start"}\n\n'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=cuerpo)

    monkeypatch.setattr(dsp, "_TRANSPORTE", httpx.MockTransport(deepseek))
    yield TestClient(api.create_app(c)), c, vistas
    c.close()


def _post(cli, token, **kw):
    return cli.post(
        dsp.RUTA + "/v1/messages?beta=true",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "deepseek-flash", "stream": True, "messages": []},
        **kw,
    )


def test_con_token_de_run_reenvia_con_la_clave_y_devuelve_el_stream(cli):
    c, _, vistas = cli
    token = dsp.emitir("run #1")
    try:
        r = _post(c, token)
    finally:
        dsp.revocar(token)
    assert r.status_code == 200 and "message_start" in r.text
    (fuera,) = vistas
    assert str(fuera.url) == dsp.UPSTREAM + "/v1/messages?beta=true"
    assert fuera.headers["authorization"] == f"Bearer {CLAVE}"
    assert token not in json.dumps(dict(fuera.headers))
    assert json.loads(fuera.content)["model"] == "deepseek-flash"


def test_sin_token_vigente_401_y_no_sale_de_casa(cli):
    c, _, vistas = cli
    revocado = dsp.emitir("run #2")
    dsp.revocar(revocado)
    for token in ("", "inventado", revocado, TOKEN_CASA):
        r = _post(c, token)
        assert r.status_code == 401 and CLAVE not in r.text
    assert vistas == []


def test_sin_clave_401_sin_salir(cli):
    c, conn, vistas = cli
    settings._guardar(conn, "deepseek_api_key", "")
    conn.commit()
    token = dsp.emitir("run #3")
    try:
        assert _post(c, token).status_code == 401
    finally:
        dsp.revocar(token)
    assert vistas == []


def test_el_token_del_run_muere_con_el_run_aunque_el_proceso_no_arranque(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "r.db")
    emitidos = []
    original = dsp.emitir
    monkeypatch.setattr(dsp, "emitir", lambda para: emitidos.append(original(para)) or emitidos[-1])

    async def no_arranca(*a, **k):
        raise OSError("exe borrado")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_arranca)
    from faros import jobs

    job = jobs.create_job(
        conn, "owner", name="ds", harness="claude-cli-deepseek", prompt="hola", model="deepseek-flash"
    )
    asyncio.run(runner.execute_job(conn, job["id"]))
    assert len(emitidos) == 1 and not dsp.vigente(emitidos[0])


def test_el_entorno_del_run_no_lleva_la_clave_en_ninguna_variable(tmp_path):
    conn = db.connect(tmp_path / "e.db")
    settings._guardar(conn, "deepseek_api_key", CLAVE)
    conn.commit()
    env = runner._env_for(conn, harness.get("claude-cli-deepseek"), "faros-run-y")
    assert not any(CLAVE in v for v in env.values())
    assert env["ANTHROPIC_BASE_URL"] == dsp.url_local()
