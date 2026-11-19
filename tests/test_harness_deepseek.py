"""#463: harness claude-cli-deepseek — `claude -p` contra la API de DeepSeek.

Lo que tiene que cumplir, sin red (la prueba en vivo está en
docs/mediciones/deepseek_463/):
- la clave de DeepSeek solo llega a los jobs de ESTE harness (F-SEG-04);
- sin clave falla cerrado: apunta igual a DeepSeek con un token de relleno,
  nunca cae a la suscripción ni manda el OAuth de Anthropic fuera;
- conserva --allowedTools y --permission-mode, y la clave no va en el comando.
"""

import json

from faros import db, harness, runner, settings
from faros.harness.claude_cli import ClaudeCliAdapter
from faros.harness.claude_cli_deepseek import (BASE_URL, MODELS, TOKEN_RELLENO,
                                               ClaudeCliDeepSeekAdapter)

CLAVE = "sk-prueba-463-no-real"


def _conn_con_clave(tmp_path):
    conn = db.connect(tmp_path / "t463.db")
    settings._guardar(conn, "deepseek_api_key", CLAVE)
    conn.commit()
    return conn


def test_registrado_junto_al_claude_cli():
    assert "claude-cli-deepseek" in harness.names()
    assert "claude-cli" in harness.names()


def test_la_clave_no_llega_ni_a_este_harness(tmp_path):
    """#1109: antes la clave iba en ANTHROPIC_AUTH_TOKEN del run; ahora el run recibe el reenvío
    del daemon y un token de run, y la clave no está en ningún valor de su entorno."""
    conn = _conn_con_clave(tmp_path)
    ds = runner._env_for(conn, harness.get("claude-cli-deepseek"), "faros-run-x")
    normal = runner._env_for(conn, harness.get("claude-cli"))
    assert ds["ANTHROPIC_AUTH_TOKEN"] == "faros-run-x"
    assert ds["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:") and BASE_URL not in ds.values()
    assert not any(CLAVE in v for v in ds.values())
    assert "ANTHROPIC_AUTH_TOKEN" not in normal and "ANTHROPIC_BASE_URL" not in normal
    assert CLAVE not in normal.values()
    assert ClaudeCliAdapter.required_settings == []


def test_las_llamadas_de_fondo_tambien_van_a_deepseek():
    env = ClaudeCliDeepSeekAdapter.env(None, {"deepseek_api_key": CLAVE})
    for var in ("ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL"):
        assert env[var] == "deepseek-flash", var


def test_sin_clave_ni_token_falla_cerrado(tmp_path):
    """Sin token de run (o sin clave) el hijo apunta igual al reenvío de FarOS, que da 401: nunca cae a
    la suscripción de Anthropic ni manda el OAuth fuera."""
    for conn in (db.connect(tmp_path / "t463_sin.db"), _conn_con_clave(tmp_path)):
        env = runner._env_for(conn, harness.get("claude-cli-deepseek"))
        assert env["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:")
        assert env["ANTHROPIC_AUTH_TOKEN"] == TOKEN_RELLENO


def test_comando_conserva_herramientas_y_no_lleva_la_clave():
    ad = harness.get("claude-cli-deepseek")
    cmd = ad.build_command({"model": "deepseek-flash", "permission_mode": "dontAsk",
                            "allowed_tools": "Read"})
    assert cmd[cmd.index("--model") + 1] == "deepseek-flash"
    assert cmd[cmd.index("--allowedTools") + 1] == "Read"
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert not any(CLAVE in a or "sk-" in a for a in cmd)


def test_solo_los_modelos_de_deepseek():
    ad = harness.get("claude-cli-deepseek")
    assert [m.id for m in ad.list_models()] == list(MODELS)
    assert ad.validate_model("deepseek-flash") and ad.validate_model("deepseek-v4-pro")
    assert not ad.validate_model("sonnet")


def test_no_apunta_el_coste_falso_de_claude():
    # Claude Code pone precios de Anthropic a una llamada a DeepSeek (0,63 $ y 0,59 $
    # medidos por llamadas de 12-15 s). El runner pasa RunResult.cost_usd tal cual a
    # finish_run → tiene que salir None del parse, no de read_cost (que el runner no usa).
    salida = json.dumps([{"type": "result", "subtype": "success", "is_error": False,
                          "result": "hola", "total_cost_usd": 0.627614, "session_id": "s",
                          "modelUsage": {"deepseek-flash": {}}}])
    res = harness.get("claude-cli-deepseek").parse_output(salida, 0)
    assert res.status == "ok" and res.output_text == "hola"
    assert res.cost_usd is None
    assert harness.get("claude-cli").parse_output(salida, 0).cost_usd == 0.627614


def test_cobra_la_api_no_la_suscripcion():
    ad = harness.get("claude-cli-deepseek")
    assert ad.billing == "api"
    assert ad.capabilities.allowed_tools and ad.capabilities.permission_mode
    assert not ad.capabilities.fallback_model
