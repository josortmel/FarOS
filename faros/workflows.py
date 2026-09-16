"""Dominio de workflows (SPEC v2): agrupación, deps, despacho, findings, hitos.

Los peers de workflow (code/verificador/adv-code/adv-seg) ESCRIBEN directo, pero
por decisión del dueño siempre reportan al coordinador por relay — la capa API añade
el recordatorio de protocolo a sus respuestas; aquí solo dominio.
"""

from __future__ import annotations

import json
from datetime import datetime

from . import service
from .service import (TicketError, AGENTS, HOUSE_OWNER, WORKFLOW_AGENTS, _now, _get,
                      _append_history, _validate_agent, _unmet_deps, _as_dict)

SEVERITIES = {"critical", "high", "medium", "low"}
# category mide NATURALEZA; severity mide URGENCIA (review Eco: una degradación
# high y un bug high piden respuestas distintas).
CATEGORIES = {"bug", "gap", "degradation", "concern"}


# ------------------------------------------------------------------ helpers

def get_workflow(conn, workflow_id) -> dict:
    row = conn.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone()
    if row is None:
        raise TicketError(f"workflow #{workflow_id} no existe")
    d = dict(row)
    d["phases"] = json.loads(d["phases"])
    return d


def _require_coordinator(conn, agent, wf: dict, action: str):
    """Despachar/descartar/cerrar es del coordinador del workflow o de la familia."""
    if agent != wf["coordinator"] and agent not in AGENTS:
        raise TicketError(f"{action} es del coordinador ({wf['coordinator']}) o de la familia, no de {agent}")


# ------------------------------------------------------------------ import

REQUIRED_TASK_FIELDS = {"key", "phase", "title", "criterio"}
DESC_FIELDS = [("objetivo", "Objetivo"), ("accion", "Acción"),
               ("archivos_a_tocar", "Archivos"), ("pre", "Pre"), ("post", "Post"),
               ("tests", "Tests"), ("rollback", "Rollback")]


def _validate_plan(plan: dict) -> None:
    """Valida TODO antes de escribir nada. Un plan inválido no importa a medias."""
    if not isinstance(plan, dict) or "workflow" not in plan or "tasks" not in plan:
        raise TicketError("plan inválido: faltan claves 'workflow' y/o 'tasks' (CONTRATO_PLAN.md)")
    wf = plan["workflow"]
    if not (wf.get("name") or "").strip():
        raise TicketError("plan inválido: workflow.name vacío")
    phases = plan.get("phases")
    if not isinstance(phases, list) or not phases or not all(isinstance(p, str) and p for p in phases):
        raise TicketError("plan inválido: phases debe ser lista no vacía de strings")
    if len(set(phases)) != len(phases):
        raise TicketError("plan inválido: fases duplicadas")
    tasks = plan["tasks"]
    if not tasks:
        raise TicketError("plan inválido: sin tareas")
    keys = [t.get("key") for t in tasks]
    if len(set(keys)) != len(keys):
        dup = sorted({k for k in keys if keys.count(k) > 1})
        raise TicketError(f"plan inválido: keys duplicadas {dup}")
    known = set(keys)
    for t in tasks:
        missing = REQUIRED_TASK_FIELDS - {k for k, v in t.items() if v}
        if missing:
            raise TicketError(f"plan inválido: tarea {t.get('key')!r} sin campos {sorted(missing)}")
        if t["phase"] not in phases:
            raise TicketError(f"plan inválido: tarea {t['key']} usa fase {t['phase']!r} no declarada")
        for dep in t.get("depende_de", []):
            if dep not in known:
                raise TicketError(f"plan inválido: {t['key']} depende de {dep!r} que no existe")
        if t.get("verification_level", "peer") not in service.VERIFICATION_LEVELS:
            raise TicketError(f"plan inválido: {t['key']} verification_level desconocido")
    # ciclos: DFS sobre depende_de
    graph = {t["key"]: list(t.get("depende_de", [])) for t in tasks}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(graph, WHITE)

    def dfs(node, path):
        color[node] = GRAY
        for nxt in graph[node]:
            if color[nxt] == GRAY:
                raise TicketError(f"plan inválido: ciclo de dependencias {' -> '.join(path + [nxt])}")
            if color[nxt] == WHITE:
                dfs(nxt, path + [nxt])
        color[node] = BLACK

    for k in graph:
        if color[k] == WHITE:
            dfs(k, [k])


def _task_description(t: dict) -> str:
    parts = []
    for field, label in DESC_FIELDS:
        v = t.get(field)
        if v:
            if isinstance(v, list):
                v = ", ".join(str(x) for x in v)
            parts.append(f"{label}: {v}")
    return "\n".join(parts)


