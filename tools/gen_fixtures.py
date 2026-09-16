#!/usr/bin/env python
"""gen_fixtures.py — genera los 12 JSON de app/renderer/fixtures/ desde UNA lista.

POR QUE EXISTE. `app/renderer/fixtures/` no eran fixtures: era un volcado del
tablero real. 153 apariciones de un nombre propio en workflow.json, 90 en
calendar.json, la ciudad del duenno en dos ficheros mas. Nadie lo habia auditado
nunca PORQUE LA CARPETA SE LLAMA «FIXTURES» — el nombre es lo que la protege de
la mirada.

EL ROSTER SALE DEL DEFAULT NEUTRO, NO DEL RESUELTO. `roster.load()` devuelve lo
que haya en esta maquina (env > roster.json > default), y en la maquina de quien
escribio esto devuelve la casa entera. Para datos que van a un repo publico eso es
exactamente lo que no se quiere: lo que se publica no lleva dentro los nombres de
nadie. Asi que por defecto se usan DEFAULT_OWNER / DEFAULT_AGENTS, que es lo que
ve un clon limpio, y `--roster live` es la opcion explicita para lo contrario.
Cambiar de uno a otro cuesta un flag y una regeneracion, no una tarde.

EL TABLERO TIENE QUE DEMOSTRAR, NO RELLENAR. Quien abra el repo manana no es un
usuario buscando una herramienta: es alguien comprobando si el sistema existe. Asi
que el tablero de ejemplo ensena EL CICLO COMPLETO, que es lo que nadie mas tiene
corriendo a diario: una tarea PROPUESTA POR UN AGENTE, una en curso con duenno
agentico y su run vivo, una hecha SIN VERIFICAR (la cola de verificacion es lo
distintivo), una verificada POR UN AGENTE DISTINTO DEL DUENNO, una bloqueada con
motivo y una recurrente con cadencia y criterio.

Uso:
    python tools/gen_fixtures.py                 # neutro, en ingles (lo que va al repo)
    python tools/gen_fixtures.py --lang es       # neutro, en espanol
    python tools/gen_fixtures.py --roster live   # con el roster de esta maquina
    python tools/gen_fixtures.py --check         # no escribe: audita lo que hay
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "app" / "renderer" / "fixtures"
sys.path.insert(0, str(REPO))

from faros import roster  # noqa: E402


# ---------------------------------------------------------------- la lista
# Esto es lo unico que hay que tocar para cambiar quien aparece en las capturas.

def quienes(modo: str) -> tuple[str, list[str]]:
    if modo == "live":
        owner, agents = roster.load() if hasattr(roster, "load") else (roster.HOUSE_OWNER, sorted(roster.AGENTS))
        return owner, [a for a in agents if a != owner]
    return roster.DEFAULT_OWNER, list(roster.DEFAULT_AGENTS)


# Dominio y rutas INVENTADOS. Nunca los de nadie: si la fixture dice
# %LOCALAPPDATA%\AgenticOS, la captura lo ensena aunque la barra diga FarOS.
DOMINIO = "example.org"
CWD_EJEMPLO = "/srv/projects/atlas"
HOME_EJEMPLO = "$FAROS_HOME"

# EL ANCLA TEMPORAL ES EL DIA EN QUE SE GENERA, NO UNA FECHA ESCRITA.
# La primera version la clavaba en una fecha fija «para que los diffs fueran
# estables», y el resultado fue un tablero cuatro meses en el pasado: la vista por
# defecto («hoy») solo admite vencidas, en curso, hechas o con vencimiento <= hoy,
# asi que con el ancla vieja TODO salia vencido y dos columnas salian vacias.
# Una fecha escrita a mano es una declaracion que envejece sin enterarse. El precio
# —que el diff cambie de un dia para otro— es mas barato que una captura que miente.
HOY = date.today()


def d(delta: int) -> str:
    return (HOY + timedelta(days=delta)).isoformat()


def dt(delta: int, hhmm: str) -> str:
    return f"{d(delta)}T{hhmm}"


TEXTOS = {
    "en": {
        "projects": ["Platform", "Website", "Research"],
        "t_recurring": "Weekly database backup",
        "t_recurring_crit": "backup file present in the archive and restore test passing",
        "t_proposed": "Rotate the API credentials",
        "t_proposed_desc": "Found while reviewing the deploy log: the key has not been rotated in 90 days.",
        "t_accepted_1": "Migrate the metrics dashboard",
        "t_accepted_2": "Write the onboarding guide",
        "t_accepted_3": "Review the accessibility report",
        "t_progress": "Watch the daemon every 2 h",
        "t_done": "Publish release notes",
        "t_done_ev": "docs/releases/2026-05.md",
        "t_verified": "Fix the calendar drag on narrow screens",
        "t_verified_note": "Reproduced at 900 px before and after: the drop lands on the right day.",
        "t_blocked": "Import the legacy tasks",
        "t_blocked_why": "waiting on the export from the old system; asked on the 8th, no answer yet",
        "j_watch": "Watch the daemon every 2 h",
        "j_digest": "Morning inbox digest",
        "j_backup": "Back up the database to the archive",
        "j_market": "Weekly market report",
        "j_sweep": "Verification sweep",
        "r_ok": "3 threads needing a reply; no newsletters.",
        "r_fail": "exit 1: web search denied without an allowlist.",
        "r_backup": "Archive written, 41 MB, restore test passed.",
        "dec_title": "Which harness runs the nightly jobs",
        "dec_ctx": "The cheap model is enough for the digest but not for the report.",
        "wf_name": "platform-v3",
        "wf_name2": "website-refresh",
        "f_open": "The daemon restart loses the run in flight",
        "f_closed": "The release notes link pointed to the old tag",
        "ho_lead": "The UI phase is halfway: the board is done, the calendar is not.",
        "ho_body": "Next session: start with the drag on narrow screens. The backend contract did not change.",
        "ms": "Core phase verified end to end",
        "ac_verified": "moved the task to verified",
        "ac_done": "completed the task with evidence",
        "mcp_note": "Local calendar MCP (stdio). Enable after finishing OAuth. Tools: list_events, create_event.",
    },
    "es": {
        "projects": ["Plataforma", "Web", "Investigación"],
        "t_recurring": "Copia semanal de la base de datos",
        "t_recurring_crit": "fichero de copia en el archivo y prueba de restauración en verde",
        "t_proposed": "Rotar las credenciales de la API",
        "t_proposed_desc": "Encontrado revisando el registro de despliegue: la clave lleva 90 días sin rotar.",
        "t_accepted_1": "Migrar el panel de métricas",
        "t_accepted_2": "Escribir la guía de alta",
        "t_accepted_3": "Revisar el informe de accesibilidad",
        "t_progress": "Vigilar el daemon cada 2 h",
        "t_done": "Publicar las notas de la versión",
        "t_done_ev": "docs/releases/2026-05.md",
        "t_verified": "Arreglar el arrastre del calendario en pantallas estrechas",
        "t_verified_note": "Reproducido a 900 px antes y después: la soltada cae en el día correcto.",
        "t_blocked": "Importar las tareas del sistema viejo",
        "t_blocked_why": "esperando la exportación del sistema anterior; pedida el día 8, sin respuesta",
        "j_watch": "Vigilar el daemon cada 2 h",
        "j_digest": "Resumen de la bandeja de la mañana",
        "j_backup": "Copiar la base de datos al archivo",
        "j_market": "Informe semanal de mercado",
        "j_sweep": "Barrido de verificación",
        "r_ok": "3 conversaciones que piden respuesta; sin boletines.",
        "r_fail": "exit 1: búsqueda web denegada sin lista de permitidos.",
        "r_backup": "Archivo escrito, 41 MB, prueba de restauración en verde.",
        "dec_title": "Qué harness ejecuta los jobs de la noche",
        "dec_ctx": "El modelo barato basta para el resumen pero no para el informe.",
        "wf_name": "plataforma-v3",
        "wf_name2": "web-renovacion",
        "f_open": "El reinicio del daemon pierde el run en vuelo",
        "f_closed": "El enlace de las notas apuntaba a la etiqueta antigua",
        "ho_lead": "La fase de interfaz va por la mitad: el tablero está, el calendario no.",
        "ho_body": "Siguiente sesión: empezar por el arrastre en pantallas estrechas. El contrato del backend no cambió.",
        "ms": "Fase núcleo verificada de punta a punta",
        "ac_verified": "movió la tarea a verificada",
        "ac_done": "completó la tarea con evidencia",
        "mcp_note": "MCP local de calendario (stdio). Activar tras completar OAuth. Tools: list_events, create_event.",
    },
}


def ticket(**kw) -> dict:
    """Un ticket con los ~29 campos del contrato. Lo que no se pasa va a null,
    que es lo que manda el motor: una fixture que omite campos ensena una UI que
    el motor real no produce."""
    base = {
        "id": 0, "project_id": 1, "kind": "manual", "title": "", "description": "",
        "owner": None, "source": "manual", "status": "proposed", "priority": "media",
        "due_at": None, "verify_criteria": None, "blocked_reason": None,
        "verification_level": "self", "evidence_type": None, "evidence": None,
        "verified_by": None, "expires_at": None, "cadence_days": None,
        "last_done_at": None, "created_by": None, "created_at": dt(-8, "09:20:09"),
        "closed_at": None, "due_state": "ok", "scheduled_at": None, "duration_min": None,
        "all_day": 0, "last_change": None, "preferred_time": None,
        "next_due_override": None,
    }
    base.update(kw)
    return base


def construir(owner: str, agents: list[str], lang: str) -> dict[str, object]:
    T = TEXTOS[lang]
    a1, a2, a3, a4 = (agents + agents)[:4]
    P = T["projects"]

    projects = [
        {"id": i + 1, "name": n, "color": c, "position": i, "archived": 0, "created_at": dt(-40, "10:00:00")}
        for i, (n, c) in enumerate(zip(P, ["oklch(58% 0.12 50)", "oklch(52% 0.10 240)", "oklch(50% 0.10 150)"]))
    ]

    # --- las seis que demuestran el ciclo -------------------------------
    propuesta = ticket(
        id=101, project_id=1, title=T["t_proposed"], description=T["t_proposed_desc"],
        owner=a1, source="agent", status="proposed", priority="alta",
        # due_at hoy: si no, la vista por defecto no la ensena y la captura del
        # README pierde justo lo que la hace distinta — una tarea que propuso un agente
        created_by=a1, created_at=dt(0, "07:12:40"), due_at=d(0), due_state="due",
        verify_criteria=T["t_recurring_crit"],
        last_change={"at": dt(0, "07:12:40"), "by": a1, "from": None, "to": "proposed"},
    )
    aceptadas = [
        # Las aceptadas llevan vencimiento HOY a proposito: la vista por defecto solo
        # admite lo que vence hoy o antes, y una columna «Aceptadas» con una sola tarjeta
        # no parece un tablero de trabajo — parece una demo vacia. Un dia real tiene varias.
        ticket(id=102, project_id=2, title=T["t_accepted_1"], owner=a2, status="accepted",
               priority="alta", due_at=d(0), created_by=owner, due_state="due",
               scheduled_at=dt(0, "09:00"), duration_min=90,
               last_change={"at": dt(-1, "18:25:00"), "by": a2, "from": "proposed", "to": "accepted"}),
        ticket(id=103, project_id=1, title=T["t_accepted_2"], owner=a3, status="accepted",
               priority="media", due_at=d(0), created_by=owner, due_state="due",
               scheduled_at=dt(0, "12:00"), duration_min=60,
               last_change={"at": dt(-1, "11:02:00"), "by": a3, "from": "proposed", "to": "accepted"}),
        # la recurrente: cadencia, criterio y un brazo agentico detras
        ticket(id=104, project_id=1, title=T["t_recurring"], owner=a4, status="accepted",
               priority="media", cadence_days=7, last_done_at=d(-7), due_at=d(0),
               due_state="due", verification_level="auto", verify_criteria=T["t_recurring_crit"],
               created_by=owner, preferred_time="03:30",
               last_change={"at": dt(-7, "03:31:00"), "by": a4, "from": "in_progress", "to": "accepted"},
               linked_job={"id": 3, "name": T["j_backup"], "status": "active",
                           "last_run": {"id": 38, "status": "ok", "verdict": "pass", "finished_at": dt(-7, "03:34:12")}}),
        ticket(id=105, project_id=3, title=T["t_accepted_3"], owner=a1, status="accepted",
               priority="baja", created_by=a2, due_at=d(3), due_state="ok",
               last_change={"at": dt(-2, "16:40:00"), "by": a1, "from": "proposed", "to": "accepted"}),
    ]
    en_curso = ticket(
        id=106, project_id=1, title=T["t_progress"], owner=a4, status="in_progress",
        priority="alta", created_by=owner, due_at=d(0), due_state="due",
        verification_level="auto", verify_criteria=T["t_recurring_crit"],
        last_change={"at": dt(0, "08:05:00"), "by": a4, "from": "accepted", "to": "in_progress"},
        linked_job={"id": 4, "name": T["j_watch"], "status": "active",
                    "last_run": {"id": 44, "status": "running", "verdict": None, "finished_at": None}},
    )
    # HECHA SIN VERIFICAR: la cola de verificacion es lo distintivo del producto
    hecha = ticket(
        id=107, project_id=2, title=T["t_done"], owner=a2, status="done",
        priority="media", created_by=owner, evidence_type="file_path", evidence=T["t_done_ev"],
        verification_level="peer", verify_criteria=T["t_recurring_crit"],
        closed_at=dt(0, "08:41:00"), due_state="ok",
        last_change={"at": dt(0, "08:41:00"), "by": a2, "from": "in_progress", "to": "done"},
    )
    # VERIFICADA POR OTRO: verifier != owner, que es la regla que sostiene el sistema
    verificada = ticket(
        id=108, project_id=2, title=T["t_verified"], owner=a3, status="verified",
        priority="alta", created_by=a2, evidence_type="command_output",
        evidence="playwright: 12 passed", verification_level="peer",
        # verificada hoy, y con vencimiento hoy, para que la columna Verified no salga
        # vacia en la vista que se captura: el ciclo se ve entero o no se ve
        verified_by=a1, closed_at=dt(0, "10:10:00"), due_at=d(0), due_state="due",
        last_change={"at": dt(0, "10:10:00"), "by": a1, "from": "done", "to": "verified"},
    )
    bloqueada = ticket(
        id=109, project_id=3, title=T["t_blocked"], owner=a1, status="blocked",
        priority="media", created_by=owner, blocked_reason=T["t_blocked_why"],
        due_at=d(-3), due_state="overdue",
        last_change={"at": dt(-3, "10:15:00"), "by": a1, "from": "accepted", "to": "blocked"},
    )

    board = {
        "view": "today", "project_id": None, "projects": projects,
        "columns": {
            "proposed": [propuesta], "accepted": aceptadas, "in_progress": [en_curso],
            "done": [hecha], "verified": [verificada],
        },
        "laterals": {"blocked": [bloqueada]},
        "counts": {"proposed": 1, "accepted": len(aceptadas), "in_progress": 1,
                   "done": 1, "verified": 1, "blocked": 1},
    }

    todos = [propuesta, *aceptadas, en_curso, hecha, verificada, bloqueada]

    # --- calendario: los mismos tickets colocados en el tiempo ----------
    cal_items = []
    # LAS HORAS SE REPARTEN. La primera version mandaba a las 10:00 todo lo que solo
    # tenia vencimiento, y la vista Semana salia con seis tarjetas apiladas en la misma
    # franja, cortadas a 25 px y sin poder leer ninguna. No es un problema del calendario:
    # es que un dia de trabajo de verdad no tiene todo a la misma hora, y una fixture que
    # lo pone asi no ensena el sistema — ensena un choque.
    # Y NO BASTA CON REPARTIR: hay que ESQUIVAR las horas que ya ocupan los tickets con
    # hora propia. Ciclar sobre una lista corta volvia a juntar tres a las 09:00, donde ya
    # habia una programada — el mismo choque con otra cara. Se lleva cuenta de lo ocupado.
    libres = ["08:30", "10:30", "11:30", "13:00", "14:30", "16:00", "17:30", "18:30"]
    ocupadas = {t["scheduled_at"][11:16] for t in todos if t["scheduled_at"]}
    turno = (h for h in libres if h not in ocupadas)
    for t in todos:
        cuando = t["scheduled_at"] or (f'{t["due_at"]}T{next(turno, "18:00")}' if t["due_at"] else None)
        if not cuando:
            continue
        cal_items.append({
            "ticket_id": t["id"], "plan_key": None, "title": t["title"], "owner": t["owner"],
            "project_id": t["project_id"], "status": t["status"], "priority": t["priority"],
            "due_state": t["due_state"], "last_change": t["last_change"],
            "cadence_days": t["cadence_days"], "preferred_time": t["preferred_time"],
            "all_day": t["all_day"], "duration_min": t["duration_min"] or 60,
            "kind": "ticket", "date": cuando[:10], "start": cuando[11:16],
            "end": None, "ghost": False, "occurrence_n": None,
        })
    # LAS OCURRENCIAS PROYECTADAS CUBREN EL RANGO ENTERO, no dos sueltas. No es rellenar:
    # es lo que hace el motor — una recurrente semanal proyecta su cadencia por delante, y
    # el calendario existe para ensenar eso. Con dos, la vista Mes salia casi en blanco y
    # un mes vacio en el README no dice «sistema joven», dice «sistema sin usar».
    for n in range(1, 5):
        cal_items.append({
            "ticket_id": 104, "plan_key": None, "title": T["t_recurring"], "owner": a4,
            "project_id": 1, "status": "accepted", "priority": "media", "due_state": "ok",
            "last_change": None, "cadence_days": 7, "preferred_time": "03:30", "all_day": 0,
            "duration_min": 30, "kind": "ticket", "date": d(7 * n), "start": "03:30",
            "end": None, "ghost": True, "occurrence_n": n,
        })
    calendar = {"from": d(-7), "to": d(21), "lanes": [owner, *agents], "slots": [], "items": cal_items}

    # --- agentes: jobs y runs -------------------------------------------
    def job(i, name, status, harness, model, sched, **kw):
        base = {
            "id": i, "name": name, "status": status, "harness": harness, "model": model,
            "prompt": f"Check the state and report what changed. Working dir: {CWD_EJEMPLO}",
            "cwd": CWD_EJEMPLO, "permission_mode": "dontAsk", "allowed_tools": ["Read", "Bash"],
            "mcp_config": None, "inherit_mcp": [], "strict_mcp": 0, "add_dirs": [],
            "effort": None, "fallback_model": None, "system_prompt": None, "json_schema": None,
            "max_budget_usd": 1.0, "billing_mode": "subscription", "timeout_s": 1800,
            "schedule": sched, "verify_level": "auto",
            "verify_criteria": T["t_recurring_crit"], "ticket_id": None, "project_id": 1,
        }
        base.update(kw)
        return base

    jobs = [
        job(1, T["j_digest"], "active", "claude-cli", "haiku",
            {"type": "recurring", "every_days": 1, "at": ["08:00"]}),
        job(2, T["j_market"], "paused", "opencode", "deepseek-v4-pro",
            {"type": "weekly", "weekdays": [0], "at": ["07:30"]}),
        job(3, T["j_backup"], "active", "claude-cli", "haiku",
            {"type": "weekly", "weekdays": [6], "at": ["03:30"]}, ticket_id=104),
        job(4, T["j_watch"], "active", "claude-cli", "haiku",
            {"type": "recurring", "every_days": 1, "at": ["09:00", "11:00", "13:00"]}, ticket_id=106),
        job(5, T["j_sweep"], "archived", "claude-cli", "sonnet", {"type": "manual"}),
    ]
    runs = {
        "1": [{"id": 51, "status": "ok", "started_at": dt(0, "08:00:04"), "finished_at": dt(0, "08:01:22"),
               "cost_usd": 0.004, "cost_kind": "equivalent", "verdict": "pass",
               "verdict_reason": None, "result_summary": T["r_ok"], "job_id": 1,
               "exit_code": 0, "session_id": "s-51", "output_path": None, "verified_at": dt(0, "08:05:00")}],
        "2": [{"id": 47, "status": "error", "started_at": dt(-7, "07:30:00"), "finished_at": dt(-7, "07:31:10"),
               "cost_usd": 0.0, "cost_kind": "equivalent", "verdict": "fail",
               "verdict_reason": T["r_fail"], "result_summary": T["r_fail"], "job_id": 2,
               "exit_code": 1, "session_id": "s-47", "output_path": None, "verified_at": dt(-7, "07:35:00")}],
        "3": [{"id": 38, "status": "ok", "started_at": dt(-7, "03:30:02"), "finished_at": dt(-7, "03:34:12"),
               "cost_usd": 0.012, "cost_kind": "equivalent", "verdict": "pass",
               "verdict_reason": None, "result_summary": T["r_backup"], "job_id": 3,
               "exit_code": 0, "session_id": "s-38", "output_path": None, "verified_at": dt(-7, "03:40:00")}],
        # run VIVO: la superficie de agentes tiene que poder ensenar algo corriendo
        "4": [{"id": 44, "status": "running", "started_at": dt(0, "09:00:01"), "finished_at": None,
               "cost_usd": None, "cost_kind": None, "verdict": None, "verdict_reason": None,
               "result_summary": None, "job_id": 4, "exit_code": None,
               "session_id": "s-44", "output_path": None, "verified_at": None}],
        "5": [],
    }
    ag_cal = {"from": d(-7), "to": d(14), "lanes": ["claude-cli", "opencode"], "items": [
        {"job_id": 1, "title": T["j_digest"], "name": T["j_digest"], "harness": "claude-cli",
         "model": "haiku", "status": "active", "schedule_human": "daily · 08:00",
         "schedule_type": "recurring", "all_day": 0, "ghost": False, "kind": "run",
         "date": d(0), "start": "08:00", "end": "08:01", "run_id": 51, "verdict": "pass",
         "verdict_reason": None, "cost_usd": 0.004, "started_at": dt(0, "08:00:04"),
         "finished_at": dt(0, "08:01:22"), "occurrence_n": None},
        {"job_id": 4, "title": T["j_watch"], "name": T["j_watch"], "harness": "claude-cli",
         "model": "haiku", "status": "active", "schedule_human": "daily · 09:00, 11:00, 13:00",
         "schedule_type": "recurring", "all_day": 0, "ghost": False, "kind": "run",
         "date": d(0), "start": "09:00", "end": None, "run_id": 44, "verdict": None,
         "verdict_reason": None, "cost_usd": None, "started_at": dt(0, "09:00:01"),
         "finished_at": None, "occurrence_n": None},
        {"job_id": 3, "title": T["j_backup"], "name": T["j_backup"], "harness": "claude-cli",
         "model": "haiku", "status": "active", "schedule_human": "weekly · 03:30",
         "schedule_type": "weekly", "all_day": 0, "ghost": True, "kind": "job",
         "date": d(2), "start": "03:30", "end": None, "run_id": None, "verdict": None,
         "verdict_reason": None, "cost_usd": None, "started_at": None,
         "finished_at": None, "occurrence_n": 1},
    ]}

    harnesses = [
        {"name": "claude-cli", "installed": True, "version": "2.1.0", "billing": "subscription",
         "capabilities": ["mcp", "json_schema", "effort"],
         "models": [{"alias": "haiku", "label": "Haiku 4.5", "tier": "cheap"},
                    {"alias": "sonnet", "label": "Sonnet 5", "tier": "mid"},
                    {"alias": "opus", "label": "Opus 4.8", "tier": "high"}],
         "models_source": "probe", "models_refreshed_at": dt(-1, "22:10:00"),
         "families": ["claude"]},
        {"name": "opencode", "installed": True, "version": "0.9.4", "billing": "api",
         "capabilities": ["mcp"],
         "models": [{"alias": "deepseek-v4-pro", "label": "DeepSeek v4 Pro", "tier": "high"}],
         "models_source": "static", "models_refreshed_at": None, "families": ["deepseek"]},
        {"name": "codex", "installed": False, "version": None, "billing": "api",
         "capabilities": [], "models": [], "models_source": "static",
         "models_refreshed_at": None, "families": []},
    ]
    mcp_servers = [{
        "id": 1, "name": "calendar", "transport": "stdio",
        "command": "node", "args": [f"{CWD_EJEMPLO}/mcp/calendar/index.js"], "url": None,
        "env": {"CALENDAR_TOKEN": "••••"}, "headers": {}, "enabled": 0,
        "note": T["mcp_note"], "source": "manual",
        "created_at": dt(-20, "12:00:00"), "updated_at": dt(-20, "12:00:00"),
    }]
    mcp_inherited = [
        {"name": "faros", "scope": "user", "transport": "stdio", "command": f"python -m faros.mcp"},
        {"name": "search", "scope": "user", "transport": "http", "command": f"https://search.{DOMINIO}/mcp"},
    ]
    decisions = [{
        "id": 1, "workflow_id": 1, "project_id": 1, "title": T["dec_title"],
        "context": T["dec_ctx"], "options": ["claude-cli", "opencode"], "asked_by": a4,
        "status": "open", "decided_by": None, "decision": None, "rationale": None,
        "deferred_until": None, "created_at": dt(-1, "17:00:00"), "decided_at": None,
        "history": [], "workflow_name": T["wf_name"],
    }]
    workflows_list = [
        {"id": 1, "name": T["wf_name"], "status": "active", "coordinator": a4, "kind": "build",
         "pct_global": 62, "open_findings": 2,
         "phases_summary": [{"phase": "1-core", "pct": 100}, {"phase": "2-ui", "pct": 40}],
         "created_at": dt(-30, "09:00:00")},
        {"id": 2, "name": T["wf_name2"], "status": "active", "coordinator": a2, "kind": "build",
         "pct_global": 18, "open_findings": 0,
         "phases_summary": [{"phase": "1-design", "pct": 18}], "created_at": dt(-6, "09:00:00")},
    ]
    # CONTRATO REAL DE workflow.json, leido de js/api.js y no deducido del board:
    # phases (lista de nombres) · tickets_by_phase (dict fase -> tickets) · findings ·
    # handoffs · milestones · recent_activity · coordinator. La primera version le puso
    # el esquema del BOARD (columns/laterals/counts) por analogia, y la app cantaba un
    # toast rojo «Cannot read properties of undefined (reading 'filter')» que ningun
    # pageerror recoge: un toast no es una excepcion. Se ve, no se detecta.
    wf_tickets = [
        {**t, "phase": ph, "ready": r, "dispatched_to": dp}
        for t, ph, r, dp in [
            (aceptadas[0], "2-ui", True, None),
            (aceptadas[1], "2-ui", True, a4),
            (en_curso, "2-ui", True, a4),
            (hecha, "1-core", True, None),
            (verificada, "1-core", True, None),
        ]
    ]
    por_fase = {"1-core": [t for t in wf_tickets if t["phase"] == "1-core"],
                "2-ui": [t for t in wf_tickets if t["phase"] == "2-ui"]}
    workflow = {
        "id": 1, "name": T["wf_name"], "status": "active", "coordinator": a4,
        "kind": "build", "pct_global": 0,  # lo recalcula el propio renderer
        "phases": ["1-core", "2-ui"],
        "tickets_by_phase": por_fase,
        "phases_summary": [],
        "findings": [
            {"id": 1, "workflow_id": 1, "ticket_id": 106, "title": T["f_open"],
             "severity": "alta", "status": "open", "found_by": a1,
             "created_at": dt(-1, "16:20:00"), "closed_at": None, "note": None},
            {"id": 2, "workflow_id": 1, "ticket_id": 107, "title": T["f_closed"],
             "severity": "media", "status": "closed", "found_by": a3,
             "created_at": dt(-4, "11:00:00"), "closed_at": dt(-2, "09:30:00"), "note": None},
        ],
        "handoffs": [
            {"id": 1, "workflow_id": 1, "author": a4, "created_at": dt(-1, "19:40:00"),
             "lead": T["ho_lead"], "body": T["ho_body"]},
        ],
        "latest_handoff": {"id": 1, "workflow_id": 1, "author": a4,
                           "created_at": dt(-1, "19:40:00"), "lead": T["ho_lead"], "body": T["ho_body"]},
        "milestones": [{"id": 1, "workflow_id": 1, "phase": "1-core", "title": T["ms"],
                        "reached_at": dt(-2, "18:00:00"), "saved_to_ecodb": 1}],
        "milestones_pending_ecodb": 0,
        "recent_activity": [
            {"at": dt(0, "10:10:00"), "by": a1, "kind": "transition",
             "text": T["ac_verified"], "ticket_id": 108},
            {"at": dt(0, "08:41:00"), "by": a2, "kind": "transition",
             "text": T["ac_done"], "ticket_id": 107},
        ],
        "projects": projects[:1],
    }
    meta = {
        "harnesses": ["claude-cli", "opencode"],
        "models": [{"alias": "haiku", "label": "Haiku 4.5", "tier": "cheap"},
                   {"alias": "sonnet", "label": "Sonnet 5", "tier": "mid"},
                   {"alias": "opus", "label": "Opus 4.8", "tier": "high"}],
        "permission_modes": ["dontAsk", "acceptEdits", "plan", "bypassPermissions"],
        "evidence_types": ["file_path", "command_output", "memory_id", "peer_attestation", "run_output"],
        "verification_levels": ["self", "peer", "auto"],
        "defaults": {"model": "haiku", "timeout_s": 1800, "permission_mode": "dontAsk",
                     "schedule": {"type": "manual"}, "verification_level": "auto"},
        "agents": [owner, *agents],
        "docs_dir": f"{HOME_EJEMPLO}/docs",
    }

    return {
        "board.json": board, "calendar.json": calendar, "agents_jobs.json": jobs,
        "agents_runs.json": runs, "agents_calendar.json": ag_cal, "harnesses.json": harnesses,
        "mcp_servers.json": mcp_servers, "mcp_inherited.json": mcp_inherited,
        "decisions.json": decisions, "workflows_list.json": workflows_list,
        "workflow.json": workflow, "meta.json": meta,
    }


# ---------------------------------------------------------------- auditoria
# La lista de lo que NO puede aparecer. El nombre viejo de la app entra aqui a
# proposito: no basta con cambiar el wordmark, porque puede viajar DENTRO de los
# datos — en una ruta de ejemplo o en un criterio de verificacion.
# Los NOMBRES PROPIOS se buscan como PALABRA COMPLETA; el resto como subcadena.
# La primera version buscaba todo como subcadena y salto con `saved_to_ecodb`, que es
# un nombre de CAMPO DEL CONTRATO del motor y no el nombre de nadie. Un detector que
# marca lo correcto obliga a revisar a mano justo lo que venia a ahorrar, y al tercer
# falso positivo se deja de mirar.
# LOS NOMBRES PROHIBIDOS SON LOS PRIVADOS, NO LOS DEL ROSTER RESUELTO.
# Lo encontro Hilo (#291) y el generador NO ARRANCABA en un clon limpio: alli no hay
# roster.json, el roster cae al DEFAULT NEUTRO, y entonces la lista de prohibidos pasa a
# ser {owner, alice, bob, carol, dave} — que son EXACTAMENTE los nombres que este fichero
# escribe. El detector se comia su propia salida: 27 fugas falsas y abortaba.
# Invisible en la maquina de la casa por construccion, porque aqui las dos listas son
# distintas. Misma forma que los trece estados vacios: una rama que el entorno de
# desarrollo no puede alcanzar.
# Si el roster de esta maquina YA es el neutro, no hay ningun nombre privado que
# proteger y la comprobacion no aplica — pero eso se DICE, no se calla.
def _nombres_privados() -> list[str]:
    neutro = {roster.DEFAULT_OWNER, *roster.DEFAULT_AGENTS}
    propios = {roster.HOUSE_OWNER, *roster.AGENTS} - neutro
    return sorted(propios)


NOMBRES = _nombres_privados()
# ESTRUCTURALES: no son datos de nadie, describen FORMAS que no deben viajar en una
# fixture publica — una ruta de usuario, un directorio de datos de Windows, un correo.
# Estas si pueden vivir en el fichero, porque no dicen QUIEN.
SUBCADENAS = [
    "AgenticOS", "LOCALAPPDATA", "@gmail", "gmail.com",
    # LAS RUTAS, EN LAS DOS CODIFICACIONES. En fuente JS y en JSON una ruta de Windows
    # va ESCAPADA, asi que los bytes son C:\\\\Users y un termino con UNA barra devuelve
    # CERO sobre un fichero que la contiene tres veces. No es un patron flojo: es una
    # codificacion que nadie habia considerado, y cualquier .js o .json puede traerla.
    # Lo encontro Hilo en api.js, donde mi barrido decia limpio y habia tres rutas suyas.
    "C:" + chr(92) + "Users",
    "C:" + chr(92)*2 + "Users",
    "/Users/", "/home/",
]

# Y LOS PROPIOS DE QUIEN INSTALA ESTO, QUE NO VIVEN AQUI.
# Este fichero existe para impedir que datos personales viajen en una fixture publica,
# asi que no puede llevarlos dentro: una ciudad y un nombre de empresa escritos aqui son
# exactamente la fuga que el fichero previene, en el fichero que la previene. Lo vio Hilo
# al parir el repo: las tres unicas coincidencias que quedaban estaban en este .py.
# Se pasan por fuera, uno por linea:
#     FAROS_DENY_FILE=ruta/deny.txt  python tools/gen_fixtures.py
# Sin fichero el generador SIGUE comprobando lo estructural y LO DICE en voz alta, que
# es lo contrario de un check que calla: un cero que no ha comparado nada se hereda.
def _terminos_propios() -> list[str]:
    ruta = os.environ.get("FAROS_DENY_FILE")
    if not ruta:
        return []
    try:
        lineas = Path(ruta).read_text(encoding="utf-8").splitlines()
    except OSError as e:
        print(f"ABORTADO: FAROS_DENY_FILE apunta a {ruta!r} y no se puede leer ({e}).",
              file=sys.stderr)
        raise SystemExit(2)
    return [l.strip() for l in lineas if l.strip() and not l.startswith("#")]
PROPIOS = _terminos_propios()
PROHIBIDO = NOMBRES + SUBCADENAS + PROPIOS


def auditar(textos: dict[str, str]) -> list[tuple[str, str]]:
    hallazgos = []
    for fichero, contenido in textos.items():
        for termino in NOMBRES:
            if termino and re.search(rf"\b{re.escape(termino)}\b", contenido, re.IGNORECASE):
                hallazgos.append((fichero, termino))
        for termino in SUBCADENAS + PROPIOS:
            if termino and re.search(re.escape(termino), contenido, re.IGNORECASE):
                hallazgos.append((fichero, termino))
    return hallazgos


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lang", choices=["en", "es"], default="en")
    ap.add_argument("--roster", choices=["neutral", "live"], default="neutral")
    ap.add_argument("--check", action="store_true", help="no escribe: audita lo que ya hay en disco")
    args = ap.parse_args()

    if args.check:
        textos = {p.name: p.read_text(encoding="utf-8") for p in sorted(OUT.glob("*.json"))}
        hallazgos = auditar(textos)
        print(f"auditados {len(textos)} ficheros contra {len(PROHIBIDO)} terminos")
        for f, t in hallazgos:
            print(f"  FUGA  {f}: {t!r}")
        # Un cero que no ha comparado nada se hereda: si la lista propia no esta
        # cargada, el LIMPIO vale menos de lo que parece y hay que decirlo AQUI,
        # pegado al veredicto, no en la ayuda.
        if not NOMBRES:
            print("AVISO: el roster de esta maquina YA es el neutro — no hay nombres")
            print("       propios que comprobar. Este LIMPIO no dice nada sobre nombres.")
        if not PROPIOS:
            print("AVISO: sin FAROS_DENY_FILE — ningun termino propio comprobado")
            print("       (ciudad, empresa, apodos). Lo estructural si: roster, rutas,")
            print("       %LOCALAPPDATA%, correos.")
        print("LIMPIO" if not hallazgos else f"\n{len(hallazgos)} fugas")
        return 1 if hallazgos else 0

    owner, agents = quienes(args.roster)
    datos = construir(owner, agents, args.lang)
    textos = {n: json.dumps(v, ensure_ascii=False, indent=2) + "\n" for n, v in datos.items()}

    # EL DETECTOR SE PRUEBA ANTES DE CREERLE. Un grep que nunca ha disparado no es
    # un detector: se le mete un termino prohibido a proposito y tiene que saltar.
    # EL CANARIO PLANTA EL CASO DIFICIL, NO EL FACIL. Plantar un nombre solo prueba
    # que el detector ve lo que ya veia; la ruta ESCAPADA es la que se coló de verdad
    # en api.js con el barrido en verde. Se plantan las dos formas y las dos tienen
    # que saltar, o no se escribe nada.
    casos = {
        "__canario_ruta_escapada__": '{"path": "C:' + chr(92)*2 + 'Users' + chr(92)*2 + 'Admin' + chr(92)*2 + 'Documents"}',
        "__canario_ruta_plana__": '{"path": "C:' + chr(92) + 'Users' + chr(92) + 'Admin"}',
    }
    # El canario de NOMBRE solo se planta si hay nombres privados que detectar. Plantarlo
    # cuando la lista esta vacia abortaria siempre en un clon limpio — cambiar un falso
    # positivo por un falso negativo del propio canario.
    if NOMBRES:
        casos["__canario_nombre__"] = json.dumps({"owner": NOMBRES[0]})
    for nombre, cuerpo in casos.items():
        if not auditar({nombre: cuerpo}):
            print(f"ABORTADO: el detector no salta con {nombre}.", file=sys.stderr)
            return 2

    hallazgos = auditar(textos) if args.roster == "neutral" else []
    if hallazgos:
        print("ABORTADO, hay fugas en lo generado:", file=sys.stderr)
        for f, t in hallazgos:
            print(f"  {f}: {t!r}", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    for nombre, texto in textos.items():
        (OUT / nombre).write_text(texto, encoding="utf-8")
    print(f"{len(textos)} ficheros escritos en {OUT.relative_to(REPO)}")
    # Un check que no comprueba y no lo dice es peor que no tenerlo: el cero se hereda.
    if not NOMBRES:
        print("AVISO: roster neutro en esta maquina — no se comprueban nombres propios")
        print("       porque no hay ninguno que proteger.")
    if PROPIOS:
        print(f"terminos propios: {len(PROPIOS)} cargados de FAROS_DENY_FILE")
    else:
        print("AVISO: sin FAROS_DENY_FILE. Se ha comprobado lo estructural (roster, rutas,")
        print("       %LOCALAPPDATA%, correos) pero NINGUN termino propio — ciudad, empresa,")
        print("       apodos. Si generas para publicar, pasa tu lista.")
    print(f"roster: {args.roster} ({owner} · {', '.join(agents)}) · idioma: {args.lang}")
    print("detector de fugas: probado con un caso plantado y sin hallazgos en lo generado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
