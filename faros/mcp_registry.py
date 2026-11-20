"""Registro de MCP propio de AgenticOS (v3.1, T5.6) — el dueño §4: «la app debería tener
su propio registro de MCP que pueda compartir entre harness … buscador … cada
agente con sus propios MCP».

Tabla mcp_servers (esquema v4). Un servidor = nombre único + transporte
(stdio: command+args+env | http/sse: url+headers). `env` y `headers` pueden
llevar secretos: se guardan en claro SOLO en la DB, se enmascaran en la API
(••••) y el backup los vacía (decisión #3 del dueño). Nunca van a meta.json.

Por run, `materialize(conn, job, run_dir)` escribe el fichero de configuración
del harness (claude-cli: --mcp-config JSON; opencode no admite MCP) dentro de
artifacts/run_<id>/ y el runner lo BORRA al terminar — el fichero temporal es el
único momento en que un secreto toca disco fuera de la DB.

Importación de la sesión local (~/.claude.json user scope): upsert por nombre
con source='local'; una entrada editada a mano (source='manual') no se pisa.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import env as _env
from .service import TicketError

MASK = "••••"
TRANSPORTS = {"stdio", "http", "sse"}
CLAUDE_JSON = Path(_env.get("CLAUDE_JSON") or Path.home() / ".claude.json")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row(r) -> dict:
    d = dict(r)
    d["args"] = json.loads(d.get("args") or "[]")
    d["env"] = json.loads(d.get("env") or "{}")
    d["headers"] = json.loads(d.get("headers") or "{}")
    d["enabled"] = bool(d["enabled"])
    return d


def _mask(d: dict) -> dict:
    out = dict(d)
    out["env"] = {k: (MASK if v else "") for k, v in d["env"].items()}
    out["headers"] = {k: (MASK if v else "") for k, v in d["headers"].items()}
    return out


def _validate(fields: dict, existing: dict | None = None) -> dict:
    out = {}
    if "name" in fields:
        name = (fields["name"] or "").strip()
        if not name or any(c in name for c in " /\\\t\n") or len(name) > 64:
            raise TicketError("name: identificador sin espacios ni barras (≤64), p.ej. 'gmail'")
        out["name"] = name
    transport = fields.get("transport", (existing or {}).get("transport", "stdio"))
    if transport not in TRANSPORTS:
        raise TicketError(f"transport inválido: {transport!r} (stdio|http|sse)")
    out["transport"] = transport
    if "command" in fields:
        out["command"] = (fields["command"] or "").strip() or None
    if "url" in fields:
        out["url"] = (fields["url"] or "").strip() or None
    if "args" in fields:
        args = fields["args"]
        if isinstance(args, str):
            args = [a for a in args.split() if a] if args.strip() else []
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise TicketError("args: lista de strings")
        out["args"] = json.dumps(args)
    for key in ("env", "headers"):
        if key in fields:
            val = fields[key] or {}
            if not isinstance(val, dict) or not all(isinstance(k, str) for k in val):
                raise TicketError(f"{key}: objeto clave→valor")
            prev = (existing or {}).get(key, {})
            clean = {}
            for k, v in val.items():
                v = "" if v is None else str(v)
                clean[k] = prev.get(k, "") if v == MASK else v  # •••• = conservar
            out[key] = json.dumps(clean)
    if "enabled" in fields:
        out["enabled"] = 1 if fields["enabled"] in (1, True, "1", "true") else 0
    if "note" in fields:
        out["note"] = (fields["note"] or "").strip()
    if "source" in fields:
        if fields["source"] not in {"manual", "local"}:
            raise TicketError("source: manual|local")
        out["source"] = fields["source"]
    # coherencia transporte ↔ campos
    merged = {**(existing or {}), **{k: (json.loads(v) if k in ("args",) else v) for k, v in out.items()}}
    if transport == "stdio" and not merged.get("command"):
        raise TicketError("stdio requiere command")
    if transport in {"http", "sse"} and not merged.get("url"):
        raise TicketError(f"{transport} requiere url")
    return out


# ------------------------------------------------------------------ CRUD


def get(conn, server_id: int, reveal: bool = False) -> dict:
    r = conn.execute("SELECT * FROM mcp_servers WHERE id=?", (server_id,)).fetchone()
    if r is None:
        raise TicketError(f"servidor MCP #{server_id} no existe")
    d = _row(r)
    return d if reveal else _mask(d)


def get_by_name(conn, name: str, reveal: bool = False) -> dict | None:
    r = conn.execute("SELECT * FROM mcp_servers WHERE name=?", (name,)).fetchone()
    if r is None:
        return None
    d = _row(r)
    return d if reveal else _mask(d)


def list_servers(conn, q: str | None = None, enabled_only: bool = False) -> list[dict]:
    sql, params, where = "SELECT * FROM mcp_servers", [], []
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        where.append(
            "(LOWER(name) LIKE ? OR LOWER(COALESCE(command,'')) LIKE ?"
            " OR LOWER(COALESCE(url,'')) LIKE ? OR LOWER(note) LIKE ?)"
        )
        params += [like, like, like, like]
    if enabled_only:
        where.append("enabled=1")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY name"
    return [_mask(_row(r)) for r in conn.execute(sql, params)]


def create(conn, agent: str, fields: dict) -> dict:
    from .service import _validate_agent

    _validate_agent(agent)
    if "name" not in fields:
        raise TicketError("name obligatorio")
    v = _validate({"transport": "stdio", "args": [], "env": {}, "headers": {}, **fields})
    if conn.execute("SELECT 1 FROM mcp_servers WHERE name=?", (v["name"],)).fetchone():
        raise TicketError(f"ya existe un servidor MCP llamado {v['name']!r}")
    now = _now()
    cols = list(v) + ["created_at", "updated_at"]
    cur = conn.execute(
        f"INSERT INTO mcp_servers ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [*v.values(), now, now]
    )
    conn.commit()
    return get(conn, cur.lastrowid)


def update(conn, agent: str, server_id: int, fields: dict) -> dict:
    from .service import _validate_agent

    _validate_agent(agent)
    existing = get(conn, server_id, reveal=True)
    v = _validate(fields, existing)
    if "name" in v and v["name"] != existing["name"]:
        if conn.execute("SELECT 1 FROM mcp_servers WHERE name=?", (v["name"],)).fetchone():
            raise TicketError(f"ya existe un servidor MCP llamado {v['name']!r}")
    v["updated_at"] = _now()
    if existing["source"] == "local" and any(k in v for k in ("command", "args", "url", "env", "headers")):
        v.setdefault("source", "manual")  # editado a mano: el import ya no lo pisa
    sets = ", ".join(f"{k}=?" for k in v)
    conn.execute(f"UPDATE mcp_servers SET {sets} WHERE id=?", [*v.values(), server_id])
    conn.commit()
    return get(conn, server_id)


def delete(conn, agent: str, server_id: int) -> dict:
    from .service import _validate_agent

    _validate_agent(agent)
    d = get(conn, server_id)
    used = [j["name"] for j in _jobs_using(conn, d["name"])]
    if used:
        raise TicketError(f"el servidor {d['name']!r} lo usan los jobs {used}: quítalo de ellos antes")
    conn.execute("DELETE FROM mcp_servers WHERE id=?", (server_id,))
    conn.commit()
    return {"deleted": server_id, "name": d["name"]}


def _jobs_using(conn, name: str) -> list[dict]:
    out = []
    for r in conn.execute("SELECT id, name, mcp_servers FROM agent_jobs WHERE mcp_servers IS NOT NULL"):
        try:
            names = json.loads(r["mcp_servers"] or "[]")
        except json.JSONDecodeError:
            names = []
        if name in names:
            out.append({"id": r["id"], "name": r["name"]})
    return out


# ------------------------------------------------------------------ import local


def import_local(conn, agent: str, path: Path | None = None) -> dict:
    """~/.claude.json (user scope) → registro. Upsert por nombre; source='manual'
    (editado a mano) no se pisa. Devuelve {imported, updated, skipped, names}."""
    from .service import _validate_agent

    _validate_agent(agent)
    p = Path(path) if path else CLAUDE_JSON
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TicketError(f"no se pudo leer {p}: {exc}")
    servers = data.get("mcpServers") or {}
    res = {"imported": 0, "updated": 0, "skipped": 0, "names": []}
    now = _now()
    for name, cfg in sorted(servers.items()):
        if not isinstance(cfg, dict):
            continue
        if cfg.get("url"):
            transport = "sse" if (cfg.get("type") == "sse") else "http"
            fields = {
                "transport": transport,
                "url": cfg["url"],
                "headers": cfg.get("headers") or {},
                "command": None,
                "args": [],
                "env": {},
            }
        else:
            fields = {
                "transport": "stdio",
                "command": cfg.get("command") or "",
                "args": [str(a) for a in (cfg.get("args") or [])],
                "env": {k: str(v) for k, v in (cfg.get("env") or {}).items()},
                "url": None,
                "headers": {},
            }
        existing = conn.execute("SELECT * FROM mcp_servers WHERE name=?", (name,)).fetchone()
        if existing is None:
            v = _validate({"name": name, "source": "local", "note": "importado de la sesión local", **fields})
            cols = list(v) + ["created_at", "updated_at"]
            conn.execute(
                f"INSERT INTO mcp_servers ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [*v.values(), now, now],
            )
            res["imported"] += 1
        elif existing["source"] == "local":
            v = _validate(fields, _row(existing))
            v["updated_at"] = now
            sets = ", ".join(f"{k}=?" for k in v)
            conn.execute(f"UPDATE mcp_servers SET {sets} WHERE id=?", [*v.values(), existing["id"]])
            res["updated"] += 1
        else:
            res["skipped"] += 1
        res["names"].append(name)
    conn.commit()
    return res


# ------------------------------------------------------------------ por run


def job_server_names(job: dict) -> list[str]:
    raw = job.get("mcp_servers")
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = [s.strip() for s in raw.split(",") if s.strip()]
    return [str(n) for n in raw if n]


def resolve(conn, names: list[str]) -> list[dict]:
    """Servidores en claro (para el fichero temporal). Error si falta o está deshabilitado."""
    out = []
    for n in names:
        d = get_by_name(conn, n, reveal=True)
        if d is None:
            raise TicketError(f"el job usa el servidor MCP {n!r} y no está en el registro")
        if not d["enabled"]:
            raise TicketError(f"el servidor MCP {n!r} está deshabilitado")
        out.append(d)
    return out


def claude_config(servers: list[dict]) -> dict:
    """Formato --mcp-config de Claude Code (mismo que ~/.claude.json mcpServers)."""
    cfg = {}
    for s in servers:
        if s["transport"] == "stdio":
            entry = {"command": s["command"], "args": s["args"]}
            if s["env"]:
                entry["env"] = s["env"]
        else:
            entry = {"type": s["transport"], "url": s["url"]}
            if s["headers"]:
                entry["headers"] = s["headers"]
        cfg[s["name"]] = entry
    return {"mcpServers": cfg}


def materialize(conn, job: dict, run_dir: Path, harness: str = "claude-cli") -> tuple[dict, Path | None]:
    """Si el job tiene mcp_servers: escribe la configuración del harness en run_dir y
    devuelve una COPIA del job preparada. el dueño (4-sep): «si se invoca un harness se
    comprueba si tiene el MCP configurado y si no, se configura antes de lanzar».
    Aquí se configura SIEMPRE por run, sin tocar la config global del usuario:
      claude-cli → mcp_config.json (--mcp-config) + allowlist mcp__<n>__* (medido T1.6)
      opencode   → no admite MCP: error (desactivado el 4-sep; el código que lo
                   configuraba se borró el 20-nov, #1361, decisión de Pepe)
    El runner borra el fichero al terminar (contiene secretos)."""
    names = job_server_names(job)
    if not names:
        return job, None
    servers = resolve(conn, names)
    j = dict(job)
    if harness == "opencode":
        raise TicketError(
            "MCP en opencode desactivado (4-sep): opencode arranca todos los servidores de su "
            "config global en ventanas visibles. "
            "Quita mcp_servers del job o usa claude-cli."
        )
    path = Path(run_dir) / "mcp_config.json"
    path.write_text(json.dumps(claude_config(servers), ensure_ascii=False), encoding="utf-8")
    j["mcp_config"] = str(path)
    inherit = job_server_names({"mcp_servers": job.get("inherit_mcp")})
    j["inherit_mcp"] = json.dumps(sorted(set(inherit) | set(names)))
    return j, path


def cleanup(path: Path | None) -> None:
    if path is None:
        return
    try:
        Path(path).unlink()
    except OSError:
        pass
