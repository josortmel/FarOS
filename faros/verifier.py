"""Verificador con modelo barato (SPEC §8).

Barre la cola `done` de nivel auto: por cada ticket, un claude -p con haiku,
sin herramientas, con --json-schema para forzar {"verdict","reason"}. pass →
verified; fail → in_progress con el motivo; uncertain (#451) → sigue done y pasa a
verificación de par.
#465 (decisión 22 de Pepe, 7-oct): DeepSeek es el último recurso. Una tarea de nivel
«external» la juzga DeepSeek por MAYORÍA de 3 corridas, y un incierto de Haiku sube a
DeepSeek ×3 antes de pararse en una persona. Sin mayoría, o sin clave, decide una
persona, como antes. El prompt vive en un fichero
editable (ensayo y error, por decisión del dueño): verify/prompt.md del repo,
sobreescribible con AGENTICOS_VERIFY_PROMPT.

La evidencia del agente se envuelve como DATO NO CONFIABLE (review Prima
F14/F15): el prompt instruye a juzgarla, no a obedecerla.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
from pathlib import Path

from . import env as _env
from . import runner, service
from .procutil import hidden_popen_kwargs

log = logging.getLogger("faros.verifier")

VERIFIER_MODEL = _env.get("VERIFY_MODEL", "haiku")
PROMPT_FILE = Path(_env.get("VERIFY_PROMPT") or Path(__file__).resolve().parent.parent / "verify" / "prompt.md")
VERDICT_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["pass", "fail", "uncertain"]},
            "reason": {"type": "string"},
        },
        "required": ["verdict", "reason"],
    }
)
EVIDENCE_MAX = 8000
# #483 (paso 3 del encargo «verificador automático para todo»): el juez ABRE lo que se le nombra.
# Solo lectura: Write, Edit y Bash no existen en su sesión (medido el 16-oct con la CLI 2.1.296).
READ_TOOLS = "Read,Grep,Glob"


def _reglas_read(ficheros: list[str]) -> str:
    """#600: reglas de permiso para leer SOLO estos ficheros. En Windows la forma `Read(//C:/…)`
    no casa y niega todo; la que sirve es la POSIX `Read(//c/…)` (medido en primera mano el
    16-oct, CLI 2.1.296)."""
    reglas = []
    for f in ficheros:
        p = Path(f).resolve().as_posix()
        if len(p) > 1 and p[1] == ":":
            p = "//" + p[0].lower() + p[2:]
        reglas.append(f"Read({p})")
    return ",".join(reglas)


# #600 (decisión de Prima): «no he podido abrir» y «lo he leído y no me convence» son dos cosas
# distintas. La primera es un fallo del verificador, no del trabajo; si se confunden, el juez
# devuelve rojos falsos y la gente deja de creerse el verificador.
COMO_DECIRLO = (
    "\n\nSi NO puedes abrir lo que necesitas, dilo con estas palabras: «no he podido abrir"
    " X». Eso NO es un juicio sobre el trabajo — es un fallo del verificador, y el"
    " veredicto en ese caso es «uncertain» por falta de acceso. Otra cosa es «lo he leído"
    " y no me convence»: eso sí es un juicio, y va con su motivo."
)
LECTURA = (
    "\n\n## Puedes abrir ficheros (solo leer)\nTienes Read, Grep y Glob sobre: {dirs}. Abre lo que el"
    " criterio o la evidencia nombran y juzga por lo que LEES, no por cómo está contado. En el"
    " motivo di dónde lo viste (fichero y línea), también si das pass. No puedes escribir ni"
    " ejecutar nada." + COMO_DECIRLO
)
LECTURA_FICHEROS = (
    "\n\n## Puedes abrir ficheros (solo leer)\nSolo puedes abrir estos ficheros, y nada"
    " más: {dirs}. No tienes Grep ni Glob. Abre lo que el criterio nombra y juzga por"
    " lo que LEES, no por cómo está contado. En el motivo di dónde lo viste (fichero y"
    " línea), también si das pass. No puedes escribir ni ejecutar nada." + COMO_DECIRLO
)
# #465: el juez de otra familia. Un veredicto suelto de DeepSeek no es fiable (tanda 6 del
# #378: el mismo caso dio pasa y falla en corridas seguidas), así que decide la mayoría de 3.
EXTERNAL_HARNESS = "claude-cli-deepseek"
EXTERNAL_MODEL = "deepseek-flash"
EXTERNAL_RUNS = 3
EXTERNAL_PROVENANCE = f"external:{EXTERNAL_MODEL}"
REASON_PART = 200  # 4 opiniones (Haiku + 3) caben en los 1000 del historial


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
    if etype == "proof":
        try:
            return (json.loads(ev).get("texto") or "(la prueba no trae texto)")[:EVIDENCE_MAX]
        except (ValueError, AttributeError):
            return ev[:EVIDENCE_MAX]
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
        verify_criteria=t.get("verify_criteria")
        or "(el ticket no declara criterio; juzga si la evidencia demuestra que el trabajo del título se hizo de verdad)",
        evidence_type=t.get("evidence_type"),
        evidence=_evidence_text(conn, t),
    )


async def _comprobar_prueba(conn, t: dict) -> dict | None:
    """#594: las comprobaciones deterministas de una evidencia «proof» (en un hilo: corre git y la
    prueba en una copia). None si la evidencia no es una prueba."""
    if t.get("evidence_type") != "proof":
        return None
    from . import credentials, prueba

    p = prueba.leer(t["evidence"], service.proof_repos(conn))
    clave = credentials.valor(conn, "EcoDB")  # #600: sin clave, las memorias quedan sin decidir
    res = await asyncio.to_thread(prueba.comprobar, p, clave)
    res["prueba"] = p
    return res


def _tope_no_fusionado(comprobado: dict | None, verdict: dict) -> dict:
    """#693: un commit que solo está en una rama que no es la de integración no puede salir «pass»: baja a
    «uncertain» y llega a una persona (los casos del 16-oct: «verificados» y fuera de master)."""
    if comprobado and comprobado.get("no_fusionado") and verdict.get("verdict") == "pass":
        return {
            **verdict,
            "verdict": "uncertain",
            "reason": f"[código] commit no fusionado (solo en {comprobado['no_fusionado']}): no puede ser pass. "
            + (verdict.get("reason") or ""),
        }
    return verdict


def _cabecera_y_final(cabecera: str | None, salida: str, n: int) -> str:
    """#766: el final de la salida, pero sin perder la cabecera de git del principio."""
    if cabecera and salida.startswith(cabecera):
        cuerpo = salida[len(cabecera) :]
        return cabecera + ("\n…" if len(cuerpo) > n else "") + cuerpo[-n:]
    return salida[-n:]


