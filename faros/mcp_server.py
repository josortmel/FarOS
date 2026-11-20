"""MCP for FarOS — a THIN client of the daemon (SPEC §6).

Every tool calls the SAME HTTP endpoint the UI uses: parity by construction.
This process does NOT open the DB (single writer: the daemon). If the daemon is
down, the error says so plainly.

Registering it in a session (.mcp.json / settings):
  {"command": "python", "args": ["-m", "faros.mcp_server"],
   "cwd": "<path-to>/FarOS"}
"""

from __future__ import annotations

import sys
from pathlib import Path

# Launched as a script (python faros/mcp_server.py, user-scope registration),
# Python puts faros/ at sys.path[0], and any module of ours whose name matches
# the stdlib shadows it (measured 2-Sep: calendar.py broke `import httpx`). We
# drop the script's own directory and guarantee the repo root instead.
_HERE = Path(__file__).resolve().parent
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != _HERE]
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

import json  # noqa: E402

import httpx  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

from . import env as _env  # noqa: E402  (los tres, después de arreglar sys.path: ver arriba)

# #339: FastMCP valida los argumentos de cada tool con un modelo pydantic que IGNORA lo que
# sobra, así que update_ticket(blocked_reason=…) devolvía 200 con el ticket sin tocar. Con
# extra="forbid", un argumento que la tool no declara falla nombrándolo. Tiene que ir ANTES de
# registrar las tools: cada modelo de argumentos hereda esta configuración al crearse.
# ArgModelBase es interno de mcp 1.x (pyproject: mcp>=1.27,<2). Si una versión lo mueve, NO se
# tumba el servidor: se avisa por stderr (stdout es el protocolo) y se vuelve al comportamiento
# de antes. Lo caza test_339 (todas las tools registradas tienen que prohibir lo que sobra).
try:
    from mcp.server.fastmcp.utilities import func_metadata as _fm

    _fm.ArgModelBase.model_config["extra"] = "forbid"
except (ImportError, AttributeError, TypeError) as _exc:
    print(
        f"[faros] AVISO #339: no se pudo prohibir argumentos desconocidos ({_exc!r}); "
        "FastMCP los descartará en silencio",
        file=sys.stderr,
    )

BASE_URL = _env.get("URL", "http://127.0.0.1:8756")
# The SAME house as the daemon, resolved by env.data_dir(). It used to hardcode
# its own path with "AgenticOS": on a NEW install the daemon writes the token to
# FarOS/token and this looked for it in AgenticOS/token -> EVERY tool returns
# 401. Prima found it while reviewing the rename.
TOKEN_FILE = _env.data_dir() / "token"

# The tool prefix comes from the KEY in the client's config (~/.claude.json), not from this name:
# measured on 28-oct and again on 4-nov, key FarOS with no variable gives mcp__FarOS__*. The name
# only makes the server call itself like its key; FAROS_MCP_NAME still overrides it.
mcp = FastMCP(_env.get("MCP_NAME", "FarOS"))


def _call(method: str, path: str, **kwargs):
    try:
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return {"error": "daemon_token_missing", "reason": f"no {TOKEN_FILE} — has the FarOS daemon started?"}
    try:
        # #303 (raised by Eco): the client used to send ONLY the old header. Here
        # you can't just pick one, because the client and the daemon do NOT update
        # at the same time:
        #   · sending only the NEW one breaks against any daemon older than today
        #     —including the one running in this house right now—;
        #   · sending only the OLD one leaves yesterday's brand on the wire and
        #     relies on the daemon's fallback never being removed.
        # We send BOTH. The new daemon reads the new one (it takes priority) and
        # the old daemon reads the one it understands. It costs one header and
        # removes the whole incompatibility window.
        r = httpx.request(
            method,
            f"{BASE_URL}{path}",
            headers={"X-FarOS-Token": token, "X-AgenticOS-Token": token},
            timeout=30,
            **kwargs,
        )
    except httpx.ConnectError:
        return {
            "error": "daemon_down",
            "reason": f"FarOS daemon not responding at {BASE_URL} — start it (python -m faros.daemon) or open the app",
        }
    try:
        body = r.json()
    except json.JSONDecodeError:
        body = {"error": "bad_response", "reason": r.text[:500]}
    if r.status_code >= 400 and "error" not in body:
        body = {"error": f"http_{r.status_code}", "reason": str(body)[:500]}
    if isinstance(body, dict) and body.get("code") == "route.not_found":
        body = _otra_version(body)
    return body


def _otra_version(body: dict) -> dict:
    """#1254: el MCP sale del código y el daemon de la última versión instalada. Una
    herramienta nueva llega antes que su ruta: decirlo con las dos versiones."""
    from . import __version__

    try:
        daemon = httpx.request("GET", f"{BASE_URL}/api/health", timeout=5).json()["version"]["daemon"]
    except Exception:
        daemon = "?"
    return {
        **body,
        "daemon_version": daemon,
        "mcp_version": __version__,
        "reason": (
            f"esta herramienta es de una versión de FarOS que todavía no está instalada: "
            f"el daemon es la {daemon} y el MCP la {__version__}. Se arregla instalando "
            f"la versión nueva, no cambiando la llamada."
        ),
    }


@mcp.tool()
def board(project_id: int | None = None, view: str = "all") -> dict:
    """Full board: columns by status, side panels, counters.
    view: all | today (the morning screen) | week | future."""
    params = {"view": view}
    if project_id:
        params["project_id"] = project_id
    return _call("GET", "/api/board", params=params)


