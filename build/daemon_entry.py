"""Entrada del daemon empaquetado (T4.2): mismo main que `python -m faros.daemon`."""
import asyncio
import os
import sys

# --home <dir>: casa alternativa (db, token, logs, backups, artifacts). Se fija ANTES
# de importar agenticos porque db.DATA_DIR se resuelve al importar.
_argv = sys.argv[1:]
for _i, _a in enumerate(_argv):
    if _a == "--home" and _i + 1 < len(_argv):
        os.environ["AGENTICOS_HOME"] = _argv[_i + 1]

from faros.daemon import main, DEFAULT_PORT  # noqa: E402

def _stdio_to_files():
    """Sin consola (console=False) sys.stdout/err son None y uvicorn muere en
    silencio (L52). Redirigimos a los logs de la casa antes de arrancar."""
    from faros.db import DATA_DIR
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None:
        sys.stdout = open(DATA_DIR / "daemon_stdout.log", "a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = open(DATA_DIR / "daemon_stderr.log", "a", encoding="utf-8", buffering=1)


if __name__ == "__main__":
    _stdio_to_files()
    if "--watchdog" in sys.argv[1:]:
        # T1.2 (v3.1): el vigilante es ESTE exe (console=False → sin ventana), no powershell.
        from faros.watchdog import main as _wd_main
        sys.exit(_wd_main([a for a in sys.argv[1:] if a != "--watchdog"]))
    port = DEFAULT_PORT
    db = None
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
        if a == "--db" and i + 1 < len(args):
            db = args[i + 1]
    asyncio.run(main(port, db))
