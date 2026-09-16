"""MCP for FarOS — a THIN client of the daemon (SPEC §6).

Every tool calls the SAME HTTP endpoint the UI uses: parity by construction.
This process does NOT open the DB (single writer: the daemon). If the daemon is
down, the error says so plainly.

Registering it in a session (.mcp.json / settings):
  {"command": "python", "args": ["-m", "faros.mcp_server"],
   "cwd": "<path-to>/FarOS"}
"""

from __future__ import annotations

import os
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
from mcp.server.fastmcp import FastMCP
from . import env as _env

BASE_URL = _env.get("URL", "http://127.0.0.1:8756")
# The SAME house as the daemon, resolved by env.data_dir(). It used to hardcode
# its own path with "AgenticOS": on a NEW install the daemon writes the token to
# FarOS/token and this looked for it in AgenticOS/token -> EVERY tool returns
# 401. Prima found it while reviewing the rename.
TOKEN_FILE = _env.data_dir() / "token"

# THE SELF-NAME IS PARAMETERIZABLE, AND STILL DEFAULTS TO "agenticos".
#
# The prefix of EVERY tool comes from it (tested: the server's self-name wins,
# not the config key). Changing it outright moves everything to mcp__faros__*
# and leaves the four live sessions without a board until they restart the MCP.
# That is why the default is not touched today.
#
# And it is why "adding a second faros entry next to agenticos" does NOT work as
# is: both entries launch the SAME server, named the same, so both expose
# mcp__agenticos__* and collide. A seamless transition needs the new entry to
# start the server UNDER A DIFFERENT NAME, which is what this variable allows:
#     entry "agenticos": no env             -> mcp__agenticos__*
#     entry "faros":     FAROS_MCP_NAME=faros -> mcp__faros__*
# Both coexist, you migrate session by session, and retire the old one cold.
# NONE IS ADDED TODAY: with no migration under way they are just extra processes.
# The mechanism is ready for #238.
mcp = FastMCP(_env.get("MCP_NAME", "agenticos"))


def _call(method: str, path: str, **kwargs):
    try:
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return {"error": "daemon_token_missing",
                "reason": f"no {TOKEN_FILE} — has the FarOS daemon started?"}
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
        r = httpx.request(method, f"{BASE_URL}{path}",
                          headers={"X-FarOS-Token": token,
                                   "X-AgenticOS-Token": token},
                          timeout=30, **kwargs)
    except httpx.ConnectError:
        return {"error": "daemon_down",
                "reason": f"FarOS daemon not responding at {BASE_URL} — start it "
                          "(python -m faros.daemon) or open the app"}
    try:
        body = r.json()
    except json.JSONDecodeError:
        body = {"error": "bad_response", "reason": r.text[:500]}
    if r.status_code >= 400 and "error" not in body:
        body = {"error": f"http_{r.status_code}", "reason": str(body)[:500]}
    return body


@mcp.tool()
def board(project_id: int | None = None, view: str = "all") -> dict:
    """Full board: columns by status, side panels, counters.
    view: all | today (the morning screen) | week | future."""
    params = {"view": view}
    if project_id:
        params["project_id"] = project_id
    return _call("GET", "/api/board", params=params)


@mcp.tool()
def my_board(agent: str) -> dict:
    """Your personal board: open tasks, recurring due, verification queue, proposals."""
    return _call("GET", "/api/my_board", params={"agent": agent})


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
def harnesses(refresh: bool = False) -> list | dict:
    """Installed harnesses with billing (subscription|api|local), capabilities and
    the models each one declares (asked of the harness, not a hardcoded list)."""
    return _call("GET", "/api/harnesses", params={"refresh": refresh} if refresh else None)


@mcp.tool()
def harness_models(name: str, refresh: bool = False) -> dict:
    """Models of a harness (refresh=True asks the harness again)."""
    return _call("GET", f"/api/harnesses/{name}/models",
                 params={"refresh": refresh} if refresh else None)