def _nota_con_head(comprobado: dict | None, nota: str) -> str:
    """#766: la nota que queda en el historial empieza por el hash que dio git en la copia."""
    if comprobado and comprobado.get("head"):
        return f"[git rev-parse HEAD {comprobado['head']}] {nota}"
    return nota


def _seccion_prueba(res: dict) -> str:
    lineas = [f"- {'OK' if c['ok'] else 'FALLA'} · {c['nombre']}: {c['detalle']}" for c in res["checks"]]
    out = "\n\n## Comprobaciones del código (#594), hechas antes que tú\n" + "\n".join(lineas)
    if res.get("salida"):
        out += "\n\nSalida de la prueba en el commit (cabecera y final):\n" + _cabecera_y_final(
            res.get("cabecera"), res["salida"], 3000
        )
    if res.get("salida_padre"):
        out += (
            "\n\nSalida con las pruebas del commit sobre el código del padre (cabecera y final):\n"
            + _cabecera_y_final(res.get("cabecera_padre"), res["salida_padre"], 1500)
        )
    if res.get("sin_decidir"):
        out += "\n\nMemorias citadas que el código no comprueba: " + ", ".join(res["sin_decidir"])
    return out


def _ficheros_de_la_evidencia(t: dict) -> list[str]:
    """#600: los ficheros que nombra un `proof` y que existen de verdad. Es lo único que el juez
    podrá leer para ese ticket. Si el proof no nombra ficheros, devuelve [] y el juez cae al
    comportamiento de siempre — un ticket mal escrito no puede producir un rojo falso."""
    if t.get("evidence_type") != "proof":
        return []
    try:
        pr = json.loads(t.get("evidence") or "")
    except ValueError:
        return []
    if not isinstance(pr, dict):
        return []
    return [f for f in pr.get("ficheros", []) if Path(f).exists()]


