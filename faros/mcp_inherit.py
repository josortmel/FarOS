"""Herencia de MCP de la sesión local (T1.6).

el dueño: un job hereda por defecto los MCP de la sesión local del usuario (incluido
agenticos) y además admite MCP concretos por job. Medido 1-sep: los servidores
de user scope LLEGAN a `claude -p` pero su uso se DENIEGA sin allowlist; el
adapter construye la allowlist a partir de inherit_mcp (ver claude_cli.py).

Aquí solo se LEE ~/.claude.json (user scope) para ofrecer el selector.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from . import env as _env

CLAUDE_JSON = Path(_env.get("CLAUDE_JSON") or Path.home() / ".claude.json")


def inherited_servers(path: Path | None = None) -> list[dict]:
    """[{name, command, scope:'user', transport}] — sin secretos (no se devuelven env)."""
    p = Path(path) if path else CLAUDE_JSON
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for name, cfg in sorted((data.get("mcpServers") or {}).items()):
        if not isinstance(cfg, dict):
            continue
        cmd = cfg.get("command") or cfg.get("url") or ""
        args = cfg.get("args") or []
        out.append({
            "name": name,
            "scope": "user",
            "transport": cfg.get("type") or ("http" if cfg.get("url") else "stdio"),
            "command": (cmd + (" " + " ".join(str(a) for a in args[:4]) if args else "")).strip()[:200],
        })
    return out


def allowlist_for(names: list[str]) -> str:
    """Patrón de allowlist por servidor heredado: mcp__<server>__* (medido en T1.6)."""
    return ",".join(f"mcp__{n}__*" for n in names)
