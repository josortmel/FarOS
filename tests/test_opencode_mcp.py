"""T5.8 (v3.1): MCP del registro en opencode — config por run vía OPENCODE_CONFIG."""
import json
from pathlib import Path

import pytest

from faros import db, jobs as jm, mcp_registry as reg


@pytest.fixture(autouse=True)
def _never_touch_real_global(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "OPENCODE_GLOBAL", tmp_path / "global_opencode.jsonc")


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def test_opencode_config_format(conn):
    reg.create(conn, "alice", {"name": "agenticos", "command": "python", "args": ["-m", "faros.mcp_server"],
                              "env": {"AGENTICOS_TOKEN": "t0k"}})
    reg.create(conn, "alice", {"name": "remote", "transport": "http", "url": "https://x/mcp",
                              "headers": {"Authorization": "Bearer zz"}})
    cfg = reg.opencode_config(reg.resolve(conn, ["agenticos", "remote"]))
    assert cfg["mcp"]["agenticos"] == {"type": "local", "command": ["python", "-m", "faros.mcp_server"],
                                       "enabled": True, "environment": {"AGENTICOS_TOKEN": "t0k"}}
    assert cfg["mcp"]["remote"] == {"type": "remote", "url": "https://x/mcp", "enabled": True,
                                    "headers": {"Authorization": "Bearer zz"}}


def test_materialize_opencode_sets_env_and_file(conn, tmp_path):
    reg.create(conn, "alice", {"name": "agenticos", "command": "python", "args": ["-m", "x"]})
    j = jm.create_job(conn, "alice", "ds", "x", harness_name="opencode", model="deepseek/deepseek-v4-pro",
                      mcp_servers=["agenticos"])
    job = jm.get_job(conn, j["id"])
    with pytest.raises(Exception, match="desactivado"):
        reg.materialize(conn, job, tmp_path, harness="opencode")
    return
    j2, path = reg.materialize(conn, job, tmp_path, harness="opencode")
    assert path.name == "opencode_mcp.json" and path.exists()
    assert j2["_env"]["OPENCODE_CONFIG"] == str(path)
    assert "mcp_config" not in j2 or j2["mcp_config"] == job.get("mcp_config")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mcp"]["agenticos"]["enabled"] is True
    assert all(v == {"enabled": False} for k, v in data["mcp"].items() if k != "agenticos")
    reg.cleanup(path)
    assert not path.exists()


def test_materialize_claude_unchanged(conn, tmp_path):
    reg.create(conn, "alice", {"name": "agenticos", "command": "python", "args": ["-m", "x"]})
    j = jm.create_job(conn, "alice", "cc", "x", mcp_servers=["agenticos"])
    j2, path = reg.materialize(conn, jm.get_job(conn, j["id"]), tmp_path, harness="claude-cli")
    assert path.name == "mcp_config.json" and j2["mcp_config"] == str(path) and "_env" not in j2
