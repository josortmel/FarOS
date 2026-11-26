"""#1222: /api/harnesses no puede parar el daemon mientras pregunta a los harness.

describe() lanza procesos (la versión, los modelos) y tarda segundos la primera vez.
Corría dentro de un `async def`, así que paraba el bucle del daemon entero: medido
en la e2e de Lienzo, un /api/health de la página siguiente tardó 2.826 ms, la app lo
abortó a los 1.500 y se quedó en modo de prueba. En la casa, lo mismo le pasa a Pepe
la primera vez que abre Agentes › Ajustes. Mientras un harness tarda, lo demás contesta.

#1372 (26-nov): sin reloj. El harness lento se queda dentro hasta que el test lo suelta
(un Event), y lo que se mira es si /api/health contestó MIENTRAS seguía dentro: sí o no.
Antes era «health < 0,3 s», que en una máquina lenta puede fallar sin fallo en el código
(Luo et al., FSE 2014: de 20 arreglos con sleep, ninguno quitó la inestabilidad).
"""

import asyncio
import threading

import httpx
import pytest

from faros import api, db
from faros import harness as h
from faros.harness.base import HarnessAdapter

TOKEN = "t-1222"
H = {"X-FarOS-Token": TOKEN}
DENTRO = threading.Event()  # el harness ha entrado en describe()
SOLTAR = threading.Event()  # el test le deja salir
SALIDO = threading.Event()  # describe() ha terminado
TOPE = 5.0  # solo para que el test no se cuelgue si el bucle se para: no es la aserción


class _Lento(HarnessAdapter):
    name = "lento-1222"

    def describe(self, settings=None, refresh=False):
        DENTRO.set()
        SOLTAR.wait(TOPE)  # como un `claude --version` frío que no acaba hasta que se le suelta
        SALIDO.set()
        return {
            "name": self.name,
            "installed": True,
            "models": [],
            "models_source": "prueba",
            "models_refreshed_at": None,
        }


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "load_token", lambda: TOKEN)
    monkeypatch.setitem(h._FACTORIES, _Lento.name, _Lento)
    monkeypatch.setattr(h, "all_adapters", lambda: [_Lento()])
    c = db.connect(str(tmp_path / "faros.db"))
    yield api.create_app(c)
    c.close()


async def _a_la_vez(app, lenta):
    for e in (DENTRO, SOLTAR, SALIDO):
        e.clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as cl:
        tarea = asyncio.create_task(cl.get(lenta, headers=H))
        # Esperar a que la lenta esté DENTRO (una condición, no un tiempo). Si la lenta
        # para el bucle, esta espera no vuelve hasta que el tope la suelte.
        for _ in range(int(TOPE * 100)):
            if DENTRO.is_set():
                break
            await asyncio.sleep(0.01)
        r = await cl.get("/api/health")
        mientras_dentro = DENTRO.is_set() and not SALIDO.is_set()
        SOLTAR.set()
        rl = await tarea
        return r.status_code, mientras_dentro, rl.status_code


@pytest.mark.parametrize("ruta", ["/api/harnesses", "/api/harnesses/lento-1222/models"])
def test_health_contesta_mientras_un_harness_tarda(app, ruta):
    sh, mientras_dentro, sl = asyncio.run(_a_la_vez(app, ruta))
    assert sh == 200 and sl == 200
    assert mientras_dentro, f"/api/health solo contestó cuando el harness ya había salido de describe() ({ruta})"
