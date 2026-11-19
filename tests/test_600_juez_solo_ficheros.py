"""#600, punto 3: para una evidencia `proof` con ficheros nombrados, el juez solo puede abrir ESOS
ficheros — ni Grep ni Glob ni el resto de la carpeta (decisión de Prima, 20-oct).

Y la distinción que quita los rojos falsos: «no he podido abrir» (fallo del verificador) no es lo
mismo que «lo he leído y no me convence» (juicio sobre el trabajo).
"""

import asyncio
import json

import pytest

from faros import verifier


class _Proc:
    def __init__(self):
        self.returncode = 0

    async def communicate(self, data):
        _Proc.prompt = data.decode("utf-8")
        return json.dumps({"type": "result", "is_error": False,
                           "structured_output": {"verdict": "pass", "reason": "visto en a.md:1"}}).encode(), b""


@pytest.fixture
def lanzado(monkeypatch):
    visto = {}

    async def fake_exec(*cmd, **kw):
        visto["cmd"], visto["cwd"] = list(cmd), kw.get("cwd")
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return visto


def _regla(path) -> str:
    p = path.resolve().as_posix()
    if len(p) > 1 and p[1] == ":":
        p = "//" + p[0].lower() + p[2:]
    return f"Read({p})"


def test_con_ficheros_nombrados_solo_se_abren_esos(lanzado, tmp_path):
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("uno", encoding="utf-8")
    b.write_text("dos", encoding="utf-8")

    asyncio.run(verifier._judge("juzga", read_dirs=[str(tmp_path)], read_files=[str(a), str(b)]))

    cmd = lanzado["cmd"]
    assert cmd[cmd.index("--tools") + 1] == "Read"            # sin Grep ni Glob
    assert cmd[cmd.index("--allowedTools") + 1] == f"{_regla(a)},{_regla(b)}"
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert lanzado["cwd"] == str(tmp_path)                    # el repo sigue de cwd
    assert "No tienes Grep ni Glob" in _Proc.prompt
    assert not any(t in ",".join(cmd) for t in ("Write", "Edit", "Bash"))


def test_la_regla_de_windows_es_la_forma_posix(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("x", encoding="utf-8")
    regla = verifier._reglas_read([str(f)])
    assert regla.startswith("Read(//")
    assert ":" not in regla.split("Read(")[1].split(")")[0][:3], "la unidad no lleva dos puntos"


def test_sin_ficheros_el_juez_sigue_como_antes(lanzado, tmp_path):
    """Un proof mal escrito no puede producir un rojo falso: cae al comportamiento de siempre."""
    asyncio.run(verifier._judge("juzga", read_dirs=[str(tmp_path)], read_files=[]))

    cmd = lanzado["cmd"]
    assert cmd[cmd.index("--tools") + 1] == "Read,Grep,Glob"
    assert cmd[cmd.index("--allowedTools") + 1] == "Read,Grep,Glob"
    assert "Tienes Read, Grep y Glob" in _Proc.prompt


def test_el_prompt_distingue_no_poder_abrir_de_no_convencer(lanzado, tmp_path):
    asyncio.run(verifier._judge("juzga", read_dirs=[str(tmp_path)]))

    assert "no he podido abrir" in _Proc.prompt
    assert "falta de acceso" in _Proc.prompt
    assert "lo he leído y no me convence" in _Proc.prompt


def test_los_ficheros_de_la_evidencia_son_solo_los_que_existen(tmp_path):
    hay = tmp_path / "hay.md"
    hay.write_text("x", encoding="utf-8")
    falta = tmp_path / "no_existe.md"
    t = {"evidence_type": "proof",
         "evidence": json.dumps({"repo": str(tmp_path), "ficheros": [str(hay), str(falta)]})}
    assert verifier._ficheros_de_la_evidencia(t) == [str(hay)]


def test_una_evidencia_que_no_es_proof_no_restringe_nada(tmp_path):
    assert verifier._ficheros_de_la_evidencia({"evidence_type": "file_path", "evidence": "x"}) == []
