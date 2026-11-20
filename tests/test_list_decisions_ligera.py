"""Pepe, 3-nov-2026: «lo de que recibíais la lista completa de todas las preguntas y respuestas
históricas es una locura… eso hay que arreglarlo».

Medido ese día: list_decisions(project_id=10) devolvió 53,8 KB, todas las decisiones de la
historia con su contexto entero, el texto de cada opción y su historial. La tool MCP tiene que
devolver por defecto solo lo ABIERTO (pendientes y aplazadas), ligero y con tope; lo decidido,
solo si se pide; y el detalle entero, solo con full=True o pidiendo ids concretos.
"""

import asyncio
import json

import pytest

from faros import mcp_server


def _decision(i, status, ctx_len=2000):
    return {
        "id": i,
        "title": f"Decisión {i}",
        "status": status,
        "project_id": 10,
        "workflow_id": None,
        "asked_by": "Prima",
        "created_at": f"2026-11-0{i % 9 + 1}T10:00:00",
        "context": "x" * ctx_len,
        "options": [{"label": "A", "text": "y" * 500}, {"label": "B", "text": "z" * 500}],
        "decision": "A" if status == "decided" else None,
        "rationale": "porque sí" if status == "decided" else None,
        "decided_by": "Pepe" if status == "decided" else None,
        "decided_at": "2026-11-03T19:00:00" if status == "decided" else None,
        "deferred_until": None,
        "history": [{"event": "asked", "actor": "Prima", "changed_at": "x", "note": None}] * 5,
    }


@pytest.fixture
def servidor(monkeypatch):
    todas = [_decision(i, "decided") for i in range(1, 90)] + [
        _decision(90, "pending"),
        _decision(91, "deferred"),
        _decision(92, "pending"),
    ]
    pedidas = []

    def falso(method, path, **kw):
        pedidas.append(kw.get("params", {}))
        st = kw.get("params", {}).get("status")
        return [d for d in todas if st is None or d["status"] == st]

    monkeypatch.setattr(mcp_server, "_call", falso)
    return pedidas


def _llamar(**args):
    out = asyncio.run(mcp_server.mcp.call_tool("list_decisions", args))
    blocks = out[0] if isinstance(out, tuple) else out
    return json.loads(blocks[0].text)


def test_por_defecto_solo_lo_abierto_y_ligero(servidor):
    r = _llamar()
    ids = [d["id"] for d in r["decisions"]]
    assert sorted(ids) == [90, 91, 92]
    assert all("context" not in d and "history" not in d for d in r["decisions"])
    assert r["decisions"][0]["options"] == ["A", "B"]
    assert len(json.dumps(r, ensure_ascii=False)) < 3000


def test_lo_decidido_solo_si_se_pide_y_con_tope(servidor):
    r = _llamar(status="decided")
    assert len(r["decisions"]) == 15
    assert r["total"] == 89
    assert r["decisions"][0]["decision"] == "A" and r["decisions"][0]["rationale"] == "porque sí"
    assert len(json.dumps(r, ensure_ascii=False)) < 6000


def test_ids_concretos_traen_el_detalle_entero(servidor):
    r = _llamar(status="decided", ids=[5, 7])
    assert [d["id"] for d in r["decisions"]] == [7, 5] or [d["id"] for d in r["decisions"]] == [5, 7]
    assert all("context" in d and "history" in d for d in r["decisions"])


def test_full_true_trae_todo(servidor):
    r = _llamar(status="pending", full=True)
    assert all("context" in d for d in r["decisions"])
