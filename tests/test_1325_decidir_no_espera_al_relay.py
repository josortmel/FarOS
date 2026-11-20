"""Lo vio Lienzo el 19-nov arreglando test_919[390] (#1325): POST /api/decisions/{id}/decide esperaba al aviso por el
relay antes de responder (api.py, `await _avisar_decision`). Con el hub caído o lento, Pepe se quedaba 2 s (o los 5 del
open_timeout) con el dedo en «Confirmar» en el móvil, que repinta cuando vuelve el POST. El aviso va aparte; la respuesta
no lo espera. Es la regla de #1266 en otra forma: en un async def, nada de red antes de responder."""

import time

from test_1189_telegram_bot import H, cliente, pedir  # noqa: F401  (mismo montaje que el aviso del 17-nov)

from faros import roster


def test_decidir_responde_sin_esperar_al_relay_y_el_aviso_llega_igual(cliente, monkeypatch):
    from faros import api

    cli, c = cliente
    avisos = []

    def relay_lento(a):
        time.sleep(1.5)  # como un hub caído en Windows (~2 s en rechazar la conexión)
        avisos.append(a)
        return {}

    monkeypatch.setattr(api, "AVISAR_RELAY", relay_lento)
    d = pedir(c)
    # «with cli»: un solo bucle de eventos vivo entre peticiones, como uvicorn en el daemon. Sin él, el TestClient
    # abre un bucle por petición y al cerrarlo espera a las tareas pendientes, y la prueba no mediría lo que pasa en casa.
    with cli:
        t0 = time.monotonic()
        r = cli.post(
            f"/api/decisions/{d['id']}/decide",
            headers=H,
            json={"agent": roster.HOUSE_OWNER, "option": "El lunes", "rationale": "x", "surface": "mobile"},
        )
        tarda = time.monotonic() - t0
        assert r.status_code == 200 and tarda < 0.5, tarda
        limite = time.monotonic() + 5
        while not avisos and time.monotonic() < limite:
            time.sleep(0.05)
    assert avisos and "alice" in avisos[0]
