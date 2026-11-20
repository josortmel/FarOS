"""#919 H2 · decisiones_vigentes en FarOS: su mitad sale de `decisions`; la de EcoDB, de su endpoint
GET /api/v1/decisiones/vigentes con la clave «EcoDB». Sin EcoDB, FarOS responde con lo suyo y lo dice."""

import json
import sqlite3
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from faros import api, db, mcp_server
from faros import credentials as cr
from faros import decisions as dec
from faros.service import HOUSE_OWNER

OPTS = [{"label": "sí", "text": "sí, hacerlo"}, {"label": "no", "text": "no hacerlo"}]
HOY = date(2026, 11, 10)
TOKEN = "t-919v"
H = {"X-FarOS-Token": TOKEN}


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _decidida(conn, titulo="¿horario de la tienda?", **kw):
    did = dec.ask(conn, "alice", titulo, OPTS)["id"]
    return dec.decide(conn, HOUSE_OWNER, did, option="sí", rationale="porque sí", **kw)["id"]


def test_cadena_a_b_c_devuelve_c_con_a_quien_sustituye(conn):
    a = _decidida(conn)
    b = _decidida(conn, supersedes=[a])
    c = _decidida(conn, supersedes=[b])
    r = dec.vigentes_faros(conn, "horario tienda", hoy=HOY)
    assert [f["id"] for f in r["vigentes"]] == [f"faros:decision:{c}"]
    assert r["vigentes"][0]["sustituye_a"][0] == f"faros:decision:{a}" and r["vigentes"][0]["fuente"] == "FarOS"


def test_caducada_fuera_y_superada_solo_con_incluir_superadas(conn):
    vieja = _decidida(conn)
    nueva = _decidida(conn, supersedes=[vieja], supersede_reason="cambió")
    _decidida(conn, valid_until="2026-11-09")
    sin = dec.vigentes_faros(conn, "horario tienda", hoy=HOY)
    assert [f["id"] for f in sin["vigentes"]] == [f"faros:decision:{nueva}"] and "superadas" not in sin
    con = dec.vigentes_faros(conn, "horario tienda", incluir_superadas=True, hoy=HOY)
    assert con["superadas"][0]["id"] == f"faros:decision:{vieja}" and con["superadas"][0]["motivo"] == "cambió"


def test_las_pendientes_y_otros_temas_no_salen(conn):
    dec.ask(conn, "alice", "¿horario de la tienda en agosto?", OPTS)  # pendiente
    _decidida(conn, titulo="¿color del logo?")
    assert dec.vigentes_faros(conn, "horario tienda", hoy=HOY)["vigentes"] == []


# ---- la mitad de EcoDB, contra un EcoDB falso ----


