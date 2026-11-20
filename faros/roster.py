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
   (#1186: con `"workflow_agents": [...]` opcional, agentes que firman su trabajo
   —un veredicto— sin ser de la familia; solo cuentan si el roster sale del fichero)
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


def _from_file() -> tuple[str, list[str], list[str]] | None:
    path = _home() / ROSTER_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    owner = data.get("owner")
    agents = data.get("agents")
    # #245: "suspended" es opcional (un roster.json anterior no lo trae).
    suspended = data.get("suspended", [])
    if not isinstance(owner, str) or not owner.strip():
        return None
    for lst in (agents, suspended):
        if not isinstance(lst, list) or not all(isinstance(a, str) and a.strip() for a in lst):
            return None
    return owner.strip(), [a.strip() for a in agents], [a.strip() for a in suspended]


def _workflow_from_file() -> list[str]:
    """#1186: agentes de workflow propios de la casa («workflow_agents» en roster.json).
    Firman lo que hacen (un veredicto, un hallazgo) sin ser de la familia. Opcional:
    si falta o está mal formada, no hay ninguno y el roster sigue valiendo."""
    try:
        data = json.loads((_home() / ROSTER_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    lst = data.get("workflow_agents", [])
    if not isinstance(lst, list) or not all(isinstance(a, str) and a.strip() for a in lst):
        return []
    return [a.strip() for a in lst]


def _from_env() -> tuple[str, list[str]] | None:
    owner = _env.get("OWNER", "").strip()
    raw = _env.get("AGENTS", "")
    agents = [a.strip() for a in raw.split(",") if a.strip()]
    if not owner or not agents:
        return None
    return owner, agents


def _resolve() -> tuple[str, set[str], set[str], str]:
    """(owner, activos, suspendidos, source). El owner siempre está entre los
    activos: el dueño de la casa mueve cualquier ticket y tiene que ser válido."""
    env = _from_env()
    if env:
        owner, agents = env
        suspended, source = [], "env"
    else:
        f = _from_file()
        if f:
            (owner, agents, suspended), source = f, "file"
        else:
            owner, agents, suspended, source = DEFAULT_OWNER, list(DEFAULT_AGENTS), [], "default"
    sus = set(suspended) - {owner}
    return owner, (set(agents) - sus) | {owner}, sus, source


def load() -> tuple[str, frozenset[str]]:
    """Devuelve (owner, agentes activos)."""
    owner, active, _, _ = _resolve()
    return owner, frozenset(active)


def reload() -> None:
    """#245: vuelve a leer el roster y lo aplica EN SU SITIO. agenda, decisions
    (DECIDERS) y workflows hicieron `from ... import AGENTS` al importar: si aquí
    se reasignara el nombre, seguirían mirando la lista vieja. Por eso AGENTS y
    SUSPENDED son sets que se vacían y se rellenan, nunca se sustituyen."""
    global HOUSE_OWNER, SOURCE
    owner, active, sus, source = _resolve()
    HOUSE_OWNER, SOURCE = owner, source
    AGENTS.clear()
    AGENTS.update(active)
    SUSPENDED.clear()
    SUSPENDED.update(sus)
    # Con el roster por env el fichero no cuenta, tampoco su lista de workflow.
    extra = set(_workflow_from_file()) if source == "file" else set()
    WORKFLOW_EXTRA.clear()
    WORKFLOW_EXTRA.update(extra - active - sus)


def save(owner: str, active: set[str], suspended: set[str]) -> None:
    """Escribe roster.json de forma atómica (tmp + replace) y lo recarga."""
    path = _home() / ROSTER_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "owner": owner,
        "agents": sorted(active | {owner}, key=str.lower),
        "suspended": sorted(suspended - {owner}, key=str.lower),
    }
    workflow = _workflow_from_file()  # #1186: la app no edita esta lista; se conserva tal cual
    if workflow:
        data["workflow_agents"] = workflow
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    reload()


# El dueño no cambia en caliente (no se renombra ni se quita), pero SOURCE sí:
# la primera escritura sobre el default crea roster.json y pasa a "file".
HOUSE_OWNER: str = ""
SOURCE: str = "default"
AGENTS: set[str] = set()
SUSPENDED: set[str] = set()
WORKFLOW_EXTRA: set[str] = set()
reload()