def _dirs_de_la_evidencia(conn, t: dict) -> list[str]:
    """#483: las carpetas que el juez puede abrir para una tarea: la del fichero de la evidencia
    o la del output del run. Sin ninguna, juzga como antes, sin herramientas."""
    etype, ev = t.get("evidence_type"), t.get("evidence") or ""
    p = None
    if etype == "proof":
        # #594: el repo (en el commit, que ya está en su rama) y las carpetas de los ficheros nombrados
        try:
            pr = json.loads(ev)
        except ValueError:
            return []
        dirs = [pr["repo"]] if pr.get("repo") and Path(pr["repo"]).is_dir() else []
        for f in pr.get("ficheros", []):
            d = str(Path(f).parent)
            if Path(f).exists() and d not in dirs:
                dirs.append(d)
        return dirs
    if etype == "file_path":
        p = Path(ev)
    elif etype == "run_output":
        try:
            p = Path(service.get_run(conn, int(ev)).get("output_path") or "")
        except (service.TicketError, ValueError):
            p = None
    return [str(p.parent)] if p and str(p) not in ("", ".") and p.exists() else []


_RUTA = re.compile(r"[A-Za-z]:[/\\][^\s`\"'<>|*?()\[\]]+")
FICHEROS_RUN_MAX = 10


def _ficheros_del_run(conn, run: dict) -> list[str]:
    """#483 (22-oct, Prima con el run 51): los ficheros que el output de un run nombra por su ruta absoluta, para que
    el juez los lea del DISCO y no de lo que el job pegó (que puede llegar cortado). Las rutas las escribe el agente,
    así que se validan antes de abrir nada: tienen que EXISTIR como fichero y caer, ya resueltas (sin «..»), dentro
    de una de las raíces de Ajustes › verifier_read_roots. Sin raíces, nada."""
    from . import settings

    raices = [
        Path(r.strip()).resolve() for r in (settings.get(conn, "verifier_read_roots") or "").split(";") if r.strip()
    ]
    if not raices or not run.get("output_path") or not Path(run["output_path"]).exists():
        return []
    texto = Path(run["output_path"]).read_text(encoding="utf-8", errors="replace")
    vistos: list[str] = []
    for m in _RUTA.finditer(texto):
        p = Path(m.group(0).rstrip(".,;:")).resolve()
        if p.is_file() and any(p.is_relative_to(r) for r in raices) and str(p) not in vistos:
            vistos.append(str(p))
        if len(vistos) >= FICHEROS_RUN_MAX:
            break
    return vistos


def _dirs_del_run(run: dict, job: dict) -> list[str]:
    """#483: para un run, la carpeta de su output (la nota de la revista, por ejemplo) y el cwd del
    job, donde viven las notas anteriores con las que comparar."""
    dirs = []
    for d in (
        Path(run["output_path"]).parent if run.get("output_path") else None,
        Path(job["cwd"]) if job.get("cwd") else None,
    ):
        if d is not None and d.is_dir() and str(d) not in dirs:
            dirs.append(str(d))
    return dirs


async def _judge(
    prompt: str,
    model: str | None = None,
    env: dict | None = None,
    read_dirs: list[str] | None = None,
    read_files: list[str] | None = None,
) -> dict:
    adapter = runner.get_adapter("claude-cli")
    # #1203: --safe-mode apaga MCP, plugins, mods, hooks y CLAUDE.md del usuario. Sin él, el juez veía
    # todas las herramientas de la casa y un Haiku lanzó un agente mientras juzgaba.
    cmd = [
        adapter.exe,
        "-p",
        "--safe-mode",
        "--output-format",
        "json",
        "--no-session-persistence",
        "--model",
        model or VERIFIER_MODEL,
        "--json-schema",
        VERDICT_SCHEMA,
    ]
    cwd = str(runner.DATA_DIR)
    if read_dirs and read_files:
        # #600 (decisión de Prima): para un `proof` con ficheros nombrados, SOLO esos ficheros.
        # Grep y Glob salen de --tools: si se quedan sin regla de permiso se deniegan en silencio
        # y el juez contesta «incierto por no poder mirar» — la falsa alarma que se quería quitar.
        reglas = _reglas_read(read_files)
        cmd += ["--tools", "Read", "--allowedTools", reglas, "--permission-mode", "dontAsk"]
        for extra in read_dirs[1:]:
            cmd += ["--add-dir", extra]
        cwd = read_dirs[0]
        prompt += LECTURA_FICHEROS.format(dirs=", ".join(read_files))
    elif read_dirs:
        # Solo lectura y sin preguntar: lo que no está en la lista no existe o se niega.
        cmd += ["--tools", READ_TOOLS, "--allowedTools", READ_TOOLS, "--permission-mode", "dontAsk"]
        for extra in read_dirs[1:]:
            cmd += ["--add-dir", extra]
        cwd = read_dirs[0]
        prompt += LECTURA.format(dirs=", ".join(read_dirs))
    else:
        cmd += ["--tools", ""]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        **hidden_popen_kwargs(),
    )
    try:
        out_b, _err_b = await asyncio.wait_for(proc.communicate(prompt.encode("utf-8")), timeout=300)
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
    if verdict.get("verdict") not in {"pass", "fail", "uncertain"}:
        raise RuntimeError(f"verificador sin verdict válido: {out[:400]!r}")
    return verdict


