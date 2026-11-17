"""#245: crear, renombrar, suspender y borrar agentes del roster, en caliente.

El perfil ES el agente del roster (Pepe, 25-sep). Contrato con la interfaz de
Lienzo (#252, 30-sep) y política de tareas decidida por Pepe (30-sep):

- Renombrar se lleva sus tareas, abiertas Y cerradas (tickets.owner), y la
  coordinación de sus workflows. La historia (created_by, verified_by,
  ticket_history, decisiones) conserva el nombre de entonces: es quién lo hizo.
- Suspender en vez de borrar («prefiero suspenderlo»): el suspendido sigue en
  roster.json y en la lista, pero no es agente válido para trabajo nuevo. Si
  tiene abiertas hay que decir a quién pasan; las cerradas conservan el nombre.
- Borrar solo un nombre sin ninguna tarea (creado por error).
- El dueño no se renombra, no se suspende y no se borra.
- Con el roster fijado por env, el env manda: toda escritura es un 409 que dice
  qué variable lo fija, en vez de fingir que guarda.

Todas las escrituras las hace el dueño de la casa: el roster es configuración
suya, y es quien firma en la historia de las tareas que se mueven.
"""

from __future__ import annotations

from . import env as _env
from . import roster
from .service import (CLOSED_STATUSES, SYSTEM_AGENTS, WORKFLOW_AGENTS, TicketError,
                      _append_history)

MAX_LEN = 40
RESERVED = {n.lower() for n in WORKFLOW_AGENTS | SYSTEM_AGENTS}


def _reserved() -> set[str]:
    """#1186: los agentes de workflow de roster.json también son nombres reservados."""
    return RESERVED | {n.lower() for n in roster.WORKFLOW_EXTRA}
_ERROR = {403: "forbidden", 404: "not_found", 409: "conflict", 422: "invalid_request"}


class RosterError(TicketError):
    """TicketError con estado HTTP propio y campos extra en el cuerpo (open_tickets)."""

    def __init__(self, message: str, status: int, field: str | None = None,
                 code: str | None = None, **extra):
        super().__init__(message, field=field, code=code)
        self.status = status
        self.error = _ERROR.get(status, "invalid_request")
        self.extra = extra


def _all() -> set[str]:
    return roster.AGENTS | roster.SUSPENDED


def _counts(conn, name: str) -> tuple[int, int]:
    closed = tuple(CLOSED_STATUSES)
    row = conn.execute(
        f"SELECT COUNT(*) AS total, SUM(status NOT IN ({','.join('?' * len(closed))})) AS open"
        " FROM tickets WHERE owner=?", (*closed, name)).fetchone()
    return int(row["open"] or 0), int(row["total"] or 0)


def listing(conn) -> dict:
    out = {"owner": roster.HOUSE_OWNER, "source": roster.SOURCE,
           "editable": roster.SOURCE != "env", "agents": []}
    if roster.SOURCE == "env":
        out["env_var"] = _env.which("AGENTS")
    for name in sorted(_all(), key=str.lower):
        open_n, total = _counts(conn, name)
        out["agents"].append({"name": name, "suspended": name in roster.SUSPENDED,
                              "open_tickets": open_n, "total_tickets": total})
    return out


def _writable() -> None:
    if roster.SOURCE == "env":
        var = _env.which("AGENTS") or "FAROS_AGENTS"
        raise RosterError(f"roster fixed by {var} env", 409, code="roster.env_fixed")


def _clean(name) -> str:
    n = str(name or "").strip()
    if not n:
        raise RosterError("el nombre no puede estar vacío", 422, field="name", code="roster.name_empty")
    if len(n) > MAX_LEN:
        raise RosterError(f"el nombre no puede pasar de {MAX_LEN} caracteres", 422,
                          field="name", code="roster.name_too_long")
    if n.lower() in _reserved():
        raise RosterError(f"«{n}» es un nombre reservado del sistema", 422,
                          field="name", code="roster.name_reserved")
    return n


def _existing(name: str) -> str:
    if name not in _all():
        raise RosterError(f"no existe el agente «{name}»", 404, code="roster.not_found")
    return name