@mcp.tool()
def dispatch_batch(agent: str, workflow_id: int, items: list) -> dict:
    """Dispatch a BATCH of workflow tasks atomically (all or nothing).
    items: [{"ticket_id": int, "to": str, "note": str?}]. Notifying each peer
    still happens out of band, not through this call."""
    return _call("POST", f"/api/workflows/{workflow_id}/dispatch_batch",
                 json={"agent": agent, "items": items})


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
def propose_ticket(agent: str, title: str, description: str = "", owner: str | None = None,
                   source: str = "proposed", priority: str = "media",
                   verification_level: str = "self", project_id: int | None = None,
                   due_at: str | None = None, verify_criteria: str | None = None,
                   expires_at: str | None = None, cadence_days: int | None = None,
                   scheduled_at: str | None = None, duration_min: int | None = None,
                   preferred_time: str | None = None, weekday: int | None = None) -> dict:
    """Propose a manual ticket (born 'proposed'; the owner accepts it at the
    decision gate). Returns the ticket + possible duplicates (FTS5). Temporal
    fields: scheduled_at 'YYYY-MM-DDTHH:MM', duration_min, preferred_time 'HH:MM'
    (recurring), weekday 0=Monday…6 (v3.2: weekly recurring anchored to a day)."""
    return _call("POST", "/api/tickets", json={k: v for k, v in locals().items() if v is not None})


@mcp.tool()
def create_agent_task(agent: str, title: str, prompt: str, model: str = "haiku",
                      description: str = "", harness: str = "claude-cli",
                      schedule: dict | None = None, verification_level: str = "auto",
                      project_id: int | None = None, verify_criteria: str | None = None,
                      permission_mode: str = "dontAsk", allowed_tools: str | None = None,
                      mcp_config: str | None = None, max_budget_usd: float | None = None,
                      timeout_s: int = 1800) -> dict:
    """Create an AGENTIC task (run by a generic headless agent).
    schedule: {"type":"manual"} | {"type":"once","run_at":"ISO"} |
    {"type":"recurring","every_days":N,"at":"HH:MM"} (recurring requires level self)."""
    body = {"agent": agent, "title": title, "description": description, "kind": "agentic",
            "verification_level": verification_level, "project_id": project_id,
            "verify_criteria": verify_criteria,
            "job": {"prompt": prompt, "model": model, "harness": harness,
                    "schedule": schedule or {"type": "manual"},
                    "permission_mode": permission_mode, "allowed_tools": allowed_tools,
                    "mcp_config": mcp_config, "max_budget_usd": max_budget_usd,
                    "timeout_s": timeout_s}}
    return _call("POST", "/api/tickets", json=body)


@mcp.tool()
def list_tickets(status: str | None = None, owner: str | None = None,
                 project_id: int | None = None, kind: str | None = None,
                 query: str | None = None, include_closed: bool = False) -> list | dict:
    """List/search tickets (query = FTS5)."""
    params = {k: v for k, v in locals().items() if v is not None and v is not False}
    return _call("GET", "/api/tickets", params=params)


@mcp.tool()
def get_ticket(ticket_id: int) -> dict:
    """One ticket with its job and latest run if it is agentic."""
    return _call("GET", f"/api/tickets/{ticket_id}")


@mcp.tool()
def transition_ticket(agent: str, ticket_id: int, action: str,
                      evidence_type: str | None = None, evidence: str | None = None,
                      verdict: str = "pass", reason: str | None = None,
                      note: str = "") -> dict:
    """State transition: accept | start | block | complete | verify | reject.
    complete requires evidence_type+evidence. verify uses verdict (pass|fail) + note.
    block/reject require reason."""
    body = {"agent": agent, "action": action, "evidence_type": evidence_type,
            "evidence": evidence, "verdict": verdict, "reason": reason, "note": note}
    return _call("POST", f"/api/tickets/{ticket_id}/transition", json=body)


@mcp.tool()
def rework_ticket(agent: str, ticket_id: int, reason: str, to: str = "accepted") -> dict:
    """v3.1: return a DONE or VERIFIED task to accepted|in_progress with a
    mandatory reason (>=10 chars). History is kept (history + evidence). Only the
    task owner, whoever verified it, or the household owner. Not for recurring."""
    return _call("POST", f"/api/tickets/{ticket_id}/transition",
                 json={"agent": agent, "action": "rework", "to": to, "reason": reason})


CLEARABLE_FIELDS = {"scheduled_at", "next_due_override", "due_at", "duration_min",
                    "preferred_time", "weekday"}