def import_plan(conn, agent, plan: dict, repo_root: str | None = None) -> dict:
    """Contrato fijo (SPEC v2 §3): el Plan JSON de workflow-diseño → workflow + tareas + deps.
    repo_root (v3.1): raíz absoluta del repo del proyecto — desde ahí se resuelven
    spec_path/plan_path y se listan reviews/ y docs/ (documents())."""
    _validate_agent(agent, family_only=True)
    _validate_plan(plan)
    wf_in = plan["workflow"]
    if conn.execute("SELECT id FROM workflows WHERE name=?", (wf_in["name"],)).fetchone():
        raise TicketError(f"workflow {wf_in['name']!r} ya existe — no hay re-import/merge en v2")

    project_id = None
    if wf_in.get("project"):
        row = conn.execute("SELECT id FROM projects WHERE name=?", (wf_in["project"],)).fetchone()
        project_id = row["id"] if row else service.create_project(conn, agent, wf_in["project"])["id"]

    if repo_root is not None:
        repo_root = _validate_repo_root(repo_root)
    cur = conn.execute(
        "INSERT INTO workflows (name, project_id, kind, spec_path, plan_path, repo_root, phases,"
        " coordinator, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (wf_in["name"], project_id, wf_in.get("kind", "construccion"),
         wf_in.get("spec_path"), wf_in.get("plan_path"), repo_root,
         json.dumps(plan["phases"]), wf_in.get("coordinator", agent), _now()))
    workflow_id = cur.lastrowid

    key_to_id: dict[str, int] = {}
    for t in plan["tasks"]:
        res = service.propose_ticket(
            conn, agent,
            title=f"[{t['key']}] {t['title']}",
            description=_task_description(t),
            project_id=project_id,
            priority=t.get("priority", "media"),
            verification_level=t.get("verification_level", "peer"),
            verify_criteria=t["criterio"],
            auto_accept=True,  # el gate de aceptación fue el diseño mismo (SPEC §3)
        )
        tid = res["ticket"]["id"]
        conn.execute(
            "UPDATE tickets SET workflow_id=?, phase=?, plan_key=?, plan_data=?,"
            " suggested_agent=? WHERE id=?",
            (workflow_id, t["phase"], t["key"], json.dumps(t, ensure_ascii=False),
             t.get("agente_sugerido"), tid))  # P1/P3 (Prima): hint de asignación + task original
        key_to_id[t["key"]] = tid
    for t in plan["tasks"]:
        for dep in t.get("depende_de", []):
            conn.execute("INSERT INTO ticket_deps (ticket_id, depends_on) VALUES (?, ?)",
                         (key_to_id[t["key"]], key_to_id[dep]))
    conn.commit()
    return workflow_status(conn, workflow_id)


# ------------------------------------------------------------------ despacho y deps

def dispatch_batch(conn, agent, workflow_id, items: list[dict]) -> dict:
    """Atomic batch dispatch: validate ALL items first, write nothing on first failure.

    Each item: {"ticket_id": int, "to": str, "note": str (optional)}.
    Returns {"dispatched": [...results...], "count": int}.
    Raises TicketError naming the culprit key on the first validation failure.
    """
    if not items:
        raise TicketError("dispatch_batch: items vacío")
    _validate_agent(agent)
    wf = get_workflow(conn, workflow_id)
    _require_coordinator(conn, agent, wf, "batch dispatch")
    if wf["status"] != "active":
        raise TicketError(f"workflow {wf['name']!r} está {wf['status']} — no se despacha")

    seen_ids = set()
    validated = []
    for i, item in enumerate(items):
        tid = item.get("ticket_id")
        to = item.get("to")
        note = item.get("note", "")
        label = f"items[{i}] (ticket #{tid})"

        if tid is None or to is None:
            raise TicketError(f"dispatch_batch: {label} — ticket_id y to son obligatorios")
        if tid in seen_ids:
            raise TicketError(f"dispatch_batch: {label} — ticket_id duplicado en el batch")
        seen_ids.add(tid)

        _validate_agent(to)
        t = _get(conn, tid)
        if t["workflow_id"] != workflow_id:
            raise TicketError(
                f"dispatch_batch: {label} — ticket no pertenece al workflow #{workflow_id}")
        if t["status"] not in {"accepted", "blocked"}:
            raise TicketError(
                f"dispatch_batch: {label} — dispatch ilegal desde {t['status']!r} "
                f"(requiere accepted|blocked)")
        unmet = _unmet_deps(conn, tid)
        if unmet:
            keys = ", ".join(d["plan_key"] or f"#{d['id']}" for d in unmet)
            raise TicketError(
                f"dispatch_batch: {label} — dependencias sin verificar ({keys})")
        validated.append((t, to, note))

    now = _now()
    results = []
    for t, to, note in validated:
        conn.execute(
            "UPDATE tickets SET owner=?, dispatched_to=?, dispatched_at=? WHERE id=?",
            (to, to, now, t["id"]))
        _append_history(conn, t["id"], t["status"], t["status"], agent,
                        f"despachada a {to} (batch)" + (f" — {note}" if note else ""))
        results.append({"ticket_id": t["id"], "plan_key": t["plan_key"], "to": to})
    conn.commit()
    return {"dispatched": results, "count": len(results)}


def dispatch_ticket(conn, agent, ticket_id, to, note="") -> dict:
    """Asigna la tarea a un peer. El AVISO va por relay (decisión del dueño) — esto
    solo registra el estado: quién la tiene desde cuándo."""
    _validate_agent(agent)
    _validate_agent(to)
    t = _get(conn, ticket_id)
    if not t["workflow_id"]:
        raise TicketError(f"ticket #{ticket_id} no pertenece a un workflow")
    wf = get_workflow(conn, t["workflow_id"])
    _require_coordinator(conn, agent, wf, "dispatch")
    if wf["status"] != "active":
        raise TicketError(f"workflow {wf['name']!r} está {wf['status']} — no se despacha")
    if t["status"] not in {"accepted", "blocked"}:
        raise TicketError(f"dispatch ilegal desde {t['status']!r} (requiere accepted|blocked)")
    unmet = _unmet_deps(conn, ticket_id)
    if unmet:
        keys = ", ".join(d["plan_key"] or f"#{d['id']}" for d in unmet)
        raise TicketError(f"dispatch bloqueado: dependencias sin verificar ({keys})")
    now = _now()
    conn.execute("UPDATE tickets SET owner=?, dispatched_to=?, dispatched_at=? WHERE id=?",
                 (to, to, now, ticket_id))
    _append_history(conn, ticket_id, t["status"], t["status"], agent,
                    f"despachada a {to}" + (f" — {note}" if note else ""))
    conn.commit()
    return _as_dict(conn, _get(conn, ticket_id))


