"""#600, punto 1: las memorias citadas por id se comprueban contra EcoDB.

Decisión de Prima (20-oct): la EXISTENCIA es determinista (200 vs 404); la PROPIEDAD es un detalle
que pesa el juez, nunca un fail — citar memorias de otro es legítimo y rebotar trabajo bueno es el
peor fallo de un verificador. Y sin clave o con EcoDB caído, la memoria queda SIN DECIDIR: no se
tumba el trabajo por un fallo del instrumento.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from faros import prueba

EXISTE = "6d40cdd2-cd17-4896-8167-be16df31e59f"
NO_EXISTE = "00000000-0000-0000-0000-000000000000"


class _EcoDB:
    """Un EcoDB de mentira: la memoria EXISTE es de Eco; lo demás, 404."""

    def __init__(self):
        clase = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                clase.vistas.append(self.path)
                if self.path.endswith(EXISTE):
                    cuerpo = json.dumps({"id": EXISTE, "agent_identifier": "Eco"}).encode()
                    self.send_response(200)
                else:
                    cuerpo = b'{"detail": "not found"}'
                    self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(cuerpo)))
                self.end_headers()
                self.wfile.write(cuerpo)

            def log_message(self, *a):
                pass

        self.vistas = []
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def parar(self):
        self.srv.shutdown()


@pytest.fixture
def ecodb():
    e = _EcoDB()
    yield e
    e.parar()


def test_una_memoria_que_existe_dice_quien_la_escribio(ecodb):
    r = prueba.comprobar_memorias([EXISTE], "clave", base=ecodb.url)
    assert r[0]["ok"] is True
    assert r[0]["agente"] == "Eco"
    assert "Eco" in r[0]["detalle"]


def test_una_memoria_que_no_existe_es_un_rojo(ecodb):
    r = prueba.comprobar_memorias([NO_EXISTE], "clave", base=ecodb.url)
    assert r[0]["ok"] is False
    assert "404" in r[0]["detalle"]


def test_la_propiedad_se_dice_pero_no_tumba_el_trabajo(ecodb, monkeypatch):
    """Eco escribió la memoria y el ticket puede ser de otro: existe = verde, y el autor va como
    detalle para que el juez lo pese. Citar memorias ajenas es legítimo."""
    monkeypatch.setattr(prueba, "ECODB_URL", ecodb.url)
    res = prueba.comprobar({"memorias": [EXISTE]}, "clave")
    assert res["ok"] is True, "existir es lo determinista; de quién sea no lo es"
    assert "Eco" in res["checks"][0]["detalle"]


def test_sin_clave_la_memoria_queda_sin_decidir(ecodb):
    r = prueba.comprobar_memorias([EXISTE], None, base=ecodb.url)
    assert r[0]["ok"] is None
    assert not ecodb.vistas, "sin clave no se pregunta nada a EcoDB"


def test_si_ecodb_no_responde_queda_sin_decidir_y_no_es_rojo():
    r = prueba.comprobar_memorias([EXISTE], "clave", base="http://127.0.0.1:9", timeout=1.0)
    assert r[0]["ok"] is None
    assert "no pude preguntar" in r[0]["detalle"]


def test_comprobar_mete_las_que_no_sabe_en_sin_decidir(ecodb, monkeypatch):
    """Sin clave no se pregunta y nada se tumba. La segunda posición (`clave`) es la que hace que
    este test sea rojo con el código anterior: antes `comprobar()` no la tenía."""
    monkeypatch.setattr(prueba, "ECODB_URL", ecodb.url)
    res = prueba.comprobar({"memorias": [EXISTE]}, None)
    assert res["sin_decidir"] == [EXISTE]
    assert res["checks"] == []
    assert res["ok"] is True, "sin poder preguntar, nada se tumba"
    assert not ecodb.vistas, "sin clave no se llama a EcoDB"


def test_comprobar_con_servidor_marca_verde_la_que_existe(ecodb, monkeypatch):
    monkeypatch.setattr(prueba, "ECODB_URL", ecodb.url)
    res = prueba.comprobar({"memorias": [EXISTE]}, "clave")
    assert res["ok"] is True
    assert [c["nombre"] for c in res["checks"]] == [f"memoria {EXISTE} existe"]
    assert res["sin_decidir"] == []


def test_comprobar_con_servidor_tumba_la_que_no_existe(ecodb, monkeypatch):
    monkeypatch.setattr(prueba, "ECODB_URL", ecodb.url)
    res = prueba.comprobar({"memorias": [NO_EXISTE]}, "clave")
    assert res["ok"] is False
    assert res["checks"][0]["ok"] is False


def test_el_403_se_explica_con_las_dos_posibilidades(monkeypatch):
    import urllib.error

    def _403(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(prueba.urllib.request, "urlopen", _403)
    r = prueba._pregunta_a_ecodb("x", "clave")
    assert r["ok"] is None
    assert "no existe o no es visible" in r["detalle"]


def test_un_id_que_no_es_uuid_no_se_pide(ecodb):
    """El id entra en una URL que se pide con la clave del daemon: sin esta puerta, un proof con
    «../../api/credentials/deliver» haría pedir otro endpoint de EcoDB con credenciales."""
    r = prueba.comprobar_memorias(["../../api/credentials/deliver"], "clave", base=ecodb.url)
    assert r[0]["ok"] is None
    assert "no parece un id" in r[0]["detalle"]
    assert not ecodb.vistas, "no se debe pedir nada a EcoDB con un id que no es UUID"


def test_el_orden_de_las_memorias_se_respeta(ecodb):
    ids = [NO_EXISTE, "no-es-uuid", EXISTE]
    r = prueba.comprobar_memorias(ids, "clave", base=ecodb.url)
    assert [x["id"] for x in r] == ids
