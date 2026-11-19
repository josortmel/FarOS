"""Lanza un proceso con claves de FarOS en su entorno, sin que el valor toque disco, argv ni la salida (#499).

Uso (por ejemplo, en un .mcp.json; ahí solo van NOMBRES):
    python faros_con_claves.py --clave X_API_KEY --clave X_API_SECRET=TWITTER_SECRET \\
        --para "mcp x-twitter" -- npx -y <servidor-mcp>

--clave NOMBRE[=VARIABLE]  la clave «NOMBRE» de Ajustes › Claves va a la variable de entorno VARIABLE del hijo
                           (si no se dice, la variable se llama igual que la clave).
--para TEXTO               para qué se pide; queda en el registro de entregas de FarOS.
--quien TEXTO              quién la pide (por defecto, «lanzador:» + el nombre del comando).

Pide cada valor al daemon de FarOS (POST /api/credentials/deliver, con el token de la casa) y arranca el
hijo con stdin, stdout y stderr heredados, así que sirve para un servidor MCP por stdio. Devuelve el código
de salida del hijo. Si una clave no se entrega, dice cuál y por qué (nunca el valor) y sale con 2 sin
arrancar nada.

Límite (dicho al dueño el 16-oct): el token de la casa lo puede leer cualquier proceso del mismo usuario de
Windows, así que esto saca las claves de transcripts, .mcp.json, líneas de comandos y logs, pero no las
esconde de quien vaya a buscarlas a propósito. Solo se entregan las que el dueño marca como entregables, y
cada petición queda registrada.
Solo stdlib: lo lanza el Python del sistema desde cualquier .mcp.json.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path


def _casa() -> Path:
    # Misma resolución que faros/env.py: FAROS_HOME, luego el nombre viejo, luego la carpeta de siempre.
    for var in ("FAROS_HOME", "AGENTICOS_HOME"):
        if os.environ.get(var):
            return Path(os.environ[var])
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AgenticOS"


def _puerto() -> str:
    return os.environ.get("FAROS_PORT") or os.environ.get("AGENTICOS_PORT") or "8756"


def _fallo(msg: str) -> "None":
    print(f"faros_con_claves: {msg}", file=sys.stderr)
    sys.exit(2)


def _pedir(nombre: str, token: str, quien: str, para: str) -> str:
    cuerpo = json.dumps({"name": nombre, "requested_by": quien, "purpose": para}).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{_puerto()}/api/credentials/deliver", data=cuerpo,
                                 method="POST", headers={"Content-Type": "application/json",
                                                         "X-FarOS-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())["value"]
    except urllib.error.HTTPError as e:
        try:
            motivo = json.loads(e.read()).get("reason") or e.reason
        except ValueError:
            motivo = e.reason
        _fallo(f"FarOS no entrega «{nombre}» (HTTP {e.code}): {motivo}")
    except (urllib.error.URLError, OSError) as e:
        _fallo(f"no se puede hablar con FarOS en el puerto {_puerto()} ({type(e).__name__}); ¿está el daemon arrancado?")


def main(argv: list[str]) -> int:
    if "--" not in argv:
        _fallo("falta «--» y el comando que hay que lanzar")
    corte = argv.index("--")
    p = argparse.ArgumentParser(prog="faros_con_claves", add_help=True)
    p.add_argument("--clave", action="append", default=[], metavar="NOMBRE[=VARIABLE]")
    p.add_argument("--para", required=True)
    p.add_argument("--quien")
    a = p.parse_args(argv[:corte])
    cmd = argv[corte + 1:]
    if not cmd:
        _fallo("después de «--» va el comando")
    if not a.clave:
        _fallo("sin ninguna --clave no hace falta este lanzador")
    try:
        token = (_casa() / "token").read_text(encoding="utf-8").strip()
    except OSError:
        _fallo(f"no encuentro el token de FarOS en {_casa()}")
    quien = a.quien or f"lanzador:{Path(cmd[0]).name}"
    env = dict(os.environ)
    valores = []
    for spec in a.clave:
        nombre, _, variable = spec.partition("=")
        nombre, variable = nombre.strip(), (variable or nombre).strip()
        if not nombre or not variable:
            _fallo(f"--clave mal escrita: {spec!r} (NOMBRE o NOMBRE=VARIABLE)")
        env[variable] = _pedir(nombre, token, quien, a.para)
        valores.append(env[variable])
    # En Windows, «npx» es npx.cmd: se resuelve aquí para no tener que pasar por un shell.
    ejecutable = shutil.which(cmd[0], path=env.get("PATH")) or cmd[0]
    try:
        # #577 (como `op run`): el stderr del hijo sale con los valores entregados cambiados por ****, para que
        # un error de autenticación no deje la clave en el log de MCP. stdout NO se toca: en un MCP por stdio
        # es el canal del protocolo.
        hijo = subprocess.Popen([ejecutable, *cmd[1:]], env=env, stderr=subprocess.PIPE)
    except OSError as e:
        _fallo(f"no se puede arrancar {cmd[0]!r}: {e.strerror or type(e).__name__}")
    hilo = threading.Thread(target=_enmascarar, args=(hijo.stderr, [v.encode("utf-8") for v in valores if v]),
                            daemon=True)
    hilo.start()
    codigo = hijo.wait()
    hilo.join(timeout=5)
    return codigo


def _enmascarar(origen, valores: list[bytes]) -> None:
    """Copia el stderr del hijo al nuestro, línea a línea, cambiando cada valor entregado por ****."""
    destino = sys.stderr.buffer
    for linea in iter(origen.readline, b""):
        for v in valores:
            linea = linea.replace(v, b"****")
        destino.write(linea)
        destino.flush()


if __name__ == "__main__":
    # Los mensajes del lanzador llevan «» y tildes: en UTF-8 se leen bien también en Git Bash (cp1252 los rompe).
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
