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
os.environ.setdefault("FAROS_TIMBRE", "0")   # #1184: los tests no tocan el relay de la casa


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
