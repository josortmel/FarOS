"""Lógica de AgenticOS: máquina de estados auditada + proyectos + jobs + runs.

Port del service de casa-tickets v0 (revisado por Prima, día 182) extendido
según SPEC §3-4. Funciones puras sobre una conexión sqlite3; cada operación es
una transacción. ticket_history es append-only: aquí no existe UPDATE ni
DELETE sobre esa tabla.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import unicodedata
from datetime import date, datetime, timedelta

from . import harness as _harness  # registro de adapters; harness solo depende de procutil (sin ciclo)
from . import piezas as _piezas  # #1346: la regla de las piezas (puro, sin base)
from . import roster  # #1186: roster.WORKFLOW_EXTRA se lee en caliente

# Roster de la casa: configuración, no código (roster.py). El público no trae
# los nombres de nadie dentro del .py.
from .roster import AGENTS, HOUSE_OWNER  # noqa: F401  (re-export histórico)

log = logging.getLogger(__name__)

# Roles de workflow (SPEC v2 §1): peers de relay que operan tareas y findings,
# pero NO crean proyectos/workflows ni despachan (eso es del coordinador/familia).
WORKFLOW_AGENTS = {"code", "verificador", "adv-code", "adv-seg"}
# Actores de sistema: escriben history pero no son miembros de la casa.
SYSTEM_AGENTS = {"system", "runner-bot", "verifier-bot"}
# v3.1 (betatesting §1): el dueño de la Casa mueve cualquier ticket desde la app.
# HOUSE_OWNER llega de roster.py (ver import arriba).
REWORK_HOOKS = []  # (conn, ticket_id, prev_status) tras devolver una done/verified
REWORK_MIN_REASON = 10

# Hooks invocados cuando un ticket llega a verified (v2: findings fixed + hitos).
VERIFIED_HOOKS = []
# Hooks invocados cuando un ticket se cierra sin verificar: rejected|expired
# (v2, review Prima F1: el finding cuya corrección muere vuelve a open).
CLOSED_HOOKS = []
SOURCES = {"encargo", "recurrente", "cola_autonoma", "proposed"}
# 16-sep: el valor viejo llevaba el nombre del duenyo. Era el ultimo sitio del
# source donde quedaba, y sobrevivio al barrido de la maniana por ser un VALOR y
# no prosa. Se puede renombrar sin migrar nada: CERO filas de la base real lo
# usan (318 tickets: 278 proposed, 23 recurrente, 17 cola_autonoma, 0 este).
# Comprobado sobre una COPIA de la base, no sobre la viva.
PRIORITIES = {"alta", "media", "baja"}
VERIFICATION_LEVELS = {"self", "peer", "auto", "external"}  # external: #465, decisión 22 (DeepSeek ×3)
# #414 (decisión 48): el criterio pide que verifique otro, pero el nivel es self (pasó con #285 y #367)
_PIDE_OTRO = re.compile(
    r"otra persona|otro miembro|\bpeer\b|no lo verifica quien|"
    r"verific\w*\s+(?:de|por)\s+otr|lo verifica(?:n)?\s+(?!quien)\w+",
    re.I,
)
AVISO_PIDE_OTRO = (
    "AVISO: el criterio pide que verifique otra persona, pero el nivel es «self» (propia) y"
    ' se sella sola: súbelo con update_ticket(verification_level="peer")'
)


def _pide_otro(verification_level, cadence_days, verify_criteria) -> bool:
    return verification_level == "self" and not cadence_days and bool(_PIDE_OTRO.search(verify_criteria or ""))


EVIDENCE_TYPES = {
    "file_path",
    "command_output",
    "memory_id",
    "peer_attestation",
    "run_output",
    "proof",
}  # proof: #594, la prueba que el verificador corre en una copia (decisión 49)


def proof_repos(conn) -> list[str]:
    from . import settings

    return [r.strip() for r in (settings.get(conn, "proof_repos") or "").split(";") if r.strip()]


KINDS = {"manual", "agentic"}
HARNESSES = set(_harness.names())
PERMISSION_MODES = {"acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan"}
CLOSED_STATUSES = {"verified", "rejected", "expired"}
DEDUP_CLOSED_WINDOW_DAYS = 60


class TicketError(Exception):
    """Operación ilegal. El mensaje es la explicación entera.
    v3.2 (T1.5): `field` opcional = qué campo del input falló, para que la UI
    lo pinte inline (el dueño: «que me lo diga en el propio menú»).
    v3.3 (#270): `code` opcional = identificador estable del error, en inglés y
    con puntos (p.ej. "auth.invalid_token"). El mensaje sigue siendo la
    explicación en español para el log y para quien no tenga traducción; el
    CÓDIGO es lo que la interfaz traduce. Si el backend manda la frase, el
    botón de idioma no manda sobre lo que lee el usuario.

    Es aditivo a propósito: un error sin `code` sigue funcionando exactamente
    igual que antes y la UI cae al mensaje. Así se pueden ir etiquetando los
    errores que de verdad llegan al usuario sin tocar los 211 sitios de golpe
    la víspera de publicar."""

    def __init__(self, message: str, field: str | None = None, code: str | None = None):
        super().__init__(message)
        self.field = field
        self.code = code


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _days_since(iso: str) -> int:
    return (date.today() - date.fromisoformat(iso[:10])).days


def _criterio_en_piezas(criterio) -> None:
    """#1346 (C4): crear o editar un criterio con una etiqueta repetida es un error."""
    try:
        _piezas.partir(criterio)
    except _piezas.PiezasError as e:
        raise TicketError(str(e), field=e.field, code=e.code)


def _validar_piezas(t, agent, verdict, piezas, cambios) -> dict:
    """#1346: valida un verify (o el complete que sella una self) contra las piezas del criterio.
    Los agentes de sistema y el dueño de la casa sin piezas: ver piezas.validar (C14, C15)."""
    huella_vieja = json.loads(t["piezas_huella"]) if t["piezas_huella"] else None
    try:
        return _piezas.validar(
            t["verify_criteria"],
            verdict,
            piezas,
            agent,
            HOUSE_OWNER,
            huella_vieja=huella_vieja,
            porque=cambios,
            sistema=SYSTEM_AGENTS,
        )
    except _piezas.PiezasError as e:
        raise TicketError(str(e), field=e.field, code=e.code)


def _append_history(conn, ticket_id, old_status, new_status, changed_by, note=None, provenance=None):
    conn.execute(
        "INSERT INTO ticket_history (ticket_id, old_status, new_status, changed_by, changed_at, note,"
        " provenance) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ticket_id, old_status, new_status, changed_by, _now(), note, provenance),
    )


PROVENANCE_MAX = 120


def _check_provenance(declared, agent=None):
    """#377: lo único que se declara es `deterministic:<herramienta>` (el test, el grep, el script
    que hizo la comprobación). external y same-family no se declaran: salen de quién verifica.
    #465: la excepción es el verificador, que declara `external:<modelo>` cuando juzga otra familia."""
    if declared is None:
        return None
    if agent == "verifier-bot" and declared.startswith("external:"):
        tool = declared[len("external:") :]
    else:
        tool = declared[len("deterministic:") :] if declared.startswith("deterministic:") else ""
    if not tool.strip() or len(declared) > PROVENANCE_MAX or any(c in declared for c in "\r\n\0"):
        raise TicketError(
            f"procedencia inválida: {declared[:60]!r}. Solo se declara 'deterministic:<herramienta>'"
            f" (una línea, hasta {PROVENANCE_MAX} caracteres); external y same-family las pone FarOS"
            " según quién verifica",
            field="provenance",
            code="verify.provenance_invalid",
        )
    return declared


def _provenance(agent, t, declared=None):
    """#377 (decisión 21 de Pepe, 8-oct): de dónde viene una verificación. El dueño de la casa es
    externo a la familia de modelos; cualquier otro (agentes y verifier-bot, que juzga con Claude)
    es same-family, y `:self` si quien sella es la dueña de la tarea."""
    if declared:
        return declared
    if agent == HOUSE_OWNER:
        return "external"
    return "same-family:self" if agent == t["owner"] else "same-family"


def _expire_lazy(conn) -> None:
    """Todo abierto con expires_at pasado caduca al leerlo. UPDATE condicional +
    rowcount gate: dos lectores concurrentes no duplican history (v0, fix F3)."""
    now = _now()
    rows = conn.execute(
        "SELECT id, status FROM tickets WHERE closed_at IS NULL AND expires_at IS NOT NULL AND expires_at < ?",
        (now,),
    ).fetchall()
    changed = False
    for row in rows:
        cur = conn.execute(
            "UPDATE tickets SET status='expired', closed_at=? WHERE id=? AND closed_at IS NULL",
            (now, row["id"]),
        )
        if cur.rowcount == 1:
            _append_history(conn, row["id"], row["status"], "expired", "system", "caducado: expires_at vencido")
            for hook in CLOSED_HOOKS:
                hook(conn, row["id"], "expired")
            changed = True
    if changed:
        conn.commit()


def _get(conn, ticket_id) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if row is None:
        raise TicketError(f"ticket #{ticket_id} no existe", code="ticket.not_found")
    return row


def laborable(d: date) -> date:
    """Sáb/dom no hay trabajo (acuerdo 13-ago): el primer día de lunes a viernes desde d."""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def next_due_date(d: dict) -> str | None:
    """Próxima fecha teórica de una recurrente (ISO date): next_due_override si
    existe (T0.3: mover la próxima sin tocar la cadencia), si no last_done +
    cadencia; sin last_done → hoy (ya toca). Una diaria (cadencia 1) es de lunes a
    viernes (Pepe, 6-nov: los descansos salen «todos los días entre semana»)."""
    if not d.get("cadence_days"):
        return None
    if d.get("next_due_override"):
        return d["next_due_override"][:10]
    cadence = int(d["cadence_days"])
    weekday = d.get("weekday")
    if weekday is not None and cadence % 7 == 0:
        # v3.2 (T1.1, el dueño 7-sep): recurrente SEMANAL anclada a un día de la semana —
        # la próxima cae siempre en ese día, aunque se estampe otro día. Sin
        # last_done: la primera ocurrencia >= hoy. Cadencia 14: una semana más.
        base = date.fromisoformat(d["last_done_at"][:10]) + timedelta(days=1) if d.get("last_done_at") else date.today()
        nxt = base + timedelta(days=(int(weekday) - base.weekday()) % 7)
        return (nxt + timedelta(weeks=cadence // 7 - 1)).isoformat()
    if d.get("last_done_at"):
        nxt = date.fromisoformat(d["last_done_at"][:10]) + timedelta(days=cadence)
    else:
        nxt = date.today()
    return (laborable(nxt) if cadence == 1 else nxt).isoformat()


def _as_dict(conn, row: sqlite3.Row) -> dict:
    """Single-ticket enrichment. For bulk use _batch_enrich instead (T0.5)."""
    d = dict(row)
    lc = conn.execute(
        "SELECT old_status, new_status, changed_by, changed_at FROM ticket_history"
        " WHERE ticket_id=? AND old_status IS NOT NULL AND old_status != new_status"
        " ORDER BY id DESC LIMIT 1",
        (d["id"],),
    ).fetchone()
    d["last_change"] = (
        {"at": lc["changed_at"], "by": lc["changed_by"], "from": lc["old_status"], "to": lc["new_status"]}
        if lc
        else None
    )
    _enrich_due(d)
    job = conn.execute("SELECT * FROM agent_jobs WHERE ticket_id=?", (d["id"],)).fetchone()
    if job:
        jd = dict(job)
        jd["schedule"] = json.loads(jd["schedule"])
        last = conn.execute(
            "SELECT id, status, started_at, finished_at, result_summary, cost_usd, cost_kind,"
            " verdict, verdict_reason FROM runs WHERE job_id=? ORDER BY id DESC LIMIT 1",
            (job["id"],),
        ).fetchone()
        if d.get("kind") == "agentic":
            d["job"] = jd
            d["last_run"] = dict(last) if last else None
        else:
            d["linked_job"] = _linked_job(jd, dict(last) if last else None)
            if (
                d.get("closed_at")
                and jd["status"] == "active"
                and jd["enabled"]
                and jd["schedule"].get("type") != "manual"
            ):
                # #518: cerrar la tarea apaga al job que cuelga de ella; que se diga al cerrar
                d["job_warning"] = (
                    f"el job #{jd['id']} «{jd['name']}» cuelga de esta tarea cerrada y no se"
                    ' dispara: desenlázalo (update_job con clear=["ticket_id"]) o páusalo'
                )
    _apply_virtual_expiry(d)
    return d


def _linked_job(jd: dict, last: dict | None) -> dict:
    """Brazo automático de una tarea manual (T1.14): lo justo para el chip."""
    return {
        "id": jd["id"],
        "name": jd["name"],
        "status": jd["status"],
        "harness": jd["harness"],
        "model": jd["model"],
        "last_run": last,
    }


def _enrich_due(d: dict) -> None:
    """Compute due_state fields from cadence/due_at. No DB access."""
    if d.get("cadence_days"):
        since = _days_since(d["last_done_at"]) if d["last_done_at"] else None
        d["age_days"] = since
        d["next_due"] = next_due_date(d)
        today_iso = date.today().isoformat()
        d["due"] = d["next_due"] <= today_iso
        d["overdue"] = d["next_due"] < today_iso
        d["due_state"] = "overdue" if d["overdue"] else ("due" if d["due"] else "ok")
    elif d.get("due_at") and d.get("closed_at") is None:
        due_day = d["due_at"][:10]
        today_iso = date.today().isoformat()
        d["due_state"] = "overdue" if due_day < today_iso else "due" if due_day == today_iso else "ok"
    else:
        d["due_state"] = "ok"


def _apply_virtual_expiry(d: dict, now_iso: str | None = None) -> None:
    """T4.5: mark a ticket as expired in memory if expires_at < now, without DB write.
    The scheduler writes the real expiry every 60s; this keeps reads consistent."""
    if (
        d.get("expires_at")
        and d.get("closed_at") is None
        and d["status"] not in CLOSED_STATUSES
        and d["expires_at"] < (now_iso or _now())
    ):
        d["status"] = "expired"
        d["closed_at"] = d["expires_at"]
        d["due_state"] = "ok"


def _in_clause(ids: list[int]) -> tuple[str, list[int]]:
    """Build a safe IN clause with placeholders."""
    return ",".join("?" * len(ids)), list(ids)


def _batch_enrich(conn, rows: list[sqlite3.Row]) -> list[dict]:
    """Bulk enrichment: replaces N calls to _as_dict with 3 queries total (T0.5).
    Hilo's jobs.py _hydrate is for the Agentes section; this batches the
    ticket-side reads only (last_change + job + last_run for agentic tickets)."""
    if not rows:
        return []
    tickets = [dict(r) for r in rows]
    ids = [t["id"] for t in tickets]
    placeholders, params = _in_clause(ids)

    # 1. Batch last_change: one query using window function
    lc_rows = conn.execute(
        "SELECT ticket_id, old_status, new_status, changed_by, changed_at"
        " FROM (SELECT ticket_id, old_status, new_status, changed_by, changed_at,"
        "   ROW_NUMBER() OVER (PARTITION BY ticket_id ORDER BY id DESC) AS rn"
        "   FROM ticket_history"
        f"  WHERE ticket_id IN ({placeholders})"
        "   AND old_status IS NOT NULL AND old_status != new_status"
        ") WHERE rn = 1",
        params,
    ).fetchall()
    lc_map = {
        r["ticket_id"]: {"at": r["changed_at"], "by": r["changed_by"], "from": r["old_status"], "to": r["new_status"]}
        for r in lc_rows
    }

    # 2+3. Jobs (agénticas → job embebido; manuales → linked_job) con su último run,
    # en UNA consulta (T0.5: workflow_status ≤ 10 queries).
    job_map, run_map = {}, {}
    job_rows = conn.execute(
        "SELECT j.*, r.id AS r_id, r.status AS r_status, r.started_at AS r_started_at,"
        " r.finished_at AS r_finished_at, r.result_summary AS r_result_summary,"
        " r.cost_usd AS r_cost_usd, r.cost_kind AS r_cost_kind, r.verdict AS r_verdict,"
        " r.verdict_reason AS r_verdict_reason"
        " FROM agent_jobs j LEFT JOIN ("
        "   SELECT * FROM (SELECT runs.*, ROW_NUMBER() OVER (PARTITION BY job_id ORDER BY id DESC) AS rn"
        "                  FROM runs) WHERE rn = 1) r ON r.job_id = j.id"
        f" WHERE j.ticket_id IN ({placeholders})",
        params,
    ).fetchall()
    for jr in job_rows:
        jd = {k: jr[k] for k in jr.keys() if not k.startswith("r_")}
        jd["schedule"] = json.loads(jd["schedule"])
        job_map[jr["ticket_id"]] = jd
        if jr["r_id"] is not None:
            run_map[jr["ticket_id"]] = {
                "job_id": jr["id"],
                "id": jr["r_id"],
                "status": jr["r_status"],
                "started_at": jr["r_started_at"],
                "finished_at": jr["r_finished_at"],
                "result_summary": jr["r_result_summary"],
                "cost_usd": jr["r_cost_usd"],
                "cost_kind": jr["r_cost_kind"],
                "verdict": jr["r_verdict"],
                "verdict_reason": jr["r_verdict_reason"],
            }

    # Assemble
    now_iso = _now()
    for t in tickets:
        t["last_change"] = lc_map.get(t["id"])
        _enrich_due(t)
        if t.get("kind") == "agentic":
            t["job"] = job_map.get(t["id"])
            t["last_run"] = run_map.get(t["id"])
        elif job_map.get(t["id"]):
            t["linked_job"] = _linked_job(job_map[t["id"]], run_map.get(t["id"]))
        _apply_virtual_expiry(t, now_iso)
    return tickets


def _validate_agent(agent: str, allow_system: bool = False, family_only: bool = False) -> str:
    valid = AGENTS if family_only else (AGENTS | WORKFLOW_AGENTS | roster.WORKFLOW_EXTRA)
    if allow_system:
        valid = valid | SYSTEM_AGENTS
    if agent not in valid:
        raise TicketError(
            f"agente desconocido o sin permiso: {agent!r} (válidos: {sorted(valid)})", code="agent.unknown"
        )
    return agent


def _unmet_deps(conn, ticket_id) -> list[dict]:
    """Dependencias sin verificar de un ticket (v2). Vacío = ready."""
    rows = conn.execute(
        "SELECT t.id, t.plan_key, t.title, t.status FROM ticket_deps d"
        " JOIN tickets t ON t.id = d.depends_on"
        " WHERE d.ticket_id=? AND t.status != 'verified'",
        (ticket_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ proyectos


def create_project(conn, agent, name, color=None) -> dict:
    _validate_agent(agent, family_only=True)
    if not (name or "").strip():
        raise TicketError("name vacío")
    dup = conn.execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone()
    if dup:
        raise TicketError(f"proyecto {name!r} ya existe (#{dup['id']})")
    pos = conn.execute("SELECT COALESCE(MAX(position),0)+1 AS p FROM projects").fetchone()["p"]
    cur = conn.execute(
        "INSERT INTO projects (name, color, position, created_at) VALUES (?, ?, ?, ?)", (name, color, pos, _now())
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM projects WHERE id=?", (cur.lastrowid,)).fetchone())


def list_projects(conn, include_archived=False) -> list[dict]:
    sql = "SELECT * FROM projects"
    if not include_archived:
        sql += " WHERE archived=0"
    sql += " ORDER BY position"
    return [dict(r) for r in conn.execute(sql)]


def update_project(conn, agent, project_id, name=None, color=None, archived=None) -> dict:
    _validate_agent(agent)
    row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if row is None:
        raise TicketError(f"proyecto #{project_id} no existe")
    sets, params = [], []
    if name is not None:
        sets.append("name=?")
        params.append(name)
    if color is not None:
        sets.append("color=?")
        params.append(color)
    if archived is not None:
        sets.append("archived=?")
        params.append(1 if archived else 0)
    if sets:
        params.append(project_id)
        conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
    return dict(conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())


def _validate_project(conn, project_id):
    if project_id is None:
        return
    if conn.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone() is None:
        raise TicketError(f"proyecto #{project_id} no existe")


# ---------------------------------------------------------------- operaciones

TEMPORAL_FIELDS = {"scheduled_at", "duration_min", "all_day", "preferred_time", "next_due_override", "weekday"}


def _validate_temporal(fields: dict, cadence_days, weekday=None) -> dict:
    """Valida y normaliza los campos temporales (T0.3). Devuelve los valores
    normalizados. scheduled_at 'YYYY-MM-DDTHH:MM'; duration_min int > 0;
    all_day 0/1; preferred_time 'HH:MM'; next_due_override 'YYYY-MM-DD' y solo
    en recurrentes. None borra el campo."""
    out = {}
    for k, v in fields.items():
        if k not in TEMPORAL_FIELDS:
            continue
        if v is None or v == "":
            out[k] = None if k != "all_day" else 0
            continue
        if k == "scheduled_at":
            try:
                dt = datetime.fromisoformat(str(v))
            except ValueError:
                raise TicketError(f"scheduled_at inválido: {v!r} (esperado YYYY-MM-DDTHH:MM)", field="scheduled_at")
            if len(str(v)) < 16:
                raise TicketError(
                    f"scheduled_at necesita hora: {v!r} (YYYY-MM-DDTHH:MM); para 'todo el día' usa all_day=1",
                    field="scheduled_at",
                )
            out[k] = dt.strftime("%Y-%m-%dT%H:%M")
        elif k == "weekday":
            # v3.2 (T1.1): día de la semana de una recurrente semanal (0=lunes … 6=domingo)
            if not cadence_days or int(cadence_days) % 7 != 0:
                raise TicketError("weekday solo aplica a recurrentes con cadencia múltiplo de 7", field="weekday")
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise TicketError(f"weekday inválido: {v!r} (0=lunes … 6=domingo)", field="weekday")
            if n < 0 or n > 6:
                raise TicketError(f"weekday fuera de rango: {n} (0=lunes … 6=domingo)", field="weekday")
            out[k] = n
        elif k == "duration_min":
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise TicketError(f"duration_min inválido: {v!r}")
            if n < 1 or n > 24 * 60:
                raise TicketError("duration_min fuera de rango [1, 1440]")
            out[k] = n
        elif k == "all_day":
            if v not in (0, 1, True, False, "0", "1"):
                raise TicketError(f"all_day inválido: {v!r} (0|1)")
            out[k] = 1 if v in (1, True, "1") else 0
        elif k == "preferred_time":
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(v)):
                raise TicketError(f"preferred_time inválido: {v!r} (HH:MM)", field="preferred_time")
            out[k] = str(v)
        elif k == "next_due_override":
            if not cadence_days:
                raise TicketError("next_due_override solo aplica a recurrentes (cadence_days)")
            try:
                nd = date.fromisoformat(str(v)[:10])
            except ValueError:
                raise TicketError(f"next_due_override inválido: {v!r} (YYYY-MM-DD)")
            # #224 (el dueño 12-sep: snap, no rechazar): sáb/dom no hay trabajo (acuerdo
            # 13-ago) — un override en fin de semana se mueve al lunes siguiente.
            # #1069: salvo que la recurrente esté anclada a ESE día («Entreno sábado», weekday 5).
            if nd.weekday() >= 5 and nd.weekday() != weekday:
                nd += timedelta(days=7 - nd.weekday())
            out[k] = nd.isoformat()
    return out


def _weekday_de_su_fecha(cadence_days, *fechas) -> int | None:
    """#376 (Pepe, 25-sep): una semanal no cambia de día. Si su dueño le ha dado una
    fecha y no un día de la semana, el día sale de ESA fecha (la primera que haya).
    Sin fecha no se inventa ninguno (#364): se queda «por ubicar»."""
    if not cadence_days or int(cadence_days) % 7:
        return None
    for f in fechas:
        if f:
            return date.fromisoformat(str(f)[:10]).weekday()
    return None


def propose_ticket(
    conn,
    agent,
    title,
    description="",
    owner=None,
    source="proposed",
    priority="media",
    verification_level="self",
    expires_at=None,
    cadence_days=None,
    project_id=None,
    kind="manual",
    due_at=None,
    verify_criteria=None,
    auto_accept=False,
    job=None,
    scheduled_at=None,
    duration_min=None,
    all_day=0,
    preferred_time=None,
    weekday=None,
) -> dict:
    """Crea un ticket. kind='agentic' exige bloque job (prompt al menos).

    auto_accept (review Eco): lo que creal dueño desde la UI nace aceptado — el gate
    Propuestas→Aceptadas es el workflow de bisagra NUESTRO, no el suyo. Las dos
    transiciones quedan igualmente en history (invariante intacto).
    v3.2: los errores de input llevan `field` (T1.5) y weekday ancla la semanal (T1.1).
    """
    _validate_agent(agent)
    # finding #27 (Lienzo, T2.1): un cliente puede mandar opcionales a null; nulo = vacío, no 500
    description = description or ""
    if not (title or "").strip():
        raise TicketError("title vacío", field="title")
    if source not in SOURCES:
        raise TicketError(f"source inválido: {source!r} (válidos: {sorted(SOURCES)})", field="source")
    if priority not in PRIORITIES:
        raise TicketError(f"priority inválida: {priority!r}", field="priority")
    if verification_level not in VERIFICATION_LEVELS:
        raise TicketError(f"verification_level inválido: {verification_level!r}", field="verification_level")
    if kind not in KINDS:
        raise TicketError(f"kind inválido: {kind!r}", field="kind")
    if owner is not None:
        try:
            _validate_agent(owner)
        except TicketError as exc:
            raise TicketError(str(exc), field="owner")
    if cadence_days is not None and cadence_days < 1:
        raise TicketError("cadence_days debe ser >= 1", field="cadence_days")
    _criterio_en_piezas(verify_criteria)
    if cadence_days is not None and verification_level != "self":
        raise TicketError(
            "las recurrentes son verification_level=self — con peer/auto nunca"
            " alcanzarían 'verified' porque complete no las cierra (SPEC §4)",
            field="verification_level",
        )
    if due_at:
        try:
            datetime.fromisoformat(str(due_at))
        except ValueError:
            raise TicketError(f"due_at inválido: {due_at!r} (YYYY-MM-DD o YYYY-MM-DDTHH:MM)", field="due_at")
    try:
        _validate_project(conn, project_id)
    except TicketError as exc:
        raise TicketError(str(exc), field="project_id")
    if kind == "agentic":
        if not job or not (job.get("prompt") or "").strip():
            raise TicketError("kind=agentic requiere job con prompt")
        _validate_job_fields(job)
        # Review Prima F10: una agéntica recurring con nivel peer/auto se cerraría
        # (verified) tras el primer run y el scheduler no podría relanzarla. Las
        # recurring heredan la semántica de recurrente: cadence + self, viva siempre.
        if job.get("schedule", {}).get("type") == "recurring":
            if cadence_days is None:
                cadence_days = int(job["schedule"]["every_days"])
            if verification_level != "self":
                raise TicketError(
                    "agentic recurring exige verification_level=self: el ticket no se"
                    " cierra por diseño (se estampa cada run); la verificación de runs"
                    " individuales es deuda v1.1 (SPEC §12)"
                )

    temporal = _validate_temporal(
        {
            "scheduled_at": scheduled_at,
            "duration_min": duration_min,
            "all_day": all_day,
            "preferred_time": preferred_time,
            "weekday": weekday,
        },
        cadence_days,
    )
    if temporal.get("weekday") is None:
        temporal["weekday"] = _weekday_de_su_fecha(cadence_days, temporal.get("scheduled_at"), due_at)

    # #364 (Pepe, 30-sep): NO hay ancla automática. «Agendar automáticamente a las 14:00 es lo
    # mismo que no poner horario»; tampoco se le exige la fecha a quien crea (eso le pasaba la
    # gestión a Pepe). Una tarea sin fecha nace «por ubicar» y es su DUEÑO quien la coloca:
    # le sale en my_board()["por_ubicar"] y en board()["laterals"]["por_ubicar"].

    _expire_lazy(conn)
    duplicates = _find_duplicates(conn, title, description)
    now = _now()
    cur = conn.execute(
        "INSERT INTO tickets (project_id, kind, title, description, owner, source,"
        " priority, due_at, verify_criteria, verification_level, expires_at,"
        " cadence_days, created_by, created_at,"
        " scheduled_at, duration_min, all_day, preferred_time, weekday)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            project_id,
            kind,
            title,
            description,
            owner,
            source,
            priority,
            due_at,
            verify_criteria,
            verification_level,
            expires_at,
            cadence_days,
            agent,
            now,
            temporal["scheduled_at"],
            temporal["duration_min"],
            temporal["all_day"],
            temporal["preferred_time"],
            temporal["weekday"],
        ),
    )
    ticket_id = cur.lastrowid
    # #152: el único 500 de dispatch_finding (3-sep) fue este INSERT con el rowid ya ocupado
    # en el índice. El id acaba de nacer: una fila del índice con ese rowid es huérfana por
    # fuerza. Se quita, se indexa la tarea y el log dice qué había, para seguirle la pista.
    huerfana = conn.execute("SELECT title FROM tickets_fts WHERE rowid=?", (ticket_id,)).fetchone()
    if huerfana:
        log.warning(
            "#152 fila huérfana en tickets_fts con rowid %s (título %r): se quita al indexar la tarea nueva %r",
            ticket_id,
            huerfana[0],
            title,
        )
        conn.execute("DELETE FROM tickets_fts WHERE rowid=?", (ticket_id,))
    conn.execute(
        "INSERT INTO tickets_fts (rowid, title, description) VALUES (?, ?, ?)", (ticket_id, title, description)
    )
    _append_history(conn, ticket_id, None, "proposed", agent, None)
    if auto_accept:
        conn.execute("UPDATE tickets SET status='accepted' WHERE id=?", (ticket_id,))
        _append_history(conn, ticket_id, "proposed", "accepted", agent, "auto-accept (creado desde la UI)")
    conn.commit()
    if kind == "agentic":
        # DEPRECATED (v3): el job nace como entidad propia vinculada al ticket
        # (jobs.py). verify_level='none' conserva la semántica v1: run ok →
        # ticket done → verificador de tickets. Las agénticas nuevas se crean
        # en la sección Agentes, no aquí (T1.14).
        from . import jobs as _jobs

        _jobs.create_job(
            conn,
            agent,
            name=title,
            prompt=job["prompt"],
            harness_name=job.get("harness", "claude-cli"),
            model=job.get("model", "haiku"),
            schedule=job.get("schedule", {"type": "manual"}),
            ticket_id=ticket_id,
            project_id=project_id,
            cwd=job.get("cwd"),
            permission_mode=job.get("permission_mode", "dontAsk"),
            allowed_tools=job.get("allowed_tools"),
            mcp_config=job.get("mcp_config"),
            max_budget_usd=job.get("max_budget_usd"),
            timeout_s=job.get("timeout_s", 1800),
            verify_level="none",
            _legacy=True,
        )
    res = {"ticket": _as_dict(conn, _get(conn, ticket_id)), "possible_duplicates": duplicates}
    if _pide_otro(verification_level, cadence_days, verify_criteria):
        res["warning"] = AVISO_PIDE_OTRO
    return res


def _validate_job_fields(job: dict) -> None:
    if job.get("harness", "claude-cli") not in HARNESSES:
        raise TicketError(f"harness inválido: {job.get('harness')!r} (válidos: {sorted(HARNESSES)})")
    if job.get("permission_mode", "dontAsk") not in PERMISSION_MODES:
        raise TicketError(f"permission_mode inválido: {job.get('permission_mode')!r}")
    sched = job.get("schedule", {"type": "manual"})
    stype = sched.get("type")
    if stype not in {"manual", "once", "recurring"}:
        raise TicketError(f"schedule.type inválido: {stype!r} (manual|once|recurring)")
    if stype == "once" and not sched.get("run_at"):
        raise TicketError("schedule once requiere run_at (ISO)")
    if stype == "recurring":
        if not sched.get("at"):
            raise TicketError("schedule recurring requiere at ('HH:MM')")
        if int(sched.get("every_days", 0)) < 1:
            raise TicketError("schedule recurring requiere every_days >= 1")
    t = job.get("timeout_s", 1800)
    if not (30 <= int(t) <= 4 * 3600):
        raise TicketError("timeout_s fuera de rango [30, 14400]")


def update_job(conn, agent, job_id, **fields) -> dict:
    """Wrapper v1 (PATCH /api/jobs/{id} del Tablero): edita el job de un ticket
    agéntico y devuelve el TICKET. El vínculo no se cambia desde aquí."""
    from . import jobs as _jobs

    allowed = {
        "harness",
        "model",
        "prompt",
        "cwd",
        "permission_mode",
        "allowed_tools",
        "mcp_config",
        "max_budget_usd",
        "timeout_s",
        "schedule",
        "enabled",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise TicketError(f"campos no editables: {sorted(unknown)}")
    j = _jobs.update_job(conn, agent, job_id, **fields)
    if j.get("ticket_id"):
        return _as_dict(conn, _get(conn, j["ticket_id"]))
    return j


_STOPWORDS_ES = frozenset(
    "a al de el en la o se un y in of vs no"
    " del las los una uno con por para que esta este esto como mas sin son hay fue ser"
    " tiene hace donde cuando sobre todo entre puede cada tipo debe forma nuevo nueva"
    " todos todas otro otra solo bien ahora".split()
)
_DEDUP_OVERLAP_MIN = 0.5  # keep candidates where >= 50% of query words appear
_DEDUP_SHARED_MIN = 2  # #73: and at least 2 shared words (1 if the query has only one)


def _normalize_word(w: str) -> str:
    # #73: quitar la tilde, no la letra. FTS5 (unicode61) guarda «vacío» como «vacio»;
    # borrando la «í» salía «vaco» y la palabra no casaba nunca.
    w = unicodedata.normalize("NFKD", w.lower())
    return re.sub(r"[^a-z0-9]", "", w)


def _find_duplicates(conn, title, description) -> list[dict]:
    words = {_normalize_word(w) for w in title.split()} - _STOPWORDS_ES - {""}
    if not words:
        return []
    terms = " OR ".join(f'"{w}"' for w in words)
    rows = conn.execute(
        "SELECT t.id, t.title, t.status, bm25(tickets_fts) AS score"
        " FROM tickets_fts JOIN tickets t ON t.id = tickets_fts.rowid"
        " WHERE tickets_fts MATCH ?"
        " AND (t.closed_at IS NULL OR t.closed_at >= datetime('now', 'localtime', ?))"
        " ORDER BY score LIMIT 15",
        (terms, f"-{DEDUP_CLOSED_WINDOW_DAYS} days"),
    ).fetchall()
    result = []
    for r in rows:
        cand_words = {_normalize_word(w) for w in r["title"].split()} - _STOPWORDS_ES - {""}
        shared = len(words & cand_words)
        if cand_words and shared / len(words) >= _DEDUP_OVERLAP_MIN and shared >= min(_DEDUP_SHARED_MIN, len(words)):
            result.append(dict(r))
    return result[:5]


def accept_ticket(conn, agent, ticket_id) -> dict:
    return _transition(conn, agent, ticket_id, from_={"proposed"}, to="accepted")


def reject_ticket(conn, agent, ticket_id, reason) -> dict:
    if not (reason or "").strip():
        raise TicketError("reject requiere reason", field="reason", code="reject.reason_required")
    # #666: una rechazada con hora dejaba citas fantasma en el tablero, y cerrada ya no se podía
    # reprogramar. Rechazar vacía la fecha; lo que tenía queda en la nota del historial.
    fecha = _fecha_de(_get(conn, ticket_id))
    nota = f"{reason} [#666, fecha vaciada al rechazar: {fecha}]" if fecha else reason
    _transition(conn, agent, ticket_id, from_={"proposed", "accepted", "blocked"}, to="rejected", note=nota, close=True)
    if fecha:
        conn.execute(
            "UPDATE tickets SET scheduled_at=NULL, duration_min=NULL, preferred_time=NULL WHERE id=?", (ticket_id,)
        )
    for hook in CLOSED_HOOKS:
        hook(conn, ticket_id, "rejected")
    conn.commit()
    return _as_dict(conn, _get(conn, ticket_id))


def _fecha_de(t) -> str:
    """#666: «scheduled_at=…, duration_min=…» con los campos de fecha que tiene, o "" si ninguno."""
    return ", ".join(f"{k}={t[k]}" for k in ("scheduled_at", "duration_min", "preferred_time") if t[k] is not None)


def _en_nombre_del_dueno(t, agent) -> str:
    """#640 (Pepe, 19-oct: «necesito que podáis modificar y verificar las tareas a mi nombre»):
    cualquiera de la casa puede empezar, completar (y así sellar las de nivel self) o pausar una tarea
    del dueño de la casa. Devuelve la marca para el historial, o "" si no es ese caso."""
    if t["owner"] == HOUSE_OWNER and agent != HOUSE_OWNER and agent not in SYSTEM_AGENTS:
        return f"[en nombre de {HOUSE_OWNER}]"
    return ""


def _con_marca(note, marca) -> str | None:
    return " ".join(p for p in (note, marca) if p) or None


def start_ticket(conn, agent, ticket_id, note="") -> dict:
    _validate_agent(agent, allow_system=True)
    _expire_lazy(conn)
    t = _get(conn, ticket_id)
    marca = _en_nombre_del_dueno(t, agent)
    if t["status"] not in {"accepted", "blocked"}:
        raise TicketError(f"start ilegal desde {t['status']!r} (requiere accepted|blocked)", code="transition.illegal")
    if t["workflow_id"]:
        unmet = _unmet_deps(conn, ticket_id)
        if unmet:
            keys = ", ".join(d["plan_key"] or f"#{d['id']}" for d in unmet)
            raise TicketError(f"start bloqueado: dependencias sin verificar ({keys})")
    if t["owner"] is None:
        # claim implícito: quien arranca sin dueño se convierte en owner (v0)
        conn.execute("UPDATE tickets SET owner=? WHERE id=?", (agent, ticket_id))
        note = (note + " " if note else "") + f"[claim: {agent} se hace owner]"
    elif (
        t["owner"] != agent
        and agent not in SYSTEM_AGENTS
        and agent != HOUSE_OWNER
        and t["verification_level"] != "peer"
        and not marca
    ):
        # v3.1: el dueño (dueño de la Casa) arranca cualquiera; queda en el history como suyo.
        # #367 (Pepe, salida A): con verificación peer lo arranca también quien lo ejecuta.
        # #640: y cualquiera de la casa arranca una tarea DEL dueño, en su nombre.
        raise TicketError(f"start solo puede hacerlo el owner ({t['owner']}), no {agent}", code="transition.not_owner")
    conn.execute("UPDATE tickets SET status='in_progress', blocked_reason=NULL WHERE id=?", (ticket_id,))
    if not t["piezas_huella"] and (t["verify_criteria"] or "").strip():
        # #1346 (C5): la huella del criterio se toma al empezar; un cambio posterior exige su porqué.
        try:
            h = _piezas.huella(t["verify_criteria"])
            conn.execute("UPDATE tickets SET piezas_huella=? WHERE id=?", (json.dumps(h), ticket_id))
        except _piezas.PiezasError:
            pass  # etiquetas repetidas de antes de la regla: el verify dará el error
    _append_history(conn, ticket_id, t["status"], "in_progress", agent, _con_marca(note, marca))
    conn.commit()
    return _as_dict(conn, _get(conn, ticket_id))


def block_ticket(conn, agent, ticket_id, reason) -> dict:
    if not (reason or "").strip():
        raise TicketError("block requiere reason", field="reason", code="block.reason_required")
    _validate_agent(agent, allow_system=True)
    _expire_lazy(conn)
    t = _get(conn, ticket_id)
    if t["status"] == "blocked":
        # #482: el motivo que ya no es cierto se reescribe sin desbloquear; el viejo queda en el historial.
        if reason == t["blocked_reason"]:
            return _as_dict(conn, t)
        conn.execute("UPDATE tickets SET blocked_reason=? WHERE id=?", (reason, ticket_id))
        _append_history(
            conn,
            ticket_id,
            "blocked",
            "blocked",
            agent,
            f"motivo cambiado: «{t['blocked_reason'] or '-'}» → «{reason}»",
        )
        conn.commit()
        return _as_dict(conn, _get(conn, ticket_id))
    if t["status"] not in {"accepted", "in_progress"}:
        raise TicketError(
            f"block ilegal desde {t['status']!r} (requiere accepted|in_progress|blocked)", code="transition.illegal"
        )
    conn.execute("UPDATE tickets SET status='blocked', blocked_reason=? WHERE id=?", (reason, ticket_id))
    _append_history(conn, ticket_id, t["status"], "blocked", agent, reason)
    conn.commit()
    return _as_dict(conn, _get(conn, ticket_id))


def pause_ticket(conn, agent, ticket_id, note) -> dict:
    """#482 (Pepe, 16-oct): una tarea que se pasó a «en curso» y no se ha empezado de verdad vuelve a
    «aceptada», con nota obligatoria, para que el tablero diga la verdad sobre qué está en curso.
    La pausa la hace su dueña o el dueño de la casa."""
    _validate_agent(agent)
    note = (note or "").strip()
    if len(note) < REWORK_MIN_REASON:
        raise TicketError(
            f"pause requiere una nota de al menos {REWORK_MIN_REASON} caracteres (por qué no está en curso de verdad)",
            field="note",
            code="pause.note_required",
        )
    _expire_lazy(conn)
    t = _get(conn, ticket_id)
    if t["status"] != "in_progress":
        raise TicketError(f"pause ilegal desde {t['status']!r} (requiere in_progress)", code="transition.illegal")
    marca = _en_nombre_del_dueno(t, agent)
    if agent not in {t["owner"], HOUSE_OWNER} and not marca:
        raise TicketError(
            f"pause solo puede hacerlo la dueña ({t['owner']}) o {HOUSE_OWNER}, no {agent}", code="pause.not_owner"
        )
    conn.execute("UPDATE tickets SET status='accepted' WHERE id=?", (ticket_id,))
    _append_history(conn, ticket_id, "in_progress", "accepted", agent, _con_marca(f"[pausa] {note}", marca))
    conn.commit()
    return _as_dict(conn, _get(conn, ticket_id))


def complete_ticket(
    conn, agent, ticket_id, evidence_type, evidence, provenance=None, piezas=None, cambios=None
) -> dict:
    """Completa con evidencia obligatoria. Sin evidencia no hay done (SPEC §4)."""
    _validate_agent(agent, allow_system=True)
    if evidence_type not in EVIDENCE_TYPES:
        raise TicketError(
            f"evidence_type inválido: {evidence_type!r} (válidos: {sorted(EVIDENCE_TYPES)})",
            field="evidence_type",
            code="complete.evidence_required",
        )
    if not (evidence or "").strip():
        raise TicketError(
            "complete requiere evidencia — 'done' sin evidencia no existe",
            field="evidence",
            code="complete.evidence_required",
        )
    provenance = _check_provenance(provenance, agent)
    if evidence_type == "proof":
        from . import prueba

        try:
            prueba.leer(evidence, proof_repos(conn))
        except prueba.PruebaError as e:
            raise TicketError(
                f"{e} (los repos admitidos están en Ajustes › proof_repos)",
                field="evidence",
                code="complete.proof_invalid",
            )
    _expire_lazy(conn)
    t = _get(conn, ticket_id)
    marca = _en_nombre_del_dueno(t, agent)
    if t["status"] not in {"in_progress", "accepted"}:
        raise TicketError(
            f"complete ilegal desde {t['status']!r} (requiere in_progress|accepted)", code="transition.illegal"
        )
    if provenance and (t["cadence_days"] or t["verification_level"] != "self"):
        # Solo verifica quien completa una tarea de nivel self; en las demás lo hará otro.
        raise TicketError(
            "la procedencia va con una verificación: este complete no verifica"
            f" (nivel {t['verification_level']}{', recurrente' if t['cadence_days'] else ''})",
            field="provenance",
            code="verify.provenance_not_a_verification",
        )
    if t["owner"] is None:
        # Claim implícito (fix F1 de v0): evita bypass del guard de peer con owner=None.
        conn.execute("UPDATE tickets SET owner=? WHERE id=?", (agent, ticket_id))
        t = _get(conn, ticket_id)
    elif (
        t["owner"] != agent
        and agent not in SYSTEM_AGENTS
        and agent != HOUSE_OWNER
        and t["verification_level"] != "peer"
        and not marca
    ):
        # #640: cualquiera de la casa completa una tarea DEL dueño, en su nombre.
        # #367 (Pepe, salida A): con verificación peer lo completa también quien lo ejecutó;
        # el sello sigue siendo de un tercero (verify_ticket rechaza al que completó).
        # el dueño es el dueño de la Casa: puede completar/estampar por cualquiera desde la
        # app (regla del 3-sep, hallazgo de Lienzo en T2.9). Queda en el history como
        # transición del dueño sobre un ticket ajeno; verifier != owner sigue intacto.
        raise TicketError(
            f"complete solo puede hacerlo el owner ({t['owner']}), no {agent}", code="transition.not_owner"
        )
    now = _now()
    aviso = _pide_otro(t["verification_level"], t["cadence_days"], t["verify_criteria"])
    sello = None
    if not t["cadence_days"] and t["verification_level"] == "self":
        # #1346 (C9): el complete de una self sella, así que es una verificación: piezas.
        sello = _validar_piezas(t, agent, "pass", piezas, cambios)

    if t["cadence_days"]:
        # Recurrente: estampa y sigue viva. La cronología queda en history.
        conn.execute(
            "UPDATE tickets SET last_done_at=?, status='accepted',"
            " evidence_type=?, evidence=?, next_due_override=NULL WHERE id=?",
            (now, evidence_type, evidence, ticket_id),
        )
        _append_history(
            conn,
            ticket_id,
            t["status"],
            "accepted",
            agent,
            _con_marca(f"recurrente ejecutada [{evidence_type}] {evidence}", marca),
        )
    elif t["verification_level"] == "self":
        conn.execute(
            "UPDATE tickets SET status='verified', evidence_type=?, evidence=?, verified_by=?, closed_at=? WHERE id=?",
            (evidence_type, evidence, agent, now, ticket_id),
        )
        _append_history(
            conn,
            ticket_id,
            t["status"],
            "verified",
            agent,
            _con_marca(
                f"self-verified [{evidence_type}] {evidence}"
                + (f" — {AVISO_PIDE_OTRO}" if aviso else "")
                + (f" — [piezas] {sello['historial']}" if sello and sello["historial"] else ""),
                marca,
            ),
            provenance=_provenance(agent, t, provenance),
        )
        for hook in VERIFIED_HOOKS:
            hook(conn, ticket_id)
    else:  # peer | auto → cola de verificación (columna "Hechas sin verificar")
        conn.execute(
            "UPDATE tickets SET status='done', evidence_type=?, evidence=? WHERE id=?",
            (evidence_type, evidence, ticket_id),
        )
        _append_history(conn, ticket_id, t["status"], "done", agent, _con_marca(f"[{evidence_type}] {evidence}", marca))
    conn.commit()
    out = _as_dict(conn, _get(conn, ticket_id))
    if aviso:
        out["warning"] = AVISO_PIDE_OTRO  # #414: se sella igual (es un aviso), pero lo dice
    if sello and sello["avisos"]:
        out["avisos_piezas"] = sello["avisos"]  # #1346: la [persona] ajena, la referencia que no se abre
    try:  # Biblioteca (#850): DESPUÉS del commit; cerrar nunca falla ni cambia por esto
        from . import library

        library.al_cerrar(conn, ticket_id, evidence_type, evidence)
    except Exception:
        log.exception("biblioteca: el gancho de cierre falló en #%s", ticket_id)
    return out


def verify_ticket(conn, agent, ticket_id, verdict, note="", provenance=None, piezas=None, cambios=None) -> dict:
    """peer: humano que no sea el owner. auto: verifier-bot (o humano no-owner).
    #377: provenance solo admite 'deterministic:<herramienta>'; si no viene, se deduce."""
    _validate_agent(agent, allow_system=True)
    if verdict not in {"pass", "fail", "incierto"}:
        raise TicketError(
            f"verdict inválido: {verdict!r} (pass|fail|incierto)", field="verdict", code="verify.verdict_invalid"
        )
    provenance = _check_provenance(provenance, agent)
    _expire_lazy(conn)
    t = _get(conn, ticket_id)
    if t["status"] != "done":
        raise TicketError(f"verify ilegal desde {t['status']!r} (requiere done)", code="transition.illegal")
    if agent == "runner-bot":
        raise TicketError("runner-bot ejecuta, no verifica")
    if agent == t["owner"]:
        raise TicketError(f"el verificador no puede ser el owner ({agent})", code="verify.owner_cannot_verify")
    if (
        t["verify_claimed_by"]
        and t["verify_claimed_by"] != agent
        and agent not in SYSTEM_AGENTS
        and agent != HOUSE_OWNER
    ):
        raise TicketError(
            f"la verificación la tiene {t['verify_claimed_by']} (#481); que la suelte primero",
            code="verify.claimed_by_other",
        )
    if t["verification_level"] == "peer" and agent in SYSTEM_AGENTS:
        raise TicketError("verification_level=peer exige verificador humano/miembro, no un bot")
    if t["verification_level"] == "external" and agent not in {"verifier-bot", HOUSE_OWNER}:
        # #465 (decisión 22): se marcó «externa» para que NO la juzgue la familia que la hizo.
        raise TicketError(
            f"una tarea de verificación externa la sella el verificador de otra familia"
            f" (DeepSeek) o {HOUSE_OWNER}, no {agent}",
            code="verify.external_only",
        )
    # #367: quien completó no sella su propio trabajo (puede no ser el owner desde la salida A)
    # #439: solo cuenta la transición HACIA done; editar un ticket ya hecho deja una fila
    # done→done a nombre de quien edita y no le convierte en quien lo completó.
    last_done = conn.execute(
        "SELECT changed_by FROM ticket_history WHERE ticket_id=? AND new_status='done'"
        " AND (old_status IS NULL OR old_status != 'done')"
        " ORDER BY id DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()
    if last_done and last_done["changed_by"] == agent:
        raise TicketError(
            f"el verificador no puede ser quien lo completó ({agent})", code="verify.completer_cannot_verify"
        )
    r = _validar_piezas(t, agent, verdict, piezas, cambios)
    con_piezas = _con_marca(note, f"[piezas] {r['historial']}" if r["historial"] else None)
    if verdict == "incierto":
        # #1346 (C3): se queda en done; decide la dueña. Ni sube sola a external ni llama a DeepSeek.
        _append_history(
            conn,
            ticket_id,
            "done",
            "done",
            agent,
            f"[incierto] {con_piezas}",
            provenance=_provenance(agent, t, provenance),
        )
        conn.commit()
        out = _as_dict(conn, _get(conn, ticket_id))
        if r["avisos"]:
            out["avisos_piezas"] = r["avisos"]
        return out
    if verdict == "pass":
        now = _now()
        conn.execute(
            "UPDATE tickets SET status='verified', verified_by=?, closed_at=? WHERE id=?", (agent, now, ticket_id)
        )
        _append_history(
            conn, ticket_id, "done", "verified", agent, con_piezas, provenance=_provenance(agent, t, provenance)
        )
        for hook in VERIFIED_HOOKS:
            hook(conn, ticket_id)
    else:
        if not (note or "").strip():
            raise TicketError("verify fail requiere note (qué falta)", field="note", code="verify.note_required")
        conn.execute("UPDATE tickets SET status='in_progress' WHERE id=?", (ticket_id,))
        _append_history(
            conn,
            ticket_id,
            "done",
            "in_progress",
            agent,
            f"verify FAIL: {con_piezas}",
            provenance=_provenance(agent, t, provenance),
        )
    conn.commit()
    out = _as_dict(conn, _get(conn, ticket_id))
    if r["avisos"]:
        out["avisos_piezas"] = r["avisos"]  # #1346 (C14, duda 2): que se vea en el momento
    return out


def claim_verification(conn, agent, ticket_id) -> dict:
    """#481 (decisión 47): apuntarse a verificar EN la tarea. El orden lo pone la base, no la
    sala: el UPDATE solo prospera si nadie la tenía, así que de dos a la vez uno la consigue y
    el otro recibe «ya la tiene X». Apuntarse otra vez a la propia no hace nada."""
    _validate_agent(agent)
    t = _get(conn, ticket_id)
    if t["closed_at"] is not None:
        raise TicketError(f"ticket #{ticket_id} está cerrado ({t['status']})", code="transition.illegal")
    if t["verification_level"] == "self":
        raise TicketError(
            "es de nivel «self»: la sella quien la hace, no hay verificación que coger", code="verify.not_needed"
        )
    if agent == t["owner"]:
        raise TicketError(f"el verificador no puede ser el owner ({agent})", code="verify.owner_cannot_verify")
    cur = conn.execute(
        "UPDATE tickets SET verify_claimed_by=?, verify_claimed_at=? WHERE id=?"
        " AND (verify_claimed_by IS NULL OR verify_claimed_by=?)",
        (agent, _now(), ticket_id, agent),
    )
    if cur.rowcount == 0:
        conn.rollback()
        quien = conn.execute("SELECT verify_claimed_by FROM tickets WHERE id=?", (ticket_id,)).fetchone()[0]
        raise TicketError(f"ya la tiene {quien}", code="verify.already_claimed")
    if t["verify_claimed_by"] != agent:
        _append_history(conn, ticket_id, t["status"], t["status"], agent, f"[verificación] la coge {agent}")
    conn.commit()
    return _as_dict(conn, _get(conn, ticket_id))


def release_verification(conn, agent, ticket_id) -> dict:
    """#481: soltar una verificación cogida. Solo quien la tiene, o el dueño de la casa."""
    _validate_agent(agent)
    t = _get(conn, ticket_id)
    if t["verify_claimed_by"] is None:
        return _as_dict(conn, t)
    if agent != t["verify_claimed_by"] and agent != HOUSE_OWNER:
        raise TicketError(
            f"la tiene {t['verify_claimed_by']}: solo la suelta quien la tiene o el dueño", code="verify.not_claimer"
        )
    conn.execute("UPDATE tickets SET verify_claimed_by=NULL, verify_claimed_at=NULL WHERE id=?", (ticket_id,))
    _append_history(
        conn, ticket_id, t["status"], t["status"], agent, f"[verificación] la suelta {t['verify_claimed_by']}"
    )
    conn.commit()
    return _as_dict(conn, _get(conn, ticket_id))


def defer_to_peer(conn, agent, ticket_id, note) -> dict:
    """#451: el juez dice «incierto» de un ticket de nivel auto. Sigue done, pasa a
    verificación de par (un bot ya no lo sella) y el motivo queda en el historial."""
    _validate_agent(agent, allow_system=True)
    t = _get(conn, ticket_id)
    if t["status"] != "done" or t["verification_level"] != "auto":
        raise TicketError(
            f"solo un ticket done de nivel auto pasa a par (está {t['status']}/{t['verification_level']})"
        )
    conn.execute("UPDATE tickets SET verification_level='peer' WHERE id=?", (ticket_id,))
    _append_history(
        conn, ticket_id, "done", "done", agent, f"verificador incierto: {note} — pasa a verificación de par"
    )
    conn.commit()
    return _as_dict(conn, _get(conn, ticket_id))


EXTERNAL_NO_MAJORITY = "verificación externa sin mayoría"


def defer_external(conn, agent, ticket_id, note) -> dict:
    """#465: DeepSeek ×3 sin mayoría en una tarea «externa». Sigue done y externa (ningún Claude la
    sella); la decide el dueño de la casa. El verificador no la vuelve a juzgar hasta que alguien la
    devuelva y se complete otra vez (external_deferred)."""
    _validate_agent(agent, allow_system=True)
    t = _get(conn, ticket_id)
    if t["status"] != "done" or t["verification_level"] != "external":
        raise TicketError(f"solo un ticket done de nivel external (está {t['status']}/{t['verification_level']})")
    _append_history(conn, ticket_id, "done", "done", agent, f"{EXTERNAL_NO_MAJORITY}: {note} — la decide {HOUSE_OWNER}")
    conn.commit()
    return _as_dict(conn, _get(conn, ticket_id))


def external_deferred(conn, ticket_id) -> bool:
    last = conn.execute(
        "SELECT changed_by, new_status, note FROM ticket_history WHERE ticket_id=? ORDER BY id DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()
    return bool(
        last
        and last["changed_by"] == "verifier-bot"
        and last["new_status"] == "done"
        and (last["note"] or "").startswith(EXTERNAL_NO_MAJORITY)
    )


def reopen_ticket(conn, agent, ticket_id, note="") -> dict:
    """Retroceso del kanban: done → in_progress (el dueño decide que lo 'hecho' no
    valía). No cierra nada, no salta evidencia. Las verificadas quedan cerradas."""
    return _transition(
        conn, agent, ticket_id, from_={"done"}, to="in_progress", note=note or "reabierto desde el tablero"
    )


def rework_ticket(conn, agent, ticket_id, to, reason) -> dict:
    """Devolver una tarea HECHA o VERIFICADA a aceptadas/en curso
    con motivo obligatorio. La historia no se pierde: el history conserva done/
    verified y añade la fila del rechazo con su nota; la evidencia se queda en el
    ticket. `verified` deja de ser terminal SOLO por esta vía explícita.
    Permitido a: owner, verificador anterior, dueño. Recurrentes: no aplica."""
    _validate_agent(agent)
    if to not in {"accepted", "in_progress"}:
        raise TicketError(
            f"rework: destino inválido {to!r} (accepted|in_progress)", field="to", code="rework.to_invalid"
        )
    reason = (reason or "").strip()
    if len(reason) < REWORK_MIN_REASON:
        raise TicketError(
            f"rework requiere un motivo de al menos {REWORK_MIN_REASON} caracteres"
            " (por qué se devuelve — esa historia vale)",
            field="reason",
            code="rework.reason_required",
        )
    _expire_lazy(conn)
    t = _get(conn, ticket_id)
    if t["status"] not in {"done", "verified"}:
        raise TicketError(f"rework ilegal desde {t['status']!r} (requiere done|verified)", code="transition.illegal")
    if t["cadence_days"]:
        raise TicketError("las recurrentes no se devuelven: estampan y siguen", code="rework.recurring")
    allowed = agent == HOUSE_OWNER or agent == t["owner"] or (t["verified_by"] and agent == t["verified_by"])
    if not allowed:
        raise TicketError(
            f"rework solo puede hacerlo el owner ({t['owner']}), quien la verificó"
            f" ({t['verified_by'] or '-'}) o {HOUSE_OWNER}, no {agent}"
        )
    prev = t["status"]
    conn.execute("UPDATE tickets SET status=?, closed_at=NULL, verified_by=NULL WHERE id=?", (to, ticket_id))
    _append_history(conn, ticket_id, prev, to, agent, f"[rechazo] {reason}")
    # #364 (Pepe, 30-sep): tampoco aquí se coloca sola. Una devuelta sin fecha no
    # desaparece (T1.3): sale en «por ubicar» de su dueño, que es quien la recoloca.
    anchored = False
    for hook in REWORK_HOOKS:
        hook(conn, ticket_id, prev)
    conn.commit()
    return {**_as_dict(conn, _get(conn, ticket_id)), "anchored": anchored}


def backlog_ticket(conn, agent, ticket_id, note="") -> dict:
    """Retroceso del kanban: in_progress → accepted. Desde el #482 es un ALIAS de pause_ticket, con sus
    reglas (nota de al menos 10 caracteres; la dueña o el dueño de la casa): antes hacía lo mismo sin
    nota y a nombre de cualquiera, y era una puerta trasera a la regla (lo vio Lienzo el 16-oct)."""
    return pause_ticket(conn, agent, ticket_id, note)


def _transition(conn, agent, ticket_id, from_, to, note=None, close=False) -> dict:
    _validate_agent(agent, allow_system=True)
    _expire_lazy(conn)
    t = _get(conn, ticket_id)
    if t["status"] not in from_:
        raise TicketError(f"{to} ilegal desde {t['status']!r} (requiere {'|'.join(sorted(from_))})")
    if close:
        conn.execute("UPDATE tickets SET status=?, closed_at=? WHERE id=?", (to, _now(), ticket_id))
    else:
        conn.execute("UPDATE tickets SET status=? WHERE id=?", (to, ticket_id))
    _append_history(conn, ticket_id, t["status"], to, agent, note)
    conn.commit()
    return _as_dict(conn, _get(conn, ticket_id))


# ------------------------------------------------------------------ runs

# v3: el dominio de jobs/runs vive en jobs.py. Estos wrappers mantienen la
# superficie v1 (API del Tablero, tests) sin duplicar lógica.


def get_job(conn, job_id) -> dict:
    from . import jobs as _jobs

    return _jobs.get_job(conn, job_id)


def create_run(conn, job_id) -> dict:
    from . import jobs as _jobs

    return _jobs.create_run(conn, job_id)


def finish_run(
    conn, run_id, status, exit_code=None, cost_usd=None, session_id=None, output_path=None, result_summary=None
) -> dict:
    from . import jobs as _jobs

    return _jobs.finish_run(
        conn,
        run_id,
        status,
        exit_code=exit_code,
        cost_usd=cost_usd,
        session_id=session_id,
        output_path=output_path,
        result_summary=result_summary,
    )


def list_runs(conn, ticket_id=None, status=None, limit=50, job_id=None) -> list[dict]:
    from . import jobs as _jobs

    return _jobs.list_runs(conn, job_id=job_id, ticket_id=ticket_id, status=status, limit=limit)


def get_run(conn, run_id) -> dict:
    row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise TicketError(f"run #{run_id} no existe")
    return dict(row)


def recover_orphan_runs(conn) -> int:
    """Al arrancar el daemon: los runs 'running' son huérfanos (su proceso murió
    con el daemon anterior). Se cierran como error y el ticket se desbloquea a
    blocked con motivo — nunca un run zombi eterno (lección L34: el registro no
    es el estado)."""
    orphans = conn.execute("SELECT id FROM runs WHERE status='running'").fetchall()
    for row in orphans:
        finish_run(conn, row["id"], "error", result_summary="(huérfano: el daemon se reinició con el run en curso)")
    return len(orphans)


# ------------------------------------------------------------------ lecturas


def list_tickets(
    conn, status=None, owner=None, query=None, include_closed=False, project_id=None, kind=None, workflow_id=None
) -> list[dict]:
    where, params = [], []
    if query:
        terms = " OR ".join(f'"{w}"' for w in set(query.split()) if len(w) > 2)
        ids = [
            r["rowid"]
            for r in conn.execute("SELECT rowid FROM tickets_fts WHERE tickets_fts MATCH ? LIMIT 50", (terms,))
        ]
        if not ids:
            return []
        where.append(f"id IN ({','.join('?' * len(ids))})")
        params += ids
    if status:
        where.append("status=?")
        params.append(status)
    if owner:
        where.append("owner=?")
        params.append(owner)
    if project_id:
        where.append("project_id=?")
        params.append(project_id)
    if kind:
        where.append("kind=?")
        params.append(kind)
    if workflow_id:
        where.append("workflow_id=?")
        params.append(workflow_id)
    if not include_closed and not (status and status in CLOSED_STATUSES):
        where.append("closed_at IS NULL")
    sql = "SELECT * FROM tickets"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE priority WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END, id"
    enriched = _batch_enrich(conn, conn.execute(sql, params).fetchall())
    if not include_closed and not (status and status in CLOSED_STATUSES):
        enriched = [t for t in enriched if t["status"] not in CLOSED_STATUSES]
    return enriched


UPDATABLE_FIELDS = {
    "title",
    "description",
    "priority",
    "due_at",
    "verify_criteria",
    "project_id",
    "owner",
    "evidence",
    "verification_level",
} | TEMPORAL_FIELDS


def _validate_level_change(t, agent, nivel) -> None:
    """#414 (decisión 48): subir el nivel lo hace cualquiera de la casa; BAJARLO a self, solo el
    dueño (si no, quien hizo el trabajo podría cambiarlo y sellárselo)."""
    if nivel not in VERIFICATION_LEVELS:
        raise TicketError(
            f"verification_level inválido: {nivel!r} (válidos: {sorted(VERIFICATION_LEVELS)})",
            field="verification_level",
            code="ticket.level_invalid",
        )
    if t["closed_at"] is not None:
        raise TicketError(
            f"ticket #{t['id']} está cerrado ({t['status']}) — su nivel ya no cambia",
            field="verification_level",
            code="ticket.level_closed",
        )
    if t["cadence_days"] or t["kind"] == "agentic":
        raise TicketError(
            "las recurrentes y las agénticas no cambian de nivel (se verifican de otra forma)",
            field="verification_level",
            code="ticket.level_fixed",
        )
    if nivel == "self" and t["verification_level"] != "self" and agent != HOUSE_OWNER:
        raise TicketError(
            "bajar el nivel a «self» solo lo hace el dueño de la casa",
            field="verification_level",
            code="ticket.level_to_self_owner_only",
        )


def update_ticket(conn, agent, ticket_id, **fields) -> dict:
    """Edición de campos de un ticket. Review Prima F8: los tickets de workflow
    solo los edita la familia/coordinador — los peers operan transiciones y
    findings, no reescriben tareas."""
    t = _get(conn, ticket_id)
    _validate_agent(agent, family_only=bool(t["workflow_id"]))
    unknown = set(fields) - UPDATABLE_FIELDS
    if unknown:
        raise TicketError(f"campos no editables: {sorted(unknown)}")
    if not fields:
        return _as_dict(conn, t)
    if t["closed_at"] is not None and set(fields) & TEMPORAL_FIELDS:
        raise TicketError(f"ticket #{ticket_id} está cerrado ({t['status']}) — no se reprograma")
    if "priority" in fields and fields["priority"] not in PRIORITIES:
        raise TicketError(f"priority inválida: {fields['priority']!r}")
    if "verification_level" in fields:
        _validate_level_change(t, agent, fields["verification_level"])
    if "verify_criteria" in fields:
        _criterio_en_piezas(fields["verify_criteria"])
    if "owner" in fields and fields["owner"] is not None:
        _validate_agent(fields["owner"])
    if "project_id" in fields:
        _validate_project(conn, fields["project_id"])
    if "evidence" in fields:
        # 14-sep (Lienzo): la evidencia se puede reescribir cuando queda obsoleta —
        # el texto estampado sigue íntegro en el note del complete (history append-only).
        if not t["evidence_type"]:
            raise TicketError(f"ticket #{ticket_id} no tiene evidencia que reescribir (sin estampar)", field="evidence")
        if not (fields["evidence"] or "").strip():
            raise TicketError("evidence vacía", field="evidence")
    wd = fields.get("weekday", t["weekday"])
    wd = int(wd) if isinstance(wd, (int, str)) and str(wd).strip().lstrip("-").isdigit() else None
    fields = {**fields, **_validate_temporal(fields, t["cadence_days"], wd)}
    if t["weekday"] is None and fields.get("weekday") is None:
        # el override NO cuenta: mover una ocurrencia no es colocar la serie
        wd = _weekday_de_su_fecha(t["cadence_days"], fields.get("scheduled_at"), fields.get("due_at"))
        if wd is not None:
            fields["weekday"] = wd
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE tickets SET {sets} WHERE id=?", [*fields.values(), ticket_id])
    if "title" in fields or "description" in fields:
        fresh = _get(conn, ticket_id)
        conn.execute(
            "UPDATE tickets_fts SET title=?, description=? WHERE rowid=?",
            (fresh["title"], fresh["description"], ticket_id),
        )
    _append_history(conn, ticket_id, t["status"], t["status"], agent, f"editado: {', '.join(sorted(fields))}")
    conn.commit()
    return _as_dict(conn, _get(conn, ticket_id))


def ticket_history(conn, ticket_id) -> dict:
    t = _get(conn, ticket_id)
    rows = conn.execute(
        "SELECT old_status, new_status, changed_by, changed_at, note, provenance"
        " FROM ticket_history WHERE ticket_id=? ORDER BY id",
        (ticket_id,),
    ).fetchall()
    return {"ticket": _as_dict(conn, t), "history": [dict(r) for r in rows]}


def board(conn, project_id=None, view="all") -> dict:
    """Tablero completo para la UI (SPEC §5): columnas por estado + laterales.
    T2.11: 'today' y 'week' miran la misma fecha que el calendario (scheduled_at
    si existe, si no due_at / next_due) y la ventana de Hoy es configurable
    (settings.today_window: overdue, due_today, in_progress)."""
    from . import settings as _settings

    open_tickets = list_tickets(conn, project_id=project_id)
    today = date.today()
    horizon = today + timedelta(days=7)
    window = _settings.get(conn, "today_window")

    today_iso = today.isoformat()
    horizon_iso = horizon.isoformat()

    def _when(t):
        """Fecha que manda: próxima recurrente > programada > vencimiento. Mismo
        orden que agenda.occurrences (bug 6, el dueño 12-sep: el calendario es la
        referencia) — antes una recurrente con scheduled_at se colocaba en la
        fecha programada y no en su next_due, y desaparecía de Hoy."""
        if t.get("cadence_days"):
            return t["next_due"]
        if t.get("scheduled_at"):
            return t["scheduled_at"][:10]
        return t["due_at"][:10] if t.get("due_at") else None

    def _in_view(t) -> bool:
        """today = triaje del día (lo sensible al tiempo); week = + próximos 7
        días; future = programado más allá; all = todo abierto. El backlog sin
        fecha vive en 'Todas', no ensucia today/week (fix B1: week ya no ≈ all)."""
        if view == "all":
            return True
        if t["status"] == "proposed":
            # #604 (Pepe, 16-oct): «las que propongáis deberían de salir siempre». Una propuesta
            # espera una decisión, no una fecha: si caía la semana siguiente, en Hoy/Semana no salía
            # y no se podía aceptar hasta navegar a su semana.
            return True
        if t["status"] == "done" and view in ("today", "week"):
            # v3.1 (el dueño §1.2, reproducido por Lienzo): una HECHA espera verificación —
            # es tan sensible al tiempo como una en curso. Sin esto, una done sin
            # fecha desaparecía de Hoy/Semana al arrastrarla (solo vivía en Todas).
            return True
        when = _when(t)
        if view == "today":
            if t["status"] == "in_progress" and window.get("in_progress", True):
                return True
            if when is None:
                return False
            if when < today_iso:
                return bool(window.get("overdue", True))
            if when == today_iso:
                return bool(window.get("due_today", True))
            return False
        if t["status"] == "in_progress" and view == "week":
            return True  # trabajo en curso = sensible al tiempo
        if when is None:
            return False
        if view == "week":
            return when <= horizon_iso
        if view == "future":
            return when > horizon_iso
        return False

    visible = [t for t in open_tickets if _in_view(t)]
    columns = {s: [] for s in ("proposed", "accepted", "in_progress", "done")}
    laterals = {
        "blocked": [],
        # #364: lo que su dueño aún no ha colocado; sale en todas las vistas hasta que lo ubique
        "por_ubicar": [t for t in open_tickets if _unplaced(t) and t["status"] != "blocked"],
    }
    for t in visible:
        if t["status"] == "blocked":
            laterals["blocked"].append(t)
        elif t["status"] in columns:
            columns[t["status"]].append(t)

    # Columna verificadas: respeta la vista (fix B2: en 'today' no arrastra
    # verificadas viejas como ruido). today = cerradas hoy; week = últimos 7 días;
    # all/future = las 30 más recientes.
    verified_all = list_tickets(conn, status="verified", project_id=project_id)
    if view == "today":
        verified_col = [t for t in verified_all if (t.get("closed_at") or "")[:10] == today_iso]
    elif view == "week":
        verified_col = [
            t for t in verified_all if (t.get("closed_at") or "")[:10] >= (today - timedelta(days=7)).isoformat()
        ]
    else:
        verified_col = verified_all[:30]

    # #388: ¿ha tenido la base alguna vez una tarea del tablero? Con la vista vacía, la UI
    # distingue «acabas de llegar» (cómo entra la primera tarea) de «cambia de vista».
    # Cualquier estado y cualquier proyecto; las agénticas viven en Agentes y no cuentan.
    # Si ya hay alguna a la vista no hace falta preguntar: la consulta extra solo corre
    # cuando lo cargado está vacío (test_board_query_count sigue en 8).
    started = (
        any(t.get("kind") != "agentic" for t in (*open_tickets, *verified_all))
        or conn.execute("SELECT EXISTS(SELECT 1 FROM tickets WHERE kind != 'agentic')").fetchone()[0] == 1
    )

    return {
        "view": view,
        "project_id": project_id,
        "started": started,
        "projects": list_projects(conn),
        "columns": {**columns, "verified": verified_col},
        "laterals": laterals,
        "counts": {
            **{k: len(v) for k, v in columns.items()},
            "verified": len(verified_col),
            "blocked": len(laterals["blocked"]),
            "por_ubicar": len(laterals["por_ubicar"]),
        },
    }


_SLIM_KEYS = (
    "id",
    "title",
    "status",
    "priority",
    "owner",
    "kind",
    "project_id",
    "source",
    "created_by",
    "created_at",
    "scheduled_at",
    "duration_min",
    "due_at",
    "due_state",
    "cadence_days",
    "preferred_time",
    "weekday",
    "next_due",
    "due",
    "overdue",
    "verification_level",
    "verified_by",
    "verify_claimed_by",
    "blocked_reason",
    "last_change",
    "workflow_id",
    "plan_key",
    "age_days",
)


def _slim(t: dict) -> dict:
    """#367: lo que hace falta para decidir qué toca + un recorte del cuerpo. El cuerpo
    entero lo sirve get_ticket (o my_board(full=True))."""
    out = {k: t[k] for k in _SLIM_KEYS if k in t}
    body = " ".join((t.get("description") or "").split())
    out["resumen"] = body[:200] + ("…" if len(body) > 200 else "")
    return out


def my_board(conn, agent, full: bool = False) -> dict:
    _validate_agent(agent)
    b = _my_board_full(conn, agent)
    if full:
        return b
    return {k: [_slim(t) for t in v] for k, v in b.items()}


def _my_board_full(conn, agent) -> dict:
    mine = list_tickets(conn, owner=agent)
    return {
        "mine_open": [t for t in mine if not t.get("cadence_days")],
        "due_recurrentes": [t for t in mine if t.get("cadence_days") and t["due"]],
        "recurrentes_al_dia": [t for t in mine if t.get("cadence_days") and not t["due"]],
        "needs_my_verification": [
            t
            for t in list_tickets(conn, status="done")
            if t["verification_level"] == "peer" and t["owner"] != agent and t.get("verify_claimed_by") in (None, agent)
        ],
        # #481: las que me he cogido para verificar (abiertas, hechas o todavía no)
        "verifications_i_claimed": [
            t for t in list_tickets(conn) if t.get("verify_claimed_by") == agent and t["status"] not in CLOSED_STATUSES
        ],
        "my_proposals_pending": [t for t in list_tickets(conn, status="proposed") if t["created_by"] == agent],
        # #364: lo mío sin fecha. Colocarlo es trabajo mío, no de quien lo creó ni de Pepe.
        "por_ubicar": [t for t in mine if _unplaced(t)],
    }


def _unplaced(t: dict) -> bool:
    """#364: tarea abierta que nadie ha colocado todavía en el tiempo."""
    if t["status"] in CLOSED_STATUSES or t.get("kind") != "manual":
        return False
    if t.get("cadence_days"):
        return not t.get("preferred_time")
    return not (t.get("scheduled_at") or t.get("due_at"))