@mcp.tool()
def my_board(agent: str, full: bool = False, limit: int = 30) -> dict:
    """Your personal board: open tasks, recurring due, verification queue, proposals,
    and «por_ubicar» (yours without a date — place them). Slim by default (Pepe, 4-nov:
    it no longer fit in one answer): each ticket is id, title, status, priority, time and
    owner; workable ones first, by time; limit caps each list and «notes» says what's left.
    Read one ticket with get_ticket, or pass full=True for everything."""
    board = _call("GET", "/api/my_board", params={"agent": agent, "full": full})
    if full or not isinstance(board, dict):
        return board  # un error del servidor pasa tal cual

    def order(t):
        parked = t.get("status") in ("blocked", "done", "verified")
        return (parked, not t.get("scheduled_at"), t.get("scheduled_at") or "", t.get("id") or 0)

    keep = ("id", "title", "status", "priority", "scheduled_at", "next_due", "owner", "due_state", "preferred_time")
    out, notes = {}, {}
    for section, tickets in board.items():
        if not isinstance(tickets, list):
            out[section] = tickets
            continue
        shown = sorted(tickets, key=order)[: max(1, limit)]
        out[section] = [{k: t.get(k) for k in keep} for t in shown]
        if len(tickets) > len(shown):
            notes[section] = f"{len(tickets) - len(shown)} más: sube limit o usa full=True."
    if notes:
        out["notes"] = notes
    return out


@mcp.tool()
def meta() -> dict:
    """Harnesses, models, levels and platform defaults."""
    return _call("GET", "/api/meta")


@mcp.tool()
def settings() -> dict:
    """App settings (secrets masked): casa_slots, verifier_model, telegram,
    api keys, today_window, backup_retention_days, verifier_enabled."""
    return _call("GET", "/api/settings")


@mcp.tool()
def patch_settings(agent: str, changes: dict) -> dict:
    """Change settings (household only). changes: {key: value}. A secret whose
    value is '••••' is left untouched; '' clears it."""
    return _call("PATCH", "/api/settings", json={"agent": agent, **changes})


@mcp.tool()
def silenciar_timbre(agent: str, hasta: str = "mañana", agente: str | None = None) -> dict:
    """Silence YOUR franja bell (the relay message FarOS sends at each task's start time), no permission
    needed. hasta: "1h", "3h", "mañana" (until 00:00 tomorrow), "siempre", an ISO datetime, or "" to turn
    it back on. While silenced nothing rings and nothing is saved for later. Only the household owner may
    pass another agente."""
    return _call("POST", "/api/timbre/silencio", json={"agent": agent, "agente": agente or agent, "hasta": hasta})


@mcp.tool()
def marcar_tiempo_libre(agent: str, ticket_id: int, libre: bool = True) -> dict:
    """Mark (or unmark, libre=False) one of YOUR tasks as free time: when it starts, the bell sends your
    free-time invitation instead of «Tenías planeado». Want a second nudge halfway through a free hour?
    Split the task in two (e.g. 15:00 and 15:30) and mark both. The household owner may mark anyone's."""
    return _call("POST", "/api/timbre/tiempo_libre", json={"agent": agent, "ticket_id": ticket_id, "libre": libre})


@mcp.tool()
def credentials() -> list | dict:
    """Claves (#460): the open list of external-service secrets (deepseek, anthropic,
    telegram, otra). Values never come back («••••»). Each one says whether FarOS uses it
    (in_use: one per kind, copied into its setting) and its last test."""
    return _call("GET", "/api/credentials")


@mcp.tool()
def credential_changes(limit: int = 50) -> list | dict:
    """Claves (#1078): who changed what in a secret and when (created, name, origin, value, in_use,
    deliverable, deleted), newest first. Never the value: a value change only says «cambiado».
    Each row carries the name, because ids are reused after a delete."""
    return _call("GET", "/api/credentials/changes", params={"limit": limit})


@mcp.tool()
def create_credential(
    agent: str, name: str, kind: str, value: str, origin: str = "", in_use: bool | None = None
) -> dict:
    """Add a secret (household only). kind: deepseek | anthropic | telegram | otra.
    Known kinds are TESTED first (free call) and NOT saved if the service rejects them
    (422 credential.test_failed). «otra» is saved untested; origin says where it comes from.
    in_use omitted: the first of its kind becomes the one FarOS uses."""
    return _call(
        "POST",
        "/api/credentials",
        json={"agent": agent, "name": name, "kind": kind, "value": value, "origin": origin, "in_use": in_use},
    )


@mcp.tool()
def update_credential(
    agent: str,
    credential_id: int,
    name: str | None = None,
    value: str | None = None,
    origin: str | None = None,
    in_use: bool | None = None,
) -> dict:
    """Edit a secret: name, origin, value (tested before saving) or in_use (true = FarOS
    uses this one for its kind; false = none). The kind can't change."""
    body = {"agent": agent}
    for k, v in (("name", name), ("value", value), ("origin", origin), ("in_use", in_use)):
        if v is not None:
            body[k] = v
    return _call("PATCH", f"/api/credentials/{credential_id}", json=body)


@mcp.tool()
def test_credential(agent: str, credential_id: int) -> dict:
    """Test a saved secret again against its service and record the result."""
    return _call("POST", f"/api/credentials/{credential_id}/test", json={"agent": agent})


@mcp.tool()
def delete_credential(agent: str, credential_id: int) -> dict:
    """Delete a secret. If FarOS was using it, its setting is left empty."""
    return _call("DELETE", f"/api/credentials/{credential_id}", params={"agent": agent})


@mcp.tool()
def harnesses(refresh: bool = False) -> list | dict:
    """Installed harnesses with billing (subscription|api|local), capabilities and
    the models each one declares (asked of the harness, not a hardcoded list)."""
    return _call("GET", "/api/harnesses", params={"refresh": refresh} if refresh else None)


@mcp.tool()
def harness_models(name: str, refresh: bool = False) -> dict:
    """Models of a harness (refresh=True asks the harness again)."""
    return _call("GET", f"/api/harnesses/{name}/models", params={"refresh": refresh} if refresh else None)


@mcp.tool()
def dispatch_batch(agent: str, workflow_id: int, items: list) -> dict:
    """Dispatch a BATCH of workflow tasks atomically (all or nothing).
    items: [{"ticket_id": int, "to": str, "note": str?}]. Notifying each peer
    still happens out of band, not through this call."""
    return _call("POST", f"/api/workflows/{workflow_id}/dispatch_batch", json={"agent": agent, "items": items})


