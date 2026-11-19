"""#1266: notify.telegram lo llaman desde el bucle del daemon (un run que falla, un veredicto, un job saltado, una
clave entregada) y httpx espera hasta 10 s. Dentro de un bucle no puede parar al resto: se manda en un hilo.
Fuera de un bucle (scripts, tests) sigue esperando la respuesta. El error de red no escribe la URL (lleva el token)."""

import asyncio
import logging
import threading
import time

import httpx
import pytest

from faros import db, notify, settings

TOKEN = "123:SECRETO-DEL-BOT"


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    settings.patch(c, "owner", {"telegram_token": TOKEN, "telegram_chat_id": "999"})
    yield c
    c.close()


class Lento:
    def __init__(self, segundos):
        self.segundos, self.hecho = segundos, threading.Event()

    def __call__(self, url, **kw):
        time.sleep(self.segundos)
        self.hecho.set()
        return type("R", (), {"status_code": 200, "text": "ok"})()


def test_dentro_del_bucle_no_para_al_daemon(conn, monkeypatch):
    lento = Lento(2.0)
    monkeypatch.setattr(notify.httpx, "post", lento)

    async def daemon():
        latidos = []

        async def latir():
            for _ in range(10):
                latidos.append(time.monotonic())
                await asyncio.sleep(0.05)

        t0 = time.monotonic()
        latido = asyncio.create_task(latir())
        assert notify.on_run_failed(conn, "revista", 7, "error") is True
        tarda = time.monotonic() - t0
        await latido
        return tarda, latidos

    tarda, latidos = asyncio.run(daemon())
    assert tarda < 0.3                                   # antes: 2 s con el bucle parado
    assert max(b - a for a, b in zip(latidos, latidos[1:])) < 0.5
    assert lento.hecho.wait(5)                           # y el mensaje sale igual


def test_fuera_de_un_bucle_espera_la_respuesta(conn, monkeypatch):
    lento = Lento(0.3)
    monkeypatch.setattr(notify.httpx, "post", lento)
    t0 = time.monotonic()
    assert notify.telegram(conn, "hola") is True
    assert time.monotonic() - t0 >= 0.3 and lento.hecho.is_set()


def test_el_error_de_red_no_escribe_la_url(conn, monkeypatch, caplog):
    def revienta(url, **kw):
        raise httpx.ConnectError(f"no llego a {url}")
    monkeypatch.setattr(notify.httpx, "post", revienta)
    with caplog.at_level(logging.INFO, logger="faros.notify"):
        assert notify.telegram(conn, "hola") is False
    assert "ConnectError" in caplog.text and TOKEN not in caplog.text
