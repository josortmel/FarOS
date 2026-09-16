"""Roster de la casa: quién es el dueño y qué agentes existen.

Esto vivía clavado en service.py como un literal con los nombres de la casa
que lo escribió. Se saca aquí para que el roster sea CONFIGURACIÓN y no
código: quien instale esto pone sus propios nombres sin tocar un .py.

No importa nada de `service` a propósito. `settings.py` sería el sitio natural,
pero importa `_validate_agent` de service y service necesita el roster al
importarse: sería circular.

Orden de resolución (gana el primero que exista):

1. env `AGENTICOS_OWNER` y `AGENTICOS_AGENTS` (lista separada por comas)
2. `<AGENTICOS_HOME>/roster.json` — `{"owner": "...", "agents": [...]}`
3. el default neutro de abajo

El env va PRIMERO a propósito: es la única forma de que un test o un arranque
desechable declare su propio roster sin depender de qué fichero haya en la
máquina. Si el fichero mandara, la suite pasaría en verde aquí por un
roster.json que en un clon limpio no existe — el verde que miente.

El default es NEUTRO a propósito: el código que se publica no lleva dentro los
nombres de nadie. Una instalación con nombres propios los pone en su roster.json.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from . import env as _env

# Default neutro. No es "la casa": es lo que ve alguien que instala esto en
# limpio y todavía no ha dicho quién es.
DEFAULT_OWNER = "owner"
DEFAULT_AGENTS = ("alice", "bob", "carol", "dave")

ROSTER_FILENAME = "roster.json"


def _home() -> Path:
    """La casa, resuelta en un solo sitio (env.data_dir()). Tenia AgenticOS
    clavado y quedaba desalineada con la del daemon tras el rename."""
    from . import env as _env
    return _env.data_dir()



def _from_file() -> tuple[str, list[str]] | None:
    path = _home() / ROSTER_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    owner = data.get("owner")
    agents = data.get("agents")
    if not isinstance(owner, str) or not owner.strip():
        return None
    if not isinstance(agents, list) or not all(
        isinstance(a, str) and a.strip() for a in agents
    ):
        return None
    return owner.strip(), [a.strip() for a in agents]


def _from_env() -> tuple[str, list[str]] | None:
    owner = _env.get("OWNER", "").strip()
    raw = _env.get("AGENTS", "")
    agents = [a.strip() for a in raw.split(",") if a.strip()]
    if not owner or not agents:
        return None
    return owner, agents


def load() -> tuple[str, frozenset[str]]:
    """Devuelve (owner, agents). El owner siempre está dentro de agents: el
    dueño de la casa mueve cualquier ticket y tiene que ser un agente válido."""
    resolved = _from_env() or _from_file()
    if resolved is None:
        owner, agents = DEFAULT_OWNER, list(DEFAULT_AGENTS)
    else:
        owner, agents = resolved
    return owner, frozenset(agents) | {owner}


HOUSE_OWNER, AGENTS = load()
