"""Scheduler del daemon (SPEC §7): tick asyncio, puntuales + recurrentes + catch-up.

Regla de catch-up (lección L47 de Faro): en CADA tick (no solo al arrancar), para cada
job recurrente se lanza UNA vez si el último fire teórico no tiene run posterior — no se
re-ejecuta cada ventana perdida. Un fire anterior al último cambio de calendario
(schedule_changed_at, #431) no cuenta como perdido. Las 'once' vencidas hace más de MISSED_GRACE_H horas se
marcan missed en history y se deshabilitan.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta

from . import backup, notify, runner, service
from . import schedule as schedule_mod

log = logging.getLogger("faros.scheduler")

TICK_SECONDS = 30
MISSED_GRACE_H = 24
BACKUP_CHECK_S = 3600  # backup.daily_tick es idempotente por día; se consulta cada hora
VERIFY_EVERY_S = 300  # veredicto por run (T1.10): barrido cada 5 min, además del tras-run
EXPIRE_EVERY_S = 60  # T4.5: caducar tickets expired — antes vivía en las lecturas


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def last_theoretical_fire(schedule: dict, created_at: str, now: datetime) -> datetime | None:
    """Último disparo teórico <= now (v3: schedule.last_fire; acepta at como
    string v1 o lista, recurring y weekly)."""
    return schedule_mod.last_fire(
        {"type": "recurring", **schedule} if "type" not in schedule else schedule, created_at, now
    )


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
        " AND (j.ticket_id IS NULL OR t.closed_at IS NULL)"
    ).fetchall()
    due = []
    for row in rows:
        job = dict(row)
        sched = json.loads(job["schedule"])
        stype = sched.get("type")
        if stype == "manual":
            continue
        last_run = conn.execute(
            "SELECT started_at, status FROM runs WHERE job_id=? ORDER BY id DESC LIMIT 1", (job["id"],)
        ).fetchone()
        if last_run and last_run["status"] == "running":
            continue
        if stype == "once":
            run_at = _parse_iso(sched["run_at"])
            if run_at > now:
                continue
            if last_run is not None:
                continue  # ya se lanzó
            if now - run_at > timedelta(hours=MISSED_GRACE_H):
                conn.execute(
                    "UPDATE agent_jobs SET enabled=0, status='paused', updated_at=? WHERE id=?",
                    (now.isoformat(timespec="seconds"), job["id"]),
                )
                if job["ticket_id"]:
                    service._append_history(
                        conn,
                        job["ticket_id"],
                        None,
                        "blocked",
                        "system",
                        f"schedule once perdida (venció {sched['run_at']}, gracia {MISSED_GRACE_H}h) — job pausado",
                    )
                    conn.execute(
                        "UPDATE tickets SET status='blocked',"
                        " blocked_reason='schedule once perdida' WHERE id=? AND closed_at IS NULL",
                        (job["ticket_id"],),
                    )
                conn.commit()
                continue
            due.append(job)
        elif stype in ("recurring", "weekly"):
            # Catch-up L47: si el último disparo teórico no tiene run posterior,
            # se lanza UNA vez — aunque el hueco cubra varios disparos.
            fire = schedule_mod.last_fire(sched, job["created_at"], now)
            if fire is None or _antes_del_cambio(job, fire):
                continue
            if last_run is None or _parse_iso(last_run["started_at"]) < fire:
                due.append(job)
    return due


def _antes_del_cambio(job: dict, fire: datetime) -> bool:
    """#431: un disparo de antes del último cambio de calendario, o de antes de que el job
    existiera, no se «perdió»: no existía cuando tocaba. Sin esto, cambiar la hora a una ya
    pasada lanzaba el job al momento, y crear un diario de las 11:00 a las 15:00 también (lo vio
    Lienzo en la e2e, 16-oct)."""
    desde = max(x for x in (job.get("created_at"), job.get("schedule_changed_at")) if x)
    return fire < _parse_iso(desde)


def skipped_jobs(conn, now: datetime) -> list[dict]:
    """#518: jobs recurrentes que tocarían ahora y NO se lanzan porque la tarea manual de la
    que cuelgan está cerrada. Antes se saltaban en silencio (la revista del 15-oct). Cada
    uno lleva «fire», el disparo que se pierde, para avisar una sola vez por disparo."""
    rows = conn.execute(
        "SELECT j.*, t.status AS ticket_status FROM agent_jobs j JOIN tickets t ON t.id = j.ticket_id"
        " WHERE j.enabled = 1 AND j.status = 'active' AND t.closed_at IS NOT NULL"
        " AND t.kind != 'agentic'"
    ).fetchall()
    out = []
    for row in rows:
        job = dict(row)
        sched = json.loads(job["schedule"])
        if sched.get("type") not in ("recurring", "weekly"):
            continue
        fire = schedule_mod.last_fire(sched, job["created_at"], now)
        if fire is None or _antes_del_cambio(job, fire):
            continue
        last_run = conn.execute(
            "SELECT started_at FROM runs WHERE job_id=? ORDER BY id DESC LIMIT 1", (job["id"],)
        ).fetchone()
        if last_run is None or _parse_iso(last_run["started_at"]) < fire:
            job["fire"] = fire.isoformat(timespec="minutes")
            out.append(job)
    return out


def _avisar_saltados(conn, now: datetime, avisados: set) -> None:
    for job in skipped_jobs(conn, now):
        if (job["id"], job["fire"]) in avisados:
            continue
        avisados.add((job["id"], job["fire"]))
        texto = (
            f"FarOS: el job #{job['id']} «{job['name']}» no se ha lanzado ({job['fire']}) porque su"
            f" tarea #{job['ticket_id']} está cerrada ({job['ticket_status']}). Desenlázalo o"
            " cuélgalo de una tarea abierta."
        )
        log.warning(texto)
        notify.telegram(conn, texto)


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
    avisados: set = set()  # #518: (job, disparo) ya avisados como saltados
    from . import timbre as timbre_mod  # #1184: el timbre de franjas por el relay

    timbre = None
    if timbre_mod.activo():  # FAROS_TIMBRE=0 lo quita del todo; el interruptor de la app se mira en cada vuelta
        estado_f, registro_f = timbre_mod.ficheros(conn)  # junto a la base: sobreviven al reinicio
        timbre = timbre_mod.Timbre(estado=estado_f, registro=registro_f)
    last_verify = time.monotonic()
    last_expire = 0.0

    async def _verify_runs():
        try:
            from . import verifier

            res = await verifier.sweep_runs(conn, emit=emit)
            if res.get("swept"):
                log.info(
                    "veredictos: %s pass, %s fail, %s errores",
                    len(res["passed"]),
                    len(res["failed"]),
                    len(res["errors"]),
                )
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
                log.info(
                    "backup diario: %s (%s KB, %s tickets, podados %s)",
                    info["backup"],
                    info["size_kb"],
                    info["production_tickets"],
                    info["pruned"] or "ninguno",
                )
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
            _avisar_saltados(conn, datetime.now(), avisados)
        except Exception:
            log.exception("aviso de jobs saltados falló — sigo vivo")
        if timbre is not None:
            # La base se lee en este hilo; el relay se habla en otro (red, puede tardar).
            plan, res = None, None
            try:
                if timbre_mod.activo(conn):
                    plan = timbre.preparar(conn)
                    avisos = timbre.avisos(plan)
                    res = await asyncio.to_thread(timbre_mod.enviar, avisos) if avisos else {}
                    if avisos:
                        log.info("timbre %s: %s", plan["ahora"].strftime("%H:%M"), res)
                else:
                    timbre.saltar()  # apagado desde la app: pasa el tiempo sin sonar
            except Exception:
                log.exception("timbre de franjas falló (¿relay caído?) — se reintenta en la vuelta siguiente")
            if plan is not None:
                timbre.confirmar(plan, res)
            try:  # #1189: lo del dueño, por el bot de Telegram (sin token o sin chat, no suena)
                from . import telegram_bot

                cfg = telegram_bot.datos(conn) if timbre_mod.activo(conn) and telegram_bot.activo() else None
                suyos = timbre.del_dueno(conn) if cfg else []
                if suyos:
                    ahora = datetime.now()
                    try:
                        await asyncio.to_thread(telegram_bot.mandar, *cfg, telegram_bot.texto_timbre(suyos, ahora))
                        resultado = "enviado"
                    except telegram_bot.BotError as e:
                        resultado = f"error: {e}"
                    timbre.marcar_dueno(suyos, ahora, resultado)
                    log.info("timbre del dueño %s: %s", ahora.strftime("%H:%M"), resultado)
            except Exception as e:
                log.warning("timbre del dueño falló (%s) — se reintenta en la vuelta siguiente", type(e).__name__)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=TICK_SECONDS)
        except asyncio.TimeoutError:
            pass
