"""#722 (28-oct): /transition aligned with /decide (#718). An INPUT error on a transition (it
names a field: evidence, evidence_type, reason, verdict, provenance) comes back as 400
invalid_request with that field. A state conflict (an illegal transition, a verification
someone else has) stays 409 transition_rejected. The renderer already shows a 400 on a
transition as a rejection, not as «Error de red» (Lienzo, e2e test_718_transicion_400_no_es_la_red).
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from faros import api, db

TOKEN = "t-722"
H = {"X-FarOS-Token": TOKEN}


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "load_token", lambda: TOKEN)
    db.connect(tmp_path / "t.db").close()
    c = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    yield TestClient(api.create_app(c))
    c.close()


def _ticket(cli, *actions):
    tid = cli.post("/api/tickets", headers=H, json={
        "agent": "owner", "title": "t722", "owner": "alice", "verification_level": "peer",
        "scheduled_at": "2031-03-05T10:00"}).json()["ticket"]["id"]
    for body in actions:
        assert _tr(cli, tid, **body).status_code == 200
    return tid


def _tr(cli, tid, **body):
    return cli.post(f"/api/tickets/{tid}/transition", headers=H, json=body)


def _is_400(r, field, code=None):
    assert r.status_code == 400, (r.status_code, r.text)
    body = r.json()
    assert body["error"] == "invalid_request" and body["field"] == field
    if code:
        assert body["code"] == code


ACCEPT = {"agent": "alice", "action": "accept"}
COMPLETE = {"agent": "alice", "action": "complete", "evidence_type": "file_path", "evidence": "x.md"}


def test_complete_with_blank_evidence_is_400_evidence(cli):
    tid = _ticket(cli, ACCEPT)
    _is_400(_tr(cli, tid, agent="alice", action="complete", evidence_type="file_path", evidence=" "),
            "evidence", "complete.evidence_required")


def test_complete_with_no_evidence_at_all_is_400_evidence_type(cli):
    # #722 (verifier, 29-oct): neither evidence_type nor evidence. The type is checked first.
    tid = _ticket(cli, ACCEPT)
    _is_400(_tr(cli, tid, agent="alice", action="complete"), "evidence_type", "complete.evidence_required")


def test_rework_with_a_short_reason_is_400_reason(cli):
    tid = _ticket(cli, ACCEPT, COMPLETE)
    _is_400(_tr(cli, tid, agent="alice", action="rework", reason="corto"), "reason", "rework.reason_required")


def test_pause_with_a_short_note_is_400_note(cli):
    tid = _ticket(cli, ACCEPT, {"agent": "alice", "action": "start"})
    _is_400(_tr(cli, tid, agent="alice", action="pause", note="no"), "note", "pause.note_required")


def test_complete_with_a_bad_evidence_type_is_400_evidence_type(cli):
    tid = _ticket(cli, ACCEPT)
    _is_400(_tr(cli, tid, agent="alice", action="complete", evidence_type="test", evidence="x"),
            "evidence_type", "complete.evidence_required")
    _is_400(_tr(cli, tid, agent="alice", action="complete", evidence="x"),
            "evidence_type", "complete.evidence_required")


def test_block_without_reason_is_400_reason(cli):
    tid = _ticket(cli, ACCEPT)
    _is_400(_tr(cli, tid, agent="alice", action="block"), "reason", "block.reason_required")


def test_bad_verdict_is_400_verdict(cli):
    tid = _ticket(cli, ACCEPT, COMPLETE)
    _is_400(_tr(cli, tid, agent="bob", action="verify", verdict="quizá"), "verdict", "verify.verdict_invalid")


def test_bad_provenance_is_400_provenance(cli):
    tid = _ticket(cli, ACCEPT, COMPLETE)
    _is_400(_tr(cli, tid, agent="bob", action="verify", provenance="external"),
            "provenance", "verify.provenance_invalid")


def test_illegal_transition_is_still_409(cli):
    tid = _ticket(cli, ACCEPT, COMPLETE)
    r = _tr(cli, tid, agent="alice", action="block", reason="no se puede desde done")
    assert r.status_code == 409 and r.json()["error"] == "transition_rejected"
    assert r.json()["code"] == "transition.illegal" and "field" not in r.json()


def test_verifying_your_own_ticket_is_still_409(cli):
    tid = _ticket(cli, ACCEPT, COMPLETE)
    r = _tr(cli, tid, agent="alice", action="verify", verdict="pass")
    assert r.status_code == 409 and r.json()["error"] == "transition_rejected"


def test_a_good_transition_still_works(cli):
    tid = _ticket(cli, ACCEPT, COMPLETE)
    r = _tr(cli, tid, agent="bob", action="verify", verdict="pass", provenance="deterministic:pytest")
    assert r.status_code == 200 and r.json()["status"] == "verified"
