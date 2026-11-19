"""#718 (23-oct): over HTTP, an INPUT error on /decide (it carries a field) must come back as
400 invalid_request with that field, as the #657 contract says and the fixtures mirror does
(api.js decide). Before, every TicketError on /decide was 409 transition_rejected and lost
its field: a bad surface looked like a state conflict. State conflicts (deciding twice)
stay 409. /transition is NOT changed here: app.js shows a 400 on a transition as «Error de
red», so moving it needs the renderer too.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from faros import api, db, decisions as dec

TOKEN = "t-718"
H = {"X-FarOS-Token": TOKEN}
OPTS = [{"label": "sí", "text": "sí, hazlo"}, {"label": "no", "text": "no lo hagas"}]


@pytest.fixture
def client_and_id(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "load_token", lambda: TOKEN)
    db.connect(tmp_path / "t.db").close()
    c = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    d = dec.ask(c, "alice", "¿lo hago?", OPTS)
    yield TestClient(api.create_app(c)), d["id"]
    c.close()


def _decide(client, did, **extra):
    body = {"agent": "owner", "option": "sí", "rationale": "porque sí", **extra}
    return client.post(f"/api/decisions/{did}/decide", headers=H, json=body)


def test_bad_surface_is_400_with_field(client_and_id):
    client, did = client_and_id
    r = _decide(client, did, surface="tele")
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "invalid_request" and body["field"] == "surface"
    assert body["code"] == "surface.invalid" and "tele" in body["reason"]


def test_deciding_twice_is_still_409(client_and_id):
    client, did = client_and_id
    assert _decide(client, did, surface="desktop").status_code == 200
    r = _decide(client, did, surface="desktop")
    assert r.status_code == 409 and r.json()["error"] == "transition_rejected"


def test_good_surface_still_decides(client_and_id):
    client, did = client_and_id
    r = _decide(client, did, surface="mobile")
    assert r.status_code == 200
    assert r.json()["history"][-1]["surface"] == "mobile"
