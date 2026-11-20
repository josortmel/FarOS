"""Permisos de la casa en Windows (#154, F-SEG-06).

La casa (token, base, backups) heredaba el ACL de %LOCALAPPDATA%. Medido el
14-oct-2026 en la máquina del dueño: además del usuario, SYSTEM y
Administradores, dos SID huérfanos (cuentas ya borradas) tenían Modificar
heredado sobre el token. No hacían daño hoy, pero el ACL de la casa no lo
decidía FarOS sino quien tocara el perfil.

harden_dir corta la herencia y deja solo al usuario actual, SYSTEM y
Administradores, por SID (los nombres cambian con el idioma de Windows). Lo
que ya hay dentro y lo que se cree después hereda ese ACL corto. Nunca lanza:
si falla, lo dice en el log y el daemon arranca igual.
"""

from __future__ import annotations

import csv
import io
import logging
import os
from pathlib import Path

from . import procutil

log = logging.getLogger("faros.acl")

_SYSTEM = "S-1-5-18"
_ADMINS = "S-1-5-32-544"


def _current_user_sid() -> str | None:
    r = procutil.run_hidden(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=30)
    row = next(csv.reader(io.StringIO(r.stdout.strip())), None) if r.returncode == 0 else None
    return row[1] if row and len(row) > 1 and row[1].startswith("S-1-") else None


def harden_dir(path: str | os.PathLike) -> bool:
    """True si la carpeta queda con el ACL corto; False si no se pudo (o no es Windows)."""
    path = Path(path)
    if os.name != "nt" or not path.is_dir():
        return False
    try:
        user = _current_user_sid()
        if not user:
            log.warning("ACL de la casa sin tocar: no sé el SID del usuario actual")
            return False
        grants = [f"*{sid}:(OI)(CI)F" for sid in (user, _SYSTEM, _ADMINS)]
        r = procutil.run_hidden(
            ["icacls", str(path), "/grant:r", *grants, "/inheritance:r"], capture_output=True, text=True, timeout=60
        )
        if r.returncode != 0:
            log.warning(
                "ACL de la casa sin tocar: icacls salió con %s: %s", r.returncode, (r.stdout + r.stderr).strip()[:300]
            )
            return False
        return True
    except Exception as e:  # noqa: BLE001 — el daemon arranca aunque esto falle
        log.warning("ACL de la casa sin tocar: %s", e)
        return False