def _hay_saldo(key: str) -> bool:
    """#465: «antes de lanzar, mirar /user/balance (is_available), no /models» (Prima, 7-oct): sin
    saldo, /models da 200 y cada llamada da 402. Si no se puede saber, se intenta igual."""
    from . import credentials

    return not credentials._sin_saldo_deepseek({"Authorization": f"Bearer {key}"})


def _external_key(conn) -> str:
    """La clave de DeepSeek si se puede usar: vacía si no hay clave o la cuenta no tiene saldo
    (se mira una vez por barrido: _saldo guarda la respuesta por clave)."""
    from . import settings

    key = settings.get(conn, "deepseek_api_key") or ""
    if key and key not in _saldo:
        _saldo[key] = _hay_saldo(key)
    return key if key and _saldo[key] else ""


_saldo: dict[str, bool] = {}


async def _judge_externo(
    conn, prompt: str, read_dirs: list[str] | None = None, read_files: list[str] | None = None
) -> dict:
    """El mismo juez, contra la API de DeepSeek con el harness del #463: entorno limpio del runner
    (lista blanca + la clave, que solo llega a este harness) y la clave nunca en el comando."""
    from . import deepseek_proxy

    token = deepseek_proxy.emitir("juez externo")  # #1109: el juez tampoco recibe la clave
    try:
        env = runner._env_for(conn, runner.get_adapter(EXTERNAL_HARNESS), token)
        return await _judge(prompt, model=EXTERNAL_MODEL, env=env, read_dirs=read_dirs, read_files=read_files)
    finally:
        deepseek_proxy.revocar(token)


def _corta(texto: str) -> str:
    texto = (texto or "").strip().replace("\n", " ")
    return texto if len(texto) <= REASON_PART else texto[: REASON_PART - 1] + "…"


async def _mayoria_externa(
    conn, prompt: str, read_dirs: list[str] | None = None, read_files: list[str] | None = None
) -> dict:
    """DeepSeek ×3. Devuelve {"verdict", "reason"}: la mayoría (2 de 3) o «uncertain» si no la hay.
    El motivo lleva las tres corridas, que quedan en el historial."""
    votos = [await _judge_externo(conn, prompt, read_dirs, read_files) for _ in range(EXTERNAL_RUNS)]
    seq = [v["verdict"] for v in votos]
    ganador = next((x for x in ("pass", "fail", "uncertain") if seq.count(x) * 2 > len(seq)), None)
    detalle = " ".join(f"{i}) {_corta(v.get('reason'))}" for i, v in enumerate(votos, 1))
    if ganador in (None, "uncertain"):
        fin = "incierto" if ganador else "sin mayoría"
        return {
            "verdict": "uncertain",
            "majority": False,
            "reason": f"DeepSeek ×{len(seq)} ({EXTERNAL_MODEL}): {', '.join(seq)} → {fin}. {detalle}",
        }
    return {
        "verdict": ganador,
        "majority": True,
        "reason": f"DeepSeek ×{len(seq)} ({EXTERNAL_MODEL}): {', '.join(seq)} → {ganador}. {detalle}",
    }


