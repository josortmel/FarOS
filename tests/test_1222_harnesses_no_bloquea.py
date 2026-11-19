"""#1222: /api/harnesses no puede parar el daemon mientras pregunta a los harness.

describe() lanza procesos (la versión, los modelos) y tarda segundos la primera vez.
Corría dentro de un `async def`, así que paraba el bucle del daemon entero: medido
en la e2e de Lienzo, un /api/health de la página siguiente tardó 2.826 ms, la app lo
abortó a los 1.500 y se quedó en modo de prueba. En la casa, lo mismo le pasa a Pepe
la primera vez que abre Agentes › Ajustes. Mientras un harness tarda, lo demás contesta.
"""

import asyncio
import sqlite3
import time

import httpx
import pytest

from faros import api, db, harness as h
from faros.harness.base import HarnessAdapter

TOKEN = "t-1222"
H = {"X-FarOS-Token": TOKEN}
LENTO = 1.0


class _Lento(HarnessAdapter):
    name = "lento-1222"

    def describe(self, settings=None, refresh=False):
        time.sleep(LENTO)  # como un `claude --version` frío
        return {"name": self.name, "installed": True, "models": [], "models_source": "prueba",
                "models_refreshed_at": None}


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "load_token", lambda: TOKEN)
    monkeypatch.setitem(h._FACTORIES, _Lento.name, _Lento)
    monkeypatch.setattr(h, "all_adapters", lambda: [_Lento()])
    c = db.connect(str(tmp_path / "faros.db"))
    yield api.create_app(c)
    c.close()


async def _a_la_vez(app, lenta):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as cl:
        t0 = time.monotonic()
        tarea = asyncio.create_task(cl.get(lenta, headers=H))
        await asyncio.sleep(0.05)               # la lenta ya está dentro
        # Si la lenta para el bucle, este sleep no vuelve hasta que acabe: por eso
        # el tiempo de /api/health se cuenta desde que DEBÍA salir (t0 + 0,05 s).
        r = await cl.get("/api/health")
        health = time.monotonic() - (t0 + 0.05)
        rl = await tarea
        return r.status_code, health, rl.status_code, time.monotonic() - t0


@pytest.mark.parametrize("ruta", ["/api/harnesses", "/api/harnesses/lento-1222/models"])
def test_health_contesta_mientras_un_harness_tarda(app, ruta):
    sh, health, sl, total = asyncio.run(_a_la_vez(app, ruta))
    assert sh == 200 and sl == 200
    assert total >= LENTO                      # la lenta de verdad ha tardado
    assert health < 0.3, f"/api/health esperó {health:.2f} s detrás de {ruta}"