@mcp.tool()
def workflow_documents(workflow_id: int) -> list | dict:
    """Workflow documents (spec, plan, reviews, measurements) with absolute path."""
    return _call("GET", f"/api/workflows/{workflow_id}/documents")


@mcp.tool()
def list_handoffs(workflow_id: int) -> list | dict:
    """All handoffs of the workflow (author and time), most recent first."""
    return _call("GET", f"/api/workflows/{workflow_id}/handoffs")


@mcp.tool()
def list_projects(include_archived: bool = False) -> list | dict:
    """List of projects."""
    return _call("GET", "/api/projects", params={"include_archived": include_archived})


@mcp.tool()
def create_project(agent: str, name: str, color: str | None = None) -> dict:
    """Create a project."""
    return _call("POST", "/api/projects", json={"agent": agent, "name": name, "color": color})


@mcp.tool()
def propose_ticket(
    agent: str,
    title: str,
    description: str = "",
    owner: str | None = None,
    source: str = "proposed",
    priority: str = "media",
    verification_level: str = "self",
    project_id: int | None = None,
    due_at: str | None = None,
    verify_criteria: str | None = None,
    expires_at: str | None = None,
    cadence_days: int | None = None,
    scheduled_at: str | None = None,
    duration_min: int | None = None,
    preferred_time: str | None = None,
    weekday: int | None = None,
) -> dict:
    """Propose a manual ticket (born 'proposed'; the owner accepts it at the
    decision gate). Returns the ticket + possible duplicates (FTS5). Temporal
    fields: scheduled_at 'YYYY-MM-DDTHH:MM', duration_min, preferred_time 'HH:MM'
    (recurring), weekday 0=Monday…6 (v3.2: weekly recurring anchored to a day).
    verification_level: self | peer | auto (Haiku judge) | external (#465: judged by
    DeepSeek, majority of 3 runs; only DeepSeek or the household owner can seal it).
    #364: nothing is auto-scheduled. A ticket without a date is born «por ubicar»
    and its OWNER places it (my_board → por_ubicar; board laterals.por_ubicar).
    When you own it, placing it is your job — pick the slot yourself.
    #1081: criterion_warnings lists what the verify_criteria breaks of the criteria rule
    ({punto, aviso}: suite without the exact command, «fixed» without red on the parent,
    something a judge can't touch…). It never blocks: fix it with update_ticket if it applies."""
    return _call("POST", "/api/tickets", json={k: v for k, v in locals().items() if v is not None})


@mcp.tool()
def create_agent_task(
    agent: str,
    title: str,
    prompt: str,
    model: str = "haiku",
    description: str = "",
    harness: str = "claude-cli",
    schedule: dict | None = None,
    verification_level: str = "auto",
    project_id: int | None = None,
    verify_criteria: str | None = None,
    permission_mode: str = "dontAsk",
    allowed_tools: str | None = None,
    mcp_config: str | None = None,
    max_budget_usd: float | None = None,
    timeout_s: int = 1800,
) -> dict:
    """Create an AGENTIC task (run by a generic headless agent).
    schedule: {"type":"manual"} | {"type":"once","run_at":"ISO"} |
    {"type":"recurring","every_days":N,"at":"HH:MM"} (recurring requires level self)."""
    body = {
        "agent": agent,
        "title": title,
        "description": description,
        "kind": "agentic",
        "verification_level": verification_level,
        "project_id": project_id,
        "verify_criteria": verify_criteria,
        "job": {
            "prompt": prompt,
            "model": model,
            "harness": harness,
            "schedule": schedule or {"type": "manual"},
            "permission_mode": permission_mode,
            "allowed_tools": allowed_tools,
            "mcp_config": mcp_config,
            "max_budget_usd": max_budget_usd,
            "timeout_s": timeout_s,
        },
    }
    return _call("POST", "/api/tickets", json=body)


@mcp.tool()
def list_tickets(
    status: str | None = None,
    owner: str | None = None,
    project_id: int | None = None,
    kind: str | None = None,
    query: str | None = None,
    include_closed: bool = False,
) -> list | dict:
    """List/search tickets (query = FTS5)."""
    params = {k: v for k, v in locals().items() if v is not None and v is not False}
    return _call("GET", "/api/tickets", params=params)


@mcp.tool()
def get_ticket(ticket_id: int) -> dict:
    """One ticket with its job and latest run if it is agentic."""
    return _call("GET", f"/api/tickets/{ticket_id}")


def _evidencia_texto(evidence):
    """#600 punto 5: un `proof` es un JSON, y FastMCP parsea a dict un argumento con pinta de JSON
    antes de validarlo, así que con `str` a secas ningún agente podía completar con proof por MCP
    («Input should be a valid string»). Se acepta el dict y se vuelve a texto: la API guarda texto."""
    if isinstance(evidence, dict):
        return json.dumps(evidence, ensure_ascii=False)
    return evidence


@mcp.tool()
def transition_ticket(
    agent: str,
    ticket_id: int,
    action: str,
    evidence_type: str | None = None,
    evidence: str | dict | None = None,
    verdict: str = "pass",
    reason: str | None = None,
    note: str = "",
    provenance: str | None = None,
) -> dict:
    """State transition: accept | start | block | complete | verify | reject | pause | claim | unclaim.
    claim (#481): put your name on a task's verification BEFORE saying it in the room; if
    someone has it already you get «ya la tiene X». unclaim releases it (only whoever has it).
    complete requires evidence_type+evidence. verify uses verdict (pass|fail) + note.
    block/reject require reason; block on an already blocked task rewrites its reason
    without unblocking it (#482). pause: in_progress → accepted when it wasn't really
    started, with a note of at least 10 chars; only the task owner or the household owner.
    #640: a task OWNED by the household owner can be started, completed (which seals a
    self-level one) and paused by anyone of the house; the history says «en nombre de …».
    provenance (#377), only on a verification (verify, or complete of a self-level task):
    'deterministic:<tool>' when a test/grep/script did the check. Leave it empty otherwise:
    FarOS records 'external' (the household owner) or 'same-family' (a Claude) by itself."""
    body = {
        "agent": agent,
        "action": action,
        "evidence_type": evidence_type,
        "evidence": _evidencia_texto(evidence),
        "verdict": verdict,
        "reason": reason,
        "note": note,
    }
    if provenance is not None:
        body["provenance"] = provenance
    return _call("POST", f"/api/tickets/{ticket_id}/transition", json=body)


