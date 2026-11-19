"""#600, punto 2: settings.maquina_quieta — la cola del verificador no corre mientras esté activa.
Sale de la revisión de Prima: sus medidas necesitan el ordenador sin trabajo del daemon encima.
"""

import asyncio

import pytest

from faros import db, service as s, settings, verifier


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _hecho(conn):
    t = s.propose_ticket(conn, "owner", "Resumen", auto_accept=True,
                         verification_level="auto")["ticket"]
    s.complete_ticket(conn, "owner", t["id"], "command_output", "resumen con 3 correos")
    return t["id"]


def _juez_que_cuenta(veces):
    async def judge(prompt, model=None, env=None, read_dirs=None, read_files=None):
        veces["n"] += 1
        return {"verdict": "pass", "reason": "leído en el fichero"}
    return judge


def test_por_defecto_la_maquina_no_esta_quieta(conn):
    assert settings.get(conn, "maquina_quieta") is False


def test_con_la_maquina_quieta_la_cola_no_llama_al_juez(conn, monkeypatch):
    _hecho(conn)
    settings.patch(conn, "owner", {"maquina_quieta": True})
    veces = {"n": 0}
    monkeypatch.setattr(verifier, "_judge", _juez_que_cuenta(veces))

    res = asyncio.run(verifier.sweep(conn))

    assert res["quiet"] is True, res
    assert res["swept"] == 0
    assert veces["n"] == 0, "el juez no puede correr con la máquina quieta"


def test_control_sin_la_quieta_el_juez_si_corre(conn, monkeypatch):
    """Si este no se pone rojo con el guardián mal puesto, el test de arriba no prueba nada."""
    _hecho(conn)
    veces = {"n": 0}
    monkeypatch.setattr(verifier, "_judge", _juez_que_cuenta(veces))

    res = asyncio.run(verifier.sweep(conn))

    assert res["swept"] == 1
    assert veces["n"] == 1
