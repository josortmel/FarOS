"""Entrypoint del daemon: API + scheduler en el mismo event loop.

Arranque: python -m faros.daemon [--port 8756] [--db PATH]
El daemon es el ÚNICO escritor de la DB (SPEC §2). La UI (Electron) y el MCP
son clientes HTTP.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import logging
import os
import socket

import uvicorn
from uvicorn.config import LOGGING_CONFIG

from . import acl, db
from .api import create_app, EventBus
from .runner import DATA_DIR
from .scheduler import scheduler_loop

DEFAULT_PORT = 8756


def uvicorn_log_config() -> dict:
    """#436: el log de uvicorn (acceso incluido) no llevaba hora, y en un log compartido
    no se podía saber qué petición era de qué app ni de cuándo (L60). Misma hora
    (asctime) que el resto de logs del daemon."""
    cfg = copy.deepcopy(LOGGING_CONFIG)
    for formatter in cfg["formatters"].values():
        formatter["fmt"] = "%(asctime)s " + formatter["fmt"]
    return cfg


def coger_puerto(port: int) -> socket.socket | None:
    """#1134: el puerto, LO PRIMERO. El 11-nov la app, con la máquina cargada, no recibió /api/health en
    1,5 s y lanzó tres daemons más; cada uno abrió la base, hizo la copia diaria, arrancó scheduler y
    barredor (y habría cerrado como huérfanos los runs vivos del bueno) y solo entonces falló al enlazar
    el puerto. Con el puerto cogido antes de tocar nada, un segundo daemon se va sin escribir en la casa."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name == "nt":
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port))
    except OSError:
        s.close()
        return None
    return s


async def main(port: int, db_path: str | None):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sock = coger_puerto(port)
    if sock is None:
        logging.getLogger("faros.daemon").warning(
            "el puerto %s ya es de otro daemon: me voy sin abrir la base (#1134)", port)
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(DATA_DIR / "daemon.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(file_handler)
    acl.harden_dir(DATA_DIR)  # #154: token, base y backups solo para el usuario, SYSTEM y Administradores

    conn = db.connect(db_path, house_migration_ok=True)  # #157: la casa solo la migra el daemon
    from . import service
    orphans = service.recover_orphan_runs(conn)
    if orphans:
        logging.getLogger("faros.daemon").warning(
            "%s run(s) huérfanos cerrados como error al arrancar", orphans)
    bus = EventBus()
    from . import deepseek_proxy
    deepseek_proxy.PUERTO = port   # #1109: el reenvío de DeepSeek vive en este daemon
    app = create_app(conn, bus)
    barredor = None
    try:    # Biblioteca (#850): recorrido al arrancar y cada hora, en su hilo y con su conexión
        from . import library
        db_file = conn.execute("PRAGMA database_list").fetchone()["file"]
        barredor = library.Barredor(db_file)
        barredor.start()
    except Exception:
        logging.getLogger("faros.daemon").exception("no se pudo arrancar el recorrido de la Biblioteca")

    stop_event = asyncio.Event()
    sched_task = asyncio.create_task(scheduler_loop(conn, emit=bus.emit, stop_event=stop_event))
    from . import telegram_bot   # #1189: el bot de Telegram del dueño (timbres aparte, en el scheduler)
    bot_task = asyncio.create_task(telegram_bot.bucle(conn, stop_event, emit=bus.emit))

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info", loop="asyncio",
                            log_config=uvicorn_log_config())
    server = uvicorn.Server(config)
    try:
        await server.serve(sockets=[sock])
    finally:
        stop_event.set()
        if barredor is not None:
            await asyncio.to_thread(barredor.stop)
            from . import library
            await asyncio.to_thread(library.parar_worker, 10)
        await sched_task
        await bot_task
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()
    asyncio.run(main(args.port, args.db))
