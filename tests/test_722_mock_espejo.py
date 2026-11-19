"""#722 (30-oct, reopened by the verifier): the fixtures mock (app/renderer/js/api.js) must answer a
transition EXACTLY like the daemon — same HTTP status, error kind, field and code. The verifier found
the mock asking for `note` on block (the daemon takes `reason`) and rework/pause input errors with no
field. Each scenario runs against the daemon (TestClient) and the mock (node, tests/_mock_espejo.mjs).
"""

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from faros import api, db

TOKEN = "t-722m"
H = {"X-FarOS-Token": TOKEN}
RUNNER = Path(__file__).with_name("_mock_espejo.mjs")

ACCEPT = {"agent": "alice", "action": "accept"}
START = {"agent": "alice", "action": "start"}
COMPLETE = {"agent": "alice", "action": "complete", "evidence_type": "file_path", "evidence": "x.md"}

SCENARIOS = [
    {"name": "block with reason", "steps": [ACCEPT, {"agent": "alice", "action": "block", "reason": "espera a Pepe"}]},
    {"name": "block with note", "steps": [ACCEPT, {"agent": "alice", "action": "block", "note": "espera a Pepe"}]},
    {"name": "block with nothing", "steps": [ACCEPT, {"agent": "alice", "action": "block"}]},
    {"name": "complete with no evidence at all", "steps": [ACCEPT, {"agent": "alice", "action": "complete"}]},
    {"name": "complete with blank evidence", "steps": [ACCEPT, {"agent": "alice", "action": "complete",
                                                                "evidence_type": "file_path", "evidence": " "}]},
    {"name": "rework with a short reason", "steps": [ACCEPT, COMPLETE, {"agent": "alice", "action": "rework",
                                                                        "reason": "corto"}]},
    {"name": "rework with a reason", "steps": [ACCEPT, COMPLETE, {"agent": "alice", "action": "rework",
                                                                  "reason": "le falta la prueba de verdad"}]},
    {"name": "pause with a short note", "steps": [ACCEPT, START, {"agent": "alice", "action": "pause", "note": "no"}]},
    {"name": "pause with a note", "steps": [ACCEPT, START, {"agent": "alice", "action": "pause",
                                                            "note": "no estaba en curso de verdad"}]},
    {"name": "illegal: block from done", "steps": [ACCEPT, COMPLETE, {"agent": "alice", "action": "block",
                                                                      "reason": "no se puede desde done"}]},
]


def _daemon(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "load_token", lambda: TOKEN)
    db.connect(tmp_path / "t.db").close()
    c = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    cli = TestClient(api.create_app(c))
    out = []
    for sc in SCENARIOS:
        tid = cli.post("/api/tickets", headers=H, json={
            "agent": "owner", "title": sc["name"], "owner": "alice"}).json()["ticket"]["id"]
        last = None
        for body in sc["steps"]:
            r = cli.post(f"/api/tickets/{tid}/transition", headers=H, json=body)
            if r.status_code < 400:
                last = {"status": 200}
            else:
                b = r.json()
                last = {"status": r.status_code, "error": b.get("error"), "field": b.get("field"), "code": b.get("code")}
        out.append(last)
    c.close()
    return out


def _mock():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    p = subprocess.run([node, str(RUNNER)], input=json.dumps(SCENARIOS), capture_output=True,
                       text=True, encoding="utf-8", timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_the_mock_answers_every_transition_like_the_daemon(tmp_path, monkeypatch):
    daemon, mock = _daemon(tmp_path, monkeypatch), _mock()
    diffs = [(sc["name"], d, m) for sc, d, m in zip(SCENARIOS, daemon, mock) if d != m]
    assert not diffs, "\n".join(f"{n}: daemon={d} mock={m}" for n, d, m in diffs)


def test_input_errors_name_their_field_and_state_errors_are_409(tmp_path, monkeypatch):
    by_name = dict(zip((s["name"] for s in SCENARIOS), _daemon(tmp_path, monkeypatch)))
    assert by_name["block with reason"] == {"status": 200}
    assert by_name["block with nothing"]["field"] == "reason"
    assert by_name["complete with no evidence at all"] == {
        "status": 400, "error": "invalid_request", "field": "evidence_type", "code": "complete.evidence_required"}
    assert by_name["complete with blank evidence"]["field"] == "evidence"
    assert by_name["rework with a short reason"] == {
        "status": 400, "error": "invalid_request", "field": "reason", "code": "rework.reason_required"}
    assert by_name["pause with a short note"] == {
        "status": 400, "error": "invalid_request", "field": "note", "code": "pause.note_required"}
    assert by_name["illegal: block from done"]["status"] == 409
    assert by_name["illegal: block from done"]["field"] is None
