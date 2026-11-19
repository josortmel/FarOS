"""#154 (F-SEG-06): la casa (token, base, backups) no hereda los permisos de
AppData\\Local. Medido el 14-oct en la casa: además del usuario, SYSTEM y
Administradores, dos SID huérfanos tenían Modificar heredado sobre el token."""

import os
import subprocess

import pytest

from faros import acl

pytestmark = pytest.mark.skipif(os.name != "nt", reason="ACL de Windows")

USERS = "S-1-5-32-545"  # BUILTIN\Users: hace de «SID de más» heredado del padre


def _sids(path) -> str:
    out = subprocess.run(["icacls", str(path)], capture_output=True, text=True).stdout
    return out


def test_harden_dir_drops_inherited_principals_from_dir_and_children(tmp_path):
    parent = tmp_path / "local"
    home = parent / "faros_home"
    (home / "backups").mkdir(parents=True)
    (home / "token").write_text("x")
    (home / "backups" / "copia.db").write_text("y")
    # un principal de más en el padre, heredado hacia abajo (como los SID huérfanos)
    subprocess.run(["icacls", str(parent), "/grant", f"*{USERS}:(OI)(CI)M"],
                   capture_output=True, check=True)
    assert "BUILTIN\\Users" in _sids(home / "token")

    assert acl.harden_dir(home) is True

    for p in (home, home / "token", home / "backups" / "copia.db"):
        out = _sids(p)
        assert "BUILTIN\\Users" not in out, out
        assert "NT AUTHORITY\\SYSTEM" in out and "BUILTIN\\Administrators" in out, out
        assert os.environ["USERNAME"].lower() in out.lower(), out
    # lo nuevo que se cree dentro hereda ya el ACL corto
    (home / "nuevo").write_text("z")
    assert "BUILTIN\\Users" not in _sids(home / "nuevo")


def test_harden_dir_is_idempotent_and_never_raises(tmp_path):
    home = tmp_path / "h"
    home.mkdir()
    assert acl.harden_dir(home) is True
    assert acl.harden_dir(home) is True
    assert acl.harden_dir(tmp_path / "no_existe") is False
