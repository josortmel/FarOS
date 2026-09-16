"""Tests del veredicto por run (T1.10) y del preámbulo (T1.9), con juez falso."""

import asyncio

import pytest

from faros import db, jobs, service as s, verifier, runner, harness


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _fake_judge(verdicts):
    calls = []

    async def judge(prompt, model=None):
        calls.append(prompt)
        return verdicts.pop(0)
    return judge, calls


def _manual(conn):
    return s.propose_ticket(conn, "owner", "Leer el correo", auto_accept=True,
                            verification_level="peer")["ticket"]


def test_sweep_runs_sets_verdict_and_completes_linked_only_on_pass(conn, monkeypatch):
    t = _manual(conn)
    j = jobs.create_job(conn, "owner", "Correo", "resume", ticket_id=t["id"],
                        verify_criteria="hay un resumen con remitentes")
    r1 = jobs.create_run(conn, j["id"]); jobs.finish_run(conn, r1["id"], "ok", result_summary="nada")
    judge, calls = _fake_judge([{"verdict": "fail", "reason": "no lista remitentes"}])
    monkeypatch.setattr(verifier, "_judge", judge)
    res = asyncio.run(verifier.sweep_runs(conn))
    assert res["swept"] == 1 and [f["run_id"] for f in res["failed"]] == [r1["id"]]
    assert "Correo (job #" in calls[0] and "hay un resumen con remitentes" in calls[0] and "nada" in calls[0]
    assert s._as_dict(conn, s._get(conn, t["id"]))["status"] == "in_progress"
    r2 = jobs.create_run(conn, j["id"]); jobs.finish_run(conn, r2["id"], "ok", result_summary="De: Ana...")
    judge, _ = _fake_judge([{"verdict": "pass", "reason": "resumen con remitentes"}])
    monkeypatch.setattr(verifier, "_judge", judge)
    res = asyncio.run(verifier.sweep_runs(conn))
    assert [p["run_id"] for p in res["passed"]] == [r2["id"]]
    fresh = s._as_dict(conn, s._get(conn, t["id"]))
    assert fresh["status"] == "done" and fresh["evidence"] == str(r2["id"])
    assert asyncio.run(verifier.sweep_runs(conn))["swept"] == 0  # nada pendiente


def test_sweep_runs_respects_verify_level_none_and_disabled(conn, monkeypatch):
    from faros import settings
    j = jobs.create_job(conn, "owner", "Sin juez", "x", verify_level="none")
    r = jobs.create_run(conn, j["id"]); jobs.finish_run(conn, r["id"], "ok")
    judge, calls = _fake_judge([])
    monkeypatch.setattr(verifier, "_judge", judge)
    assert asyncio.run(verifier.sweep_runs(conn))["swept"] == 0 and calls == []
    j2 = jobs.create_job(conn, "owner", "Con juez", "x")
    r2 = jobs.create_run(conn, j2["id"]); jobs.finish_run(conn, r2["id"], "ok")
    settings.patch(conn, "owner", {"verifier_enabled": False})
    assert asyncio.run(verifier.sweep_runs(conn)).get("disabled") is True and calls == []


def test_preamble_composed_and_sent_via_append_system_prompt(conn):
    j = jobs.create_job(conn, "owner", "Resumen de correo", "resume mis correos",
                        schedule={"type": "weekly", "weekdays": [0, 2, 4], "at": ["08:00"]},
                        verify_criteria="lista con remitente y asunto")
    text = runner.compose_preamble(j, 42)
    assert "«Resumen de correo»" in text and "run #42" in text
    assert "lunes, miércoles, viernes a las 08:00" in text and "remitente y asunto" in text
    assert "verdict: pass" in text  # la prohibición explícita
    cmd, stdin = runner.build_invocation(harness.get("claude-cli"), j, 42)
    assert "--append-system-prompt" in cmd and cmd[cmd.index("--append-system-prompt") + 1] == text
    assert stdin == b"resume mis correos"  # el prompt del job va por stdin, intacto
    j2 = jobs.create_job(conn, "owner", "JSON", "dame json", json_schema={"type": "object"})
    assert "ÚNICAMENTE el JSON" in runner.compose_preamble(j2, None)


def test_preamble_prepended_when_adapter_has_no_flag(conn):
    from faros.harness.base import HarnessAdapter, RunResult

    class Plain(HarnessAdapter):
        name = "plain"

        def build_command(self, job):
            return ["plain"]

        def parse_output(self, stdout, exit_code):
            return RunResult("ok", exit_code, None, None, stdout, None)

    j = jobs.create_job(conn, "owner", "P", "hola")
    cmd, stdin = runner.build_invocation(Plain(), j, 1)
    # el preambulo se antepone: se comprueba con lo que el preambulo SIGNIFICA, no con la
    # marca. Antes decia b"AgenticOS" y se puso rojo al renombrar el producto en
    # verify/agent_preamble.md. El test hizo su trabajo — pero se anclaba justo al dato
    # que mas cambia, y eso lo convierte en un test que hay que arreglar cada vez que
    # cambia algo que no es lo suyo.
    assert cmd == ["plain"] and stdin.endswith(b"\nhola")
    assert "no eres una sesión interactiva".encode("utf-8") in stdin.lower()
