"""#312 (rework de Prima, 5-oct) — los errores que no son de FarOS también llevan code.

El CHANGELOG dice «Every error carries a stable code». Era verdad para lo que lanza FarOS
(TicketError, _AuthError) y falso para lo que lanza el framework: una ruta que no existe
(404), un método que la ruta no admite (405) y un cuerpo o un parámetro que FastAPI no
puede validar (422) salían con {"detail": ...}, sin code y fuera del contrato plano del
#270. Un cliente se los encuentra de verdad: una errata en la ruta, un JSON roto.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from faros import api, db

TOKEN = "t-312"
H = {"X-FarOS-Token": TOKEN}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "load_token", lambda: TOKEN)
    db.connect(tmp_path / "t.db").close()
    c = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    yield TestClient(api.create_app(c))
    c.close()


def _plano(r, status, code):
    assert r.status_code == status, r.text
    body = r.json()
    assert "detail" not in body, body
    assert body["code"] == code and body["error"] and body["reason"], body
    return body


def test_404_ruta_inexistente(client):
    _plano(client.get("/api/no-existe", headers=H), 404, "route.not_found")


def test_405_metodo_no_admitido(client):
    _plano(client.delete("/api/tickets/1", headers=H), 405, "method.not_allowed")


def test_422_json_mal_formado(client):
    r = client.post(
        "/api/tickets", headers={**H, "Content-Type": "application/json"}, content=b'{"agent": "alice", "title": '
    )
    _plano(r, 422, "request.malformed_json")


def test_422_parametro_con_tipo_equivocado_lleva_su_campo(client):
    body = _plano(client.get("/api/board?project_id=abc", headers=H), 422, "project_id.invalid")
    assert body["field"] == "project_id"


def test_el_401_sigue_mandando_sin_token(client):
    r = client.post(
        "/api/tickets", headers={"Content-Type": "application/json"}, content=b'{"agent": "alice", "title": '
    )
    assert r.status_code in (401, 422)  # quien gane, lleva code
    assert r.json()["code"] in ("auth.invalid_token", "request.malformed_json")
