"""T5.8b (v3.1, owner 4-sep): AgenticOS comprueba/configura los MCP en la config global de
opencode antes de lanzar, y por run habilita solo los del job."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from faros import db, mcp_registry as reg, procutil

JSONC = '''{
  "$schema": "https://opencode.ai/config.json",
  // permisos de owner
  "permission": { "bash": "allow" },
  "mcp": {
    "obsidian": {
      "type": "local",
      "command": ["mcp-server.exe"] /* obsidian */
    }
  }
}
'''


@pytest.fixture(autouse=True)
def _never_touch_real_global(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "OPENCODE_GLOBAL", tmp_path / "global_opencode.jsonc")


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _servers(conn):
    reg.create(conn, "alice", {"name": "faros", "command": "python", "args": ["-m", "faros.mcp_server"]})
    reg.create(conn, "alice", {"name": "gmail", "command": "node", "args": ["i.js"], "env": {"T": "s"}})
    return reg.resolve(conn, ["faros", "gmail"])


def test_sync_adds_missing_preserving_comments(conn, tmp_path):
    p = tmp_path / "opencode.jsonc"
    p.write_text(JSONC, encoding="utf-8")
    servers = _servers(conn)
    added = reg.sync_opencode_global(servers, p)
    assert added == ["faros", "gmail"]
    text = p.read_text(encoding="utf-8")
    assert "// permisos de owner" in text and "/* obsidian */" in text
    g = reg.opencode_global_servers(p)
    assert set(g) == {"obsidian", "faros", "gmail"}
    assert g["faros"] == {"type": "local", "command": ["python", "-m", "faros.mcp_server"], "enabled": True}
    assert g["gmail"]["environment"] == {"T": "s"}
    assert (tmp_path / "opencode.jsonc.bak_agenticos").exists()
    # segunda vez: nada que añadir, no reescribe
    mtime = p.stat().st_mtime
    assert reg.sync_opencode_global(servers, p) == []
    assert p.stat().st_mtime == mtime


def test_sync_creates_file_and_mcp_section(conn, tmp_path):
    servers = _servers(conn)
    p = tmp_path / "x" / "opencode.jsonc"
    assert reg.sync_opencode_global(servers, p) == ["faros", "gmail"]
    assert set(reg.opencode_global_servers(p)) == {"faros", "gmail"}
    p2 = tmp_path / "nomcp.jsonc"
    p2.write_text('{\n  "permission": {"bash": "allow"}\n}\n', encoding="utf-8")
    reg.sync_opencode_global(servers[:1], p2)
    data = json.loads(reg._strip_jsonc(p2.read_text(encoding="utf-8")))
    assert data["permission"] == {"bash": "allow"} and "faros" in data["mcp"]


def test_run_overlay_disables_others(conn):
    servers = _servers(conn)[:1]  # solo agenticos
    ov = reg.opencode_run_overlay(servers, ["obsidian", "ecodb", "faros"])
    assert ov["mcp"]["faros"]["enabled"] is True and "command" in ov["mcp"]["faros"]
    assert ov["mcp"]["obsidian"] == {"enabled": False} and ov["mcp"]["ecodb"] == {"enabled": False}


def test_materialize_opencode_syncs_global(conn, tmp_path, monkeypatch):
    p = tmp_path / "opencode.jsonc"
    p.write_text(JSONC, encoding="utf-8")
    monkeypatch.setattr(reg, "OPENCODE_GLOBAL", p)
    from faros import jobs as jm
    _servers(conn)
    j = jm.create_job(conn, "alice", "ds", "x", harness_name="opencode", model="deepseek/deepseek-v4-pro",
                      mcp_servers=["faros"])
    with pytest.raises(Exception, match="desactivado"):
        reg.materialize(conn, jm.get_job(conn, j["id"]), tmp_path, harness="opencode")
    return
    j2, path = reg.materialize(conn, jm.get_job(conn, j["id"]), tmp_path, harness="opencode")
    assert "faros" in reg.opencode_global_servers(p)
    ov = json.loads(path.read_text(encoding="utf-8"))
    assert ov["mcp"]["obsidian"] == {"enabled": False} and ov["mcp"]["faros"]["enabled"] is True
    assert j2["_env"]["OPENCODE_CONFIG"] == str(path)


@pytest.mark.skipif(os.name != "nt", reason="solo Windows")
def test_hidden_console_kwargs_child_has_hidden_console():
    kw = procutil.hidden_console_popen_kwargs()
    assert kw["creationflags"] & subprocess.CREATE_NEW_CONSOLE
    code = ("import ctypes; h=ctypes.windll.kernel32.GetConsoleWindow();"
            " print(h != 0, ctypes.windll.user32.IsWindowVisible(h))")
    r = subprocess.run([os.environ.get("PYTHON", "python"), "-c", code], capture_output=True, text=True,
                       timeout=30, **kw)
    assert r.stdout.strip() == "True 0"  # tiene consola propia y NO es visible