@mcp.tool()
def update_ticket(agent: str, ticket_id: int, title: str | None = None,
                  description: str | None = None, priority: str | None = None,
                  due_at: str | None = None, verify_criteria: str | None = None,
                  project_id: int | None = None, owner: str | None = None,
                  scheduled_at: str | None = None, duration_min: int | None = None,
                  all_day: int | None = None, preferred_time: str | None = None,
                  next_due_override: str | None = None, weekday: int | None = None,
                  evidence: str | None = None, clear: list[str] | None = None) -> dict:
    """Edit ticket fields (same PATCH as the UI). Temporal fields (T0.3):
    scheduled_at 'YYYY-MM-DDTHH:MM', duration_min, all_day 0|1, preferred_time
    'HH:MM', next_due_override 'YYYY-MM-DD' (recurring only; Sat/Sun snaps to
    Monday), weekday 0=Monday…6 (v3.2, weekly recurring only). evidence:
    rewrites the evidence of an already-stamped ticket (the original stays in history).
    To CLEAR a field use clear=["scheduled_at", "next_due_override", ...]
    (3a, 14-Sep): the MCP client carries neither "" nor None, so clearing goes
    through an explicit list. Clearable fields: scheduled_at, next_due_override,
    due_at, duration_min, preferred_time, weekday."""
    body = {k: v for k, v in locals().items()
            if v is not None and k not in ("ticket_id", "clear")}
    if clear:
        bad = set(clear) - CLEARABLE_FIELDS
        if bad:
            # #303 (spotted by Prima reading the 67): this is NOT a description,
            # it is what the tool RETURNS to an agent when it gets it wrong. It
            # used to say "campo no borrable" — in Spanish, and it also used the
            # `error` field as prose when everywhere else in the system `error` is
            # a STABLE CODE and `reason` is the explanation. An agent that branches
            # on `error` cannot branch on a sentence.
            return {"error": "invalid_request",
                    "code": "ticket.field_not_clearable",
                    "reason": f"not clearable: {', '.join(sorted(bad))}",
                    "fields": sorted(bad),
                    "clearable": sorted(CLEARABLE_FIELDS)}
        body.update({k: None for k in clear})
    return _call("PATCH", f"/api/tickets/{ticket_id}", json=body)


@mcp.tool()
def ticket_history(ticket_id: int) -> dict:
    """Full append-only timeline of the ticket."""
    return _call("GET", f"/api/tickets/{ticket_id}/history")


@mcp.tool()
def update_agent_task(agent: str, job_id: int, prompt: str | None = None,
                      model: str | None = None, schedule: dict | None = None,
                      enabled: bool | None = None, permission_mode: str | None = None,
                      allowed_tools: str | None = None, mcp_config: str | None = None,
                      max_budget_usd: float | None = None, timeout_s: int | None = None) -> dict:
    """Edit the job of an agentic task (prompt, model, schedule, enabled...)."""
    body = {k: v for k, v in locals().items() if v is not None and k != "job_id"}
    return _call("PATCH", f"/api/jobs/{job_id}", json=body)


# ------------------------------------------------------------------ calendario (v3)

@mcp.tool()
def calendar(from_date: str, to_date: str, agent: str | None = None,
             project_id: int | None = None) -> dict:
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
def schedule_ticket(agent: str, ticket_id: int, scheduled_at: str | None = None,
                    duration_min: int | None = None, all_day: int | None = None,
                    note: str = "") -> dict:
    """Schedule/move a task: scheduled_at 'YYYY-MM-DDTHH:MM' ("" = remove),
    duration_min, all_day 0|1. Readable history; 400 if illegal."""
    body = {k: v for k, v in locals().items() if v is not None and k != "ticket_id"}
    return _call("POST", f"/api/tickets/{ticket_id}/schedule", json=body)


@mcp.tool()
def shift_next_occurrence(agent: str, ticket_id: int, date: str, note: str = "") -> dict:
    """Move ONLY the next occurrence of a recurring task to `date` (YYYY-MM-DD;
    "" restores last_done+cadence). The cadence does not change."""
    return _call("POST", f"/api/tickets/{ticket_id}/shift_next",
                 json={"agent": agent, "date": date, "note": note})


@mcp.tool()
def search(q: str) -> dict:
    """Global search: tickets (board/office), jobs (agents) and decisions
    (office), max 10 per group, with `section` to navigate."""
    return _call("GET", "/api/search", params={"q": q})


