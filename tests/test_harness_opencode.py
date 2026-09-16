"""Tests for the opencode adapter (T1.4)."""

import json
import pytest

from faros.harness.opencode import OpenCodeAdapter, _parse_events


FIXTURE_EVENTS = """{"type":"step_start","timestamp":1788349158681,"sessionID":"ses_test123","part":{"id":"prt_1","messageID":"msg_1","sessionID":"ses_test123","type":"step-start"}}
{"type":"text","timestamp":1788349159276,"sessionID":"ses_test123","part":{"id":"prt_2","messageID":"msg_1","sessionID":"ses_test123","type":"text","text":"The capital of France is ","time":{"start":1788349159200,"end":1788349159270}}}
{"type":"text","timestamp":1788349159400,"sessionID":"ses_test123","part":{"id":"prt_3","messageID":"msg_1","sessionID":"ses_test123","type":"text","text":"Paris.","time":{"start":1788349159280,"end":1788349159390}}}"""

FIXTURE_ERROR = """{"type":"error","timestamp":1788349123742,"sessionID":"ses_err456","error":{"name":"UnknownError","data":{"message":"Model not found: fake/model"}}}"""

FIXTURE_MODELS = """opencode/big-pickle
opencode/deepseek-v4-flash-free
opencode/mimo-v2.5-free
deepseek/deepseek-chat
deepseek/deepseek-reasoner
deepseek/deepseek-v4-flash
deepseek/deepseek-v4-pro"""


def test_parse_events_extracts_all():
    events = _parse_events(FIXTURE_EVENTS)
    assert len(events) == 3
    assert events[0]["type"] == "step_start"
    assert events[1]["type"] == "text"
    assert events[2]["type"] == "text"


def test_parse_output_concatenates_text():
    adapter = OpenCodeAdapter.__new__(OpenCodeAdapter)
    result = adapter.parse_output(FIXTURE_EVENTS, exit_code=0)
    assert result.status == "ok"
    assert result.output_text == "The capital of France is Paris."
    assert result.session_id == "ses_test123"


def test_parse_output_error():
    adapter = OpenCodeAdapter.__new__(OpenCodeAdapter)
    result = adapter.parse_output(FIXTURE_ERROR, exit_code=1)
    assert result.status == "error"
    assert "Model not found" in result.output_text
    assert result.session_id == "ses_err456"


def test_parse_output_empty():
    adapter = OpenCodeAdapter.__new__(OpenCodeAdapter)
    result = adapter.parse_output("", exit_code=0)
    assert result.status == "ok"
    assert result.output_text == ""


def test_build_command():
    adapter = OpenCodeAdapter.__new__(OpenCodeAdapter)
    adapter.exe = "/usr/bin/opencode"
    job = {"model": "deepseek/deepseek-v4-pro", "cwd": "/tmp/work",
           "permission_mode": "dontAsk"}
    cmd = adapter.build_command(job)
    assert cmd == ["/usr/bin/opencode", "run", "-m", "deepseek/deepseek-v4-pro",
                   "--format", "json", "--dir", "/tmp/work",
                   "--dangerously-skip-permissions"]


def test_build_command_minimal():
    adapter = OpenCodeAdapter.__new__(OpenCodeAdapter)
    adapter.exe = "opencode"
    job = {"model": "opencode/mimo-v2.5-free"}
    cmd = adapter.build_command(job)
    assert cmd == ["opencode", "run", "-m", "opencode/mimo-v2.5-free",
                   "--format", "json"]


def test_read_cost_billed():
    adapter = OpenCodeAdapter.__new__(OpenCodeAdapter)
    result = type("R", (), {"cost_usd": 0.0042})()
    cost, kind = adapter.read_cost(result)
    assert cost == 0.0042
    assert kind == "billed"


def test_read_cost_none():
    adapter = OpenCodeAdapter.__new__(OpenCodeAdapter)
    result = type("R", (), {"cost_usd": None})()
    cost, kind = adapter.read_cost(result)
    assert cost is None
    assert kind == "none"


def test_adapter_metadata():
    assert OpenCodeAdapter.name == "opencode"
    assert OpenCodeAdapter.billing == "api"
    assert OpenCodeAdapter.executable == "opencode"
    assert OpenCodeAdapter.capabilities.budget is True
    assert OpenCodeAdapter.capabilities.mcp is False  # T5.8 medido y desactivado 4-sep