def build_run_prompt(conn, run: dict, job: dict) -> str:
    """Mismo juez, evidencia = output del run. El 'ticket' es el job (T1.10)."""
    output = "(run sin output)"
    if run.get("output_path") and Path(run["output_path"]).exists():
        entero = Path(run["output_path"]).read_text(encoding="utf-8", errors="replace")
        output = entero[:EVIDENCE_MAX]
        if len(entero) > EVIDENCE_MAX:
            # #922: si no se le dice, el juez da por ausente lo que solo quedó fuera del corte (run 67).
            # La carpeta del output es legible para el juez (_dirs_del_run).
            output += (
                f"\n\n[EVIDENCIA CORTADA: arriba van los primeros {EVIDENCE_MAX} de {len(entero)} caracteres. "
                f"El fichero completo es {run['output_path']}: léelo con Read antes de dar por ausente "
                f"algo que no veas aquí.]"
            )
    elif run.get("result_summary"):
        output = run["result_summary"][:EVIDENCE_MAX]
    return _load_template().format(
        title=f"{job['name']} (job #{job['id']}, run #{run['id']})",
        description=job.get("prompt") or "(el job no trae prompt)",
        verify_criteria=job.get("verify_criteria")
        or "(el job no declara criterio; juzga si el output es sustantivo, coherente con el encargo y sin afirmaciones no demostradas)",
        evidence_type="run_output",
        evidence=output,
    )


async def sweep_runs(conn, emit=None) -> dict:
    """Veredicto POR RUN (T1.10): runs ok sin veredicto de jobs con verify_level=auto.
    pass + ticket vinculado → jobs.set_verdict completa el ticket (brazo automático).
    uncertain (#451) no completa nada y queda para una persona."""
    from . import jobs, settings

    if not settings.get(conn, "verifier_enabled"):
        return {"swept": 0, "passed": [], "failed": [], "uncertain": [], "errors": [], "disabled": True}
    if settings.get(conn, "maquina_quieta"):
        # #600: misma razón que en sweep() — los runs tampoco se juzgan con la máquina quieta
        return {"swept": 0, "passed": [], "failed": [], "uncertain": [], "errors": [], "quiet": True}
    model = settings.get(conn, "verifier_model")
    _saldo.clear()
    queue = jobs.runs_pending_verdict(conn)
    passed, failed, uncertain, errors = [], [], [], []
    by_verdict = {"pass": passed, "fail": failed, "uncertain": uncertain}
    for run in queue:
        provenance = None
        try:
            job = jobs.get_job(conn, run["job_id"])
            prompt = build_run_prompt(conn, run, job)
            dirs = _dirs_del_run(run, job)
            ficheros = _ficheros_del_run(conn, run)
            if ficheros:
                # #483: con ficheros nombrados, el juez lee SOLO esos y el output del run (regla de #600)
                ficheros = [run["output_path"], *ficheros]
                dirs = dirs or [str(Path(run["output_path"]).parent)]
            verdict = await _judge(prompt, model=model, read_dirs=dirs, read_files=ficheros or None)
            if verdict["verdict"] == "uncertain" and _external_key(conn):
                # #465: el incierto de Haiku sube a DeepSeek ×3; sin mayoría, sigue incierto.
                ext = await _mayoria_externa(conn, prompt, dirs, ficheros or None)
                verdict = {
                    "verdict": ext["verdict"],
                    "reason": f"Haiku incierto: {_corta(verdict.get('reason'))} | {ext['reason']}",
                }
                if ext["majority"]:
                    provenance = EXTERNAL_PROVENANCE
        except Exception as exc:
            log.exception("verificador falló en run #%s", run["id"])
            errors.append({"run_id": run["id"], "error": str(exc)})
            continue
        reason = (verdict.get("reason") or "").strip()[:1000] or "(sin motivo)"
        try:
            res = jobs.set_verdict(conn, run["id"], verdict["verdict"], reason, provenance=provenance)
        except service.TicketError as exc:  # p.ej. alguien lo juzgó a mano entre medias
            errors.append({"run_id": run["id"], "error": str(exc)})
            continue
        by_verdict[res["verdict"]].append({"run_id": run["id"], "job_id": run["job_id"], "reason": reason})
        if emit:
            emit(
                "verify.done",
                {
                    "run_id": run["id"],
                    "job_id": run["job_id"],
                    "ticket_id": run.get("ticket_id"),
                    "verdict": res["verdict"],
                },
            )
    return {"swept": len(queue), "passed": passed, "failed": failed, "uncertain": uncertain, "errors": errors}


