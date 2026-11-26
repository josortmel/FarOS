"""#1266: notify.telegram lo llaman desde el bucle del daemon (un run que falla, un veredicto, un job saltado, una
clave entregada) y httpx espera hasta 10 s. Dentro de un bucle no puede parar al resto: se manda en un hilo.
Fuera de un bucle (scripts, tests) sigue esperando la respuesta. El error de red no escribe la URL (lleva el token)."""

import asyncio
import logging
import threading

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


TOPE = 5.0  # solo para no colgar un test si el envío sí para el bucle: no es la aserción


class Lento:
    """Un post de Telegram que no contesta hasta que el test lo suelta (#1372: sin reloj)."""

    def __init__(self):
        self.dentro, self.soltar, self.hecho = threading.Event(), threading.Event(), threading.Event()

    def __call__(self, url, **kw):
        self.dentro.set()
        self.soltar.wait(TOPE)
        self.hecho.set()
        return type("R", (), {"status_code": 200, "text": "ok"})()


def test_dentro_del_bucle_no_para_al_daemon(conn, monkeypatch):
    lento = Lento()
    monkeypatch.setattr(notify.httpx, "post", lento)

    async def daemon():
        latidos = []

        async def latir():
            for _ in range(10):
                latidos.append(1)
                await asyncio.sleep(0)

        latido = asyncio.create_task(latir())
        assert notify.on_run_failed(conn, "revista", 7, "error") is True
        volvio_sin_esperar = not lento.hecho.is_set()
        await latido
        late_con_el_post_colgado = len(latidos) == 10 and not lento.hecho.is_set()
        return volvio_sin_esperar, late_con_el_post_colgado

    volvio_sin_esperar, late_con_el_post_colgado = asyncio.run(daemon())
    lento.soltar.set()
    assert volvio_sin_esperar, "on_run_failed esperó al post de Telegram dentro del bucle"
    assert late_con_el_post_colgado, "el bucle no latió mientras el post seguía colgado"
    assert lento.hecho.wait(TOPE)  # y el mensaje sale igual


def test_fuera_de_un_bucle_espera_la_respuesta(conn, monkeypatch):
    lento = Lento()
    monkeypatch.setattr(notify.httpx, "post", lento)
    volvio, visto = threading.Event(), {}

    def ayudante():  # con el post ya dentro, apunta si telegram() ha vuelto, y solo entonces lo suelta
        lento.dentro.wait(TOPE)
        visto["volvio_con_el_post_colgado"] = volvio.is_set()
        lento.soltar.set()

    a = threading.Thread(target=ayudante)
    a.start()
    r = notify.telegram(conn, "hola")  # en el hilo de la conexión (SQLite no se comparte entre hilos)
    volvio.set()
    a.join(TOPE)
    # #1372: la prueba de que espera, sin el «>= 0,25 s»: no volvió mientras el post seguía colgado.
    assert visto.get("volvio_con_el_post_colgado") is False, "fuera de un bucle, telegram() no esperó la respuesta"
    assert r is True and lento.hecho.is_set()


def test_el_error_de_red_no_escribe_la_url(conn, monkeypatch, caplog):
    def revienta(url, **kw):
        raise httpx.ConnectError(f"no llego a {url}")

    monkeypatch.setattr(notify.httpx, "post", revienta)
    with caplog.at_level(logging.INFO, logger="faros.notify"):
        assert notify.telegram(conn, "hola") is False
    assert "ConnectError" in caplog.text and TOKEN not in caplog.text
