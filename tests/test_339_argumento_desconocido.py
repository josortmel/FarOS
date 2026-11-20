"""#339 (Prima, 13-oct) — una tool MCP que recibe un argumento que no declara tiene que FALLAR
nombrándolo, no descartarlo en silencio.

Medido el 21-sep y otra vez el 13-oct: update_ticket(ticket_id=…, blocked_reason="…") por MCP
devuelve 200 con el ticket entero y blocked_reason: null. La API no tiene la culpa (PATCH con
blocked_reason da 400 «campos no editables: ['blocked_reason']» desde la v3.0.0): el campo no
llega nunca, porque FastMCP valida los argumentos con un modelo pydantic que IGNORA lo que sobra
(mcp 1.27.1, ArgModelBase sin extra). Al servidor le llega un PATCH vacío, que devuelve el ticket
tal cual. Fallo silencioso idéntico a éxito: quien lo escribe cree que quedó guardado.
"""

import asyncio

import pytest

from faros import mcp_server


@pytest.fixture
def llamadas(monkeypatch):
    hechas = []

    def falso(method, path, **kw):
        hechas.append((method, path, kw))
        return {"ok": True}

    monkeypatch.setattr(mcp_server, "_call", falso)
    return hechas


def test_argumento_desconocido_falla_y_lo_nombra(llamadas):
    with pytest.raises(Exception) as e:
        asyncio.run(
            mcp_server.mcp.call_tool("update_ticket", {"agent": "Prima", "ticket_id": 339, "blocked_reason": "x"})
        )
    assert "blocked_reason" in str(e.value)
    assert llamadas == [], "no debe llegar ningún PATCH al servidor"


def test_todas_las_tools_prohiben_argumentos_desconocidos():
    """Si mcp mueve ArgModelBase, mcp_server se degrada en silencio al comportamiento viejo
    (solo avisa por stderr). Este test es el que lo caza. Cubre también las tools que se
    registren antes de la línea, que no heredarían el extra='forbid'."""
    tools = mcp_server.mcp._tool_manager.list_tools()
    assert tools
    sueltas = [t.name for t in tools if t.fn_metadata.arg_model.model_config.get("extra") != "forbid"]
    assert sueltas == []


def test_los_argumentos_declarados_siguen_pasando(llamadas):
    asyncio.run(mcp_server.mcp.call_tool("update_ticket", {"agent": "Prima", "ticket_id": 339, "title": "nuevo"}))
    assert llamadas == [("PATCH", "/api/tickets/339", {"json": {"agent": "Prima", "title": "nuevo"}})]