@mcp.tool()
def rework_ticket(agent: str, ticket_id: int, reason: str, to: str = "accepted") -> dict:
    """v3.1: return a DONE or VERIFIED task to accepted|in_progress with a
    mandatory reason (>=10 chars). History is kept (history + evidence). Only the
    task owner, whoever verified it, or the household owner. Not for recurring."""
    return _call(
        "POST",
        f"/api/tickets/{ticket_id}/transition",
        json={"agent": agent, "action": "rework", "to": to, "reason": reason},
    )


CLEARABLE_FIELDS = {"scheduled_at", "next_due_override", "due_at", "duration_min", "preferred_time", "weekday"}
JOB_CLEARABLE_FIELDS = {"ticket_id"}  # #518


@mcp.tool()
def update_ticket(
    agent: str,
    ticket_id: int,
    title: str | None = None,
    description: str | None = None,
    priority: str | None = None,
    due_at: str | None = None,
    verify_criteria: str | None = None,
    project_id: int | None = None,
    owner: str | None = None,
    scheduled_at: str | None = None,
    duration_min: int | None = None,
    all_day: int | None = None,
    preferred_time: str | None = None,
    next_due_override: str | None = None,
    weekday: int | None = None,
    evidence: str | dict | None = None,
    verification_level: str | None = None,
    clear: list[str] | None = None,
) -> dict:
    """Edit ticket fields (same PATCH as the UI). verification_level (#414): self | peer |
    auto | external; anyone may raise it, only the household owner lowers it to self; not on
    closed, recurring or agentic tasks. Temporal fields (T0.3):
    scheduled_at 'YYYY-MM-DDTHH:MM', duration_min, all_day 0|1, preferred_time
    'HH:MM', next_due_override 'YYYY-MM-DD' (recurring only; Sat/Sun snaps to
    Monday), weekday 0=Monday…6 (v3.2, weekly recurring only). evidence:
    rewrites the evidence of an already-stamped ticket (the original stays in history).
    To CLEAR a field use clear=["scheduled_at", "next_due_override", ...]
    (3a, 14-Sep): the MCP client carries neither "" nor None, so clearing goes
    through an explicit list. Clearable fields: scheduled_at, next_due_override,
    due_at, duration_min, preferred_time, weekday."""
    body = {k: v for k, v in locals().items() if v is not None and k not in ("ticket_id", "clear")}
    if "evidence" in body:
        body["evidence"] = _evidencia_texto(body["evidence"])
    if clear:
        bad = set(clear) - CLEARABLE_FIELDS
        if bad:
            # #303 (spotted by Prima reading the 67): this is NOT a description,
            # it is what the tool RETURNS to an agent when it gets it wrong. It
            # used to say "campo no borrable" — in Spanish, and it also used the
            # `error` field as prose when everywhere else in the system `error` is
            # a STABLE CODE and `reason` is the explanation. An agent that branches
            # on `error` cannot branch on a sentence.
            return {
                "error": "invalid_request",
                "code": "ticket.field_not_clearable",
                "reason": f"not clearable: {', '.join(sorted(bad))}",
                "fields": sorted(bad),
                "clearable": sorted(CLEARABLE_FIELDS),
            }
        body.update({k: None for k in clear})
    return _call("PATCH", f"/api/tickets/{ticket_id}", json=body)


@mcp.tool()
def ticket_history(ticket_id: int) -> dict:
    """Full append-only timeline of the ticket."""
    return _call("GET", f"/api/tickets/{ticket_id}/history")


@mcp.tool()
def update_agent_task(
    agent: str,
    job_id: int,
    prompt: str | None = None,
    model: str | None = None,
    schedule: dict | None = None,
    enabled: bool | None = None,
    permission_mode: str | None = None,
    allowed_tools: str | None = None,
    mcp_config: str | None = None,
    max_budget_usd: float | None = None,
    timeout_s: int | None = None,
) -> dict:
    """Edit the job of an agentic task (prompt, model, schedule, enabled...)."""
    body = {k: v for k, v in locals().items() if v is not None and k != "job_id"}
    return _call("PATCH", f"/api/jobs/{job_id}", json=body)


# ------------------------------------------------------------------ calendario (v3)


@mcp.tool()
def calendar(from_date: str, to_date: str, agent: str | None = None, project_id: int | None = None) -> dict:
    """Board occurrences in a range (YYYY-MM-DD, inclusive): scheduled ones,
    due dates with no time, and projected recurring ones (ghost=true for future
    ones). Never includes agentic jobs."""
    params = {"from": from_date, "to": to_date}
    if agent:
        params["agent"] = agent
    if project_id:
        params["project_id"] = project_id
    return _call("GET", "/api/calendar", params=params)


@mcp.tool()
def schedule_ticket(
    agent: str,
    ticket_id: int,
    scheduled_at: str | None = None,
    duration_min: int | None = None,
    all_day: int | None = None,
    note: str = "",
) -> dict:
    """Schedule/move a task: scheduled_at 'YYYY-MM-DDTHH:MM' ("" = remove),
    duration_min, all_day 0|1. Readable history; 400 if illegal."""
    body = {k: v for k, v in locals().items() if v is not None and k != "ticket_id"}
    return _call("POST", f"/api/tickets/{ticket_id}/schedule", json=body)


