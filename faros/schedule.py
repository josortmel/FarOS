"""Schedules de jobs v3 (T1.5). Puro: sin DB, testeable con reloj falso.

Tipos (contrato del plan):
  {"type":"manual"}
  {"type":"once","run_at":"YYYY-MM-DDTHH:MM[:SS]"}
  {"type":"recurring","every_days":N,"at":["HH:MM", ...]}     — cada N días, una o varias horas
  {"type":"weekly","weekdays":[0..6],"at":["HH:MM", ...]}     — días concretos (0=lunes)
`at` admite string (formato v1) y se normaliza a lista. Combinables: weekly con
varias horas es "L-X-V a las 08:00 y 20:00".

Catch-up (L47): el scheduler compara last_fire (último disparo teórico <= now)
con el último run; un hueco de varios días lanza UNA vez, no cada ventana.
"""

from __future__ import annotations

import re
from datetime import datetime, date, time as dtime, timedelta

from .service import TicketError

TYPES = {"manual", "once", "recurring", "weekly"}
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
WEEKDAY_NAMES = ["L", "M", "X", "J", "V", "S", "D"]
WEEKDAY_LONG = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _times(raw) -> list[str]:
    if raw is None:
        raise TicketError("schedule requiere at ('HH:MM' o lista)")
    if isinstance(raw, str):
        raw = [s.strip() for s in raw.split(",") if s.strip()]
    if not isinstance(raw, list) or not raw:
        raise TicketError("schedule.at: lista no vacía de 'HH:MM'")
    out = []
    for t in raw:
        if not isinstance(t, str) or not _HHMM.match(t):
            raise TicketError(f"schedule.at: hora inválida {t!r} (HH:MM)")
        if t not in out:
            out.append(t)
    return sorted(out)


def normalize(schedule) -> dict:
    """Valida y devuelve el schedule canónico. TicketError si es inválido."""
    if schedule is None:
        return {"type": "manual"}
    if not isinstance(schedule, dict):
        raise TicketError("schedule debe ser un objeto")
    stype = schedule.get("type")
    if stype not in TYPES:
        raise TicketError(f"schedule.type inválido: {stype!r} ({'|'.join(sorted(TYPES))})")
    if stype == "manual":
        return {"type": "manual"}
    if stype == "once":
        run_at = schedule.get("run_at")
        if not run_at:
            raise TicketError("schedule once requiere run_at (ISO)")
        try:
            dt = datetime.fromisoformat(str(run_at))
        except ValueError:
            raise TicketError(f"schedule.run_at inválido: {run_at!r}")
        return {"type": "once", "run_at": dt.isoformat(timespec="seconds")}
    at = _times(schedule.get("at"))
    if stype == "recurring":
        try:
            every = int(schedule.get("every_days", 0))
        except (TypeError, ValueError):
            raise TicketError("schedule.every_days debe ser entero")
        if every < 1:
            raise TicketError("schedule recurring requiere every_days >= 1")
        return {"type": "recurring", "every_days": every, "at": at}
    weekdays = schedule.get("weekdays")
    if not isinstance(weekdays, list) or not weekdays:
        raise TicketError("schedule weekly requiere weekdays (lista 0=lunes..6=domingo)")
    try:
        wd = sorted({int(x) for x in weekdays})
    except (TypeError, ValueError):
        raise TicketError("schedule.weekdays: enteros 0..6")
    if any(x < 0 or x > 6 for x in wd):
        raise TicketError("schedule.weekdays: valores 0..6 (0=lunes)")
    return {"type": "weekly", "weekdays": wd, "at": at}


def _anchor(created_at: str) -> date:
    return date.fromisoformat(created_at[:10])


def fires_on(schedule: dict, created_at: str, day: date) -> bool:
    stype = schedule["type"]
    if stype == "recurring":
        anchor = _anchor(created_at)
        return day >= anchor and (day - anchor).days % int(schedule["every_days"]) == 0
    if stype == "weekly":
        return day >= _anchor(created_at) and day.weekday() in schedule["weekdays"]
    return False


def _day_fires(schedule: dict, day: date) -> list[datetime]:
    return [datetime.combine(day, dtime(int(t[:2]), int(t[3:]))) for t in schedule["at"]]


def _span(schedule: dict) -> int:
    """Cuántos días hacia atrás/adelante hay que mirar para encontrar un fire."""
    if schedule["type"] == "recurring":
        return int(schedule["every_days"]) + 1
    return 8


def last_fire(schedule: dict, created_at: str, now: datetime) -> datetime | None:
    """Último disparo teórico <= now. manual → None; once → run_at si ya pasó."""
    sched = normalize(schedule)
    if sched["type"] == "manual":
        return None
    if sched["type"] == "once":
        run_at = datetime.fromisoformat(sched["run_at"])
        return run_at if run_at <= now else None
    day = now.date()
    for _ in range(_span(sched)):
        if fires_on(sched, created_at, day):
            past = [f for f in _day_fires(sched, day) if f <= now]
            if past:
                return max(past)
        day -= timedelta(days=1)
    return None


def next_fire(schedule: dict, created_at: str, now: datetime) -> datetime | None:
    """Primer disparo teórico > now. manual → None; once → run_at si es futuro."""
    sched = normalize(schedule)
    if sched["type"] == "manual":
        return None
    if sched["type"] == "once":
        run_at = datetime.fromisoformat(sched["run_at"])
        return run_at if run_at > now else None
    day = max(now.date(), _anchor(created_at))
    for _ in range(_span(sched) + 1):
        if fires_on(sched, created_at, day):
            future = [f for f in _day_fires(sched, day) if f > now]
            if future:
                return min(future)
        day += timedelta(days=1)
    return None


def human(schedule: dict) -> str:
    """Texto para la UI y el preámbulo del agente."""
    try:
        sched = normalize(schedule)
    except TicketError:
        return "programación inválida"
    if sched["type"] == "manual":
        return "manual (solo al lanzarlo)"
    if sched["type"] == "once":
        return f"una vez, el {sched['run_at'][:16].replace('T', ' a las ')}"
    hours = " y ".join(sched["at"])
    if sched["type"] == "recurring":
        n = sched["every_days"]
        return f"{'cada día' if n == 1 else f'cada {n} días'} a las {hours}"
    days = ", ".join(WEEKDAY_LONG[d] for d in sched["weekdays"])
    return f"{days} a las {hours}"
