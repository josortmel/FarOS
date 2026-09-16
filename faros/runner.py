"""Runner de trabajo agéntico headless (SPEC §7).

HarnessAdapter: contrato para enchufar harness. v1 implementa ClaudeCliAdapter
(claude -p). El prompt viaja por STDIN — sin límites de línea de comandos ni
quoting hell de Windows.

Los agentes lanzados son GENÉRICOS: cwd aislado en el workspace del job (sin
CLAUDE.md de la casa), sin identidad, sin sesión persistente.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess

from .procutil import hidden_popen_kwargs, hidden_console_popen_kwargs
from pathlib import Path

from . import service, harness
from .harness.base import RunResult  # noqa: F401  (re-export: verifier y tests lo usan)

from .db import DATA_DIR  # una sola resolución de la casa (AGENTICOS_HOME > LOCALAPPDATA)
from . import env as _env
ARTIFACTS_DIR = DATA_DIR / "artifacts"
JOBS_DIR = DATA_DIR / "jobs"
SUMMARY_MAX = 2000


# La redaccion vive en env.py desde #301: la necesita tambien api.py para
# /api/health, y api.py no puede importar runner. Mismo nombre aqui para no
# tocar a sus llamadores.
from .env import redact_home as _redact_home  # noqa: E402

# run_id → (proceso, job_object) de los runs en curso, para poder cancelarlos.
LIVE_RUNS: dict[int, tuple] = {}


async def cancel_run(conn, run_id: int) -> dict:
    """Mata el proceso del run en curso y lo marca cancelled. El execute_job que
    lo lanzó verá el run ya cerrado y no lo pisará."""
    entry = LIVE_RUNS.get(run_id)
    if entry is None:
        run = service.get_run(conn, run_id)
        if run["status"] == "running":  # huérfano sin proceso registrado
            return service.finish_run(conn, run_id, "cancelled",
                                      result_summary="(cancelado; proceso no localizado)")
        raise service.TicketError(f"run #{run_id} no está en curso ({run['status']})")
    proc, job_obj = entry
    await _kill_tree(proc, job=job_obj)
    return {"run_id": run_id, "cancelling": True}


def get_adapter(name: str):
    """Adapter del registro (faros.harness). TicketError si no existe o no
    está instalado — el error llega al botón/tool con motivo, no a un log."""
    try:
        adapter = harness.get(name)
    except KeyError as exc:
        raise service.TicketError(str(exc))
    except RuntimeError as exc:  # ejecutable ausente
        raise service.TicketError(f"harness {name!r} no disponible: {exc}")
    return adapter


PREAMBLE_FILE = Path(_env.get("AGENT_PREAMBLE")
                     or Path(__file__).resolve().parent.parent / "verify" / "agent_preamble.md")


def compose_preamble(job: dict, run_id: int | None) -> str:
    """Bloque fijo de contexto para el agente genérico (T1.9). Editable en
    verify/agent_preamble.md. Vacío si la plantilla no existe."""
    from . import schedule as schedule_mod
    try:
        template = PREAMBLE_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""
    if job.get("json_schema"):
        deliver = ("- Tu respuesta final debe ser ÚNICAMENTE el JSON que cumple el esquema "
                   "exigido (salida estructurada). Sin texto alrededor.")
    else:
        deliver = ("- Empieza con un resumen de 1-3 líneas de lo que hiciste y encontraste.\n"
                   "- Después el resultado completo en markdown claro (listas, no párrafos largos).\n"
                   "- Si algo falló o no pudiste hacerlo, dilo explícitamente al principio.")
    return template.format(
        job_name=job.get("name") or f"job #{job.get('id')}",
        job_id=job.get("id"), run_id=run_id if run_id is not None else "-",
        schedule_human=schedule_mod.human(job.get("schedule") or {"type": "manual"}),
        harness=job.get("harness"), model=job.get("model"),
        verify_criteria=(job.get("verify_criteria") or
                         "(sin criterio declarado: se juzgará que el resultado sea sustantivo, "
                         "coherente con el encargo y sin afirmaciones no demostradas)"),
        deliver_instructions=deliver,
    )


def build_invocation(adapter, job: dict, run_id: int | None) -> tuple[list[str], bytes]:
    """(comando, stdin). El preámbulo va por el flag del adapter si lo tiene;
    si no, antepuesto al prompt por stdin."""
    cmd = adapter.build_command(job)
    preamble = compose_preamble(job, run_id)
    prompt = job["prompt"]
    if preamble:
        extra = adapter.preamble_args(preamble)
        if extra:
            cmd = cmd + extra
        else:
            prompt = preamble + "\n" + prompt
    return cmd, prompt.encode("utf-8")


_ENV_WHITELIST = {
    "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR",
    "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "HOME",
    "COMSPEC", "PATHEXT", "USERNAME",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "COMMONPROGRAMFILES",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
}


def _env_for(conn, adapter) -> dict:
    """Clean env for the subprocess: only whitelisted OS vars + what the adapter
    asks from settings. Prevents leaking GITHUB_TOKEN, AWS keys, etc. (F-SEG-02).
    Only the settings declared in adapter.required_settings reach env() (F-SEG-04)."""
    from . import settings as settings_mod
    env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_WHITELIST}
    try:
        needed = {k: settings_mod.get(conn, k) for k in adapter.required_settings
                  if k in settings_mod.DEFAULTS}
        env.update({k: str(v) for k, v in adapter.env(needed).items() if v})
    except Exception:
        pass
    return env


def _workspace_for(job: dict) -> Path:
    if job.get("cwd"):
        p = Path(job["cwd"])
    else:
        p = JOBS_DIR / str(job["id"]) / "workspace"
    p.mkdir(parents=True, exist_ok=True)
    return p


async def _kill_tree(proc: asyncio.subprocess.Process, job=None) -> None:
    """Kill the process and its entire tree. Uses Job Objects (atomic, no race)
    when available, falls back to taskkill /T /F."""
    if proc.returncode is not None:
        return
    if job is not None:
        job.terminate()
    elif os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(proc.pid), "/T", "/F",
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **hidden_popen_kwargs())
        await killer.wait()
    else:
        proc.kill()
    try:
        await asyncio.wait_for(proc.wait(), timeout=10)
    except asyncio.TimeoutError:
        pass


async def execute_job(conn, job_id: int, emit=None, run: dict | None = None) -> dict:
    """Lanza un run del job y aplica el resultado al ticket. Devuelve el run cerrado.

    Si `run` viene creado (endpoint /launch: valida ANTES de responder al botón),
    se reutiliza; si no, se crea aquí (scheduler). conn se usa solo en el hilo del
    event loop — cada llamada a service es síncrona hasta su commit.
    """
    from . import jobs as jobs_mod
    job = jobs_mod.get_job(conn, job_id)
    adapter = get_adapter(job["harness"])
    if run is None:
        run = jobs_mod.create_run(conn, job_id)
    if emit:
        emit("run.started", {"run_id": run["id"], "ticket_id": run["ticket_id"], "job_id": job_id})

    run_dir = ARTIFACTS_DIR / f"run_{run['id']}"
    run_dir.mkdir(parents=True, exist_ok=True)
    workspace = _workspace_for(job)
    # v3.1 (T5.6): MCP del registro → fichero temporal por run (se borra al acabar).
    from . import mcp_registry as _reg
    mcp_tmp = None
    try:
        job, mcp_tmp = _reg.materialize(conn, job, run_dir, harness=adapter.name)
    except Exception as exc:  # registro incompleto: el run falla con motivo, no el daemon
        (run_dir / "stderr.log").write_text(f"mcp: {exc}", encoding="utf-8")
        return jobs_mod.finish_run(conn, run["id"], "error", exit_code=-1,
                                   output_path=None, result_summary=f"(mcp: {exc})")
    cmd, stdin_bytes = build_invocation(adapter, job, run["id"])

    job_obj = None
    if os.name == "nt":
        try:
            from .jobobj import JobObject
            job_obj = JobObject()
        except Exception:
            pass  # fallback to taskkill in _kill_tree

    timed_out = False
    cancelled = False
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workspace),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**_env_for(conn, adapter), **(job.get("_env") or {})},  # _env: T5.8 (OPENCODE_CONFIG)
            # T1.1: sin consola (el dueño, queja 5ª). CREATE_NEW_CONSOLE+SW_HIDE se midió el 4-sep y NO
            # evita las ventanas de los nietos con Windows Terminal como terminal por defecto.
            **hidden_popen_kwargs(),
        )
        if job_obj is not None:
            try:
                job_obj.assign(proc.pid)
            except Exception:
                pass  # fallback to taskkill
        LIVE_RUNS[run["id"]] = (proc, job_obj)
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(stdin_bytes),
                timeout=job["timeout_s"])
        except asyncio.TimeoutError:
            timed_out = True
            await _kill_tree(proc, job=job_obj)
            stdout_b, stderr_b = b"", b"(timeout)"
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        exit_code = proc.returncode
        # Terminado por señal (cancel externo mató el árbol): exit code negativo/no-cero
        cancelled = exit_code not in (0, None) and not timed_out and not stdout.strip()
    except Exception as exc:  # spawn imposible (exe borrado, permisos...)
        stdout, stderr, exit_code = "", f"spawn failed: {exc}", -1
    finally:
        LIVE_RUNS.pop(run["id"], None)
        if job_obj is not None:
            job_obj.close()
        _reg.cleanup(mcp_tmp)  # secretos fuera de disco en cuanto el proceso muere

    # A partir de aquí NADA puede dejar el run en 'running': parse o escritura
    # de artifacts que reviente se registra como error, no como run zombi.
    try:
        if timed_out:
            result = RunResult("timeout", exit_code, None, None, stdout, None)
        else:
            result = adapter.parse_output(stdout, exit_code if exit_code is not None else -1)

        (run_dir / "output.md").write_text(result.output_text or "(sin output)", encoding="utf-8")
        if stderr.strip():
            (run_dir / "stderr.log").write_text(stderr, encoding="utf-8")
        (run_dir / "meta.json").write_text(json.dumps({
            "cmd": [("<mcp_config>" if c == str(mcp_tmp) else _redact_home(c))
                    for c in cmd],
            "workspace": _redact_home(str(workspace)), "exit_code": exit_code,
            "status": result.status, "cost_usd": result.cost_usd,
            "session_id": result.session_id, "raw": result.raw,
        }, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        summary = (result.output_text or "").strip()[:SUMMARY_MAX] or f"({result.status}, sin output)"
    except Exception as exc:
        result = RunResult("error", exit_code, None, None, stdout[:SUMMARY_MAX], None)
        summary = f"(runner error: {exc})"
        try:
            (run_dir / "stderr.log").write_text(f"{stderr}\n\nrunner exception: {exc}",
                                                encoding="utf-8")
        except OSError:
            pass

    closed = jobs_mod.finish_run(
        conn, run["id"], result.status,
        exit_code=exit_code, cost_usd=result.cost_usd, session_id=result.session_id,
        output_path=str(run_dir / "output.md"), result_summary=summary)
    if emit:
        emit("run.finished", {"run_id": run["id"], "ticket_id": run["ticket_id"],
                              "status": result.status})
    return closed