@mcp.tool()
def shift_next_occurrence(agent: str, ticket_id: int, date: str, note: str = "") -> dict:
    """Move ONLY the next occurrence of a recurring task to `date` (YYYY-MM-DD;
    "" restores last_done+cadence). The cadence does not change."""
    return _call("POST", f"/api/tickets/{ticket_id}/shift_next", json={"agent": agent, "date": date, "note": note})


@mcp.tool()
def search(q: str) -> dict:
    """Global search: tickets (board/office), jobs (agents), decisions
    and workflows (office), max 10 per group, with `section` to navigate."""
    return _call("GET", "/api/search", params={"q": q})


# ------------------------------------------------------------------ decisiones (v3)


@mcp.tool()
def ask_decision(
    agent: str,
    title: str,
    options: list,
    context: str = "",
    workflow_id: int | None = None,
    project_id: int | None = None,
) -> dict:
    """Open a decision for the owner. options: [{"label": "...", "text": "the whole
    literal option"}, ...] — never A/B/C. Replaces the old file-based decisions."""
    body = {k: v for k, v in locals().items() if v is not None}
    return _call("POST", "/api/decisions", json=body)


@mcp.tool()
def list_decisions(
    status: str | None = None,
    workflow_id: int | None = None,
    project_id: int | None = None,
    ids: list[int] | None = None,
    limit: int = 15,
    full: bool = False,
) -> list | dict:
    """Decisions, slim by default (Pepe, 3-nov: the whole history in every call was madness).
    No status → only the OPEN ones (pending + deferred). status: pending | deferred | decided.
    Each comes with title, option labels, answer, rationale and dates; no context, option texts
    or history. limit caps how many (newest first). For the full body pass ids=[...] or full=True."""
    params = {
        k: v for k, v in (("status", status), ("workflow_id", workflow_id), ("project_id", project_id)) if v is not None
    }
    rows = _call("GET", "/api/decisions", params=params)
    if not isinstance(rows, list):
        return rows  # un error del servidor pasa tal cual
    if ids:
        wanted = set(ids)
        chosen = [d for d in rows if d.get("id") in wanted]
        return {"decisions": chosen, "total": len(chosen), "shown": len(chosen)}
    if status is None:
        rows = [d for d in rows if d.get("status") in ("pending", "deferred")]
    total = len(rows)
    rows = rows[: max(1, limit)]
    if not full:
        keep = (
            "id",
            "title",
            "status",
            "project_id",
            "workflow_id",
            "asked_by",
            "created_at",
            "decision",
            "rationale",
            "decided_by",
            "decided_at",
            "deferred_until",
        )
        rows = [
            {**{k: d.get(k) for k in keep}, "options": [o.get("label") for o in d.get("options") or []]} for d in rows
        ]
    out = {"decisions": rows, "total": total, "shown": len(rows)}
    if total > len(rows):
        out["note"] = f"{total - len(rows)} más: sube limit, filtra por project_id o pide ids."
    return out


@mcp.tool()
def decisiones_vigentes(tema: str, agente: str | None = None, incluir_superadas: bool = False, limit: int = 10) -> dict:
    """#919: the decisions that HOLD TODAY on a topic, from FarOS decisions AND EcoDB memories
    (decision/acuerdo). Use it BEFORE acting on something with decisions behind it. A superseded
    one is replaced by its successor («sustituye_a»); expired ones (valid_until past) are dropped.
    Each row says its «fuente» (FarOS ids are faros:decision:<n>) and its «rango» within that source: the two halves are
    interleaved by relevance, not by date. incluir_superadas=true to audit.
    If EcoDB is down or has no key, FarOS answers with its half and says so in «avisos»."""
    params = {"tema": tema, "incluir_superadas": str(bool(incluir_superadas)).lower(), "limit": limit}
    if agente:
        params["agente"] = agente
    return _call("GET", "/api/decisions/vigentes", params=params)


@mcp.tool()
def decide(
    agent: str,
    decision_id: int,
    rationale: str,
    option: str | None = None,
    decision: str | None = None,
    supersedes: list[int] | None = None,
    supersede_kind: str | None = None,
    supersede_reason: str | None = None,
    decidido_por: dict | None = None,
    valid_until: str | None = None,
) -> dict:
    """Decide (usually the owner): option = chosen label, or decision = free text.
    rationale is mandatory. It cannot be decided twice.
    #919: if this decision changes earlier ones, name them in supersedes=[ids]: they get marked
    «superseded by» this one (supersede_kind total|parcial|nunca_valida — the last one owner only;
    supersede_reason one line). decidido_por = who actually decided {tipo: humano|agente|equipo,
    quien}; valid_until = YYYY-MM-DD for something that only holds until a date."""
    body = {k: v for k, v in locals().items() if v is not None and k != "decision_id"}
    body["surface"] = "mcp"  # #657: decidido por MCP (ni escritorio ni móvil)
    return _call("POST", f"/api/decisions/{decision_id}/decide", json=body)


@mcp.tool()
def defer_decision(agent: str, decision_id: int, until: str | None = None, note: str = "") -> dict:
    """Defer a decision (until YYYY-MM-DD optional)."""
    body = {k: v for k, v in locals().items() if v is not None and k != "decision_id"}
    return _call("POST", f"/api/decisions/{decision_id}/defer", json=body)


# ------------------------------------------------------------------ Agentes (v3)


@mcp.tool()
def list_jobs(status: str | None = None, project_id: int | None = None, include_archived: bool = False) -> list | dict:
    """Agentic jobs (Agents section): active|paused|archived, with last run,
    verdict, next_fire and spend."""
    params = {k: v for k, v in locals().items() if v not in (None, False)}
    return _call("GET", "/api/jobs", params=params)


