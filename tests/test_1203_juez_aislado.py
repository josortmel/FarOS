"""#1203 (13-nov): el juez no ve las herramientas de la casa. Con solo --tools y --allowedTools un
`claude -p` seguía cargando todas las MCP (ecodb, relay, Drive…) y el mod de faro-agentes, y un juez
Haiku lanzó un Verifier mientras juzgaba. --safe-mode apaga MCP, plugins, mods, hooks y CLAUDE.md;
las herramientas integradas que pide --tools siguen (medido: un juez en safe-mode leyó un fichero)."""

import asyncio
import json

import pytest

from faros import verifier


class _Proc:
    returncode = 0

    async def communicate(self, data):
        return json.dumps({"type": "result", "is_error": False,
                           "structured_output": {"verdict": "pass", "reason": "x.md:1"}}).encode(), b""


@pytest.fixture
def lanzado(monkeypatch):
    visto = {}

    async def fake_exec(*cmd, **kw):
        visto["cmd"] = list(cmd)
        return _Proc()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return visto


@pytest.mark.parametrize("kw", [
    {},                                                        # sin nada que abrir
    {"read_dirs": ["C:/carpeta"]},                             # carpeta de la evidencia
    {"read_dirs": ["C:/carpeta"], "read_files": ["C:/carpeta/x.md"]},   # solo esos ficheros
    {"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:1"}},     # el camino de DeepSeek (#1109)
])
def test_el_juez_arranca_en_safe_mode(lanzado, kw):
    asyncio.run(verifier._judge("juzga", **kw))
    assert "--safe-mode" in lanzado["cmd"]
