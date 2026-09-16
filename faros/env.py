"""Lectura de variables de entorno con compatibilidad hacia atras.

Desde la 3.4 las variables se llaman FAROS_*. Las AGENTICOS_* siguen
funcionando y seguiran funcionando hasta que alguien decida retirarlas
DELIBERADAMENTE, no por descuido.

POR QUE ESTO EXISTE Y NO UN SED: el dia del rename hay scripts, tareas
programadas, arneses de prueba y sesiones vivas lanzadas con el entorno viejo.
Si el codigo deja de mirar AGENTICOS_HOME de golpe, todos ellos dejan de
encontrar su casa Y NO SE QUEJAN: arrancan contra una base vacia, pintan un
tablero sin tickets, y quien lo vea pensara que perdio los datos. Un fallo
callado con cara de catastrofe.

El orden es: FAROS_X primero (lo nuevo manda), AGENTICOS_X despues (lo viejo
sigue valiendo), default al final.
"""

from __future__ import annotations

import os
from pathlib import Path

PREFIX_NEW = "FAROS_"
PREFIX_OLD = "AGENTICOS_"


def get(name: str, default: str | None = None) -> str | None:
    """`name` va SIN prefijo: get("HOME") mira FAROS_HOME y AGENTICOS_HOME."""
    val = os.environ.get(PREFIX_NEW + name)
    if val:
        return val
    val = os.environ.get(PREFIX_OLD + name)
    if val:
        return val
    return default


def which(name: str) -> str | None:
    """Cual de las dos esta puesta, o None. Para poder DECIRLO en un log en
    vez de que el usuario adivine por que su casa esta donde esta."""
    if os.environ.get(PREFIX_NEW + name):
        return PREFIX_NEW + name
    if os.environ.get(PREFIX_OLD + name):
        return PREFIX_OLD + name
    return None


def data_dir() -> Path:
    """LA casa, resuelta en UN SOLO SITIO.

    Vive aqui y no en db.py porque la necesitan modulos que NO deben importar
    db: el servidor MCP (cliente fino del daemon), el roster (que no importa
    nada nuestro a proposito) y el vigilante.

    POR QUE ES UNA FUNCION COMPARTIDA Y NO UNA LINEA REPETIDA, y lo encontro
    Prima revisando el rename: db.py se llevo el fallback FarOS->AgenticOS y
    otros TRES modulos se quedaron con "AgenticOS" clavado. En una instalacion
    NUEVA el daemon escribe el token en FarOS/token y el MCP lo buscaba en
    AgenticOS/token: no lo encuentra y TODAS las tools devuelven 401. Un
    investigador que clone, instale y configure el MCP no puede usar ni una.
    Los tests no lo veian porque fijan FAROS_HOME, que salta el fallback
    entero — el unico entorno donde el fallo existe es el que no tocan.
    """
    home = get("HOME")
    if home:
        return Path(home)
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    nueva, vieja = base / "FarOS", base / "AgenticOS"
    if nueva.is_dir():
        return nueva
    if vieja.is_dir():
        return vieja
    return nueva


def redact_home(text: str) -> str:
    """Sustituye el perfil del usuario por ~ en cualquier ruta que salga de
    aqui hacia fuera (#266 / F-SEG-05).

    Vivia en runner.py y se muda aqui por la misma razon que data_dir(): la
    necesita mas de un modulo y el segundo no puede importar al primero.
    runner.py la sigue exportando con su nombre de siempre.

    No se borra la ruta —seguiria haciendo falta para depurar— se le quita la
    parte que identifica a la persona."""
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    if not home:
        return text
    out = text.replace(home, "~")
    # Windows mezcla separadores segun quien construyo la ruta.
    return out.replace(home.replace("\\", "/"), "~")
