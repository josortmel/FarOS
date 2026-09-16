"""Scheduler del daemon (SPEC §7): tick asyncio, puntuales + recurrentes + catch-up.

Regla de catch-up (lección L47 de Faro): al arrancar, para cada job recurrente se
lanza UNA vez si el último fire teórico no tiene run posterior — no se re-ejecuta
cada ventana perdida. Las 'once' vencidas hace más de MISSED_GRACE_H horas se
marcan missed en history y se deshabilitan.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, date, time as dtime, timedelta

from . import service, runner, backup, schedule as schedule_mod

log = logging.getLogger("faros.scheduler")

TICK_SECONDS = 30
MISSED_GRACE_H = 24
BACKUP_CHECK_S = 3600  # backup.daily_tick es idempotente por día; se consulta cada hora
VERIFY_EVERY_S = 300   # veredicto por run (T1.10): barrido cada 5 min, además del tras-run
EXPIRE_EVERY_S = 60    # T4.5: caducar tickets expired — antes vivía en las lecturas


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def last_theoretical_fire(schedule: dict, created_at: str, now: datetime) -> datetime | None:
    """Último disparo teórico <= now (v3: schedule.last_fire; acepta at como
    string v1 o lista, recurring y weekly)."""
    return schedule_mod.last_fire({"type": "recurring", **schedule} if "type" not in schedule
                                  else schedule, created_at, now)


def next_fire(schedule: dict, created_at: str, now: datetime) -> datetime | None:
    """Próximo disparo teórico > now (para la UI de Agentes). manual → None."""
    return schedule_mod.next_fire(schedule, created_at, now)


def due_jobs(conn, now: datetime) -> list[dict]:
    """Jobs que deben lanzarse ahora. Puro sobre la DB — testeable con reloj falso.
    v3: el job es entidad propia (status active); el ticket vinculado es opcional
    y, si está cerrado, el job no se lanza."""
    rows = conn.execute(
        "SELECT j.*, t.status AS ticket_status, t.closed_at AS ticket_closed"
        " FROM agent_jobs j LEFT JOIN tickets t ON t.id = j.ticket_id"
        " WHERE j.enabled = 1 AND j.status = 'active'"
        " AND (j.ticket_id IS NULL OR t.closed_at IS NULL)").fetchall()
    due = []
    for row in rows:
        job = dict(row)
        sched = json.loads(job["schedule"])
        stype = sched.get("type")
        if stype == "manual":
            continue
        last_run = conn.execute(
            "SELECT started_at, status FROM runs WHERE job_id=? ORDER BY id DESC LIMIT 1",
            (job["id"],)).fetchone()
        if last_run and last_run["status"] == "running":
            continue
        if stype == "once":
            run_at = _parse_iso(sched["run_at"])
            if run_at > now:
                continue
            if last_run is not None:
                continue  # ya se lanzó
            if now - run_at > timedelta(hours=MISSED_GRACE_H):
                conn.execute("UPDATE agent_jobs SET enabled=0, status='paused', updated_at=?"
                             " WHERE id=?", (now.isoformat(timespec="seconds"), job["id"]))
                if job["ticket_id"]:
                    service._append_history(conn, job["ticket_id"], None, "blocked", "system",
                                            f"schedule once perdida (venció {sched['run_at']}, "
                                            f"gracia {MISSED_GRACE_H}h) — job pausado")
                    conn.execute(
                        "UPDATE tickets SET status='blocked',"
                        " blocked_reason='schedule once perdida' WHERE id=? AND closed_at IS NULL",
                        (job["ticket_id"],))
                conn.commit()
                continue
            due.append(job)
        elif stype in ("recurring", "weekly"):
            # Catch-up L47: si el último disparo teórico no tiene run posterior,
            # se lanza UNA vez — aunque el hueco cubra varios disparos.
            fire = schedule_mod.last_fire(sched, job["created_at"], now)
            if fire is None:
                continue
            if last_run is None or _parse_iso(last_run["started_at"]) < fire:
                due.append(job)
    return due


async def scheduler_loop(conn, emit=None, stop_event: asyncio.Event | None = None):
    """Los jobs se lanzan como tasks concurrentes: un run de 30 min no puede
    congelar el tick. Compartir conn es seguro: cada llamada a service es
    síncrona de principio a commit; solo se intercala en los await del
    subprocess. El anti-solape real vive en el índice único de la DB."""
    stop_event = stop_event or asyncio.Event()
    in_flight: set[asyncio.Task] = set()
    log.info("scheduler arrancado (tick %ss)", TICK_SECONDS)

    async def _launch(job_id: int):
        try:
            await runner.execute_job(conn, job_id, emit=emit)
        except service.TicketError as e:
            log.warning("job #%s no lanzado: %s", job_id, e)
        except Exception:
            log.exception("job #%s reventó fuera del contrato", job_id)

    last_backup_check = 0.0
    last_verify = time.monotonic()
    last_expire = 0.0

    async def _verify_runs():
        try:
            from . import verifier
            res = await verifier.sweep_runs(conn, emit=emit)
            if res.get("swept"):
                log.info("veredictos: %s pass, %s fail, %s errores",
                         len(res["passed"]), len(res["failed"]), len(res["errors"]))
        except Exception:
            log.exception("barrido de veredictos falló — sigo vivo")

    while not stop_event.is_set():
        # T4.5: expire tickets via scheduler instead of on every GET
        if (time.monotonic() - last_expire) >= EXPIRE_EVERY_S:
            last_expire = time.monotonic()
            try:
                service._expire_lazy(conn)
            except Exception:
                log.exception("expire tick falló — sigo vivo")
        if (time.monotonic() - last_verify) >= VERIFY_EVERY_S:
            last_verify = time.monotonic()
            task = asyncio.create_task(_verify_runs())
            in_flight.add(task)
            task.add_done_callback(in_flight.discard)
        # Copia diaria de la DB (T0.6, Eco): al arrancar y luego cada hora.
        if (time.monotonic() - last_backup_check) >= BACKUP_CHECK_S or last_backup_check == 0.0:
            last_backup_check = time.monotonic()
            try:
                info = backup.daily_tick(conn, datetime.now())
                log.info("backup diario: %s (%s KB, %s tickets, podados %s)",
                         info["backup"], info["size_kb"], info["production_tickets"],
                         info["pruned"] or "ninguno")
            except Exception:
                log.exception("backup diario falló — sigo vivo")
        try:
            for job in due_jobs(conn, datetime.now()):
                log.info("lanzando job #%s (ticket #%s) por schedule", job["id"], job["ticket_id"])
                task = asyncio.create_task(_launch(job["id"]))
                in_flight.add(task)
                task.add_done_callback(in_flight.discard)
        except Exception:
            log.exception("tick del scheduler falló — sigo vivo")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=TICK_SECONDS)
        except asyncio.TimeoutError:
            pass
