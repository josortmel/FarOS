"""Entrypoint del daemon: API + scheduler en el mismo event loop.

Arranque: python -m faros.daemon [--port 8756] [--db PATH]
El daemon es el ÚNICO escritor de la DB (SPEC §2). La UI (Electron) y el MCP
son clientes HTTP.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import uvicorn

from . import db
from .api import create_app, EventBus
from .runner import DATA_DIR
from .scheduler import scheduler_loop

DEFAULT_PORT = 8756


async def main(port: int, db_path: str | None):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(DATA_DIR / "daemon.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(file_handler)

    conn = db.connect(db_path)
    from . import service
    orphans = service.recover_orphan_runs(conn)
    if orphans:
        logging.getLogger("faros.daemon").warning(
            "%s run(s) huérfanos cerrados como error al arrancar", orphans)
    bus = EventBus()
    app = create_app(conn, bus)

    stop_event = asyncio.Event()
    sched_task = asyncio.create_task(scheduler_loop(conn, emit=bus.emit, stop_event=stop_event))

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info", loop="asyncio")
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        stop_event.set()
        await sched_task
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()
    asyncio.run(main(args.port, args.db))
