"""#600 punto 5 (22-oct, en la casa con la 3.3.8): completar una tarea con evidencia `proof` POR MCP
fallaba siempre. El texto del proof es un JSON, y FastMCP, al ver un argumento `str | None` con
pinta de JSON, lo parsea a dict ANTES de validar; la validación pide str y responde «Input should be
a valid string». Un agente no podía declarar su prueba por la vía por la que completa sus tareas.
"""

import asyncio
import json

import pytest

from faros import mcp_server

PROOF = {"repo": "C:/proyectos/AgenticOS", "commit": "1caad37", "en": "master",
         "prueba": "python -m pytest -q tests/test_499_entrega.py", "rojo_en_padre": False,
         "texto": "línea con tildes"}


@pytest.fixture
def llamadas(monkeypatch):
    hechas = []

    def falso(method, path, **kw):
        hechas.append((method, path, kw))
        return {"ok": True}

    monkeypatch.setattr(mcp_server, "_call", falso)
    return hechas


@pytest.mark.parametrize("evidencia", [json.dumps(PROOF, ensure_ascii=False), PROOF])
def test_complete_con_proof_por_mcp_llega_como_texto_json(llamadas, evidencia):
    asyncio.run(mcp_server.mcp.call_tool("transition_ticket", {
        "agent": "Hilo", "ticket_id": 658, "action": "complete",
        "evidence_type": "proof", "evidence": evidencia}))
    (_, path, kw), = llamadas
    assert path == "/api/tickets/658/transition"
    assert isinstance(kw["json"]["evidence"], str)
    assert json.loads(kw["json"]["evidence"]) == PROOF


@pytest.mark.parametrize("evidencia", [json.dumps(PROOF, ensure_ascii=False), PROOF])
def test_reescribir_evidencia_proof_por_mcp_llega_como_texto_json(llamadas, evidencia):
    asyncio.run(mcp_server.mcp.call_tool("update_ticket", {
        "agent": "Hilo", "ticket_id": 658, "evidence": evidencia}))
    (_, _, kw), = llamadas
    assert isinstance(kw["json"]["evidence"], str)
    assert json.loads(kw["json"]["evidence"]) == PROOF


def test_evidencia_de_texto_normal_no_cambia(llamadas):
    asyncio.run(mcp_server.mcp.call_tool("transition_ticket", {
        "agent": "Hilo", "ticket_id": 1, "action": "complete",
        "evidence_type": "command_output", "evidence": "22 passed"}))
    assert llamadas[0][2]["json"]["evidence"] == "22 passed"