# ------------------------------------------------------------------ decisiones (v3)

@mcp.tool()
def ask_decision(agent: str, title: str, options: list, context: str = "",
                 workflow_id: int | None = None, project_id: int | None = None) -> dict:
    """Open a decision for the owner. options: [{"label": "...", "text": "the whole
    literal option"}, ...] — never A/B/C. Replaces the old file-based decisions."""
    body = {k: v for k, v in locals().items() if v is not None}
    return _call("POST", "/api/decisions", json=body)


@mcp.tool()
def list_decisions(status: str | None = None, workflow_id: int | None = None,
                   project_id: int | None = None) -> list | dict:
    """Decisions: pending | deferred | decided (with who, when, why)."""
    params = {k: v for k, v in locals().items() if v is not None}
    return _call("GET", "/api/decisions", params=params)


@mcp.tool()
def decide(agent: str, decision_id: int, rationale: str, option: str | None = None,
           decision: str | None = None) -> dict:
    """Decide (usually the owner): option = chosen label, or decision = free text.
    rationale is mandatory. It cannot be decided twice."""
    body = {k: v for k, v in locals().items() if v is not None and k != "decision_id"}
    return _call("POST", f"/api/decisions/{decision_id}/decide", json=body)


@mcp.tool()
def defer_decision(agent: str, decision_id: int, until: str | None = None, note: str = "") -> dict:
    """Defer a decision (until YYYY-MM-DD optional)."""
    body = {k: v for k, v in locals().items() if v is not None and k != "decision_id"}
    return _call("POST", f"/api/decisions/{decision_id}/defer", json=body)


# ------------------------------------------------------------------ Agentes (v3)

@mcp.tool()
def list_jobs(status: str | None = None, project_id: int | None = None,
              include_archived: bool = False) -> list | dict:
    """Agentic jobs (Agents section): active|paused|archived, with last run,
    verdict, next_fire and spend."""
    params = {k: v for k, v in locals().items() if v not in (None, False)}
    return _call("GET", "/api/jobs", params=params)


@mcp.tool()
def create_job(agent: str, name: str, prompt: str, harness: str = "claude-cli",
               model: str = "haiku", schedule: dict | None = None,
               ticket_id: int | None = None, project_id: int | None = None,
               verify_criteria: str | None = None, verify_level: str = "auto",
               permission_mode: str = "dontAsk", allowed_tools: str | None = None,
               inherit_mcp: list | None = None, mcp_config: str | None = None,
               strict_mcp: bool = False, add_dirs: list | None = None,
               effort: str | None = None, fallback_model: str | None = None,
               system_prompt: str | None = None, json_schema: dict | None = None,
               max_budget_usd: float | None = None, timeout_s: int = 1800) -> dict:
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
def update_job(agent: str, job_id: int, **fields) -> dict:
    """Edit fields of a job (name, prompt, model, schedule, inherit_mcp, add_dirs,
    effort, fallback_model, system_prompt, json_schema, verify_criteria, verify_level,
    ticket_id, project_id, enabled...)."""
    return _call("PATCH", f"/api/jobs/{job_id}", json={"agent": agent, **fields})


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
def mcp_upsert(agent: str, name: str, transport: str = "stdio", command: str | None = None,
               args: list[str] | None = None, url: str | None = None, env: dict | None = None,
               headers: dict | None = None, enabled: bool = True, note: str = "") -> dict:
    """Add or edit (by name) an MCP server in the registry. env/headers may carry
    secrets: stored in the database, masked on read, emptied in backups."""
    existing = _call("GET", "/api/mcp", params={"q": name})
    match = next((x for x in existing if isinstance(existing, list) and x.get("name") == name), None) \
        if isinstance(existing, list) else None
    body = {"agent": agent, "name": name, "transport": transport, "command": command,
            "args": args or [], "url": url, "env": env or {}, "headers": headers or {},
            "enabled": enabled, "note": note}
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
    """Manual verdict on an ok run that was never judged: pass|fail plus a reason.
    A pass on a linked job completes its ticket."""
    return _call("POST", f"/api/runs/{run_id}/verdict",
                 json={"agent": agent, "verdict": verdict, "reason": reason})


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
    return _call("POST", "/api/workflows/import",
                 json={"agent": agent, "plan": plan, "repo_root": repo_root})


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
def close_workflow(agent: str, workflow_id: int, status: str = "closed",
                   reason: str | None = None, repo_root: str | None = None) -> dict:
    """Change the workflow's status (active|paused|closed|cancelled|archived) and/or its
    repo_root. Coordinator or owner only. closed creates the workflow_closed
    milestone; cancelled requires a reason and rejects the workflow's open tickets;
    archived is only reachable from closed|cancelled; to unarchive, set closed."""
    return _call("PATCH", f"/api/workflows/{workflow_id}",
                 json={"agent": agent, "status": status, "reason": reason, "repo_root": repo_root})


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
    return _call("PATCH", f"/api/workflows/{workflow_id}",
                 json={"agent": agent, "status": "cancelled", "reason": reason})


