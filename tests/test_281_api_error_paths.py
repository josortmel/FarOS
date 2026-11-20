"""#281 (parte 2) — los caminos de error de la API, por la suite y no a mano.

El contrato de errores (#270): cuerpos PLANOS con `error`, `reason` y, cuando se sabe,
`field` y `code`. Hasta hoy se había comprobado contra un daemon vivo a mano: un
investigador que clonara el repo no encontraba un solo assert sobre 401/400/409.
Se aserta la FORMA completa, no solo el estado.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from faros import api, db

TOKEN = "t-281"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "load_token", lambda: TOKEN)
    db.connect(tmp_path / "t.db").close()
    c = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    yield TestClient(api.create_app(c))
    c.close()


H = {"X-FarOS-Token": TOKEN}
AUTH_401 = {"error": "unauthorized", "code": "auth.invalid_token"}


@pytest.mark.parametrize("headers", [{}, {"X-FarOS-Token": "malo"}, {"X-AgenticOS-Token": "malo"}])
def test_401_without_or_with_a_bad_token(client, headers):
    r = client.get("/api/board", headers=headers)
    assert r.status_code == 401
    body = r.json()
    assert {k: body[k] for k in AUTH_401} == AUTH_401 and body["reason"]


def test_401_on_the_event_stream_too(client):
    # el síntoma de la L57 era justo este endpoint en 401 con el tablero vacío
    r = client.get("/api/events")
    assert r.status_code == 401 and r.json()["code"] == "auth.invalid_token"


def test_health_needs_no_token(client):
    assert client.get("/api/health").status_code == 200


def test_400_carries_error_reason_field_and_code(client):
    r = client.post("/api/tickets", json={"title": "sin quien crea"}, headers=H)
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "invalid_request" and body["reason"]
    assert body["field"] == "agent" and body["code"] == "request.agent_required"


def test_400_unknown_field_is_named(client):
    r = client.post("/api/tickets", json={"agent": "owner", "title": "x", "inventado": 1}, headers=H)
    assert r.status_code == 400
    assert r.json()["field"] == "inventado" and r.json()["code"] == "request.unknown_field"


def test_409_illegal_transition_is_transition_rejected(client):
    t = client.post("/api/tickets", json={"agent": "owner", "title": "x", "owner": "alice"}, headers=H).json()["ticket"]
    r = client.post(
        f"/api/tickets/{t['id']}/transition",
        json={"agent": "alice", "action": "complete", "evidence_type": "file_path", "evidence": "x.md"},
        headers=H,
    )
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "transition_rejected" and body["reason"] and body["code"] == "transition.illegal"


def test_unknown_ticket_carries_its_code(client):
    r = client.get("/api/tickets/999999", headers=H)
    assert r.status_code == 400
    assert r.json()["code"] == "ticket.not_found" and r.json()["error"] == "invalid_request"


# ---------------------------------------------------------------- #312
# Los seis caminos de error que midió el ticket: TODOS llevan un `code` estable.
# Los que no tienen uno específico heredan <field>.invalid, o request.invalid.


@pytest.mark.parametrize(
    "path, body, code",
    [
        ("/api/tickets", {"title": "x"}, "request.agent_required"),
        ("/api/tickets", {"agent": "nadie", "title": "x"}, "agent.unknown"),
        ("/api/tickets", {"agent": "owner", "title": "  "}, "title.invalid"),
        ("/api/tickets", {"agent": "owner", "title": "x", "priority": "urgentisima"}, "priority.invalid"),
    ],
)
def test_312_every_validation_error_carries_a_code(client, path, body, code):
    r = client.post(path, json=body, headers=H)
    assert r.status_code == 400 and r.json()["code"] == code, r.json()


def test_312_error_without_field_gets_request_invalid(client):
    t = client.post("/api/tickets", json={"agent": "owner", "title": "x", "owner": "alice"}, headers=H).json()["ticket"]
    r = client.patch(f"/api/tickets/{t['id']}", json={"agent": "alice", "duration_min": 99999}, headers=H)
    assert r.status_code == 400 and r.json()["code"] == "request.invalid", r.json()
