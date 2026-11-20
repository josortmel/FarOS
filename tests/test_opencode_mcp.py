"""MCP del registro con opencode: desactivado desde el 4-sep (opencode arrancaba todos los
servidores de su config global en ventanas visibles). El código que lo configuraba se
borró el 20-nov (#1361, decisión de Pepe); queda el error, que es lo que se prueba aquí."""

import pytest

from faros import db
from faros import jobs as jm
from faros import mcp_registry as reg


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def test_materialize_opencode_is_refused_and_writes_nothing(conn, tmp_path):
    reg.create(conn, "alice", {"name": "agenticos", "command": "python", "args": ["-m", "x"]})
    j = jm.create_job(
        conn, "alice", "ds", "x", harness_name="opencode", model="deepseek/deepseek-v4-pro", mcp_servers=["agenticos"]
    )
    with pytest.raises(Exception, match="desactivado"):
        reg.materialize(conn, jm.get_job(conn, j["id"]), tmp_path, harness="opencode")
    assert list(tmp_path.iterdir()) == []


def test_materialize_claude_unchanged(conn, tmp_path):
    reg.create(conn, "alice", {"name": "agenticos", "command": "python", "args": ["-m", "x"]})
    j = jm.create_job(conn, "alice", "cc", "x", mcp_servers=["agenticos"])
    j2, path = reg.materialize(conn, jm.get_job(conn, j["id"]), tmp_path, harness="claude-cli")
    assert path.name == "mcp_config.json" and j2["mcp_config"] == str(path) and "_env" not in j2
