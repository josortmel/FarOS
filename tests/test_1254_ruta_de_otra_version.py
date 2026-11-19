"""#1254: el MCP ofrece herramientas que el daemon instalado todavía no tiene.

El MCP lee el código de master y el daemon es el de la última versión instalada. Entre
una cosa y otra, una herramienta nueva (silenciar_timbre, el 14-nov) llega al daemon y
le contesta 404 route.not_found, y quien la llama no sabe si se ha equivocado él. Ahora
el error dice que es una herramienta de una versión que no está instalada, con las dos.
"""

import httpx
import pytest

from faros import __version__, mcp_server


class _R:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


@pytest.fixture
def daemon(monkeypatch, tmp_path):
    tok = tmp_path / "token"
    tok.write_text("t", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "TOKEN_FILE", tok)
    pedidas = []

    def falso(method, url, **kw):
        pedidas.append((method, url.rsplit("/api", 1)[-1]))
        if url.endswith("/api/health"):
            return _R(200, {"ok": True, "version": {"daemon": "3.3.30", "schema": 13}})
        if "/nueva" in url:
            return _R(404, {"error": "not_found", "reason": "Not Found", "code": "route.not_found"})
        if "/falta" in url:
            return _R(404, {"error": "not_found", "reason": "ticket 999 no existe", "code": "ticket.not_found"})
        return _R(200, {"ok": True})

    monkeypatch.setattr(httpx, "request", falso)
    return pedidas


def test_una_ruta_que_el_daemon_no_tiene_nombra_las_dos_versiones(daemon):
    r = mcp_server._call("POST", "/api/nueva", json={})
    assert r["code"] == "route.not_found"
    assert "3.3.30" in r["reason"] and __version__ in r["reason"], r["reason"]
    assert r["daemon_version"] == "3.3.30" and r["mcp_version"] == __version__


def test_un_404_de_un_ticket_que_no_existe_no_se_toca(daemon):
    r = mcp_server._call("GET", "/api/falta")
    assert r == {"error": "not_found", "reason": "ticket 999 no existe", "code": "ticket.not_found"}
    assert ("GET", "/health") not in daemon  # solo se pregunta la versión cuando falta la ruta


def test_lo_que_va_bien_no_cambia(daemon):
    assert mcp_server._call("GET", "/api/board") == {"ok": True}
