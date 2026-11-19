"""Pepe, 4-nov-2026 (#989): my_board devolvía 61.778 caracteres con 41 tareas y no cabía en una
respuesta. Por defecto, lo justo para decidir qué toca, lo trabajable primero y con tope."""

import json

from faros import mcp_server


def _ticket(i, status="accepted", when="2026-11-05T10:00"):
    return {"id": i, "title": f"Tarea {i}", "status": status, "priority": "alta", "owner": "Prima",
            "scheduled_at": when, "preferred_time": None, "due_state": "ok", "resumen": "x" * 200,
            "blocked_reason": "y" * 200, "created_at": "2026-11-01T10:00:00", "source": "proposed"}


def _board():
    return {"mine_open": [_ticket(i) for i in range(1, 41)] + [_ticket(99, "blocked", "2026-10-01T10:00")],
            "needs_my_verification": [_ticket(50)], "por_ubicar": [_ticket(60, when=None)]}


def _tool(monkeypatch, board, **kwargs):
    monkeypatch.setattr(mcp_server, "_call", lambda *a, **k: board)
    fn = getattr(mcp_server.my_board, "fn", mcp_server.my_board)
    return fn("Prima", **kwargs)


def test_slim_board_fits_and_says_what_is_left(monkeypatch):
    out = _tool(monkeypatch, _board())
    assert len(json.dumps(out, ensure_ascii=False)) < 10_000
    assert len(out["mine_open"]) == 30
    assert "11 más" in out["notes"]["mine_open"]
    assert "resumen" not in out["mine_open"][0]


def test_blocked_go_after_what_can_be_worked_on(monkeypatch):
    out = _tool(monkeypatch, _board(), limit=100)
    assert out["mine_open"][-1]["id"] == 99
    assert "notes" not in out


def test_full_returns_everything(monkeypatch):
    board = _board()
    assert _tool(monkeypatch, board, full=True) is board


def test_a_server_error_passes_through(monkeypatch):
    error = {"error": "x"}
    assert _tool(monkeypatch, error) == error