@mcp.tool()
def create_job(
    agent: str,
    name: str,
    prompt: str,
    harness: str = "claude-cli",
    model: str = "haiku",
    schedule: dict | None = None,
    ticket_id: int | None = None,
    project_id: int | None = None,
    verify_criteria: str | None = None,
    verify_level: str = "auto",
    permission_mode: str = "dontAsk",
    allowed_tools: str | None = None,
    inherit_mcp: list | None = None,
    mcp_config: str | None = None,
    strict_mcp: bool = False,
    add_dirs: list | None = None,
    effort: str | None = None,
    fallback_model: str | None = None,
    system_prompt: str | None = None,
    json_schema: dict | None = None,
    max_budget_usd: float | None = None,
    timeout_s: int = 1800,
) -> dict:
    """Create an agentic job (an entity of its own; ticket_id optional = the
    automatic arm of a manual Board task). schedule: {"type":"manual"} |
    {"type":"once","run_at":ISO} | {"type":"recurring","every_days":N,"at":["HH:MM",...]} |
    {"type":"weekly","weekdays":[0..6],"at":["HH:MM",...]}. inherit_mcp: names of
    MCP servers from the local session (auto allowlist). max_budget_usd only if billing=api."""
    body = {k: v for k, v in locals().items() if v is not None}
    return _call("POST", "/api/jobs", json=body)


@mcp.tool()
def get_job(job_id: int) -> dict:
    """One job with its last run, verdict, next_fire and totals."""
    return _call("GET", f"/api/jobs/{job_id}")


@mcp.tool()
def update_job(
    agent: str,
    job_id: int,
    name: str | None = None,
    harness: str | None = None,
    model: str | None = None,
    prompt: str | None = None,
    cwd: str | None = None,
    permission_mode: str | None = None,
    allowed_tools: str | None = None,
    mcp_config: str | None = None,
    inherit_mcp: list | None = None,
    mcp_servers: list | None = None,
    strict_mcp: bool | None = None,
    add_dirs: list | None = None,
    effort: str | None = None,
    fallback_model: str | None = None,
    system_prompt: str | None = None,
    json_schema: dict | None = None,
    max_budget_usd: float | None = None,
    timeout_s: int | None = None,
    schedule: dict | None = None,
    verify_level: str | None = None,
    verify_criteria: str | None = None,
    ticket_id: int | None = None,
    project_id: int | None = None,
    enabled: bool | None = None,
    clear: list[str] | None = None,
) -> dict:
    """Edit fields of a job; only the ones you pass change. schedule has the same
    shape as in create_job. Only the parameters listed here exist: any other
    argument is dropped before it reaches the daemon, silently if it travels with
    an editable one (a call with no editable field returns an error).
    To UNLINK the job from its task use clear=["ticket_id"] (#518: a job hanging
    from a closed task does not fire; get_job says so in not_firing)."""
    # #401: con **fields, FastMCP publicaba UN parámetro «fields» y el daemon lo rechazaba.
    body = {k: v for k, v in locals().items() if v is not None and k not in ("job_id", "clear")}
    if clear:
        bad = set(clear) - JOB_CLEARABLE_FIELDS
        if bad:
            return {
                "error": "invalid_request",
                "code": "job.field_not_clearable",
                "reason": f"not clearable: {', '.join(sorted(bad))}",
                "fields": sorted(bad),
                "clearable": sorted(JOB_CLEARABLE_FIELDS),
            }
        body.update({k: None for k in clear})
    # Un campo que no está en el esquema (no editable) FastMCP lo descarta callado: sin
    # este corte, el PATCH iría vacío, daría 200 y parecería que cambió algo.
    if len(body) == 1:
        return {
            "error": "invalid_request",
            "code": "request.invalid",
            "reason": "update_job sin ningún campo editable; los editables son los parámetros de esta herramienta",
        }
    return _call("PATCH", f"/api/jobs/{job_id}", json=body)


@mcp.tool()
def job_status(agent: str, job_id: int, action: str) -> dict:
    """pause | resume | archive | duplicate."""
    if action not in {"pause", "resume", "archive", "duplicate"}:
        return {"error": "invalid_request", "reason": "action: pause|resume|archive|duplicate"}
    return _call("POST", f"/api/jobs/{job_id}/{action}", json={"agent": agent})


@mcp.tool()
def agents_calendar(from_date: str, to_date: str) -> dict:
    """Agents calendar: upcoming job occurrences (once/recurring/weekly) and past runs
    with their verdict, between from_date and to_date (YYYY-MM-DD, 400 days max)."""
    return _call("GET", "/api/agents/calendar", params={"from": from_date, "to": to_date})


@mcp.tool()
def agents_schedule(days: int = 7) -> dict:
    """Upcoming runs of every active job — the Agents timeline."""
    return _call("GET", "/api/agents/schedule", params={"days": days})


@mcp.tool()
def agents_results() -> list | dict:
    """The morning read: every job with its last run and verdict."""
    return _call("GET", "/api/agents/results")


@mcp.tool()
def mcp_list(q: str | None = None) -> list | dict:
    """The app's own MCP server registry, searchable. Secrets come back masked."""
    return _call("GET", "/api/mcp", params={"q": q} if q else None)


@mcp.tool()
def mcp_upsert(
    agent: str,
    name: str,
    transport: str = "stdio",
    command: str | None = None,
    args: list[str] | None = None,
    url: str | None = None,
    env: dict | None = None,
    headers: dict | None = None,
    enabled: bool = True,
    note: str = "",
) -> dict:
    """Add or edit (by name) an MCP server in the registry. env/headers may carry
    secrets: stored in the database, masked on read, emptied in backups."""
    existing = _call("GET", "/api/mcp", params={"q": name})
    match = (
        next((x for x in existing if isinstance(existing, list) and x.get("name") == name), None)
        if isinstance(existing, list)
        else None
    )
    body = {
        "agent": agent,
        "name": name,
        "transport": transport,
        "command": command,
        "args": args or [],
        "url": url,
        "env": env or {},
        "headers": headers or {},
        "enabled": enabled,
        "note": note,
    }
    if match:
        return _call("PATCH", f"/api/mcp/{match['id']}", json=body)
    return _call("POST", "/api/mcp", json=body)


