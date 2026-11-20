"""Bootstrap de la suite.

La suite declara SU PROPIO roster antes de importar nada de `agenticos`.

Por qué existe este fichero: el roster se resuelve en tiempo de import
(`agenticos/roster.py`) y el default del código es neutro. Sin esto, los tests
que usan los nombres de la casa pasarían aquí solo porque existe un
`roster.json` en la máquina de quien los corre — y se caerían en un clon limpio
o en CI. Un verde que depende de un fichero de tu casa no es un verde.

Fijar el roster por env (que gana sobre el fichero) hace que la suite corra
igual en cualquier máquina, con roster.json o sin él.
"""

import os

os.environ.setdefault("FAROS_OWNER", "owner")
os.environ.setdefault("FAROS_AGENTS", "owner,alice,bob,carol,dave")
os.environ.setdefault("FAROS_TIMBRE", "0")  # #1184: los tests no tocan el relay de la casa

# #1354: lo mismo con Claude Code. El adaptador busca `claude` en el PATH al
# crearse, aunque el test simule el lanzamiento: con Claude Code instalado
# pasaban 31 tests que en una máquina limpia (el CI) fallaban. Un `claude` falso
# va por delante en el PATH desde aquí, antes de importar nada, porque un
# fixture llegaría tarde a lo que se crea al importar. Sale con 0 y no imprime
# nada; los tests que necesitan un modelo de verdad van marcados `judge`.
import atexit  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402

_BIN_FALSO = tempfile.mkdtemp(prefix="faros-test-bin-")
if os.name == "nt":
    with open(os.path.join(_BIN_FALSO, "claude.cmd"), "w", newline="\r\n") as f:
        f.write("@echo off\nexit /b 0\n")
else:
    _claude = os.path.join(_BIN_FALSO, "claude")
    with open(_claude, "w") as f:
        f.write("#!/bin/sh\nexit 0\n")
    os.chmod(_claude, 0o755)
os.environ["FAROS_TEST_BIN"] = _BIN_FALSO
os.environ["PATH"] = _BIN_FALSO + os.pathsep + os.environ.get("PATH", "")
atexit.register(shutil.rmtree, _BIN_FALSO, ignore_errors=True)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _saldo_sin_red(monkeypatch):
    """#465: el verificador mira el saldo de DeepSeek antes de usarlo (red). En la suite, hay saldo."""
    from faros import verifier

    monkeypatch.setattr(verifier, "_hay_saldo", lambda key: True)


@pytest.fixture(autouse=True)
def _claves_sin_red(monkeypatch):
    """#460: guardar una clave la PRUEBA contra su servicio (red). En la suite, nunca:
    se sustituye por una prueba falsa que rechaza los valores que empiezan por «mala».
    Devuelve la lista de llamadas, por si un test quiere ver si se probó."""
    from faros import credentials

    llamadas = []

    def falsa(kind, value):
        llamadas.append((kind, value))
        if value.startswith("mala"):
            return False, "el servicio la rechaza (HTTP 401)"
        return True, "prueba falsa"

    monkeypatch.setattr(credentials, "PROBAR", falsa)
    return llamadas
