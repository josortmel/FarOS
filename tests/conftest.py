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
