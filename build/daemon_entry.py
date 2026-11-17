"""Entrada del daemon empaquetado (T4.2): mismo main que `python -m faros.daemon`."""
import argparse
import asyncio
import io
import os
import sys

# #453 (6-oct, memoria 45b0c150): los argumentos se validan ANTES de importar faros y antes de tocar la casa.
# Antes se recorrían a mano y lo desconocido se ignoraba: «faros-daemon.exe --help» arrancaba un daemon contra
# %LOCALAPPDATA% (aquella vez solo cayó porque el 8756 estaba ocupado). Ahora --help imprime la ayuda y sale con
# 0, y un argumento desconocido (o un puerto que no es número) sale con 2, sin crear nada.


def _parser():
    ap = argparse.ArgumentParser(prog="faros-daemon",
                                 description="Daemon de FarOS (API local, scheduler, verificador).")
    ap.add_argument("--home", help="casa alternativa (base, token, logs, copias); por defecto %%LOCALAPPDATA%%\\FarOS")
    ap.add_argument("--port", type=int, help="puerto de la API local (por defecto 8756 o FAROS_PORT)")
    ap.add_argument("--db", help="ruta de la base, si no es la de la casa")
    ap.add_argument("--watchdog", action="store_true",
                    help="modo vigilante: comprueba el health y relanza la tarea del daemon (admite --port, --task, --home)")
    return ap


def _parse(argv):
    # Sin consola (console=False) sys.stdout/err son None y argparse no tendría dónde escribir la ayuda o el error.
    # Se tapan solo durante el análisis y se devuelven a None, para que _stdio_to_files los mande luego a los logs.
    out, err = sys.stdout, sys.stderr
    if out is None:
        sys.stdout = io.StringIO()
    if err is None:
        sys.stderr = io.StringIO()
    try:
        if "--watchdog" in argv:
            # El vigilante tiene su propio argparse estricto (faros/watchdog.py): le llega el resto tal cual.
            return None, [a for a in argv if a != "--watchdog"]
        return _parser().parse_args(argv), None
    finally:
        sys.stdout, sys.stderr = out, err


_ARGS, _WATCHDOG_ARGV = _parse(sys.argv[1:])

# --home <dir>: casa alternativa (db, token, logs, backups, artifacts). Se fija ANTES de importar faros porque
# db.DATA_DIR se resuelve al importar. Las dos variables, como hace el vigilante.
if _ARGS is not None and _ARGS.home:
    os.environ["FAROS_HOME"] = _ARGS.home
    os.environ["AGENTICOS_HOME"] = _ARGS.home

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
    if _WATCHDOG_ARGV is not None:
        # T1.2 (v3.1): el vigilante es ESTE exe (console=False → sin ventana), no powershell.
        from faros.watchdog import main as _wd_main
        sys.exit(_wd_main(_WATCHDOG_ARGV))
    asyncio.run(main(_ARGS.port if _ARGS.port is not None else DEFAULT_PORT, _ARGS.db))
