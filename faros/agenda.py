"""Capa temporal del Tablero (T2.1): ocurrencias de tareas en un rango.

(Se llama agenda.py y no calendar.py: un módulo calendar.py sombrea al de la
librería estándar cuando mcp_server.py se lanza como script — medido 2-sep.)

Proyecta en [from, to] (fechas ISO, inclusivas):
  - kind=scheduled: tareas con scheduled_at (hora + duración, o all_day)
  - kind=due:       tareas con due_at sin scheduled_at (banda "sin hora")
  - kind=recurrence: recurrentes — la PRÓXIMA (real, ghost=false) y las siguientes
                    proyectadas (ghost=true), respetando next_due_override (mueve
                    solo la próxima) y preferred_time.
NUNCA incluye jobs agénticos: decisión 4 del dueño, tienen su calendario en Agentes.
Puro sobre la DB (solo lecturas). Carriles = agentes de la Casa.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from . import service
from .service import AGENTS, TicketError

MAX_RANGE_DAYS = 400
MAX_OCCURRENCES_PER_TICKET = 60


def _parse_date(s: str, name: str) -> date:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        raise TicketError(f"{name} inválido: {s!r} (YYYY-MM-DD)")


def _end_time(start: str, duration_min: int | None) -> str | None:
    if not start or not duration_min:
        return None
    h, m = int(start[:2]), int(start[3:5])
    total = h * 60 + m + int(duration_min)
    return f"{min(total // 60, 23):02d}:{total % 60:02d}" if total < 24 * 60 else "23:59"


def _base(t: dict) -> dict:
    return {
        "ticket_id": t["id"], "plan_key": t.get("plan_key"), "title": t["title"],
        "owner": t.get("owner"), "project_id": t.get("project_id"), "status": t["status"],
        "priority": t.get("priority"), "due_state": t.get("due_state"),
        "last_change": t.get("last_change"), "cadence_days": t.get("cadence_days"),
        "preferred_time": t.get("preferred_time"), "all_day": bool(t.get("all_day")),
        "duration_min": t.get("duration_min"),
    }


def occurrences(conn, from_date, to_date, agent=None, project_id=None) -> dict:
    d_from, d_to = _parse_date(from_date, "from"), _parse_date(to_date, "to")
    if d_to < d_from:
        raise TicketError("to < from")
    if (d_to - d_from).days > MAX_RANGE_DAYS:
        raise TicketError(f"rango demasiado grande (> {MAX_RANGE_DAYS} días)")
    if agent is not None and agent not in AGENTS:
        raise TicketError(f"agente desconocido: {agent!r}")
    tickets = [t for t in service.list_tickets(conn, project_id=project_id, owner=agent,
                                               include_closed=True)
               if t.get("kind") == "manual"]
    items = []
    for t in tickets:
        base = _base(t)
        if t.get("closed_at"):
            # Hecha/verificada/rechazada: se ve el DIA en que se cerro (el trabajo del
            # dia incluye lo terminado), en gris y sin operar (el motor da 409 igual).
            day = t["closed_at"][:10]
            if d_from.isoformat() <= day <= d_to.isoformat():
                sched = t.get("scheduled_at") or ""
                # v3.2 (T1.4, el dueño: «todas tienen hora al menos en la verificacion»):
                # una cerrada lleva hora si tiene CUALQUIERA conocida — la programada
                # del dia, si no la preferida (recurrente), si no la del cierre.
                if t.get("all_day"):
                    start = None  # finding #26 (Prima): «todo el día» no tiene hora, ni cerrada
                elif sched[:10] == day:
                    start = sched[11:16]
                else:
                    start = t.get("preferred_time") or (t["closed_at"][11:16] or None)
                items.append({**base, "kind": "closed", "date": day, "start": start,
                              "end": _end_time(start, t.get("duration_min")),
                              "ghost": False, "occurrence_n": 0,
                              "closed_at": t["closed_at"]})
            continue
        if t.get("cadence_days"):
            items += _recurrence_items(base, t, d_from, d_to)
        elif t.get("scheduled_at"):
            day = t["scheduled_at"][:10]
            if d_from.isoformat() <= day <= d_to.isoformat():
                start = None if t.get("all_day") else t["scheduled_at"][11:16]
                items.append({**base, "kind": "scheduled", "date": day, "start": start,
                              "end": _end_time(start, t.get("duration_min")),
                              "ghost": False, "occurrence_n": 0})
        elif t.get("due_at"):
            day = t["due_at"][:10]
            if d_from.isoformat() <= day <= d_to.isoformat():
                items.append({**base, "kind": "due", "date": day, "start": None, "end": None,
                              "ghost": False, "occurrence_n": 0})
    items.sort(key=lambda x: (x["date"], x["start"] or "", x["ticket_id"]))
    from . import settings
    return {
        "from": d_from.isoformat(), "to": d_to.isoformat(),
        "lanes": sorted(AGENTS),
        "slots": settings.get(conn, "casa_slots"),
        "items": items,
    }


def _recurrence_items(base: dict, t: dict, d_from: date, d_to: date) -> list[dict]:
    cadence = int(t["cadence_days"])
    next_due = date.fromisoformat(t["next_due"])  # override > last_done+cadencia > hoy
    start = t.get("preferred_time")
    end = _end_time(start, t.get("duration_min")) if start else None
    out = []
    # Vencida: la próxima real puede ser anterior al rango → se muestra el día
    # de inicio del rango como vencida (no se pierde), sin duplicar.
    day, n = next_due, 0
    if day < d_from:
        overdue = t.get("due_state") == "overdue"
        # v3.2 (T1.1): una semanal anclada a un día (weekday) vencida se ve en SU
        # día dentro del rango, como ocurrencia real, no apilada al inicio del rango.
        anchored = t.get("weekday") is not None
        if overdue and not anchored:
            out.append({**base, "kind": "recurrence", "date": d_from.isoformat(),
                        "start": start, "end": end, "ghost": False, "occurrence_n": 0,
                        "overdue_since": next_due.isoformat()})
        # las siguientes proyectadas parten de la próxima real, no del rango
        while day < d_from and n < MAX_OCCURRENCES_PER_TICKET:
            day += timedelta(days=cadence); n += 1
        if overdue and anchored:
            # La vencida se ve en la PRIMERA ocurrencia de su día de la semana dentro del
            # rango (paso 7, no el de cadencia): una quincenal vencida cuyo paso de 14 cae
            # fuera de la semana vista desaparecía (finding de Prima, T4.1, 7-sep).
            show = next_due + timedelta(weeks=((d_from - next_due).days + 6) // 7)
            if show <= d_to:
                out.append({**base, "kind": "recurrence", "date": show.isoformat(),
                            "start": start, "end": end, "ghost": False, "occurrence_n": 0,
                            "overdue_since": next_due.isoformat()})
                if day == show:
                    day += timedelta(days=cadence); n += 1
    while day <= d_to and n < MAX_OCCURRENCES_PER_TICKET:
        out.append({**base, "kind": "recurrence", "date": day.isoformat(),
                    "start": start, "end": end, "ghost": n > 0, "occurrence_n": n})
        day += timedelta(days=cadence); n += 1
    return out


# ------------------------------------------------------------------ operaciones (T2.2)

def schedule_ticket(conn, agent, ticket_id, scheduled_at=None, duration_min=None,
                    all_day=None, note="") -> dict:
    """Mover de día/hora, estirar duración, marcar todo el día. History legible."""
    t = service._get(conn, ticket_id)
    if t["closed_at"] is not None:
        raise TicketError(f"ticket #{ticket_id} está cerrado ({t['status']}) — no se reprograma")
    if t["kind"] != "manual":
        raise TicketError("las agénticas no se programan desde el Tablero (decisión 4)")
    fields = {}
    if scheduled_at is not None:
        fields["scheduled_at"] = scheduled_at or ""
    if duration_min is not None:
        fields["duration_min"] = duration_min or ""
    if all_day is not None:
        fields["all_day"] = all_day
    if not fields:
        raise TicketError("schedule: nada que cambiar (scheduled_at, duration_min, all_day)")
    before = f"{t['scheduled_at'] or 'sin programar'}" + (f" ({t['duration_min']} min)" if t["duration_min"] else "")
    res = service.update_ticket(conn, agent, ticket_id, **fields)
    after = f"{res['scheduled_at'] or 'sin programar'}" + (f" ({res['duration_min']} min)" if res["duration_min"] else "")
    service._append_history(conn, ticket_id, res["status"], res["status"], agent,
                            f"programada: {before} → {after}" + (f" — {note}" if note else ""))
    conn.commit()
    return service._as_dict(conn, service._get(conn, ticket_id))


def shift_next_occurrence(conn, agent, ticket_id, new_date, note="") -> dict:
    """Mueve SOLO la próxima ocurrencia de una recurrente (la cadencia no cambia)."""
    t = service._get(conn, ticket_id)
    if not t["cadence_days"]:
        raise TicketError(f"ticket #{ticket_id} no es recurrente")
    if t["closed_at"] is not None:
        raise TicketError(f"ticket #{ticket_id} está cerrado")
    if new_date in (None, ""):
        target = ""
    else:
        target = _parse_date(new_date, "date")
        if target < date.today():
            raise TicketError(f"la próxima ocurrencia no puede ir al pasado ({target.isoformat()})")
        target = target.isoformat()
    before = service._as_dict(conn, t)["next_due"]
    res = service.update_ticket(conn, agent, ticket_id, next_due_override=target)
    service._append_history(conn, ticket_id, res["status"], res["status"], agent,
                            f"próxima ocurrencia: {before} → {res['next_due']} (cadencia {t['cadence_days']} días intacta)"
                            + (f" — {note}" if note else ""))
    conn.commit()
    return service._as_dict(conn, service._get(conn, ticket_id))