async def sweep(conn, emit=None) -> dict:
    """Verifica la cola done de nivel auto. Los peer se quedan para humanos, y un
    incierto (#451) pasa a peer: sigue done y lo sella una persona."""
    from . import settings

    if settings.get(conn, "maquina_quieta"):
        # #600: con la máquina quieta (las medidas de Prima) la cola no se toca — ni juicio ni
        # veredicto. No es un fallo: se reanuda sola al apagarlo.
        return {"swept": 0, "verified": [], "bounced": [], "uncertain": [], "errors": [], "quiet": True}
    model = settings.get(conn, "verifier_model")  # T0.4: ajuste en DB, env como fallback
    _saldo.clear()
    queue = [
        t
        for t in service.list_tickets(conn, status="done")
        if t["verification_level"] == "auto"
        or (t["verification_level"] == "external" and not service.external_deferred(conn, t["id"]))
    ]
    verified, bounced, uncertain, errors = [], [], [], []
    for t in queue:
        provenance = None
        external = t["verification_level"] == "external"
        try:
            prompt = build_prompt(conn, t)
            dirs = _dirs_de_la_evidencia(conn, t)
            ficheros = _ficheros_de_la_evidencia(t)
            comprobado = await _comprobar_prueba(conn, t)
            if comprobado is not None and not comprobado["ok"]:
                # #594: lo que el código ya ha visto en rojo no se le pregunta a ningún juez
                malas = "; ".join(f"{c['nombre']}: {c['detalle']}" for c in comprobado["checks"] if not c["ok"])
                res = service.verify_ticket(
                    conn,
                    "verifier-bot",
                    t["id"],
                    "fail",
                    note=_nota_con_head(comprobado, f"[comprobación del código] {malas}")[:1000],
                    provenance="deterministic:faros.prueba",
                )
                bounced.append({"ticket_id": t["id"], "reason": malas})
                if emit:
                    emit("verify.done", {"ticket_id": t["id"], "verdict": "fail"})
                continue
            if comprobado is not None:
                prompt += _seccion_prueba(comprobado)
            if external:
                if not _external_key(conn):
                    raise RuntimeError(
                        "verificación externa sin clave de DeepSeek o sin saldo"
                        " (Ajustes › Claves): la tarea sigue hecha sin juzgar"
                    )
                verdict = await _mayoria_externa(conn, prompt, dirs, ficheros)
                provenance = EXTERNAL_PROVENANCE if verdict["majority"] else None
            else:
                verdict = await _judge(prompt, model=model, read_dirs=dirs, read_files=ficheros)
                if verdict["verdict"] == "uncertain" and _external_key(conn):
                    # #465: el incierto de Haiku sube a DeepSeek ×3 antes de pararse en una persona.
                    ext = await _mayoria_externa(conn, prompt, dirs, ficheros)
                    verdict = {
                        "verdict": ext["verdict"],
                        "majority": ext["majority"],
                        "reason": f"Haiku incierto: {_corta(verdict.get('reason'))} | {ext['reason']}",
                    }
                    provenance = EXTERNAL_PROVENANCE if ext["majority"] else None
            verdict = _tope_no_fusionado(comprobado, verdict)
        except Exception as exc:
            log.exception("verificador falló en ticket #%s", t["id"])
            errors.append({"ticket_id": t["id"], "error": str(exc)})
            continue
        reason = _nota_con_head(comprobado, (verdict.get("reason") or "").strip() or "(sin motivo)")[:1000]
        if verdict["verdict"] == "uncertain":
            if external:
                service.defer_external(conn, "verifier-bot", t["id"], reason)
            else:
                service.defer_to_peer(conn, "verifier-bot", t["id"], reason)
            uncertain.append({"ticket_id": t["id"], "reason": reason})
        else:
            res = service.verify_ticket(
                conn, "verifier-bot", t["id"], verdict["verdict"], note=reason, provenance=provenance
            )
            (verified if res["status"] == "verified" else bounced).append({"ticket_id": t["id"], "reason": reason})
        if emit:
            emit("verify.done", {"ticket_id": t["id"], "verdict": verdict["verdict"]})
    return {"swept": len(queue), "verified": verified, "bounced": bounced, "uncertain": uncertain, "errors": errors}
