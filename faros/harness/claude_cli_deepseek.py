"""Adapter claude-cli contra la API de DeepSeek (#463, decisión de Pepe del 7-oct).

Es el mismo `claude -p` que el adapter claude-cli: conserva --allowedTools,
--permission-mode y los MCP, que opencode no deja limitar. Pero el hijo habla
con el endpoint de DeepSeek compatible con Anthropic. Antes se midió fuera de
FarOS, en el verificador del #378 (Prima, 20 corridas, 7-oct).

La clave sale de Ajustes › Claves (#460, copiada en settings.deepseek_api_key)
y SOLO llega a los jobs de este harness (required_settings, F-SEG-04): un job
claude-cli normal no la ve.

Falla cerrado: sin clave, el hijo apunta igual a DeepSeek con un token de
relleno. Así recibe un 401 en vez de caer a la suscripción de Anthropic o de
mandar el token OAuth de Anthropic a otra empresa.
"""
from __future__ import annotations

from .base import Capabilities, ModelInfo
from .claude_cli import ClaudeCliAdapter

BASE_URL = "https://api.deepseek.com/anthropic"
# GET https://api.deepseek.com/models, 9-oct-2026: exactamente estos dos.
MODELS = ("deepseek-flash", "deepseek-v4-pro")
# Las llamadas de fondo de Claude Code (los alias opus/sonnet/haiku y los
# subagentes) también tienen que ir a DeepSeek; si no, salen hacia modelos de
# Anthropic que este endpoint no tiene.
BACKGROUND_MODEL = "deepseek-flash"
TOKEN_RELLENO = "falta-la-clave-de-deepseek-en-ajustes"


class ClaudeCliDeepSeekAdapter(ClaudeCliAdapter):
    name = "claude-cli-deepseek"
    billing = "api"  # paga la cuenta de DeepSeek, no la suscripción
    # #1109: el hijo NO recibe la clave. runner._env_for pone ANTHROPIC_BASE_URL en el reenvío del
    # daemon (faros/deepseek_proxy.py) y un token que solo vale mientras dura su run.
    via_proxy = True
    TOKEN_RELLENO = TOKEN_RELLENO
    required_settings = ["deepseek_api_key"]
    # effort y fallback_model, no: el primero no está medido contra DeepSeek, y el
    # fallback sería un modelo de Anthropic que este endpoint no sirve.
    capabilities = Capabilities(mcp=True, allowed_tools=True, permission_mode=True,
                                json_schema=True, effort=False, fallback_model=False,
                                system_prompt=True, add_dir=True, budget=False,
                                inherit_mcp=True)

    def fetch_models(self, settings=None):
        return ([ModelInfo(m, m, "seed", None, "deepseek", None, None, None,
                           "API de DeepSeek compatible con Anthropic") for m in MODELS],
                "seed")

    def validate_model(self, model: str) -> bool:
        return model in MODELS

    def parse_output(self, stdout: str, exit_code: int):
        # Claude Code calcula total_cost_usd con precios de ANTHROPIC aunque hable con
        # DeepSeek: el 9-oct dijo 0,63 $ y 0,59 $ por llamadas de 12-15 s a deepseek-flash.
        # El runner pasa ese número tal cual a finish_run, que lo apunta como «billed» y lo
        # suma contra el max_budget_usd del job: un dato falso que además podría cortar un
        # job. El coste real está en la cuenta de DeepSeek; aquí, desconocido antes que
        # inventado (finish_run lo deja en cost_kind «none»).
        res = super().parse_output(stdout, exit_code)
        res.cost_usd = None
        return res

    def env(self, settings: dict) -> dict:
        key = (settings or {}).get("deepseek_api_key") or TOKEN_RELLENO
        return {
            "ANTHROPIC_BASE_URL": BASE_URL,
            "ANTHROPIC_AUTH_TOKEN": key,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": BACKGROUND_MODEL,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": BACKGROUND_MODEL,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": BACKGROUND_MODEL,
            "CLAUDE_CODE_SUBAGENT_MODEL": BACKGROUND_MODEL,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
