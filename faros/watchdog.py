"""Vigilante del daemon SIN PowerShell (T1.2, v3.1).

el dueño (betatesting 3-sep, 2º): «cada minutos una ventana de powershell se abre y
se cierra rápido, incluso con la app cerrada». Era la tarea AgenticOS-watchdog:
powershell.exe cada 5 min; `-WindowStyle Hidden` NO evita el flash porque la
consola se crea antes de que PowerShell lea el argumento.

Ahora el vigilante es el propio exe del daemon (console=False, sin ventana por
construcción) en modo `--watchdog`, o `pythonw -m faros.watchdog` en
desarrollo. Hace GET /api/health; si no responde, arranca la tarea del daemon
con `schtasks /Run` (oculto, procutil) y deja una línea en watchdog.log.
Nunca escribe a stdout: bajo pythonw no existe.

Uso: python -m faros.watchdog [--port 8756] [--task AgenticOS-daemon] [--home DIR]
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from . import env as _env

DEFAULT_PORT = 8756
DEFAULT_TASK = "AgenticOS-daemon"


def _data_dir() -> Path:
    return _env.data_dir()


def _log(data_dir: Path, line: str) -> None:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        with open(data_dir / "watchdog.log", "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except OSError:
        pass


def health_ok(port: int, timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def start_task(task: str) -> tuple[int, str]:
    """schtasks /Run sin ventana. Devuelve (returncode, salida)."""
    from .procutil import run_hidden
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "schtasks.exe")
    try:
        r = run_hidden([exe, "/Run", "/TN", task], capture_output=True, text=True, timeout=30)
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except (OSError, ValueError) as exc:  # subprocess errors heredan de OSError o son ValueError
        return -1, str(exc)
    except Exception as exc:  # TimeoutExpired
        return -1, str(exc)


def check(port: int = DEFAULT_PORT, task: str = DEFAULT_TASK, data_dir: Path | None = None,
          _health=health_ok, _start=start_task) -> bool:
    """Una pasada del vigilante. True si el daemon estaba vivo o se relanzó."""
    data_dir = data_dir or _data_dir()
    if _health(port):
        return True
    rc, out = _start(task)
    _log(data_dir, f"health caido en :{port} -> schtasks /Run {task} (rc={rc}) {out[:200]}")
    return rc == 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="faros.watchdog")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--task", default=DEFAULT_TASK)
    ap.add_argument("--home", default=None)
    args = ap.parse_args(argv)
    if args.home:
        # Se ESCRIBEN las dos: la nueva porque es la que manda, y la vieja
        # porque cualquier proceso hijo lanzado desde aqui puede ser todavia
        # codigo que solo mira AGENTICOS_HOME. Durante la transicion, escribir
        # una sola deja al hijo buscando su casa donde no esta.
        os.environ["FAROS_HOME"] = args.home
        os.environ["AGENTICOS_HOME"] = args.home
    ok = check(args.port, args.task)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
