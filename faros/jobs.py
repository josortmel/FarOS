"""Jobs agénticos como entidad propia (T1.1, decisión 3 del dueño).

Un job tiene su ciclo (active | paused | archived), sus runs y un veredicto
POR RUN. El vínculo a un ticket manual del Tablero es OPCIONAL: es "el brazo
automático de esa tarea" — run ok + veredicto pass → completa el ticket con
evidencia run_output. Un job sin ticket no toca tickets.

Tablero = agentes con nombre. Agentes = jobs. Aquí vive lo segundo.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from . import harness, service
from .service import (
    CLOSED_STATUSES,
    TicketError,
    _append_history,
    _get,
    _now,
    _validate_agent,
    _validate_project,
)

JOB_STATUSES = {"active", "paused", "archived"}
VERIFY_LEVELS = {"auto", "none"}
EDITABLE = {
    "name",
    "harness",
    "model",
    "prompt",
    "cwd",
    "permission_mode",
    "allowed_tools",
    "mcp_config",
    "inherit_mcp",
    "mcp_servers",
    "strict_mcp",
    "add_dirs",
    "effort",
    "fallback_model",
    "system_prompt",
    "json_schema",
    "max_budget_usd",
    "timeout_s",
    "schedule",
    "verify_level",
    "verify_criteria",
    "ticket_id",
    "project_id",
    "enabled",
}
_JSON_LISTS = ("inherit_mcp", "add_dirs", "mcp_servers")


# ------------------------------------------------------------------ helpers


def _billing_for(harness_name: str) -> str | None:
    try:
        return harness.get(harness_name).billing
    except Exception:
        return None


def _tope_no_aplicable(harness_name: str, billing: str | None) -> str | None:
    """Por qué un max_budget_usd no se puede aplicar en este harness, o None si sí. #580 (Lienzo, 16-oct):
    además de billing=api hace falta que el harness dé un coste fiable (capabilities.budget). En
    claude-cli-deepseek el coste se anula (el de Claude Code es falso con DeepSeek), así que el tope nunca
    saltaría: una promesa falsa."""
    if billing != "api":
        return (
            f"el harness {harness_name!r} factura por {billing or 'modo desconocido'}, "
            "el tope en USD solo aplica a billing=api"
        )
    try:
        puede = getattr(harness.get(harness_name).capabilities, "budget", True)
    except Exception:
        puede = True
    if not puede:
        return f"el harness {harness_name!r} no puede aplicar un tope en USD (no da un coste fiable)"
    return None


def _normalize(fields: dict, conn=None, harness_current=None) -> dict:
    """Serializa listas y valida tipos simples. Devuelve copia lista para SQL."""
    out = dict(fields)
    for k in _JSON_LISTS:
        if k in out and out[k] is not None:
            v = out[k]
            if isinstance(v, str):
                try:
                    v = json.loads(v) if v.strip().startswith("[") else [s.strip() for s in v.split(",") if s.strip()]
                except json.JSONDecodeError:
                    raise TicketError(f"{k}: lista JSON o valores separados por comas")
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise TicketError(f"{k}: lista de strings")
            out[k] = json.dumps(v)
    if "json_schema" in out and out["json_schema"] not in (None, ""):
        v = out["json_schema"]
        if not isinstance(v, str):
            v = json.dumps(v)
        try:
            json.loads(v)
        except json.JSONDecodeError:
            raise TicketError("json_schema: JSON inválido")
        out["json_schema"] = v
    if "schedule" in out and out["schedule"] is not None:
        from . import schedule as _schedule

        raw = out["schedule"]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raise TicketError("schedule: JSON inválido")
        out["schedule"] = json.dumps(_schedule.normalize(raw))  # v3: recurring multi-hora, weekly
    if "strict_mcp" in out:
        out["strict_mcp"] = 1 if out["strict_mcp"] in (1, True, "1", "true") else 0
    if "model" in out and out.get("model") and conn is not None:
        # v3.1 (T5.4): ID concreto o alias del catálogo del harness; typo → error con la lista
        hname = out.get("harness") or harness_current or "claude-cli"
        try:
            adapter = harness.get(hname)
        except (KeyError, RuntimeError):
            adapter = None
        if adapter is not None and hname == "claude-cli":
            from . import settings as _settings
            from .harness import model_catalog as _mc

            entries = _mc.merge_cache(_mc.seed_entries(list(_mc.ALIASES)), _settings.get(conn, "model_catalog"))
            ok, why = _mc.is_acceptable(out["model"], entries)
            if not ok:
                raise TicketError(f"model: {why}")
    if conn is not None and out.get("mcp_servers"):
        # v3.1 (T5.6): cada nombre debe existir en el registro
        from . import mcp_registry as _reg

        for n in json.loads(out["mcp_servers"]):
            if _reg.get_by_name(conn, n) is None:
                raise TicketError(f"el servidor MCP {n!r} no está en el registro (Ajustes › MCP)")
    if "enabled" in out:
        out["enabled"] = 1 if out["enabled"] in (1, True, "1", "true") else 0
    if "verify_level" in out and out["verify_level"] not in VERIFY_LEVELS:
        raise TicketError(f"verify_level inválido: {out['verify_level']!r} (auto|none)")
    if "name" in out and not (out["name"] or "").strip():
        raise TicketError("name vacío")
    return out


def _validate_link(conn, ticket_id, job_id=None, legacy=False):
    if ticket_id is None:
        return
    t = _get(conn, ticket_id)
    if t["kind"] != "manual" and not legacy:
        raise TicketError(f"ticket #{ticket_id} no es manual — solo se vincula a tareas del Tablero")
    if t["closed_at"] is not None:
        raise TicketError(f"ticket #{ticket_id} está cerrado ({t['status']}) — no se vincula")
    other = conn.execute(
        "SELECT id FROM agent_jobs WHERE ticket_id=? AND id != COALESCE(?, -1)", (ticket_id, job_id)
    ).fetchone()
    if other:
        raise TicketError(f"ticket #{ticket_id} ya tiene brazo automático: job #{other['id']}")


def _row(conn, job_id):
    row = conn.execute("SELECT * FROM agent_jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise TicketError(f"job #{job_id} no existe")
    return row


def _hydrate(conn, row) -> dict:
    d = dict(row)
    d["schedule"] = json.loads(d["schedule"]) if d.get("schedule") else {"type": "manual"}
    for k in _JSON_LISTS:
        d[k] = json.loads(d[k]) if d.get(k) else []
    last = conn.execute(
        "SELECT id, status, started_at, finished_at, cost_usd, cost_kind, verdict,"
        " verdict_reason, verified_at, result_summary FROM runs WHERE job_id=?"
        " ORDER BY id DESC LIMIT 1",
        (d["id"],),
    ).fetchone()
    d["last_run"] = dict(last) if last else None
    tot = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN cost_kind='billed' THEN cost_usd END),0) AS billed,"
        " COALESCE(SUM(CASE WHEN cost_kind='equivalent' THEN cost_usd END),0) AS equiv,"
        " COUNT(*) AS n FROM runs WHERE job_id=?",
        (d["id"],),
    ).fetchone()
    d["total_cost_usd"] = round(tot["billed"], 4)
    d["total_equivalent_usd"] = round(tot["equiv"], 4)
    d["runs_count"] = tot["n"]
    d["running"] = bool(conn.execute("SELECT 1 FROM runs WHERE job_id=? AND status='running'", (d["id"],)).fetchone())
    try:
        from . import scheduler

        d["next_fire"] = (
            scheduler.next_fire(d["schedule"], d["created_at"], datetime.now())
            if d["status"] == "active" and d["enabled"]
            else None
        )
        if isinstance(d["next_fire"], datetime):
            d["next_fire"] = d["next_fire"].isoformat(timespec="minutes")
    except Exception:
        d["next_fire"] = None
    if d.get("ticket_id"):
        t = conn.execute("SELECT id, title, status, closed_at FROM tickets WHERE id=?", (d["ticket_id"],)).fetchone()
        d["ticket"] = dict(t) if t else None
    else:
        d["ticket"] = None
    if d["ticket"] and d["ticket"]["closed_at"] and d["status"] == "active" and d["enabled"]:
        # #518: el scheduler no lanza un job colgado de una tarea cerrada; que tampoco lo anuncie
        d["next_fire"] = None
        d["not_firing"] = (
            f"no se dispara: la tarea #{d['ticket_id']} está cerrada ({d['ticket']['status']});"
            " desenlázalo (ticket_id a null) o cuélgalo de una tarea abierta"
        )
    return d


# ------------------------------------------------------------------ CRUD


def create_job(
    conn,
    agent,
    name,
    prompt,
    harness_name="claude-cli",
    model="haiku",
    schedule=None,
    ticket_id=None,
    project_id=None,
    **opts,
) -> dict:
    _validate_agent(agent)
    legacy = bool(opts.pop("_legacy", False))
    if not (prompt or "").strip():
        raise TicketError("prompt vacío")
    fields = {
        "name": name,
        "prompt": prompt,
        "harness": harness_name,
        "model": model,
        "schedule": schedule or {"type": "manual"},
        "ticket_id": ticket_id,
        "project_id": project_id,
        **opts,
    }
    unknown = set(fields) - EDITABLE
    if unknown:
        raise TicketError(f"campos desconocidos: {sorted(unknown)}")
    fields = _normalize(fields, conn)
    # El schedule ya está validado por schedule.normalize (v3, incluye weekly);
    # a service._validate_job_fields solo le pedimos harness/permisos/timeout.
    service._validate_job_fields({**fields, "schedule": {"type": "manual"}})
    _validate_project(conn, project_id)
    _validate_link(conn, ticket_id, legacy=legacy)
    billing = _billing_for(fields["harness"])
    warning = None
    motivo = _tope_no_aplicable(fields["harness"], billing)
    if fields.get("max_budget_usd") not in (None, "", 0) and motivo:
        warning = f"max_budget_usd ignorado: {motivo}"
        fields["max_budget_usd"] = None
    fields["billing_mode"] = billing
    fields.setdefault("timeout_s", 1800)
    fields.setdefault("verify_level", "auto")
    fields.setdefault("permission_mode", "dontAsk")
    fields.setdefault("strict_mcp", 0)
    fields.setdefault("enabled", 1)
    fields["status"] = "active"
    fields["created_at"] = _now()
    fields["updated_at"] = fields["created_at"]
    cols = list(fields)
    cur = conn.execute(
        f"INSERT INTO agent_jobs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [fields[c] for c in cols]
    )
    job_id = cur.lastrowid
    if ticket_id and not legacy:
        st = _get(conn, ticket_id)["status"]
        _append_history(conn, ticket_id, st, st, agent, f"brazo automático: job #{job_id} '{name}' vinculado")
    conn.commit()
    out = get_job(conn, job_id)
    if warning:
        out["warning"] = warning
    return out


def get_job(conn, job_id) -> dict:
    return _hydrate(conn, _row(conn, job_id))


def list_jobs(conn, status=None, project_id=None, include_archived=False) -> list[dict]:
    where, params = [], []
    if status:
        if status not in JOB_STATUSES:
            raise TicketError(f"status inválido: {status!r}")
        where.append("status=?")
        params.append(status)
    elif not include_archived:
        where.append("status != 'archived'")
    if project_id:
        where.append("project_id=?")
        params.append(project_id)
    sql = "SELECT * FROM agent_jobs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY status = 'active' DESC, id"
    return [_hydrate(conn, r) for r in conn.execute(sql, params)]


def update_job(conn, agent, job_id, **fields) -> dict:
    _validate_agent(agent)
    row = _row(conn, job_id)
    unknown = set(fields) - EDITABLE
    if unknown:
        raise TicketError(f"campos no editables: {sorted(unknown)}")
    if not fields:
        return _hydrate(conn, row)
    fields = _normalize(fields, conn, harness_current=row["harness"])
    merged = {**dict(row), **fields}
    service._validate_job_fields(
        {**merged, "schedule": {"type": "manual"}, "prompt": merged.get("prompt") or row["prompt"]}
    )
    if "project_id" in fields:
        _validate_project(conn, fields["project_id"])
    if "ticket_id" in fields:
        _validate_link(conn, fields["ticket_id"], job_id)
    if "harness" in fields:
        fields["billing_mode"] = _billing_for(fields["harness"])
    billing = fields.get("billing_mode", row["billing_mode"])
    warning = None
    motivo = _tope_no_aplicable(fields.get("harness", row["harness"]), billing)
    if fields.get("max_budget_usd") not in (None, "", 0) and motivo:
        warning = f"max_budget_usd ignorado: {motivo}"
        fields["max_budget_usd"] = None
    fields["updated_at"] = _now()
    if "schedule" in fields and json.loads(fields["schedule"]) != json.loads(row["schedule"] or "{}"):
        # #431: solo el cambio de calendario (no cualquier edición, que anularía una recuperación L47)
        fields["schedule_changed_at"] = fields["updated_at"]
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE agent_jobs SET {sets} WHERE id=?", [*fields.values(), job_id])
    conn.commit()
    out = get_job(conn, job_id)
    if warning:
        out["warning"] = warning
    return out


def set_status(conn, agent, job_id, status) -> dict:
    """pause | resume (active) | archive. Archivar exige que no haya run en curso."""
    _validate_agent(agent)
    if status not in JOB_STATUSES:
        raise TicketError(f"status inválido: {status!r} ({'|'.join(sorted(JOB_STATUSES))})")
    row = _row(conn, job_id)
    if row["status"] == "archived" and status != "archived":
        raise TicketError(f"job #{job_id} está archivado — duplícalo para volver a usarlo")
    if (
        status == "archived"
        and conn.execute("SELECT 1 FROM runs WHERE job_id=? AND status='running'", (job_id,)).fetchone()
    ):
        raise TicketError(f"job #{job_id} tiene un run en curso — cancélalo antes de archivar")
    conn.execute("UPDATE agent_jobs SET status=?, updated_at=? WHERE id=?", (status, _now(), job_id))
    conn.commit()
    return get_job(conn, job_id)


def duplicate_job(conn, agent, job_id, name=None) -> dict:
    """Copia todo salvo runs, vínculo a ticket (uno por ticket) y estado (nace activo)."""
    _validate_agent(agent)
    row = dict(_row(conn, job_id))
    skip = {"id", "created_at", "updated_at", "status", "ticket_id"}
    fields = {k: v for k, v in row.items() if k not in skip}
    fields["name"] = name or f"{row['name']} (copia)"
    fields["status"] = "active"
    fields["created_at"] = fields["updated_at"] = _now()
    cols = list(fields)
    cur = conn.execute(
        f"INSERT INTO agent_jobs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [fields[c] for c in cols]
    )
    conn.commit()
    return get_job(conn, cur.lastrowid)


# ------------------------------------------------------------------ runs


def create_run(conn, job_id) -> dict:
    """Registra el arranque. Anti-solape: un running por job (índice único)."""
    job = get_job(conn, job_id)
    if job["status"] != "active" or not job["enabled"]:
        raise TicketError(
            f"job #{job_id} está {job['status']}{'' if job['enabled'] else ' (deshabilitado)'} — no se lanza"
        )
    if job["running"]:
        raise TicketError(f"job #{job_id} ya tiene un run en curso")
    # T1.7: el tope en USD solo existe donde se factura por uso (billing=api).
    if job["billing_mode"] == "api" and job.get("max_budget_usd"):
        if job["total_cost_usd"] >= float(job["max_budget_usd"]):
            raise TicketError(
                f"job #{job_id} agotó su presupuesto: {job['total_cost_usd']:.4f} USD gastados"
                f" de {float(job['max_budget_usd']):.2f} — sube el tope o archívalo"
            )
    ticket_id = job["ticket_id"]
    if ticket_id:
        t = _get(conn, ticket_id)
        if t["status"] in CLOSED_STATUSES:
            raise TicketError(f"ticket #{ticket_id} vinculado está cerrado ({t['status']}) — pausa o desvincula el job")
    cur = conn.execute(
        "INSERT INTO runs (job_id, ticket_id, started_at, status) VALUES (?, ?, ?, 'running')",
        (job_id, ticket_id, _now()),
    )
    run_id = cur.lastrowid
    if ticket_id:
        # El tablero refleja que el brazo automático está trabajando (semántica v1).
        t = _get(conn, ticket_id)
        if t["status"] in {"proposed", "accepted", "blocked"}:
            if t["status"] == "proposed":
                _append_history(conn, ticket_id, "proposed", "accepted", "runner-bot", "auto-accept al lanzar")
                conn.execute("UPDATE tickets SET status='accepted' WHERE id=?", (ticket_id,))
            conn.execute(
                "UPDATE tickets SET status='in_progress', blocked_reason=NULL,"
                " owner=COALESCE(owner, 'runner-bot') WHERE id=?",
                (ticket_id,),
            )
            _append_history(
                conn,
                ticket_id,
                t["status"] if t["status"] != "proposed" else "accepted",
                "in_progress",
                "runner-bot",
                f"run #{run_id} lanzado",
            )
        else:
            _append_history(conn, ticket_id, t["status"], t["status"], "runner-bot", f"run #{run_id} lanzado")
    conn.commit()
    return dict(conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())


def _streak_already_open(conn, job_id, run_id) -> bool:
    """#662, decisión 74 de Pepe: «uno por racha». True si el run ANTERIOR del mismo job ya fue un
    fallo (terminó error/timeout/cancelled, o terminó ok y el verificador dijo fail): entonces ya
    se avisó al empezar la racha y este fallo no avisa. Un run bueno la cierra."""
    prev = conn.execute(
        "SELECT status, verdict FROM runs WHERE job_id=? AND id<? AND status!='running' ORDER BY id DESC LIMIT 1",
        (job_id, run_id),
    ).fetchone()
    return prev is not None and (prev["status"] in ("error", "timeout", "cancelled") or prev["verdict"] == "fail")


def finish_run(
    conn, run_id, status, exit_code=None, cost_usd=None, session_id=None, output_path=None, result_summary=None
) -> dict:
    if status not in {"ok", "error", "timeout", "cancelled"}:
        raise TicketError(f"status de run inválido: {status!r}")
    run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if run is None:
        raise TicketError(f"run #{run_id} no existe")
    if run["status"] != "running":
        raise TicketError(f"run #{run_id} ya está cerrado ({run['status']})")
    job = get_job(conn, run["job_id"])
    cost_kind = "none"
    if cost_usd is not None:
        cost_kind = {"subscription": "equivalent", "api": "billed"}.get(job["billing_mode"], "none")
    conn.execute(
        "UPDATE runs SET finished_at=?, status=?, exit_code=?, cost_usd=?, cost_kind=?,"
        " session_id=?, output_path=?, result_summary=? WHERE id=? AND status='running'",
        (_now(), status, exit_code, cost_usd, cost_kind, session_id, output_path, result_summary, run_id),
    )
    try:  # Biblioteca (#850): artifacts/run_<id> con su job y su ticket; antes del cierre del ticket
        from . import library

        library.al_terminar_run(conn, run_id, run["job_id"], run["ticket_id"])
    except Exception:
        logging.getLogger(__name__).exception("biblioteca: el gancho de run falló en #%s", run_id)
    if status in ("error", "timeout", "cancelled"):
        try:  # T4.4 (Eco): aviso al dueño; sin token es no-op. #662: uno por racha
            from . import notify

            if _streak_already_open(conn, run["job_id"], run_id):
                notify.log.info("aviso omitido: run #%s de «%s» sigue una racha de fallos", run_id, job["name"])
            else:
                notify.on_run_failed(conn, job["name"], run_id, status, result_summary or "")
        except Exception:
            pass
    ticket_id = run["ticket_id"]
    if ticket_id:
        t = _get(conn, ticket_id)
        if status == "ok":
            if job["verify_level"] == "none" and t["status"] in {"in_progress", "accepted"}:
                service.complete_ticket(conn, "runner-bot", ticket_id, "run_output", str(run_id))
            else:
                _append_history(
                    conn,
                    ticket_id,
                    t["status"],
                    t["status"],
                    "runner-bot",
                    f"run #{run_id} terminó ok — esperando veredicto",
                )
        elif t["status"] in {"accepted", "in_progress"}:
            service.block_ticket(
                conn,
                "runner-bot",
                ticket_id,
                f"run #{run_id} terminó {status}" + (f" (exit {exit_code})" if exit_code is not None else ""),
            )
    conn.commit()
    return dict(conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())


def set_verdict(conn, run_id, verdict, reason, by="verifier-bot", provenance=None) -> dict:
    """Veredicto POR RUN (deuda v1.1 cerrada). pass + ticket vinculado abierto →
    completa el ticket con evidencia run_output (y lo verifica si su nivel es auto,
    para no juzgar dos veces). fail → nota en el ticket, estado intacto.
    uncertain (#451) → como fail pero sin aviso: no completa nada, no se vuelve a juzgar
    solo, y una persona lo resuelve después con pass o fail."""
    if verdict not in {"pass", "fail", "uncertain"}:
        raise TicketError(f"verdict inválido: {verdict!r} (pass|fail|uncertain)")
    run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if run is None:
        raise TicketError(f"run #{run_id} no existe")
    if run["status"] != "ok":
        raise TicketError(f"run #{run_id} no terminó ok ({run['status']}) — no se juzga")
    if run["verdict"] and not (run["verdict"] == "uncertain" and verdict != "uncertain"):
        raise TicketError(f"run #{run_id} ya tiene veredicto ({run['verdict']})")
    reason = (reason or "").strip()[:1000] or "(sin motivo)"
    conn.execute(
        "UPDATE runs SET verdict=?, verdict_reason=?, verified_at=? WHERE id=?", (verdict, reason, _now(), run_id)
    )
    if verdict == "fail":
        try:  # T4.4 (Eco). #662: uno por racha
            from . import notify

            name = get_job(conn, run["job_id"])["name"]
            if _streak_already_open(conn, run["job_id"], run_id):
                notify.log.info("aviso omitido: run #%s de «%s» sigue una racha de fallos", run_id, name)
            else:
                notify.on_verdict_fail(conn, name, run_id, reason)
        except Exception:
            pass
    ticket_id = run["ticket_id"]
    if ticket_id:
        t = _get(conn, ticket_id)
        if verdict == "pass" and t["status"] in {"in_progress", "accepted"}:
            done = service.complete_ticket(conn, "runner-bot", ticket_id, "run_output", str(run_id))
            if done["status"] == "done" and t["verification_level"] == "auto":
                service.verify_ticket(
                    conn,
                    "verifier-bot",
                    ticket_id,
                    "pass",
                    note=f"veredicto pass del run #{run_id}: {reason}",
                    provenance=provenance,
                )
        elif t["closed_at"] is None:
            etiqueta = "incierto (lo resuelve una persona)" if verdict == "uncertain" else verdict
            _append_history(
                conn, ticket_id, t["status"], t["status"], by, f"run #{run_id} veredicto {etiqueta}: {reason}"
            )
    conn.commit()
    return dict(conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())


def list_runs(conn, job_id=None, ticket_id=None, status=None, limit=50) -> list[dict]:
    where, params = [], []
    if job_id:
        where.append("job_id=?")
        params.append(job_id)
    if ticket_id:
        where.append("ticket_id=?")
        params.append(ticket_id)
    if status:
        where.append("status=?")
        params.append(status)
    sql = "SELECT * FROM runs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params)]


def runs_pending_verdict(conn) -> list[dict]:
    """Runs ok sin veredicto cuyo job pide verificación automática."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT r.* FROM runs r JOIN agent_jobs j ON j.id = r.job_id"
            " WHERE r.status='ok' AND r.verdict IS NULL AND j.verify_level='auto' ORDER BY r.id"
        )
    ]


def results(conn) -> list[dict]:
    """La lectura de la mañana: cada job (no archivado) con su último run y veredicto."""
    return [{"job": j, "last_run": j["last_run"]} for j in list_jobs(conn)]
