"""Adapter claude -p (claude CLI). Flags verificados primera mano en
`claude --help` 2.1.257 (2-sep): --add-dir, --effort, --fallback-model,
--json-schema, --system-prompt, --mcp-config, --strict-mcp-config,
--allowedTools, --permission-mode, --max-budget-usd, --output-format,
--no-session-persistence, --model. El COMPORTAMIENTO de effort/fallback/
system-prompt/json-schema/add-dir lo mide Prima en T1.8 antes de que la UI
los exponga; aquí solo se emiten cuando el job trae el campo.

Modelos: el CLI no tiene subcomando de listar (medido: `claude models` entra
como prompt). Fuentes (decisión 1): (b) aliases documentados en --help +
nombre completo libre validado por sonda; (a) GET /v1/models si hay API key
en settings. T1.3 (Prima) completa sonda y API; aquí va el parser del --help.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

from ..procutil import run_hidden
from pathlib import Path

from .base import HarnessAdapter, Capabilities, ModelInfo, RunResult

SUMMARY_MAX = 2000
_ALIAS_LABELS = {"fable": "Fable (alias: el más capaz)", "opus": "Opus (alias)",
                 "sonnet": "Sonnet (alias)", "haiku": "Haiku (alias: barato)"}
_ALIAS_TIER = {"fable": "max", "opus": "high", "sonnet": "mid", "haiku": "cheap"}


def parse_help_aliases(help_text: str) -> list[str]:
    """Extrae los aliases documentados en la ayuda de --model ("'fable', 'opus', or 'sonnet'")."""
    m = re.search(r"--model <model>(.*?)(?:\n\s{2}--|\Z)", help_text, re.S)
    if not m:
        return []
    block = m.group(1)
    found = re.findall(r"'([a-z][a-z0-9-]{2,})'", block)
    aliases = []
    for a in found:
        if a not in aliases and not a.startswith("claude-"):
            aliases.append(a)
    if "haiku" not in aliases:
        aliases.append("haiku")  # alias vigente aunque el ejemplo del --help no lo cite
    return aliases


def _probe_model(exe: str, model: str, timeout: int = 20) -> bool:
    """Sonda medida por Prima (docs/mediciones/modelos_claude_cli.md §2)."""
    from .model_discovery import UNRECOGNIZED_MARKER
    try:
        r = run_hidden([exe, "-p", "--model", model, "--tools", "",
                        "--max-budget-usd", "0.001", "--no-session-persistence", "x"],
                       capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        return False
    return UNRECOGNIZED_MARKER not in ((r.stderr or "") + (r.stdout or ""))


class ClaudeCliAdapter(HarnessAdapter):
    name = "claude-cli"
    billing = "subscription"  # OAuth: consume la suscripción, no precio API (el dueño)
    executable = "claude"
    capabilities = Capabilities(mcp=True, allowed_tools=True, permission_mode=True,
                                json_schema=True, effort=True, fallback_model=True,
                                system_prompt=True, add_dir=True, budget=False,
                                inherit_mcp=True)

    def __init__(self):
        super().__init__()
        self.exe = self._find_exe()

    @staticmethod
    def _find_exe() -> str:
        # El shim npm es un .cmd (no ejecutable sin shell); debajo hay un .exe nativo.
        shim = shutil.which("claude")
        if shim:
            exe = Path(shim).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
            if exe.exists():
                return str(exe)
            return shim
        raise RuntimeError("claude CLI no encontrado en PATH")

    def find_exe(self):
        return self.exe

    def _help_text(self) -> str:
        try:
            out = run_hidden([self.exe, "--help"], capture_output=True, text=True, timeout=30)
            return out.stdout or ""
        except (subprocess.SubprocessError, OSError):
            return ""

    def fetch_models(self, settings=None):
        """v3.1 (T5.4): familias + IDs concretos. Aliases del --help + semilla medida
        (model_catalog.SEED) + resultado del último refresco (settings.model_catalog)
        + /v1/models si hay API key."""
        from . import model_catalog as mc
        aliases = parse_help_aliases(self._help_text())
        entries = mc.merge_cache(mc.seed_entries(aliases), (settings or {}).get("model_catalog"))
        models = [ModelInfo(e.id, e.label, e.source, e.tier or _ALIAS_TIER.get(e.id), e.family,
                            e.alias_of, e.validated, e.validated_at, e.note) for e in entries]
        api_key = (settings or {}).get("anthropic_api_key")
        if api_key:
            # Fuente (a) de la decisión 1 — medida por Prima (T1.3): única que
            # garantiza acceso para la cuenta. Coste $0 (listado, no inferencia).
            from .model_discovery import list_models_api
            api_models = list_models_api(api_key)
            if api_models:
                seen = {m.id for m in models}
                models += [ModelInfo(m.name, m.name, "api", None, mc.family_of(m.name),
                                     None, True, mc.datetime.now().strftime("%Y-%m-%d"), "de /v1/models")
                           for m in api_models if m.name not in seen]
                return models, "help+measured+api"
        return models, "help+measured"

    def probe_ids(self) -> list[str]:
        """IDs que el refresco vuelve a sondar (todo lo que no es alias)."""
        return [m.id for m in self.list_models() if not m.alias_of and m.source != "help"]

    def probe(self, model: str) -> bool:
        return _probe_model(self.exe, model)

    def validate_model(self, model: str) -> bool:
        """Fast-path: lista conocida o forma de nombre completo. Si no, la sonda
        de Prima (T1.3): [claude-code:unrecognized_model] en stderr = no existe
        en el catálogo del CLI, coste $0. OJO: un nombre RECONOCIDO sí intenta
        la API — por eso la sonda va con --max-budget-usd mínimo y sin tools,
        y solo se llama cuando el fast-path no decide."""
        if any(m.id == model for m in self.list_models()):
            return True
        if re.fullmatch(r"claude-[a-z0-9-]+(\[1m\])?", model):
            return True
        return _probe_model(self.exe, model)

    def build_command(self, job: dict) -> list[str]:
        cmd = [self.exe, "-p", "--output-format", "json", "--no-session-persistence",
               "--model", job["model"]]
        if job.get("permission_mode"):
            cmd += ["--permission-mode", job["permission_mode"]]
        allowed = job.get("allowed_tools")
        inherit = job.get("inherit_mcp")
        if isinstance(inherit, str) and inherit.strip():
            try:
                inherit = json.loads(inherit)
            except json.JSONDecodeError:
                inherit = [s.strip() for s in inherit.split(",") if s.strip()]
        if inherit:
            # Herencia de MCP de la sesión local: allowlist automática de los
            # servidores marcados (medido 1-sep: sin allowlist se DENIEGAN).
            extra = ",".join(f"mcp__{name}__*" for name in inherit)
            allowed = f"{allowed},{extra}" if allowed else extra
        if allowed:
            cmd += ["--allowedTools", allowed]
        if job.get("mcp_config"):
            cmd += ["--mcp-config", job["mcp_config"]]
            if job.get("strict_mcp"):
                cmd += ["--strict-mcp-config"]
        add_dirs = job.get("add_dirs")
        if isinstance(add_dirs, str) and add_dirs.strip():
            try:
                add_dirs = json.loads(add_dirs)
            except json.JSONDecodeError:
                add_dirs = [s.strip() for s in add_dirs.split(";") if s.strip()]
        if add_dirs:
            cmd += ["--add-dir", *add_dirs]
        if job.get("effort"):
            cmd += ["--effort", str(job["effort"])]
        if job.get("fallback_model"):
            cmd += ["--fallback-model", job["fallback_model"]]
        if job.get("system_prompt"):
            cmd += ["--system-prompt", job["system_prompt"]]
        if job.get("json_schema"):
            schema = job["json_schema"]
            if not isinstance(schema, str):
                schema = json.dumps(schema)
            cmd += ["--json-schema", schema]
        # max_budget_usd NO se emite: billing=subscription, el tope no aplica (el dueño).
        return cmd

    def preamble_args(self, preamble: str) -> list[str]:
        # Decisión de método (T1.9, medido por Prima en T1.8): --append-system-prompt
        # AÑADE sin sustituir el system prompt de Claude Code → el agente conserva
        # tools/MCP. job.system_prompt (--system-prompt) es el modo "agente limpio".
        return ["--append-system-prompt", preamble]

    def parse_output(self, stdout: str, exit_code: int) -> RunResult:
        try:
            data = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            status = "ok" if exit_code == 0 and stdout.strip() else "error"
            return RunResult(status, exit_code, None, None, stdout, None)
        if isinstance(data, list):
            # claude ≥2.1.206 (verificado en vivo): --output-format json emite la
            # LISTA de mensajes; el veredicto es el último item type=="result".
            results = [m for m in data if isinstance(m, dict) and m.get("type") == "result"]
            if not results:
                return RunResult("error" if exit_code != 0 else "ok",
                                 exit_code, None, None, stdout[:SUMMARY_MAX], None)
            data = results[-1]
        text = data.get("result") or ""
        is_error = bool(data.get("is_error")) or data.get("subtype") not in (None, "success")
        return RunResult(
            "error" if (is_error or exit_code != 0) else "ok",
            exit_code,
            data.get("total_cost_usd"),
            data.get("session_id"),
            text,
            data,
            data.get("structured_output"),
        )
