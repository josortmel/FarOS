"""Verificador con modelo barato (SPEC §8).

Barre la cola `done` de nivel auto: por cada ticket, un claude -p con haiku,
sin herramientas, con --json-schema para forzar {"verdict","reason"}. pass →
verified; fail → in_progress con el motivo. El prompt vive en un fichero
editable (ensayo y error, por decisión del dueño): verify/prompt.md del repo,
sobreescribible con AGENTICOS_VERIFY_PROMPT.

La evidencia del agente se envuelve como DATO NO CONFIABLE (review Prima
F14/F15): el prompt instruye a juzgarla, no a obedecerla.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess

from .procutil import hidden_popen_kwargs
from pathlib import Path

from . import service, runner
from . import env as _env

log = logging.getLogger("faros.verifier")

VERIFIER_MODEL = _env.get("VERIFY_MODEL", "haiku")
PROMPT_FILE = Path(_env.get("VERIFY_PROMPT")
                   or Path(__file__).resolve().parent.parent / "verify" / "prompt.md")
VERDICT_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
})
EVIDENCE_MAX = 8000


def _load_template() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8")


def _evidence_text(conn, t: dict) -> str:
    etype, ev = t.get("evidence_type"), t.get("evidence") or ""
    if etype == "run_output":
        try:
            run = service.get_run(conn, int(ev))
            if run.get("output_path") and Path(run["output_path"]).exists():
                return Path(run["output_path"]).read_text(encoding="utf-8")[:EVIDENCE_MAX]
        except (service.TicketError, ValueError):
            pass
        return f"(run {ev}: output no disponible)"
    if etype == "file_path":
        p = Path(ev)
        if p.exists():
            head = ""
            try:
                head = p.read_text(encoding="utf-8", errors="replace")[:2000]
            except OSError:
                head = "(binario o ilegible)"
            return f"El fichero {ev} EXISTE ({p.stat().st_size} bytes). Comienzo:\n{head}"
        return f"El fichero {ev} NO EXISTE en disco."
    return ev[:EVIDENCE_MAX]


def build_prompt(conn, t: dict) -> str:
    return _load_template().format(
        title=t["title"],
        description=t.get("description") or "(sin descripción)",
        verify_criteria=t.get("verify_criteria") or "(el ticket no declara criterio; juzga si la evidencia demuestra que el trabajo del título se hizo de verdad)",
        evidence_type=t.get("evidence_type"),
        evidence=_evidence_text(conn, t),
    )


async def _judge(prompt: str, model: str | None = None) -> dict:
    adapter = runner.get_adapter("claude-cli")
    cmd = [adapter.exe, "-p", "--output-format", "json", "--no-session-persistence",
           "--model", model or VERIFIER_MODEL, "--tools", "", "--json-schema", VERDICT_SCHEMA]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(runner.DATA_DIR), **hidden_popen_kwargs())
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(prompt.encode("utf-8")),
                                              timeout=300)
    except asyncio.TimeoutError:
        await runner._kill_tree(proc)
        raise RuntimeError("verificador: timeout de 300s")
    out = out_b.decode("utf-8", errors="replace")
    data = json.loads(out)
    # claude 2.1.206 emite la lista de mensajes; el veredicto es el último 'result'.
    if isinstance(data, list):
        results = [m for m in data if isinstance(m, dict) and m.get("type") == "result"]
        if not results:
            raise RuntimeError(f"verificador sin mensaje result: {out[:400]!r}")
        data = results[-1]
    if data.get("is_error"):
        raise RuntimeError(f"verificador devolvió error: {data.get('result')!r}")
    verdict = data.get("structured_output")
    if not verdict:
        raw = data.get("result") or "{}"
        verdict = json.loads(raw) if raw.strip().startswith("{") else {}
    if verdict.get("verdict") not in {"pass", "fail"}:
        raise RuntimeError(f"verificador sin verdict válido: {out[:400]!r}")
    return verdict


def build_run_prompt(conn, run: dict, job: dict) -> str:
    """Mismo juez, evidencia = output del run. El 'ticket' es el job (T1.10)."""
    output = "(run sin output)"
    if run.get("output_path") and Path(run["output_path"]).exists():
        output = Path(run["output_path"]).read_text(encoding="utf-8", errors="replace")[:EVIDENCE_MAX]
    elif run.get("result_summary"):
        output = run["result_summary"][:EVIDENCE_MAX]
    return _load_template().format(
        title=f"{job['name']} (job #{job['id']}, run #{run['id']})",
        description=(job.get("prompt") or "")[:3000],
        verify_criteria=job.get("verify_criteria") or
        "(el job no declara criterio; juzga si el output es sustantivo, coherente con el encargo y sin afirmaciones no demostradas)",
        evidence_type="run_output",
        evidence=output,
    )


async def sweep_runs(conn, emit=None) -> dict:
    """Veredicto POR RUN (T1.10): runs ok sin veredicto de jobs con verify_level=auto.
    pass + ticket vinculado → jobs.set_verdict completa el ticket (brazo automático)."""
    from . import jobs, settings
    if not settings.get(conn, "verifier_enabled"):
        return {"swept": 0, "passed": [], "failed": [], "errors": [], "disabled": True}
    model = settings.get(conn, "verifier_model")
    queue = jobs.runs_pending_verdict(conn)
    passed, failed, errors = [], [], []
    for run in queue:
        try:
            job = jobs.get_job(conn, run["job_id"])
            verdict = await _judge(build_run_prompt(conn, run, job), model=model)
        except Exception as exc:
            log.exception("verificador falló en run #%s", run["id"])
            errors.append({"run_id": run["id"], "error": str(exc)})
            continue
        reason = (verdict.get("reason") or "").strip()[:1000] or "(sin motivo)"
        try:
            res = jobs.set_verdict(conn, run["id"], verdict["verdict"], reason)
        except service.TicketError as exc:  # p.ej. alguien lo juzgó a mano entre medias
            errors.append({"run_id": run["id"], "error": str(exc)})
            continue
        (passed if res["verdict"] == "pass" else failed).append(
            {"run_id": run["id"], "job_id": run["job_id"], "reason": reason})
        if emit:
            emit("verify.done", {"run_id": run["id"], "job_id": run["job_id"],
                                 "ticket_id": run.get("ticket_id"), "verdict": res["verdict"]})
    return {"swept": len(queue), "passed": passed, "failed": failed, "errors": errors}


async def sweep(conn, emit=None) -> dict:
    """Verifica la cola done de nivel auto. Los peer se quedan para humanos."""
    from . import settings
    model = settings.get(conn, "verifier_model")  # T0.4: ajuste en DB, env como fallback
    queue = [t for t in service.list_tickets(conn, status="done")
             if t["verification_level"] == "auto"]
    verified, bounced, errors = [], [], []
    for t in queue:
        try:
            verdict = await _judge(build_prompt(conn, t), model=model)
        except Exception as exc:
            log.exception("verificador falló en ticket #%s", t["id"])
            errors.append({"ticket_id": t["id"], "error": str(exc)})
            continue
        reason = (verdict.get("reason") or "").strip()[:1000] or "(sin motivo)"
        res = service.verify_ticket(conn, "verifier-bot", t["id"], verdict["verdict"],
                                    note=reason)
        (verified if res["status"] == "verified" else bounced).append(
            {"ticket_id": t["id"], "reason": reason})
        if emit:
            emit("verify.done", {"ticket_id": t["id"], "verdict": verdict["verdict"]})
    return {"swept": len(queue), "verified": verified, "bounced": bounced, "errors": errors}