class _EcoDBFalso(BaseHTTPRequestHandler):
    pedidas: list = []

    def do_GET(self):  # noqa: N802
        type(self).pedidas.append((self.path, self.headers.get("Authorization")))
        cuerpo = json.dumps(
            {
                "vigentes": [{"id": "m-1", "fecha": "2026-11-11", "fuente": "EcoDB"}],
                "errores": [{"sustituta_no_legible": True, "de": "m-0"}],
                "avisos": [],
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *a):
        pass


@pytest.fixture
def ecodb():
    _EcoDBFalso.pedidas = []
    srv = HTTPServer(("127.0.0.1", 0), _EcoDBFalso)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_ecodb_se_pide_con_la_clave_y_los_parametros(ecodb):
    r = dec.vigentes_ecodb("clave-x", "horario tienda", agente="Hilo", limit=7, base=ecodb)
    ruta, auth = _EcoDBFalso.pedidas[0]
    u = urlparse(ruta)
    assert u.path == "/api/v1/decisiones/vigentes" and auth == "Bearer clave-x"
    assert parse_qs(u.query) == {
        "tema": ["horario tienda"],
        "agente": ["Hilo"],
        "limit": ["7"],
        "incluir_superadas": ["false"],
    }
    assert r["vigentes"][0]["id"] == "m-1"


def test_las_dos_fuentes_se_mezclan_por_fecha(conn, ecodb):
    c = _decidida(conn)
    f = dec.vigentes_faros(conn, "horario tienda", hoy=HOY)
    e = dec.vigentes_ecodb("clave-x", "horario tienda", base=ecodb)
    r = dec.combinar(f, e, 10, False)
    assert [x["fuente"] for x in r["vigentes"]] == ["FarOS", "EcoDB"]  # alternadas, FarOS primero
    assert r["errores"] == [{"sustituta_no_legible": True, "de": "m-0"}]
    assert f"faros:decision:{c}" in [x["id"] for x in r["vigentes"]]


def test_la_mezcla_respeta_la_relevancia_no_la_fecha():
    """Prima, 10-nov: ordenar por fecha y cortar tiraba la decisión vieja y relevante (la 54) detrás de
    otras nuevas sin relación. Cada mitad llega ya ordenada por relevancia: se alternan y se cortan."""
    f = {
        "vigentes": [
            {"id": "faros:decision:54", "fecha": "2026-10-19"},
            {"id": "faros:decision:99", "fecha": "2026-11-09"},
        ],
        "errores": [],
    }
    e = {
        "vigentes": [{"id": "m-viejo", "fecha": "2026-09-01"}, {"id": "m-nuevo", "fecha": "2026-11-10"}],
        "errores": [],
        "avisos": [],
    }
    r = dec.combinar(f, e, 3, False)
    assert [x["id"] for x in r["vigentes"]] == ["faros:decision:54", "m-viejo", "faros:decision:99"]
    assert [x["rango"] for x in r["vigentes"]] == [1, 1, 2]  # su puesto dentro de su fuente


def test_sin_clave_o_sin_ecodb_responde_farOS_y_lo_dice():
    sin_clave = dec.vigentes_ecodb("", "horario tienda")
    assert sin_clave["vigentes"] == [] and "sin la clave" in sin_clave["avisos"][0]
    caido = dec.vigentes_ecodb("clave-x", "horario tienda", base="http://127.0.0.1:9", timeout=2)
    assert caido["vigentes"] == [] and caido["avisos"][0].startswith("EcoDB no respondió")


# ---- por HTTP y por MCP ----


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "load_token", lambda: TOKEN)
    db.connect(tmp_path / "t.db").close()
    c = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    yield TestClient(api.create_app(c)), c
    c.close()


def test_por_http_no_lo_confunde_con_una_decision_y_usa_la_clave_ecodb(cliente, ecodb, monkeypatch):
    client, c = cliente
    _decidida(c)
    monkeypatch.setattr(cr, "valor", lambda conn, name: "clave-de-claves" if name == "EcoDB" else "")
    monkeypatch.setattr("faros.prueba.ECODB_URL", ecodb)
    r = client.get("/api/decisions/vigentes", headers=H, params={"tema": "horario tienda"})
    assert r.status_code == 200, r.text
    assert sorted(x["fuente"] for x in r.json()["vigentes"]) == ["EcoDB", "FarOS"]
    assert _EcoDBFalso.pedidas[0][1] == "Bearer clave-de-claves"


def test_el_mcp_llama_al_endpoint(monkeypatch):
    llamadas = []
    monkeypatch.setattr(mcp_server, "_call", lambda m, path, **kw: llamadas.append((m, path, kw.get("params"))) or {})
    fn = getattr(mcp_server.decisiones_vigentes, "fn", mcp_server.decisiones_vigentes)
    fn(tema="horario tienda", agente="Hilo", incluir_superadas=True, limit=5)
    assert llamadas == [
        (
            "GET",
            "/api/decisions/vigentes",
            {"tema": "horario tienda", "incluir_superadas": "true", "limit": 5, "agente": "Hilo"},
        )
    ]