@mcp.tool()
def mcp_delete(agent: str, server_id: int) -> dict:
    """Delete a server from the registry. Fails if any job still uses it."""
    return _call("DELETE", f"/api/mcp/{server_id}", params={"agent": agent})


@mcp.tool()
def mcp_import_local(agent: str) -> dict:
    """Import into the registry the MCP servers of the local session
    (~/.claude.json, user scope)."""
    return _call("POST", "/api/mcp/import_local", json={"agent": agent})


@mcp.tool()
def mcp_inherited() -> list | dict:
    """MCP servers from the user's local session (~/.claude.json, user scope) that a
    job can inherit."""
    return _call("GET", "/api/mcp/inherited")


@mcp.tool()
def run_verdict(agent: str, run_id: int, verdict: str, reason: str) -> dict:
    """Manual verdict on an ok run that was never judged, or that the judge left
    'uncertain': pass|fail plus a reason. A pass on a linked job completes its ticket."""
    return _call("POST", f"/api/runs/{run_id}/verdict", json={"agent": agent, "verdict": verdict, "reason": reason})


@mcp.tool()
def verify_runs() -> dict:
    """Run the cheap judge now over every ok run that has no verdict yet."""
    return _call("POST", "/api/verify/runs")


@mcp.tool()
def launch_task(job_id: int) -> dict:
    """Launch a run of the job right now (the button). Refuses if a run is already in
    progress or the linked ticket is closed."""
    return _call("POST", f"/api/jobs/{job_id}/launch")


@mcp.tool()
def list_runs(ticket_id: int | None = None, status: str | None = None, limit: int = 50) -> list | dict:
    """Runs (agent executions), most recent first."""
    params = {k: v for k, v in locals().items() if v is not None}
    return _call("GET", "/api/runs", params=params)


@mcp.tool()
def get_run_output(run_id: int) -> dict:
    """Full output of a run — the result a person reads."""
    return _call("GET", f"/api/runs/{run_id}/output")


@mcp.tool()
def verify_sweep() -> dict:
    """Run the cheap verifier against the done queue at auto level."""
    return _call("POST", "/api/verify/sweep")


# ------------------------------------------------------------------ workflows (v2)


@mcp.tool()
def import_plan(agent: str, plan: dict, repo_root: str | None = None) -> dict:
    """Import a design Plan (CONTRATO_PLAN.md) into a workflow with its tasks and
    dependencies. Owner and agents only. Validates everything before writing, so a
    bad plan imports nothing rather than half of it.
    repo_root: absolute repo folder, so the app can open spec/plan/reviews."""
    return _call("POST", "/api/workflows/import", json={"agent": agent, "plan": plan, "repo_root": repo_root})


@mcp.tool()
def list_workflows(status: str | None = None, q: str | None = None) -> list | dict:
    """Workflows with overall progress and open findings. status: active|paused|closed|
    cancelled|archived|open|finished. q searches by name."""
    params = {k: v for k, v in (("status", status), ("q", q)) if v}
    return _call("GET", "/api/workflows", params=params or None)


@mcp.tool()
def workflow_status(workflow_id: int) -> dict:
    """WHERE WE ARE: tickets per phase (with ready/unmet_deps), progress, findings,
    dispatched work, milestones still to be saved to the memory store, the latest
    handoff and recent activity. This is the first thing a fresh session reads."""
    return _call("GET", f"/api/workflows/{workflow_id}")


@mcp.tool()
def close_workflow(
    agent: str, workflow_id: int, status: str = "closed", reason: str | None = None, repo_root: str | None = None
) -> dict:
    """Change the workflow's status (active|paused|closed|cancelled|archived) and/or its
    repo_root. Coordinator or owner only. closed creates the workflow_closed
    milestone; cancelled requires a reason and rejects the workflow's open tickets;
    archived is only reachable from closed|cancelled; to unarchive, set closed."""
    return _call(
        "PATCH",
        f"/api/workflows/{workflow_id}",
        json={"agent": agent, "status": status, "reason": reason, "repo_root": repo_root},
    )


@mcp.tool()
def archive_workflow(agent: str, workflow_id: int) -> dict:
    """Archive a closed or cancelled workflow (the Archived tab)."""
    return _call("PATCH", f"/api/workflows/{workflow_id}", json={"agent": agent, "status": "archived"})


@mcp.tool()
def unarchive_workflow(agent: str, workflow_id: int) -> dict:
    """Take a workflow out of Archived; it goes back to closed."""
    return _call("PATCH", f"/api/workflows/{workflow_id}", json={"agent": agent, "status": "closed"})


@mcp.tool()
def cancel_workflow(agent: str, workflow_id: int, reason: str) -> dict:
    """Cancel a workflow with a reason. Its open tickets become rejected, carrying that
    reason as their note."""
    return _call(
        "PATCH", f"/api/workflows/{workflow_id}", json={"agent": agent, "status": "cancelled", "reason": reason}
    )


@mcp.tool()
def dispatch_ticket(agent: str, ticket_id: int, to: str, note: str = "") -> dict:
    """Assign a task to a peer (code/verifier/adv-*/an agent). Coordinator or owner
    only. Requires the task's dependencies to be verified. THE PEER IS NOTIFIED OUT
    OF BAND — this only records the state."""
    return _call("POST", f"/api/tickets/{ticket_id}/dispatch", json={"agent": agent, "to": to, "note": note})


@mcp.tool()
def dispatchable_tickets(workflow_id: int) -> list | dict:
    """The coordinator's dispatchable queue: every task in the workflow whose
    dependencies are all verified, in one call."""
    return _call("GET", "/api/tickets", params={"workflow_id": workflow_id, "ready": "true"})