def add_dep(conn, agent, ticket_id, depends_on) -> dict:
    _validate_agent(agent, family_only=True)
    t = _get(conn, ticket_id)
    d = _get(conn, depends_on)
    if ticket_id == depends_on:
        raise TicketError("una tarea no puede depender de sí misma")
    if t["workflow_id"] is None or t["workflow_id"] != d["workflow_id"]:
        raise TicketError("las dependencias son entre tareas del MISMO workflow")
    if t["status"] not in {"accepted", "blocked", "proposed"}:
        # Review Prima F5: añadir dep a una tarea ya en marcha crea inconsistencia
        raise TicketError(f"no se añaden dependencias a una tarea en {t['status']!r}"
                          " — solo antes de empezar (accepted|blocked|proposed)")
    # ciclo: ¿depends_on alcanza ticket_id siguiendo deps?
    stack, seen = [depends_on], set()
    while stack:
        cur = stack.pop()
        if cur == ticket_id:
            raise TicketError(f"dependencia rechazada: crearía un ciclo (#{depends_on} ya depende de #{ticket_id})")
        if cur in seen:
            continue
        seen.add(cur)
        stack += [r["depends_on"] for r in conn.execute(
            "SELECT depends_on FROM ticket_deps WHERE ticket_id=?", (cur,))]
    conn.execute("INSERT OR IGNORE INTO ticket_deps (ticket_id, depends_on) VALUES (?, ?)",
                 (ticket_id, depends_on))
    _append_history(conn, ticket_id, t["status"], t["status"], agent,
                    f"dependencia añadida: espera a #{depends_on} ({d['plan_key'] or d['title'][:30]})")
    conn.commit()
    return _as_dict(conn, _get(conn, ticket_id))


def remove_dep(conn, agent, ticket_id, depends_on) -> dict:
    _validate_agent(agent, family_only=True)
    t = _get(conn, ticket_id)
    cur = conn.execute("DELETE FROM ticket_deps WHERE ticket_id=? AND depends_on=?",
                       (ticket_id, depends_on))
    if cur.rowcount:
        _append_history(conn, ticket_id, t["status"], t["status"], agent,
                        f"dependencia eliminada: #{depends_on}")
        conn.commit()
    return _as_dict(conn, _get(conn, ticket_id))


# ------------------------------------------------------------------ findings

def add_finding(conn, agent, workflow_id, severity, title, detail="", ticket_id=None,
                category="bug", suggestion="") -> dict:
    _validate_agent(agent)
    wf = get_workflow(conn, workflow_id)
    if severity not in SEVERITIES:
        raise TicketError(f"severity inválida: {severity!r} (válidas: {sorted(SEVERITIES)})")
    if category not in CATEGORIES:
        raise TicketError(f"category inválida: {category!r} (válidas: {sorted(CATEGORIES)})")
    if not (title or "").strip():
        raise TicketError("finding sin title")
    if ticket_id is not None:
        t = _get(conn, ticket_id)
        if t["workflow_id"] != workflow_id:
            raise TicketError(f"ticket #{ticket_id} no es del workflow {wf['name']!r}")
    cur = conn.execute(
        "INSERT INTO findings (workflow_id, ticket_id, severity, category, title,"
        " detail, suggestion, found_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (workflow_id, ticket_id, severity, category, title, detail, suggestion,
         agent, _now()))
    conn.commit()
    return get_finding(conn, cur.lastrowid)


def get_finding(conn, finding_id) -> dict:
    row = conn.execute("SELECT * FROM findings WHERE id=?", (finding_id,)).fetchone()
    if row is None:
        raise TicketError(f"finding #{finding_id} no existe")
    d = dict(row)
    if d["correction_ticket_id"]:
        c = conn.execute("SELECT id, plan_key, status FROM tickets WHERE id=?",
                         (d["correction_ticket_id"],)).fetchone()
        d["correction_ticket"] = dict(c) if c else None
    else:
        d["correction_ticket"] = None
    return d


def list_findings(conn, workflow_id, status=None) -> list[dict]:
    get_workflow(conn, workflow_id)
    findings = _batch_findings(conn, workflow_id)
    if status:
        findings = [f for f in findings if f["status"] == status]
    return findings


def dispatch_finding(conn, agent, finding_id, note="", phase=None) -> dict:
    """Materializa el bug en una tarea FIX del mismo workflow. finding → dispatched."""
    _validate_agent(agent)
    f = get_finding(conn, finding_id)
    wf = get_workflow(conn, f["workflow_id"])
    _require_coordinator(conn, agent, wf, "dispatch de finding")
    if f["status"] != "open":
        raise TicketError(f"finding #{finding_id} está {f['status']!r} — solo se despachan open")
    origin = f"finding #{finding_id} de {f['found_by']} ({f['severity']}/{f['category']})"
    if f["ticket_id"]:
        origin += f" sobre ticket #{f['ticket_id']}"
    desc = f["detail"]
    if f.get("suggestion"):
        # Review Eco: la sugerencia del adversarial es lo más valioso — viaja al FIX
        desc += f"\n\nSugerencia de {f['found_by']}: {f['suggestion']}"
    res = service.propose_ticket(
        conn, agent,
        title=f"FIX: {f['title']}",
        description=f"{desc}\n\nOrigen: {origin}" + (f"\nNota: {note}" if note else ""),
        project_id=wf["project_id"],
        priority="alta" if f["severity"] in ("critical", "high") else "media",
        verification_level="peer",
        verify_criteria=f"El hallazgo original ya no reproduce: {f['title']}",
        auto_accept=True)
    tid = res["ticket"]["id"]
    conn.execute("UPDATE tickets SET workflow_id=?, phase=? WHERE id=?",
                 (f["workflow_id"], phase or (
                     _get(conn, f["ticket_id"])["phase"] if f["ticket_id"] else wf["phases"][-1] if wf["phases"] else None),
                  tid))
    conn.execute("UPDATE findings SET status='dispatched', correction_ticket_id=? WHERE id=?",
                 (tid, finding_id))
    _append_history(conn, tid, None, "accepted", agent, f"corrección creada desde {origin}")
    conn.commit()
    return get_finding(conn, finding_id)


