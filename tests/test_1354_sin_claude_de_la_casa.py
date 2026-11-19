"""#1354 (CI/CD): la suite no usa el Claude Code instalado en la máquina.

Sin esto, 31 tests pasaban solo porque `claude` estaba en el PATH de quien los
corría: el adaptador busca el ejecutable de verdad al crearse, aunque el test
simule el lanzamiento. En una máquina limpia (el CI de GitHub) fallaban.
conftest.py pone un `claude` falso por delante en el PATH.
"""

import os
import shutil
from pathlib import Path


def test_el_claude_que_ven_los_tests_es_el_falso_de_la_suite():
    encontrado = shutil.which("claude")
    assert encontrado, "sin ningún claude en el PATH, los tests del adaptador no pueden crearlo"
    assert Path(encontrado).parent == Path(os.environ["FAROS_TEST_BIN"])
