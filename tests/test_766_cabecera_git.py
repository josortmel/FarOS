"""#766 — la salida del verificador empieza por `git rev-parse HEAD`.

Hasta ahora el commit de una verificación lo declarábamos nosotros (VERSION_TESTED, o el hash
escrito en la nota): una declaración, no una prueba. Ahora la salida de cada prueba empieza por lo
que dice git en la copia donde se corrió, y por si esa copia tenía cambios sin commitear; la
sección que lee el juez no pierde esa cabecera al cortar la salida, y la nota que queda en el
historial empieza por el hash."""

import subprocess

import pytest

from faros import prueba, verifier


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True)


def _sha(repo, ref="HEAD"):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", ref],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "master")
    (r / "calc.py").write_text("def suma(a, b):\n    return a - b\n", encoding="utf-8")
    (r / "test_calc.py").write_text("x = 1\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "con el fallo")
    (r / "calc.py").write_text("def suma(a, b):\n    return a + b\n", encoding="utf-8")
    (r / "test_calc.py").write_text(
        "import sys\nfrom calc import suma\nsys.exit(0 if suma(2, 2) == 4 else 1)\n", encoding="utf-8")
    _git(r, "commit", "-q", "-am", "arreglo y su prueba")
    return r


def test_766_la_salida_empieza_por_el_hash_que_da_git(repo):
    r = prueba.ejecutar(str(repo), _sha(repo), "python test_calc.py")
    assert r["salida"].startswith(f"git rev-parse HEAD: {_sha(repo)}\ngit status: limpio\n")
    assert r["head"] == _sha(repo) and r["code"] == 0


def test_766_con_las_pruebas_traidas_al_padre_dice_que_hay_cambios(repo):
    padre = _sha(repo, "HEAD^")
    r = prueba.ejecutar(str(repo), padre, "python test_calc.py", traer=(_sha(repo), ["test_calc.py"]))
    assert r["salida"].startswith(f"git rev-parse HEAD: {padre}\ngit status: CON CAMBIOS sin commitear")
    assert "test_calc.py" in r["salida"].split("\n\n")[0]


def test_766_la_seccion_del_juez_no_pierde_la_cabecera_con_una_salida_larga(repo):
    (repo / "habla.py").write_text("print('x' * 10000)\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "una prueba que habla mucho")
    p = prueba.leer(f'{{"repo": "{repo.as_posix()}", "commit": "{_sha(repo)}", "prueba": "python habla.py"}}')
    res = prueba.comprobar(p)
    seccion = verifier._seccion_prueba(res)
    assert f"git rev-parse HEAD: {_sha(repo)}" in seccion
    assert res["head"] == _sha(repo)


def test_766_la_nota_del_historial_empieza_por_el_hash():
    assert verifier._nota_con_head({"head": "abc123"}, "todo bien") == "[git rev-parse HEAD abc123] todo bien"
    assert verifier._nota_con_head(None, "todo bien") == "todo bien"
    assert verifier._nota_con_head({"head": None}, "sin prueba") == "sin prueba"
