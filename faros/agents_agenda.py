"""Calendario PROPIO de Agentes (v3.1, T5.1) — el dueño, queja 1ª: ver en el tiempo los jobs
programados (una vez, cada N días, días de la semana) y lo que ya corrió.

Decisión 4 (2-sep) intacta: este calendario NO se mezcla con el del Tablero
(/api/calendar). Mismo shape de item que agenda.occurrences para que Lienzo
reutilice Día/Semana/Mes/Año:

  kind ∈ {job_once, job_recurring, run}
  date 'YYYY-MM-DD', start 'HH:MM'|None, end 'HH:MM'|None, all_day bool
  job_id, name/title, harness, model, schedule_human, status (job: active|paused;
  run: ok|error|timeout|cancelled|running), run_id, verdict, cost_usd, ghost.

Las ocurrencias futuras se proyectan con schedule.next_fire (misma función que
usa el scheduler: lo que se pinta es lo que va a disparar). Los runs se pintan
el día que empezaron. Un job pausado NO proyecta futuro (no va a disparar) pero
sus runs pasados siguen.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from . import jobs as jm, schedule as sched_mod
from .service import TicketError

MAX_RANGE_DAYS = 400
MAX_OCCURRENCES_PER_JOB = 120


def _parse(s: str, name: str) -> date:
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        raise TicketError(f"{name} inválido: {s!r} (YYYY-MM-DD)")


def _job_base(j: dict) -> dict:
    return {
        "job_id": j["id"], "title": j["name"], "name": j["name"],
        "harness": j["harness"], "model": j["model"], "status": j["status"],
        "schedule_human": sched_mod.human(j["schedule"]),
        "schedule_type": (j["schedule"] or {}).get("type", "manual"),
        "all_day": False, "ghost": False,
    }


def occurrences(conn, from_date, to_date, now: datetime | None = None) -> dict:
    d_from, d_to = _parse(from_date, "from"), _parse(to_date, "to")
    if d_to < d_from:
        raise TicketError("to < from")
    if (d_to - d_from).days > MAX_RANGE_DAYS:
        raise TicketError(f"rango demasiado grande (> {MAX_RANGE_DAYS} días)")
    now = now or datetime.now()
    range_start = datetime.combine(d_from, datetime.min.time())
    range_end = datetime.combine(d_to, datetime.max.time())
    items: list[dict] = []

    jobs = jm.list_jobs(conn, include_archived=False)
    by_id = {j["id"]: j for j in jobs}
    for j in jobs:
        stype = (j["schedule"] or {}).get("type", "manual")
        if j["status"] != "active" or stype == "manual":
            continue
        base = _job_base(j)
        kind = "job_once" if stype == "once" else "job_recurring"
        # Proyección desde el borde del rango o desde ahora (lo pasado sin run no se pinta:
        # el scheduler lo dispara por catch-up y entonces será un run).
        cursor = max(range_start, now) - timedelta(seconds=1)
        for n in range(MAX_OCCURRENCES_PER_JOB):
            nf = sched_mod.next_fire(j["schedule"], j["created_at"], cursor)
            if nf is None or nf > range_end:
                break
            items.append({**base, "kind": kind, "date": nf.date().isoformat(),
                          "start": nf.strftime("%H:%M"), "end": None,
                          "fire_at": nf.isoformat(timespec="minutes"), "occurrence_n": n})
            cursor = nf
            if stype == "once":
                break

    runs = conn.execute(
        "SELECT * FROM runs WHERE started_at >= ? AND started_at <= ? ORDER BY started_at",
        (range_start.isoformat(timespec="seconds"), range_end.isoformat(timespec="seconds"))).fetchall()
    for r in runs:
        r = dict(r)
        j = by_id.get(r["job_id"]) or jm.get_job(conn, r["job_id"])
        base = _job_base(j)
        start = r["started_at"][11:16]
        end = r["finished_at"][11:16] if r.get("finished_at") and r["finished_at"][:10] == r["started_at"][:10] else None
        items.append({**base, "kind": "run", "status": r["status"], "date": r["started_at"][:10],
                      "start": start, "end": end, "run_id": r["id"], "verdict": r.get("verdict"),
                      "verdict_reason": r.get("verdict_reason"), "cost_usd": r.get("cost_usd"),
                      "started_at": r["started_at"], "finished_at": r.get("finished_at"),
                      "occurrence_n": 0})

    items.sort(key=lambda x: (x["date"], x["start"] or "", x["job_id"]))
    return {"from": d_from.isoformat(), "to": d_to.isoformat(),
            "lanes": sorted({j["harness"] for j in jobs}) or ["claude-cli"],
            "items": items}