def dismiss_finding(conn, agent, finding_id, note) -> dict:
    _validate_agent(agent)
    f = get_finding(conn, finding_id)
    wf = get_workflow(conn, f["workflow_id"])
    _require_coordinator(conn, agent, wf, "dismiss de finding")
    if f["status"] not in {"open", "dispatched"}:
        raise TicketError(f"finding #{finding_id} está {f['status']!r}")
    if not (note or "").strip():
        raise TicketError("dismiss requiere note (por qué se descarta)")
    # ORDEN IMPORTA: primero dismissed (así el CLOSED_HOOK del reject no ve
    # 'dispatched' y no reabre), después el reject del FIX (review Prima F2:
    # descartar un finding despachado no deja huérfano su ticket de corrección).
    conn.execute("UPDATE findings SET status='dismissed', resolved_at=?, resolved_note=? WHERE id=?",
                 (_now(), note, finding_id))
    if f["status"] == "dispatched" and f["correction_ticket_id"]:
        fix = _get(conn, f["correction_ticket_id"])
        if fix["closed_at"] is None:
            service.reject_ticket(conn, agent, fix["id"],
                                  f"finding #{finding_id} descartado: {note}")
    conn.commit()
    return get_finding(conn, finding_id)


# ------------------------------------------------------------------ hook verified

def _on_ticket_verified(conn, ticket_id) -> None:
    """(1) findings cuya corrección era este ticket → fixed. (2) hito de fase."""
    t = _get(conn, ticket_id)
    rows = conn.execute(
        "SELECT id FROM findings WHERE correction_ticket_id=? AND status='dispatched'",
        (ticket_id,)).fetchall()
    for r in rows:
        conn.execute("UPDATE findings SET status='fixed', resolved_at=?,"
                     " resolved_note='corrección verificada (ticket #' || ? || ')' WHERE id=?",
                     (_now(), ticket_id, r["id"]))
    if t["workflow_id"] and t["phase"]:
        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM tickets WHERE workflow_id=? AND phase=?"
            " AND closed_at IS NULL", (t["workflow_id"], t["phase"])).fetchone()["n"]
        verified = conn.execute(
            "SELECT COUNT(*) AS n FROM tickets WHERE workflow_id=? AND phase=?"
            " AND status='verified'", (t["workflow_id"], t["phase"])).fetchone()["n"]
        exists = conn.execute(
            "SELECT id FROM milestones WHERE workflow_id=? AND kind='phase_done' AND phase=?",
            (t["workflow_id"], t["phase"])).fetchone()
        if pending == 0 and verified > 0 and exists is None:
            conn.execute("INSERT INTO milestones (workflow_id, kind, phase, created_at)"
                         " VALUES (?, 'phase_done', ?, ?)", (t["workflow_id"], t["phase"], _now()))
    conn.commit()


def _on_ticket_closed(conn, ticket_id, new_status) -> None:
    """Review Prima F1: si el ticket FIX muere (rejected/expired), su finding NO
    se queda 'dispatched' para siempre — vuelve a open con nota, salvo que el
    cierre venga del propio dismiss (entonces el finding ya está dismissed)."""
    rows = conn.execute(
        "SELECT id FROM findings WHERE correction_ticket_id=? AND status='dispatched'",
        (ticket_id,)).fetchall()
    for r in rows:
        conn.execute(
            "UPDATE findings SET status='open', correction_ticket_id=NULL,"
            " resolved_note=COALESCE(resolved_note,'') || ? WHERE id=?",
            (f"[FIX #{ticket_id} terminó {new_status} — finding reabierto] ", r["id"]))
    conn.commit()


def _on_ticket_rework(conn, ticket_id, prev_status) -> None:
    """v3.1: un FIX verificado que se devuelve → su finding vuelve a 'dispatched'
    (estaba 'fixed' por _on_ticket_verified). Un FIX solo 'done' no había tocado
    el finding todavía."""
    if prev_status != "verified":
        return
    rows = conn.execute(
        "SELECT id FROM findings WHERE correction_ticket_id=? AND status='fixed'",
        (ticket_id,)).fetchall()
    for r in rows:
        conn.execute(
            "UPDATE findings SET status='dispatched', resolved_at=NULL,"
            " resolved_note=COALESCE(resolved_note,'') || ? WHERE id=?",
            (f"[FIX #{ticket_id} devuelto a trabajo — finding reabierto como dispatched] ", r["id"]))
    conn.commit()


service.VERIFIED_HOOKS.append(_on_ticket_verified)
service.CLOSED_HOOKS.append(_on_ticket_closed)
service.REWORK_HOOKS.append(_on_ticket_rework)


# ------------------------------------------------------------------ actividad / handoffs

def recent_activity(conn, workflow_id, limit=30, since=None,
                    _validated=False) -> list[dict]:
    """Qué pasó desde ayer (review Eco SNAG-5): transiciones+notas de los tickets
    del workflow y findings creados, cronológico descendente."""
    if not _validated:
        get_workflow(conn, workflow_id)
    params = [workflow_id]
    since_sql = ""
    if since:
        since_sql = " AND h.changed_at >= ?"
        params.append(since)
    rows = conn.execute(
        "SELECT h.changed_at AS at, h.changed_by AS who, t.id AS ticket_id,"
        " t.plan_key, t.title, h.old_status, h.new_status, h.note"
        " FROM ticket_history h JOIN tickets t ON t.id = h.ticket_id"
        f" WHERE t.workflow_id=?{since_sql} ORDER BY h.id DESC LIMIT ?",
        params + [limit]).fetchall()
    acts = [{"kind": "ticket", **dict(r)} for r in rows]
    fparams = [workflow_id]
    fsince = ""
    if since:
        fsince = " AND created_at >= ?"
        fparams.append(since)
    frows = conn.execute(
        "SELECT id AS finding_id, created_at AS at, found_by AS who, severity,"
        " category, title, status FROM findings"
        f" WHERE workflow_id=?{fsince} ORDER BY id DESC LIMIT ?",
        fparams + [limit]).fetchall()
    acts += [{"kind": "finding", **dict(r)} for r in frows]
    acts.sort(key=lambda a: a["at"], reverse=True)
    return acts[:limit]


