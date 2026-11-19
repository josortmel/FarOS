"""#594 (paso 2 del encargo «verificador automático para todo», Pepe 16-oct; decisión 49): las
comprobaciones deterministas y la copia aislada, sobre un repo git de usar y tirar."""

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from faros import prueba


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True)


def _sha(repo, ref="HEAD"):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", ref],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "master")
    (r / "calc.py").write_text("def suma(a, b):\n    return a - b\n", encoding="utf-8")
    (r / "comprueba.py").write_text(
        "import sys\nfrom calc import suma\nsys.exit(0 if suma(2, 2) == 4 else 1)\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "con el fallo")
    (r / "calc.py").write_text("def suma(a, b):\n    return a + b\n", encoding="utf-8")
    _git(r, "commit", "-q", "-am", "arreglo")
    return r


def test_la_prueba_pasa_en_el_commit_y_falla_en_el_padre(repo):
    p = prueba.leer(f'{{"repo": "{repo.as_posix()}", "commit": "{_sha(repo)}", '
                    '"prueba": "python comprueba.py", "rojo_en_padre": true}')
    r = prueba.comprobar(p)
    assert r["ok"], r["checks"]
    nombres = [c["nombre"] for c in r["checks"]]
    assert nombres == ["commit existe", "commit en master", "prueba en el commit", "prueba en el padre (debe fallar)"]


def test_si_ya_pasaba_en_el_padre_no_demuestra_el_arreglo(repo):
    (repo / "otro.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "un cambio que no arregla nada")
    p = prueba.leer(f'{{"repo": "{repo.as_posix()}", "commit": "{_sha(repo)}", '
                    '"prueba": "python comprueba.py", "rojo_en_padre": true}')
    r = prueba.comprobar(p)
    assert not r["ok"] and r["checks"][-1]["ok"] is False


def test_una_rama_sin_fusionar_no_esta_en_master(repo):
    _git(repo, "checkout", "-q", "-b", "rama")
    (repo / "nuevo.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "en la rama")
    r = prueba.comprobar(prueba.leer(f'{{"repo": "{repo.as_posix()}", "commit": "{_sha(repo)}"}}'))
    assert not r["ok"]
    assert r["checks"][1]["nombre"] == "commit en master" and "NO está" in r["checks"][1]["detalle"]


def test_la_copia_no_ve_claves_ni_la_casa_y_se_borra(repo, monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", "secreto-594")
    monkeypatch.setenv("FAROS_HOME", "C:/la-casa-de-verdad")
    antes = {d for d in os.listdir(tempfile.gettempdir()) if d.startswith(prueba.PREFIJO)}
    r = prueba.ejecutar(str(repo), _sha(repo),
                        "python -c \"import os; print(os.environ.get('FAKE_API_KEY')); print(os.environ['FAROS_HOME'])\"")
    assert r["code"] == 0
    assert "secreto-594" not in r["salida"] and "la-casa-de-verdad" not in r["salida"]
    assert prueba.PREFIJO in r["salida"]                       # la casa del comando está en la copia
    despues = {d for d in os.listdir(tempfile.gettempdir()) if d.startswith(prueba.PREFIJO)}
    assert despues == antes                                      # la copia se borró
    lista = subprocess.run(["git", "-C", str(repo), "worktree", "list"], capture_output=True, text=True).stdout
    assert len(lista.strip().splitlines()) == 1                 # y git ya no la lista


def test_se_mata_si_se_pasa_de_tiempo(repo):
    r = prueba.ejecutar(str(repo), _sha(repo), "python -c \"import time; time.sleep(60)\"", timeout=2)
    assert r["timeout"] is True and r["code"] is None


def test_prueba_mal_declarada_se_rechaza(repo):
    with pytest.raises(prueba.PruebaError):
        prueba.leer("no es json")
    with pytest.raises(prueba.PruebaError):
        prueba.leer('{"commit": "abc"}')                            # sin repo
    with pytest.raises(prueba.PruebaError):
        prueba.leer('{"repo": "C:/x", "prueba": "pytest"}')        # sin commit
    with pytest.raises(prueba.PruebaError):
        prueba.leer('{"repo": "C:/otro", "commit": "abc"}', repos_admitidos=[str(repo)])
    with pytest.raises(prueba.PruebaError):
        prueba.leer('{"inventado": 1}')


def test_ficheros_nombrados(tmp_path):
    f = tmp_path / "informe.md"
    f.write_text("hecho", encoding="utf-8")
    r = prueba.comprobar({"ficheros": [str(f), str(tmp_path / "no.md")]})
    assert [c["ok"] for c in r["checks"]] == [True, False] and not r["ok"]


def test_un_test_nuevo_se_corre_contra_el_codigo_viejo(tmp_path):
    # El caso real del 16-oct (ba42d4c): el commit trae el arreglo Y su test. En el padre el test no
    # existía, así que correr «la prueba del padre» no demuestra nada; hay que traer el test nuevo.
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "master")
    (r / "calc.py").write_text("def suma(a, b):\n    return a - b\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "con el fallo")
    (r / "calc.py").write_text("def suma(a, b):\n    return a + b\n", encoding="utf-8")
    (r / "test_calc.py").write_text(
        "import sys\nfrom calc import suma\nsys.exit(0 if suma(2, 2) == 4 else 1)\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "arreglo con su test")
    assert prueba.ficheros_de_prueba(str(r), _sha(r)) == ["test_calc.py"]
    res = prueba.comprobar(prueba.leer(f'{{"repo": "{r.as_posix()}", "commit": "{_sha(r)}", '
                                       '"prueba": "python test_calc.py", "rojo_en_padre": true}'))
    assert res["ok"], res["checks"]
    assert "salió con 1" in res["checks"][-1]["detalle"]


# ------------------------------------------------------------------ #693 (22-oct): la rama la pone el repo, no quien completa

def test_693_un_repo_en_main_se_comprueba_contra_main(tmp_path):
    # EcoDB y EcoRelay trabajan en main: sin «en», antes se miraba «master», que allí no existe → fail siempre
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "uno")
    res = prueba.comprobar(prueba.leer(f'{{"repo": "{r.as_posix()}", "commit": "{_sha(r)}"}}'))
    assert res["ok"], res["checks"]
    assert res["checks"][1]["nombre"] == "commit en main"
    assert not res.get("no_fusionado")


def test_693_declarar_otra_rama_no_tumba_pero_marca_no_fusionado(repo):
    _git(repo, "checkout", "-q", "-b", "mi-rama")
    (repo / "nuevo.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "solo en mi rama")
    res = prueba.comprobar(prueba.leer(f'{{"repo": "{repo.as_posix()}", "commit": "{_sha(repo)}", "en": "mi-rama"}}'))
    assert res["ok"]                                   # el commit SÍ está en la rama que dice
    assert res["no_fusionado"] == "mi-rama"            # pero no en la de integración: lo sabe el verificador
    assert any("no fusionado en master" in s for s in res["sin_decidir"])


def test_693_un_pass_de_un_commit_no_fusionado_baja_a_incierto():
    from faros import verifier
    v = verifier._tope_no_fusionado({"no_fusionado": "mi-rama"}, {"verdict": "pass", "reason": "todo bien"})
    assert v["verdict"] == "uncertain" and "no fusionado" in v["reason"]
    assert verifier._tope_no_fusionado({"no_fusionado": "mi-rama"}, {"verdict": "fail", "reason": "x"})["verdict"] == "fail"
    assert verifier._tope_no_fusionado({}, {"verdict": "pass", "reason": "x"})["verdict"] == "pass"
    assert verifier._tope_no_fusionado(None, {"verdict": "pass", "reason": "x"})["verdict"] == "pass"
