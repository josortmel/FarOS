"""Tests del contrato HarnessAdapter v2 y su registro (T1.2)."""

import json

import pytest

from faros import harness
from faros.harness.base import HarnessAdapter, Capabilities, ModelInfo, RunResult
from faros.harness.claude_cli import parse_help_aliases, ClaudeCliAdapter


HELP_SNIPPET = """
  --mcp-config <configs...>             Load MCP servers from JSON files or
  --model <model>                       Model for the current session. Provide
                                        an alias for the latest model (e.g.
                                        'fable', 'opus', or 'sonnet') or a
                                        model's full name (e.g.
                                        'claude-sonnet-4-5-20250929').
  --no-session-persistence              Disable session persistence - sessions
"""


def test_registry_has_claude_cli():
    assert "claude-cli" in harness.names()
    a = harness.get("claude-cli")
    assert a.billing == "subscription" and a.capabilities.mcp is True


def test_fake_adapter_registers_and_describes():
    class Fake(HarnessAdapter):
        name = "fake-harness"
        billing = "local"
        executable = "definitely-not-installed-xyz"
        capabilities = Capabilities(json_schema=True)

        def fetch_models(self, settings=None):
            return [ModelInfo("m1", "Model 1", "cli")], "cli"

        def build_command(self, job):
            return ["fake", job["model"]]

        def parse_output(self, stdout, exit_code):
            return RunResult("ok", exit_code, None, None, stdout, None)

    harness.register(Fake)
    try:
        d = [x for x in harness.all_adapters() if x.name == "fake-harness"][0].describe()
        assert d["installed"] is False and d["billing"] == "local"
        assert d["capabilities"]["json_schema"] is True and d["capabilities"]["mcp"] is False
        assert d["models"] == []  # no instalado → no se le pregunta
        inst = harness.get("fake-harness")
        assert [m.id for m in inst.list_models(refresh=True)] == ["m1"]
        assert inst.read_cost(RunResult("ok", 0, 0.5, None, "", None)) == (None, "none")
    finally:
        harness._FACTORIES.pop("fake-harness", None)
        harness._INSTANCES.pop("fake-harness", None)


def test_parse_help_aliases_from_snippet():
    assert parse_help_aliases(HELP_SNIPPET) == ["fable", "opus", "sonnet", "haiku"]


def test_parse_help_aliases_empty_on_garbage():
    assert parse_help_aliases("nothing here") == []


def test_claude_read_cost_is_equivalent_not_billed():
    a = harness.get("claude-cli")
    assert a.read_cost(RunResult("ok", 0, 0.03, None, "", None)) == (0.03, "equivalent")
    assert a.read_cost(RunResult("ok", 0, None, None, "", None)) == (None, "none")


def test_claude_build_command_v3_flags():
    a = harness.get("claude-cli")
    job = {"model": "haiku", "permission_mode": "dontAsk", "allowed_tools": "Read",
           "inherit_mcp": json.dumps(["agenticos"]), "mcp_config": "C:/x/mcp.json",
           "strict_mcp": 0, "add_dirs": json.dumps(["C:/data"]), "effort": "high",
           "fallback_model": "sonnet", "system_prompt": "eres un job",
           "json_schema": {"type": "object"}, "max_budget_usd": 5}
    cmd = a.build_command(job)
    assert cmd[1:5] == ["-p", "--output-format", "json", "--no-session-persistence"]
    assert "--allowedTools" in cmd and "Read,mcp__agenticos__*" in cmd
    assert "--strict-mcp-config" not in cmd  # strict solo si strict_mcp=1
    assert "--add-dir" in cmd and "C:/data" in cmd
    assert "--effort" in cmd and "--fallback-model" in cmd and "--system-prompt" in cmd
    assert "--json-schema" in cmd and '{"type": "object"}' in cmd
    assert "--max-budget-usd" not in cmd  # suscripción: el tope no aplica


def test_claude_build_command_minimal_is_unchanged_from_v1():
    a = harness.get("claude-cli")
    cmd = a.build_command({"model": "haiku", "permission_mode": "dontAsk"})
    assert cmd[1:] == ["-p", "--output-format", "json", "--no-session-persistence",
                       "--model", "haiku", "--permission-mode", "dontAsk"]


def test_claude_validate_model_accepts_full_names():
    a = harness.get("claude-cli")
    assert a.validate_model("claude-opus-4-6") is True
    assert a.validate_model("gpt-9") is False


def test_claude_parse_output_list_format():
    a = harness.get("claude-cli")
    out = json.dumps([{"type": "system"}, {"type": "result", "subtype": "success",
                                            "result": "hola", "total_cost_usd": 0.01,
                                            "session_id": "s1", "structured_output": {"k": 1}}])
    r = a.parse_output(out, 0)
    assert r.status == "ok" and r.output_text == "hola" and r.structured == {"k": 1}