def _not_owner(name: str, verb: str) -> None:
    if name == roster.HOUSE_OWNER:
        raise RosterError(f"el dueño no se puede {verb}", 403, code="roster.owner_protected")


def _taken(n: str, besides: str | None = None) -> None:
    if any(a.lower() == n.lower() and a != besides for a in _all()):
        raise RosterError(f"ya existe un agente «{n}»", 409, field="name", code="roster.name_taken")


def _commit(conn, active: set[str], suspended: set[str]) -> None:
    """Fichero y base juntos: si el fichero no se puede escribir, la base no cambia."""
    try:
        roster.save(roster.HOUSE_OWNER, active, suspended)
    except OSError:
        conn.rollback()
        raise
    conn.commit()


def create(conn, name) -> dict:
    _writable()
    n = _clean(name)
    _taken(n)
    _commit(conn, roster.AGENTS | {n}, set(roster.SUSPENDED))
    return {"agent": n}


def rename(conn, old: str, new) -> dict:
    _writable()
    n = _clean(new)
    _not_owner(old, "renombrar")
    _existing(old)
    _taken(n, besides=old)
    if n == old:
        return {"agent": n, "moved": 0}
    rows = conn.execute("SELECT id, status FROM tickets WHERE owner=?", (old,)).fetchall()
    for r in rows:
        _append_history(conn, r["id"], r["status"], r["status"], roster.HOUSE_OWNER,
                        f"roster: dueño {old} → {n} (renombrado)")
    conn.execute("UPDATE tickets SET owner=? WHERE owner=?", (n, old))
    conn.execute("UPDATE workflows SET coordinator=? WHERE coordinator=?", (n, old))
    active, sus = set(roster.AGENTS), set(roster.SUSPENDED)
    target = sus if old in sus else active
    target.discard(old)
    target.add(n)
    _commit(conn, active, sus)
    return {"agent": n, "moved": len(rows)}


def set_suspended(conn, name: str, suspended: bool, reassign_to: str | None = None) -> dict:
    _writable()
    _not_owner(name, "suspender")
    _existing(name)
    active, sus = set(roster.AGENTS), set(roster.SUSPENDED)
    if not suspended:
        if name in sus:
            sus.discard(name)
            active.add(name)
            _commit(conn, active, sus)
        return {"agent": name, "suspended": False, "reassigned": 0}
    closed = tuple(CLOSED_STATUSES)
    rows = conn.execute(
        f"SELECT id, status FROM tickets WHERE owner=? AND status NOT IN ({','.join('?' * len(closed))})",
        (name, *closed)).fetchall()
    if rows and not reassign_to:
        n = len(rows)
        raise RosterError(f"«{name}» tiene {n} tarea{'s' if n != 1 else ''} abierta{'s' if n != 1 else ''}:"
                          " di a quién pasan",
                          409, code="roster.open_tickets", open_tickets=len(rows))
    if reassign_to is not None and (reassign_to == name or reassign_to not in roster.AGENTS):
        raise RosterError("reassign_to tiene que ser otro agente activo", 422,
                          field="reassign_to", code="roster.bad_reassign")
    for r in rows:
        _append_history(conn, r["id"], r["status"], r["status"], roster.HOUSE_OWNER,
                        f"roster: dueño {name} → {reassign_to} ({name} suspendido)")
        conn.execute("UPDATE tickets SET owner=? WHERE id=?", (reassign_to, r["id"]))
    active.discard(name)
    sus.add(name)
    _commit(conn, active, sus)
    return {"agent": name, "suspended": True, "reassigned": len(rows)}


def delete(conn, name: str) -> dict:
    _writable()
    _not_owner(name, "quitar")
    _existing(name)
    _, total = _counts(conn, name)
    if total:
        raise RosterError(f"«{name}» tiene tareas: suspéndelo en vez de borrarlo", 409,
                          code="roster.has_tickets", total_tickets=total)
    _commit(conn, roster.AGENTS - {name}, roster.SUSPENDED - {name})
    return {"agent": name, "removed": True, "reassigned": 0}