@mcp.tool()
def add_dep(agent: str, ticket_id: int, depends_on: int) -> dict:
    """Add a dependency between two tasks of the same workflow. Owner and agents only;
    refuses cycles and tasks already under way."""
    return _call("POST", f"/api/tickets/{ticket_id}/deps", json={"agent": agent, "depends_on": depends_on})


@mcp.tool()
def add_finding(
    agent: str,
    workflow_id: int,
    severity: str,
    title: str,
    detail: str = "",
    suggestion: str = "",
    category: str = "bug",
    ticket_id: int | None = None,
) -> dict:
    """Record a finding on the workflow's bug table. severity: critical|high|medium|low
    (how urgent). category: bug|gap|degradation|concern (what kind). suggestion
    travels to the FIX ticket if the finding is dispatched. If you hold a workflow
    role, tell the coordinator you filed it — this does not notify anyone."""
    body = {
        "agent": agent,
        "severity": severity,
        "title": title,
        "detail": detail,
        "suggestion": suggestion,
        "category": category,
    }
    if ticket_id:
        body["ticket_id"] = ticket_id
    return _call("POST", f"/api/workflows/{workflow_id}/findings", json=body)


@mcp.tool()
def list_findings(workflow_id: int, status: str | None = None) -> list | dict:
    """The workflow's bug table (open|dispatched|fixed|dismissed), by severity."""
    params = {"status": status} if status else None
    return _call("GET", f"/api/workflows/{workflow_id}/findings", params=params)


@mcp.tool()
def dispatch_finding(agent: str, finding_id: int, note: str = "", phase: str | None = None) -> dict:
    """Turn an open finding into a FIX task in the workflow (coordinator or owner only).
    The finding becomes dispatched, and moves to fixed on its own once the FIX is
    verified."""
    body = {"agent": agent, "note": note}
    if phase:
        body["phase"] = phase
    return _call("POST", f"/api/findings/{finding_id}/dispatch", json=body)


@mcp.tool()
def dismiss_finding(agent: str, finding_id: int, note: str) -> dict:
    """Dismiss a finding, with a mandatory reason — a dismissal is knowledge too. If it
    had been dispatched, its FIX ticket is rejected automatically."""
    return _call("POST", f"/api/findings/{finding_id}/dismiss", json={"agent": agent, "note": note})


@mcp.tool()
def workflow_activity(workflow_id: int, since: str | None = None, limit: int = 30) -> list | dict:
    """What happened since `since` (ISO): transitions, dispatches, findings. The delta
    for a session coming back."""
    params = {"limit": limit}
    if since:
        params["since"] = since
    return _call("GET", f"/api/workflows/{workflow_id}/activity", params=params)


@mcp.tool()
def handoff_draft(workflow_id: int) -> dict:
    """A handoff draft composed from the real state: progress, dispatched work, open
    findings, pending milestones, recent activity. The coordinator edits it and
    saves it with write_handoff."""
    return _call("GET", f"/api/workflows/{workflow_id}/handoff_draft")


@mcp.tool()
def write_handoff(agent: str, workflow_id: int, content: str) -> dict:
    """Save the workflow's handoff here — one place that survives between sessions.
    Coordinator or owner only. workflow_status returns it."""
    return _call("POST", f"/api/workflows/{workflow_id}/handoffs", json={"agent": agent, "content": content})


@mcp.tool()
def milestone_memory_draft(workflow_id: int, milestone_id: int) -> dict:
    """A prose draft of the milestone, ready to be stored as a memory: what was done,
    what was decided, what was found, what was dismissed, what is still open. Save
    it with your own memory store and then call mark_milestone_saved(memory_id).
    FarOS does not keep long-term memory itself — this is the handover to whatever
    does."""
    return _call("GET", f"/api/workflows/{workflow_id}/milestones/{milestone_id}/memory_draft")


@mcp.tool()
def mark_milestone_saved(agent: str, workflow_id: int, milestone_id: int, memory_id: str) -> dict:
    """Mark the milestone as saved to your memory store; pass the id it gave you."""
    return _call(
        "POST",
        f"/api/workflows/{workflow_id}/milestones/{milestone_id}/mark_saved",
        json={"agent": agent, "memory_id": memory_id},
    )


# ------------------------------------------------------------------ Library (#850)

LIBRARY_GET_MAX = 8000


@mcp.tool()
def library_search(
    q: str | None = None, clase: str | None = None, desde: str | None = None, hasta: str | None = None, limite: int = 20
) -> list | dict:
    """Search the Library (what FarOS and the agents produce: magazines, readings, reports,
    run results, project docs, reports for the owner). Full text in q (2+ letter words); clase: revista |
    lectura | faro | resultado | documentacion | informe; desde/hasta: YYYY-MM-DD. Without q it lists by date.
    Each row has `id`, `titulo`, `fecha`, `clase` and `abrir` (URL if it is an interactive page)."""
    params = {
        k: v
        for k, v in (("q", q), ("clase", clase), ("desde", desde), ("hasta", hasta), ("limite", limite))
        if v is not None
    }
    return _call("GET", "/api/library/items", params=params)


@mcp.tool()
def library_get(id: int, max_chars: int = LIBRARY_GET_MAX) -> dict:
    """Read a Library entry as text (max 8000 characters; `truncado` tells you if there is more).
    `id` comes from library_search."""
    return _call(
        "GET",
        f"/api/library/items/{id}/content",
        params={"formato": "texto", "max": max(1, min(max_chars, LIBRARY_GET_MAX))},
    )


@mcp.tool()
def library_add(ruta: str, clase: str, ticket: int | None = None) -> dict:
    """Register a file you produced in the Library. It must be inside one of the Library roots
    (the daemon decides; anything else is refused). clase: revista | lectura | faro | resultado |
    documentacion | informe. ticket links it to a ticket."""
    body = {"ruta": ruta, "clase": clase}
    if ticket is not None:
        body["ticket"] = ticket
    return _call("POST", "/api/library/items", json=body)


if __name__ == "__main__":
    mcp.run()
