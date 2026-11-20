"""#600 (resto del paso 2 del encargo «verificador automático para todo», Pepe 16-oct).
Punto 6: la copia desechable se borra SIEMPRE, también cuando la prueba deja dentro ficheros de
solo lectura (objetos de git). Lo cazó la sesión «code» el 16-oct: shutil.rmtree(ignore_errors=True)
dejaba faros-prueba-*/ en %TEMP% en 3 de 8 corridas.
"""

import tempfile
from pathlib import Path

from faros import prueba


def _git(repo, *args):
    import subprocess

    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True
    )


def _sha(repo):
    import subprocess

    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_la_copia_se_borra_aunque_la_prueba_deje_ficheros_de_solo_lectura(tmp_path):
    """El caso real: los objetos de git son de solo lectura y rmtree se rendía con ellos."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "master")
    (r / "bloquea.py").write_text(
        "import os, stat\n"
        # dentro de la copia, git se lo lleva al quitar el worktree; donde de verdad se queda
        # es en el TEMP del entorno limpio (base/tmp), que no pasa por git
        "p = os.path.join(os.environ['TEMP'], 'objeto.bin')\n"
        "open(p, 'w').write('x')\n"
        "os.chmod(p, stat.S_IREAD)\n"
        "sub = os.path.join(os.environ['TEMP'], 'pack')\n"
        "os.mkdir(sub)\n"
        "q = os.path.join(sub, 'idx.bin')\n"
        "open(q, 'w').write('y')\n"
        "os.chmod(q, stat.S_IREAD)\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "deja ficheros de solo lectura")

    antes = set(Path(tempfile.gettempdir()).glob(prueba.PREFIJO + "*"))
    res = prueba.ejecutar(str(r), _sha(r), "python bloquea.py")
    assert res["code"] == 0, res

    sobra = set(Path(tempfile.gettempdir()).glob(prueba.PREFIJO + "*")) - antes
    assert not sobra, f"la copia no se borró: {sorted(map(str, sobra))}"


def test_los_tests_de_app_encuentran_sus_modulos_de_node(tmp_path):
    """Punto 4: node_modules no está versionado, así que la copia (un worktree) no lo trae.
    Sin NODE_PATH, un test de app/ que haga require de una dependencia no la encuentra."""
    r = tmp_path / "repo"
    (r / "app" / "node_modules" / "faro-mod").mkdir(parents=True)
    (r / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    (r / "app" / "node_modules" / "faro-mod" / "package.json").write_text(
        '{"name": "faro-mod", "main": "index.js"}', encoding="utf-8"
    )
    (r / "app" / "node_modules" / "faro-mod" / "index.js").write_text("module.exports = 42;\n", encoding="utf-8")
    (r / "app" / "prueba.js").write_text(
        "const v = require('faro-mod');\nif (v !== 42) process.exit(1);\n", encoding="utf-8"
    )
    _git(r, "init", "-q", "-b", "master")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "test de app con una dependencia")

    res = prueba.ejecutar(str(r), _sha(r), "node app/prueba.js")
    assert res["code"] == 0, res


def test_el_entorno_limpio_lleva_el_usuario(tmp_path, monkeypatch):
    """#595 (22-oct), preparando la corrida 2: una prueba «suite completa» no podía pasar NUNCA en el ejecutor,
    porque test_acl_154 lee os.environ["USERNAME"] y el entorno limpio no lo heredaba (KeyError). El lanzador de
    jobs sí lo hereda (runner.py). El nombre de usuario no es un secreto."""
    monkeypatch.setenv("USERNAME", "alguien")
    env = prueba.entorno_limpio(tmp_path)
    assert env["USERNAME"] == "alguien"
    assert "ANTHROPIC_API_KEY" not in env and "DEEPSEEK_API_KEY" not in env