@mcp.tool()
def dispatch_ticket(agent: str, ticket_id: int, to: str, note: str = "") -> dict:
    """Assign a task to a peer (code/verifier/adv-*/an agent). Coordinator or owner
    only. Requires the task's dependencies to be verified. THE PEER IS NOTIFIED OUT
    OF BAND — this only records the state."""
    return _call("POST", f"/api/tickets/{ticket_id}/dispatch",
                 json={"agent": agent, "to": to, "note": note})


@mcp.tool()
def dispatchable_tickets(workflow_id: int) -> list | dict:
    """The coordinator's dispatchable queue: every task in the workflow whose
    dependencies are all verified, in one call."""
    return _call("GET", "/api/tickets", params={"workflow_id": workflow_id, "ready": "true"})


@mcp.tool()
def add_dep(agent: str, ticket_id: int, depends_on: int) -> dict:
    """Add a dependency between two tasks of the same workflow. Owner and agents only;
    refuses cycles and tasks already under way."""
    return _call("POST", f"/api/tickets/{ticket_id}/deps",
                 json={"agent": agent, "depends_on": depends_on})


@mcp.tool()
def add_finding(agent: str, workflow_id: int, severity: str, title: str,
                detail: str = "", suggestion: str = "", category: str = "bug",
                ticket_id: int | None = None) -> dict:
    """Record a finding on the workflow's bug table. severity: critical|high|medium|low
    (how urgent). category: bug|gap|degradation|concern (what kind). suggestion
    travels to the FIX ticket if the finding is dispatched. If you hold a workflow
    role, tell the coordinator you filed it — this does not notify anyone."""
    body = {"agent": agent, "severity": severity, "title": title, "detail": detail,
            "suggestion": suggestion, "category": category}
    if ticket_id:
        body["ticket_id"] = ticket_id
    return _call("POST", f"/api/workflows/{workflow_id}/findings", json=body)


@mcp.tool()
def list_findings(workflow_id: int, status: str | None = None) -> list | dict:
    """The workflow's bug table (open|dispatched|fixed|dismissed), by severity."""
    params = {"status": status} if status else None
    return _call("GET", f"/api/workflows/{workflow_id}/findings", params=params)


@mcp.tool()
def dispatch_finding(agent: str, finding_id: int, note: str = "",
                     phase: str | None = None) -> dict:
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
    return _call("POST", f"/api/findings/{finding_id}/dismiss",
                 json={"agent": agent, "note": note})


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
    return _call("POST", f"/api/workflows/{workflow_id}/handoffs",
                 json={"agent": agent, "content": content})


@mcp.tool()
def milestone_memory_draft(workflow_id: int, milestone_id: int) -> dict:
    """A prose draft of the milestone, ready to be stored as a memory: what was done,
    what was decided, what was found, what was dismissed, what is still open. Save
    it with your own memory store and then call mark_milestone_saved(memory_id).
    FarOS does not keep long-term memory itself — this is the handover to whatever
    does."""
    return _call("GET", f"/api/workflows/{workflow_id}/milestones/{milestone_id}/memory_draft")


@mcp.tool()
def mark_milestone_saved(agent: str, workflow_id: int, milestone_id: int,
                         memory_id: str) -> dict:
    """Mark the milestone as saved to your memory store; pass the id it gave you."""
    return _call("POST", f"/api/workflows/{workflow_id}/milestones/{milestone_id}/mark_saved",
                 json={"agent": agent, "memory_id": memory_id})


if __name__ == "__main__":
    mcp.run()