def write_handoff(conn, agent, workflow_id, content) -> dict:
    """El coordinador deja el handoff EN AgenticOS (decisión del dueño): entre
    sesiones y compactaciones, el contexto vive en un solo sitio."""
    _validate_agent(agent)
    wf = get_workflow(conn, workflow_id)
    _require_coordinator(conn, agent, wf, "escribir handoff")
    if not (content or "").strip():
        raise TicketError("handoff vacío")
    cur = conn.execute(
        "INSERT INTO handoffs (workflow_id, content, created_by, created_at)"
        " VALUES (?, ?, ?, ?)", (workflow_id, content, agent, _now()))
    conn.commit()
    return dict(conn.execute("SELECT * FROM handoffs WHERE id=?", (cur.lastrowid,)).fetchone())


def list_handoffs(conn, workflow_id) -> list[dict]:
    """Todos los handoffs (autor y hora), más reciente primero (T3.5)."""
    get_workflow(conn, workflow_id)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM handoffs WHERE workflow_id=? ORDER BY id DESC", (workflow_id,))]


def documents(conn, workflow_id, repo_root=None) -> list[dict]:
    """T3.6: documentos abribles del workflow. Base = carpeta que contiene el
    plan (plan_path relativo a repo_root) — spec, plan, reviews/, docs/mediciones/."""
    from pathlib import Path
    from . import settings as _settings
    wf = get_workflow(conn, workflow_id)
    # v3.1 (T4.2): la raíz viene del workflow, luego de settings; el paquete solo
    # como último recurso de desarrollo (en el daemon empaquetado NO es el repo).
    root = Path(repo_root or wf.get("repo_root") or _settings.get(conn, "repo_root_default")
                or Path(__file__).resolve().parent.parent)
    out, seen = [], set()

    def add(kind, path: Path):
        resolved = path.resolve()
        if not resolved.is_relative_to(root.resolve()):
            return
        if resolved.exists() and resolved.is_file() and str(resolved) not in seen:
            seen.add(str(resolved))
            out.append({"kind": kind, "name": resolved.name, "path": str(resolved),
                        "size": resolved.stat().st_size,
                        "modified": datetime.fromtimestamp(resolved.stat().st_mtime).isoformat(timespec="minutes")})

    for kind, rel in (("spec", wf.get("spec_path")), ("plan", wf.get("plan_path"))):
        if rel:
            p = Path(rel)
            add(kind, p if p.is_absolute() else root / p)
    for kind, sub in (("review", "reviews"), ("medicion", "docs/mediciones"), ("doc", "docs")):
        d = root / sub
        if d.is_dir():
            for p in sorted(d.iterdir()):
                if p.suffix.lower() in {".md", ".json", ".txt", ".html"}:
                    add(kind, p)
    return out


def latest_handoff(conn, workflow_id) -> dict | None:
    row = conn.execute(
        "SELECT * FROM handoffs WHERE workflow_id=? ORDER BY id DESC LIMIT 1",
        (workflow_id,)).fetchone()
    return dict(row) if row else None


def handoff_draft(conn, workflow_id) -> dict:
    """Borrador auto-compuesto del handoff: el coordinador lo edita y lo guarda
    con write_handoff. Responde 'por dónde vamos + qué leer' para la sesión nueva."""
    st = workflow_status(conn, workflow_id, embed_tickets=True)
    lines = [f"HANDOFF — workflow '{st['name']}' ({st['status']}), {_now()[:16]}.",
             f"Progreso global: {st['pct_global']}% verificado.",
             ""]
    for ps in st["phases_summary"]:
        lines.append(f"Fase {ps['phase']}: {ps['verified']}/{ps['total']} verificadas"
                     f" · {ps['in_progress']} en curso · {ps['done']} por verificar"
                     f" · {ps['ready']} listas para despachar · {ps['blocked']} bloqueadas")
    if st["dispatched"]:
        lines += ["", "Despachadas ahora mismo:"]
        for d in st["dispatched"]:
            lines.append(f"- {d['plan_key'] or '#'+str(d['ticket_id'])} → {d['to']}"
                         f" ({d['status']}, desde {d['at'][:16]})")
    open_f = [f for f in st.get("findings", []) if f["status"] == "open"]
    if open_f:
        lines += ["", f"Findings ABIERTOS sin despachar ({len(open_f)}):"]
        for f in open_f:
            lines.append(f"- [{f['severity']}/{f['category']}] {f['title']} ({f['found_by']})")
    if st.get("decisions_pending"):
        lines += ["", f"Bisagras PENDIENTES de {HOUSE_OWNER} ({len(st['decisions_pending'])}):"]
        for d in st["decisions_pending"]:
            opts = " / ".join(o["label"] for o in d["options"])
            lines.append(f"- #{d['id']} {d['title']} [{d['status']}] — opciones: {opts}")
    if st["milestones_pending_ecodb"]:
        lines += ["", "Hitos SIN memoria EcoDB guardada: " + ", ".join(
            f"#{m['id']} {m['kind']} {m.get('phase') or ''}".strip()
            for m in st["milestones_pending_ecodb"])]
    acts = recent_activity(conn, workflow_id, limit=10)
    if acts:
        lines += ["", "Última actividad:"]
        for a in acts:
            if a["kind"] == "ticket":
                lines.append(f"- {a['at'][:16]} {a['who']}: {a['plan_key'] or '#'+str(a['ticket_id'])}"
                             f" → {a['new_status']}" + (f" ({a['note']})" if a["note"] else ""))
            else:
                lines.append(f"- {a['at'][:16]} {a['who']}: FINDING [{a['severity']}] {a['title']}")
    lines += ["", f"Qué leer: Spec {st.get('spec_path') or '-'} · Plan {st.get('plan_path') or '-'}",
              "Siguiente paso sugerido: despachar las 'ready' de la fase más temprana incompleta.",
              "", f"[Editar: decisiones de la sesión, bloqueos no registrados, preguntas a {HOUSE_OWNER}]"]
    return {"content": "\n".join(lines)}


