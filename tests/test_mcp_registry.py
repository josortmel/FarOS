"""T5.6 (v3.1): registro de MCP — CRUD, búsqueda, import local, materialización por run."""
import json
from pathlib import Path

import pytest

from faros import db, jobs as jm, mcp_registry as reg, runner
from faros.service import TicketError


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def test_create_masks_secrets_and_search(conn):
    s = reg.create(conn, "alice", {"name": "gmail", "command": "node", "args": ["dist/index.js"],
                                  "env": {"GMAIL_TOKEN": "abc123"}, "note": "correo de owner"})
    assert s["env"] == {"GMAIL_TOKEN": "••••"} and s["source"] == "manual" and s["enabled"] is True
    raw = reg.get(conn, s["id"], reveal=True)
    assert raw["env"] == {"GMAIL_TOKEN": "abc123"}
    reg.create(conn, "alice", {"name": "ecodb", "command": "python", "args": ["-m", "ecodb"]})
    assert [x["name"] for x in reg.list_servers(conn, q="MAIL")] == ["gmail"]
    assert [x["name"] for x in reg.list_servers(conn, q="owner")] == ["gmail"]
    assert len(reg.list_servers(conn)) == 2


def test_validation(conn):
    with pytest.raises(TicketError, match="stdio requiere command"):
        reg.create(conn, "alice", {"name": "x"})
    with pytest.raises(TicketError, match="requiere url"):
        reg.create(conn, "alice", {"name": "x", "transport": "http"})
    with pytest.raises(TicketError, match="name"):
        reg.create(conn, "alice", {"name": "con espacio", "command": "a"})
    reg.create(conn, "alice", {"name": "dup", "command": "a"})
    with pytest.raises(TicketError, match="ya existe"):
        reg.create(conn, "alice", {"name": "dup", "command": "b"})


def test_update_keeps_secret_when_masked(conn):
    s = reg.create(conn, "alice", {"name": "srv", "command": "a", "env": {"K": "secreto"}})
    reg.update(conn, "alice", s["id"], {"env": {"K": "••••", "OTRA": "v"}, "note": "editado"})
    raw = reg.get(conn, s["id"], reveal=True)
    assert raw["env"] == {"K": "secreto", "OTRA": "v"} and raw["note"] == "editado"


def test_import_local_upsert_and_manual_not_overwritten(conn, tmp_path):
    cj = tmp_path / "claude.json"
    cj.write_text(json.dumps({"mcpServers": {
        "ecodb": {"command": "python", "args": ["-m", "ecodb"], "env": {"ECODB_KEY": "k1"}},
        "remote": {"type": "http", "url": "https://x/mcp"},
    }}), encoding="utf-8")
    res = reg.import_local(conn, "alice", cj)
    assert res["imported"] == 2 and res["names"] == ["ecodb", "remote"]
    e = reg.get_by_name(conn, "ecodb", reveal=True)
    assert e["source"] == "local" and e["env"] == {"ECODB_KEY": "k1"}
    assert reg.get_by_name(conn, "remote")["transport"] == "http"
    # cambia el fichero: local se actualiza
    cj.write_text(json.dumps({"mcpServers": {"ecodb": {"command": "python3", "args": []}}}), encoding="utf-8")
    res = reg.import_local(conn, "alice", cj)
    assert res["updated"] == 1 and reg.get_by_name(conn, "ecodb")["command"] == "python3"
    # editado a mano → manual → el import no lo pisa
    reg.update(conn, "alice", e["id"], {"command": "mi-python"})
    assert reg.get_by_name(conn, "ecodb")["source"] == "manual"
    res = reg.import_local(conn, "alice", cj)
    assert res["skipped"] == 1 and reg.get_by_name(conn, "ecodb")["command"] == "mi-python"


def test_delete_blocked_if_job_uses_it(conn):
    s = reg.create(conn, "alice", {"name": "used", "command": "a"})
    j = jm.create_job(conn, "alice", "job", "x", mcp_servers=["used"])
    with pytest.raises(TicketError, match="lo usan los jobs"):
        reg.delete(conn, "alice", s["id"])
    jm.update_job(conn, "alice", j["id"], mcp_servers=[])
    assert reg.delete(conn, "alice", s["id"])["name"] == "used"


def test_job_mcp_servers_must_exist(conn):
    with pytest.raises(TicketError, match="no está en el registro"):
        jm.create_job(conn, "alice", "job", "x", mcp_servers=["nope"])


def test_materialize_writes_config_and_allowlist(conn, tmp_path):
    reg.create(conn, "alice", {"name": "gmail", "command": "node", "args": ["i.js"], "env": {"T": "s3cr3t"}})
    reg.create(conn, "alice", {"name": "remote", "transport": "http", "url": "https://x/mcp",
                              "headers": {"Authorization": "Bearer zz"}})
    j = jm.create_job(conn, "alice", "job", "x", mcp_servers=["gmail", "remote"], inherit_mcp=["agenticos"])
    job = jm.get_job(conn, j["id"])
    j2, path = reg.materialize(conn, job, tmp_path)
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    assert cfg["mcpServers"]["gmail"] == {"command": "node", "args": ["i.js"], "env": {"T": "s3cr3t"}}
    assert cfg["mcpServers"]["remote"] == {"type": "http", "url": "https://x/mcp",
                                           "headers": {"Authorization": "Bearer zz"}}
    assert j2["mcp_config"] == str(path)
    assert json.loads(j2["inherit_mcp"]) == ["agenticos", "gmail", "remote"]
    adapter = runner.get_adapter("claude-cli")
    cmd = adapter.build_command(j2)
    i = cmd.index("--allowedTools")
    assert "mcp__gmail__*" in cmd[i + 1] and "mcp__remote__*" in cmd[i + 1] and "mcp__agenticos__*" in cmd[i + 1]
    assert cmd[cmd.index("--mcp-config") + 1] == str(path)
    reg.cleanup(path)
    assert not Path(path).exists()


def test_materialize_disabled_server_fails(conn, tmp_path):
    s = reg.create(conn, "alice", {"name": "off", "command": "a"})
    j = jm.create_job(conn, "alice", "job", "x", mcp_servers=["off"])
    reg.update(conn, "alice", s["id"], {"enabled": False})
    with pytest.raises(TicketError, match="deshabilitado"):
        reg.materialize(conn, jm.get_job(conn, j["id"]), tmp_path)


def test_backup_scrubs_mcp_env(conn, tmp_path):
    from faros import backup
    reg.create(conn, "alice", {"name": "gmail", "command": "node", "env": {"T": "s3cr3t"}})
    dest = tmp_path / "copy.db"
    import sqlite3
    out = sqlite3.connect(str(dest))
    conn.backup(out)
    out.close()
    backup._scrub_secrets(dest)
    bk = sqlite3.connect(str(dest))
    env = bk.execute("SELECT env FROM mcp_servers WHERE name='gmail'").fetchone()[0]
    bk.close()
    assert json.loads(env) == {"T": ""}
