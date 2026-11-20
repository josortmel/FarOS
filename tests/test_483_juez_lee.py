"""#483 (paso 3 del encargo «verificador automático para todo», Pepe 16-oct): el juez abre lo que
se le nombra con herramientas de SOLO LECTURA. Sin carpeta que abrir, juzga como antes."""

import asyncio
import json

import pytest

from faros import db, verifier


class _Proc:
    def __init__(self):
        self.returncode = 0

    async def communicate(self, data):
        _Proc.prompt = data.decode("utf-8")
        return json.dumps(
            {"type": "result", "is_error": False, "structured_output": {"verdict": "pass", "reason": "visto en x.md:1"}}
        ).encode(), b""


@pytest.fixture
def lanzado(monkeypatch):
    visto = {}

    async def fake_exec(*cmd, **kw):
        visto["cmd"], visto["cwd"] = list(cmd), kw.get("cwd")
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return visto


def test_con_carpeta_el_juez_lee_y_no_escribe(lanzado, tmp_path):
    asyncio.run(verifier._judge("juzga", read_dirs=[str(tmp_path), "C:/otra"]))
    cmd = lanzado["cmd"]
    assert cmd[cmd.index("--tools") + 1] == "Read,Grep,Glob"
    assert cmd[cmd.index("--allowedTools") + 1] == "Read,Grep,Glob"
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert cmd[cmd.index("--add-dir") + 1] == "C:/otra"
    assert lanzado["cwd"] == str(tmp_path)
    assert "Puedes abrir ficheros" in _Proc.prompt and "fichero y línea" in _Proc.prompt
    assert not any(t in ",".join(cmd) for t in ("Write", "Edit", "Bash"))


def test_sin_carpeta_juzga_como_antes_sin_herramientas(lanzado):
    asyncio.run(verifier._judge("juzga"))
    cmd = lanzado["cmd"]
    assert cmd[cmd.index("--tools") + 1] == "" and "--permission-mode" not in cmd
    assert "Puedes abrir ficheros" not in _Proc.prompt


def test_la_carpeta_sale_de_la_evidencia(tmp_path):
    c = db.connect(":memory:")
    f = tmp_path / "informe.md"
    f.write_text("hecho", encoding="utf-8")
    assert verifier._dirs_de_la_evidencia(c, {"evidence_type": "file_path", "evidence": str(f)}) == [str(tmp_path)]
    assert verifier._dirs_de_la_evidencia(c, {"evidence_type": "file_path", "evidence": str(tmp_path / "no.md")}) == []
    assert verifier._dirs_de_la_evidencia(c, {"evidence_type": "command_output", "evidence": "ok"}) == []


def test_para_un_run_su_output_y_el_cwd_del_job(tmp_path):
    notas = tmp_path / "notas"
    notas.mkdir()
    out = tmp_path / "runs" / "41.md"
    out.parent.mkdir()
    out.write_text("nota", encoding="utf-8")
    dirs = verifier._dirs_del_run({"output_path": str(out)}, {"cwd": str(notas)})
    assert dirs == [str(out.parent), str(notas)]
    assert verifier._dirs_del_run({"output_path": None}, {"cwd": None}) == []