# ------------------------------------------------------------------ estado / hitos

def list_workflows(conn, status=None, q=None) -> list[dict]:
    """status: active|paused|closed|cancelled|archived, o 'open' (active+paused) o
    'finished' (closed+cancelled). q (v3.1): búsqueda por nombre, sin mayúsculas."""
    sql, params, where = "SELECT * FROM workflows", [], []
    if status == "open":
        where.append("status IN ('active','paused')")
    elif status == "finished":
        where.append("status IN ('closed','cancelled')")
    elif status:
        where.append("status=?"); params.append(status)
    if q and q.strip():
        where.append("LOWER(name) LIKE ?"); params.append(f"%{q.strip().lower()}%")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return []
    wf_ids = [r["id"] for r in rows]
    ph = ",".join("?" * len(wf_ids))
    ticket_counts = {wid: {"total": 0, "verified": 0} for wid in wf_ids}
    for r in conn.execute(
            f"SELECT workflow_id, COUNT(*) AS n,"
            f" SUM(CASE WHEN status='verified' THEN 1 ELSE 0 END) AS v"
            f" FROM tickets WHERE workflow_id IN ({ph}) GROUP BY workflow_id", wf_ids):
        ticket_counts[r["workflow_id"]] = {"total": r["n"], "verified": r["v"]}
    finding_counts = dict.fromkeys(wf_ids, 0)
    for r in conn.execute(
            f"SELECT workflow_id, COUNT(*) AS n FROM findings"
            f" WHERE workflow_id IN ({ph}) AND status='open' GROUP BY workflow_id", wf_ids):
        finding_counts[r["workflow_id"]] = r["n"]
    phase_data = {}
    for r in conn.execute(
            f"SELECT workflow_id, phase, COUNT(*) AS n,"
            f" SUM(CASE WHEN status='verified' THEN 1 ELSE 0 END) AS verified,"
            f" SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,"
            f" SUM(CASE WHEN status='in_progress' THEN 1 ELSE 0 END) AS in_progress,"
            f" SUM(CASE WHEN status='blocked' THEN 1 ELSE 0 END) AS blocked,"
            f" SUM(CASE WHEN status='accepted' THEN 1 ELSE 0 END) AS accepted"
            f" FROM tickets WHERE workflow_id IN ({ph}) AND phase IS NOT NULL"
            f" GROUP BY workflow_id, phase", wf_ids):
        phase_data.setdefault(r["workflow_id"], {})[r["phase"]] = dict(r)
    out = []
    for r in rows:
        wf = dict(r)
        wid = wf["id"]
        phases = json.loads(wf["phases"])
        tc = ticket_counts[wid]
        pct = round(100 * tc["verified"] / tc["total"]) if tc["total"] else 0
        pd = phase_data.get(wid, {})
        ps = []
        for p in phases:
            d = pd.get(p, {})
            ps.append({"phase": p, "total": d.get("n", 0), "verified": d.get("verified", 0),
                        "done": d.get("done", 0), "in_progress": d.get("in_progress", 0),
                        "blocked": d.get("blocked", 0), "accepted": d.get("accepted", 0),
                        "ready": 0, "dispatched": 0})
        out.append({"id": wid, "name": wf["name"], "status": wf["status"],
                     "coordinator": wf["coordinator"], "kind": wf["kind"],
                     "pct_global": pct, "open_findings": finding_counts[wid],
                     "phases_summary": ps, "created_at": wf["created_at"]})
    return out


def _batch_unmet_deps(conn, ticket_ids: list[int]) -> dict[int, list[dict]]:
    """Batch version of _unmet_deps: one query for all tickets (T0.5)."""
    if not ticket_ids:
        return {}
    ph = ",".join("?" * len(ticket_ids))
    rows = conn.execute(
        "SELECT d.ticket_id, t.id, t.plan_key, t.title, t.status"
        " FROM ticket_deps d JOIN tickets t ON t.id = d.depends_on"
        f" WHERE d.ticket_id IN ({ph}) AND t.status != 'verified'",
        ticket_ids).fetchall()
    result: dict[int, list[dict]] = {tid: [] for tid in ticket_ids}
    for r in rows:
        result[r["ticket_id"]].append({"id": r["id"], "plan_key": r["plan_key"],
                                        "title": r["title"], "status": r["status"]})
    return result


def _batch_findings(conn, workflow_id) -> list[dict]:
    """Batch list_findings: one query with correction ticket join (T0.5)."""
    rows = conn.execute(
        "SELECT f.*, ct.id AS ct_id, ct.plan_key AS ct_plan_key, ct.status AS ct_status"
        " FROM findings f LEFT JOIN tickets ct ON ct.id = f.correction_ticket_id"
        " WHERE f.workflow_id=?"
        " ORDER BY CASE f.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1"
        "   WHEN 'medium' THEN 2 ELSE 3 END, f.id", (workflow_id,)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if d.pop("ct_id", None):
            d["correction_ticket"] = {"id": r["ct_id"], "plan_key": r["ct_plan_key"],
                                       "status": r["ct_status"]}
        else:
            d["correction_ticket"] = None
        d.pop("ct_plan_key", None)
        d.pop("ct_status", None)
        result.append(d)
    return result


def workflow_status(conn, workflow_id, embed_tickets=True) -> dict:
    """El payload del contrato con Lienzo (A-F): tickets embebidos por fase con
    ready/unmet_deps, findings con corrección embebida, milestones pendientes."""
    wf = get_workflow(conn, workflow_id)
    tickets = service.list_tickets(conn, include_closed=True, workflow_id=workflow_id)
    unmet_map = _batch_unmet_deps(conn, [t["id"] for t in tickets])
    for t in tickets:
        unmet = unmet_map.get(t["id"], [])
        t["unmet_deps"] = unmet
        t["ready"] = not unmet

    by_phase = {p: [] for p in wf["phases"]}
    stray = []
    for t in sorted(tickets, key=lambda x: (x.get("plan_key") or "", x["id"])):
        if t["phase"] in by_phase:
            by_phase[t["phase"]].append(t)
        else:
            stray.append(t)

    def _summary(ts):
        c = {"total": len(ts), "verified": 0, "done": 0, "in_progress": 0,
             "blocked": 0, "accepted": 0, "ready": 0, "dispatched": 0}
        for t in ts:
            s = t["status"]
            if s in c:
                c[s] += 1
            if t["ready"] and s in ("accepted", "blocked"):
                c["ready"] += 1
            if t.get("dispatched_to") and s not in ("verified", "rejected", "expired"):
                c["dispatched"] += 1
        return c

    phases_summary = [{"phase": p, **_summary(by_phase[p])} for p in wf["phases"]]
    total = sum(ps["total"] for ps in phases_summary) + len(stray)
    verified = sum(ps["verified"] for ps in phases_summary) + sum(
        1 for t in stray if t["status"] == "verified")
    findings = _batch_findings(conn, workflow_id)
    milestones = [dict(r) for r in conn.execute(
        "SELECT * FROM milestones WHERE workflow_id=? ORDER BY id", (workflow_id,))]
    out = {
        **wf,
        "pct_global": round(100 * verified / total) if total else 0,
        "phases_summary": phases_summary,
        "open_findings": sum(1 for f in findings if f["status"] == "open"),
        "findings_by_status": {s: sum(1 for f in findings if f["status"] == s)
                               for s in ("open", "dispatched", "fixed", "dismissed")},
        "milestones": milestones,
        "milestones_pending_ecodb": [m for m in milestones if not m["ecodb_saved"]],
        "dispatched": [{"ticket_id": t["id"], "plan_key": t["plan_key"], "to": t["dispatched_to"],
                        "at": t["dispatched_at"], "status": t["status"]}
                       for t in tickets if t.get("dispatched_to")
                       and t["status"] not in ("verified", "rejected", "expired")],
    }
    # T3.3: bisagras del workflow — lo que el dueño tiene que decidir, visible aquí.
    from . import decisions as _dec
    wf_decisions = _dec.list_decisions(conn, workflow_id=workflow_id)
    out["decisions_pending"] = [d for d in wf_decisions if d["status"] != "decided"]
    out["decisions_pending_count"] = len(out["decisions_pending"])
    if embed_tickets:
        out["decisions"] = wf_decisions
        out["tickets_by_phase"] = by_phase
        out["stray_tickets"] = stray
        out["findings"] = findings
        out["latest_handoff"] = latest_handoff(conn, workflow_id)
        out["recent_activity"] = recent_activity(conn, workflow_id, limit=20,
                                                   _validated=True)
    return out


WORKFLOW_STATUSES = {"active", "paused", "closed", "cancelled", "archived"}
FINISHED = {"closed", "cancelled"}


def _validate_repo_root(repo_root: str) -> str:
    from pathlib import Path
    p = Path(repo_root)
    if not p.is_absolute() or not p.is_dir():
        raise TicketError(f"repo_root debe ser una carpeta absoluta existente: {repo_root!r}")
    return str(p.resolve())


def update_workflow(conn, agent, workflow_id, status=None, repo_root=None, reason=None, close=False) -> dict:
    """active|paused|closed como en v2; v3.1 (el dueño, queja 4ª): cancelled (con motivo) y
    archived (solo desde closed|cancelled; desarchivar = volver a closed)."""
    wf = get_workflow(conn, workflow_id)
    _require_coordinator(conn, agent, wf, "cambiar el workflow")
    if repo_root is not None:
        conn.execute("UPDATE workflows SET repo_root=? WHERE id=?",
                     (_validate_repo_root(repo_root), workflow_id))
    if status:
        if status not in WORKFLOW_STATUSES:
            raise TicketError(f"status inválido: {status!r} ({'|'.join(sorted(WORKFLOW_STATUSES))})")
        cur = wf["status"]
        if status == cur:
            pass
        elif status == "archived":
            if cur not in FINISHED:
                if not close:
                    raise TicketError(f"solo se archiva un workflow cerrado o cancelado (está {cur!r}):"
                                      " ciérralo o cancélalo primero, o pasa close=true (cerrar y archivar)")
                # el dueño (4-sep): "archivar" desde el menú de un activo = cerrar + archivar en un paso
                conn.execute("INSERT INTO milestones (workflow_id, kind, created_at)"
                             " VALUES (?, 'workflow_closed', ?)", (workflow_id, _now()))
                conn.execute("UPDATE workflows SET status='archived', closed_at=?, archived_at=? WHERE id=?",
                             (_now(), _now(), workflow_id))
            else:
                conn.execute("UPDATE workflows SET status='archived', archived_at=? WHERE id=?",
                             (_now(), workflow_id))
        elif cur == "archived":
            if status == "closed":
                conn.execute("UPDATE workflows SET status='closed', archived_at=NULL WHERE id=?",
                             (workflow_id,))
            elif status == "active":
                # el dueño (4-sep): "los archivados igual pero para volver a activarse" — un paso
                conn.execute("UPDATE workflows SET status='active', archived_at=NULL, closed_at=NULL"
                             " WHERE id=?", (workflow_id,))
            else:
                raise TicketError("un workflow archivado solo vuelve a 'closed' (desarchivar) o 'active' (reactivar)")
        elif status == "cancelled":
            if not (reason or "").strip():
                raise TicketError("cancelar un workflow requiere reason (por qué se abandona)")
            conn.execute("UPDATE workflows SET status='cancelled', closed_at=? WHERE id=?",
                         (_now(), workflow_id))
            for t in conn.execute("SELECT id, status FROM tickets WHERE workflow_id=? AND closed_at IS NULL",
                                  (workflow_id,)).fetchall():
                conn.execute("UPDATE tickets SET status='rejected', closed_at=? WHERE id=?",
                             (_now(), t["id"]))
                _append_history(conn, t["id"], t["status"], "rejected", agent,
                                f"[workflow cancelado] {reason.strip()}")
        elif status == "closed":
            conn.execute("UPDATE workflows SET status='closed', closed_at=? WHERE id=?",
                         (_now(), workflow_id))
            conn.execute("INSERT INTO milestones (workflow_id, kind, created_at)"
                         " VALUES (?, 'workflow_closed', ?)", (workflow_id, _now()))
        elif cur in FINISHED and status in {"active", "paused"}:
            conn.execute("UPDATE workflows SET status=?, closed_at=NULL WHERE id=?", (status, workflow_id))
        else:
            conn.execute("UPDATE workflows SET status=? WHERE id=?", (status, workflow_id))
    conn.commit()
    return workflow_status(conn, workflow_id, embed_tickets=False)


def memory_draft(conn, workflow_id, milestone_id) -> dict:
    """Borrador de memoria EcoDB listo para que el coordinador lo guarde con SU MCP
    (el daemon no toca credenciales de EcoDB — SPEC v2 §2)."""
    wf = get_workflow(conn, workflow_id)
    m = conn.execute("SELECT * FROM milestones WHERE id=? AND workflow_id=?",
                     (milestone_id, workflow_id)).fetchone()
    if m is None:
        raise TicketError(f"milestone #{milestone_id} no existe en workflow #{workflow_id}")
    m = dict(m)
    if m["kind"] == "phase_done":
        tickets = [t for t in service.list_tickets(conn, include_closed=True)
                   if t.get("workflow_id") == workflow_id and t.get("phase") == m["phase"]]
        scope = f"fase '{m['phase']}'"
    else:
        tickets = [t for t in service.list_tickets(conn, include_closed=True)
                   if t.get("workflow_id") == workflow_id]
        scope = "workflow completo"
    findings = list_findings(conn, workflow_id)
    f_rel = [f for f in findings if m["kind"] == "workflow_closed"
             or (f["ticket_id"] in {t["id"] for t in tickets} if f["ticket_id"] else True)]
    # Prosa buscable, no volcado de datos (review Eco): frase de apertura con el
    # resumen, y las decisiones/notas del history — es lo que una memoria necesita.
    verified_n = sum(1 for t in tickets if t["status"] == "verified")
    fixed_n = sum(1 for f in f_rel if f["status"] == "fixed")
    dism = [f for f in f_rel if f["status"] == "dismissed"]
    open_items = [t for t in tickets if t["closed_at"] is None]
    lines = [
        f"Workflow '{wf['name']}': {scope} completado el {m['created_at'][:10]} "
        f"bajo coordinación de {wf['coordinator']}. {verified_n} de {len(tickets)} tareas "
        f"verificadas; {fixed_n} hallazgos corregidos de {len(f_rel)} registrados.",
        "",
        "Qué se hizo:"]
    for t in tickets:
        ev = f" Evidencia: [{t['evidence_type']}] {str(t['evidence'])[:60]}." if t.get("evidence_type") else ""
        lines.append(f"- {t.get('plan_key') or '#'+str(t['id'])} {t['title']} — {t['status']}"
                     f" (hizo {t.get('owner') or '-'}, verificó {t.get('verified_by') or '-'}).{ev}")
    notes = [dict(r) for r in conn.execute(
        "SELECT DISTINCT h.note FROM ticket_history h JOIN tickets t ON t.id=h.ticket_id"
        " WHERE t.workflow_id=? AND h.note IS NOT NULL AND h.note != ''"
        " AND h.note NOT LIKE 'self-verified%' AND h.note NOT LIKE '[%'"
        " ORDER BY h.id", (workflow_id,))]
    if notes:
        lines += ["", "Decisiones y notas de ejecución:"]
        lines += [f"- {n['note'][:160]}" for n in notes[:15]]
    if f_rel:
        lines += ["", "Hallazgos:"]
        for f in f_rel:
            fix = f" Corregido en FIX #{f['correction_ticket_id']}." if f["status"] == "fixed" else ""
            lines.append(f"- [{f['severity']}/{f['category']}] {f['title']}"
                         f" (cazado por {f['found_by']}, {f['status']}).{fix}")
    if dism:
        lines += ["", "Descartados con motivo (conocimiento negativo):"]
        lines += [f"- {f['title']}: {f['resolved_note']}" for f in dism]
    if open_items:
        lines += ["", "Queda abierto deliberadamente:"]
        lines += [f"- {t.get('plan_key') or '#'+str(t['id'])} {t['title']} ({t['status']})"
                  for t in open_items]
    lines += ["", f"Spec: {wf.get('spec_path') or '-'} | Plan: {wf.get('plan_path') or '-'}."]
    return {"milestone": m,
            "suggested_type": "tecnico",
            "suggested_tags": ["workflow", wf["name"], "hito",
                               m["phase"] or "cierre", "agentic-os"],
            "content": "\n".join(lines)}


def mark_milestone_saved(conn, agent, workflow_id, milestone_id, memory_id) -> dict:
    _validate_agent(agent)
    wf = get_workflow(conn, workflow_id)
    _require_coordinator(conn, agent, wf, "marcar hito guardado")
    cur = conn.execute("UPDATE milestones SET ecodb_saved=1, memory_id=? WHERE id=? AND workflow_id=?",
                       (memory_id, milestone_id, workflow_id))
    if cur.rowcount == 0:
        raise TicketError(f"milestone #{milestone_id} no existe en workflow #{workflow_id}")
    conn.commit()
    return dict(conn.execute("SELECT * FROM milestones WHERE id=?", (milestone_id,)).fetchone())
