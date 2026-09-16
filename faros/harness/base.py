"""Contrato HarnessAdapter v2 (T1.2).

Un harness es un ejecutable de agente headless (claude -p, opencode run, ...).
Cada adapter declara cómo se factura, qué sabe hacer, qué modelos ofrece (se
los PREGUNTA al harness — nada de listas duras, decisión del dueño), cómo se
construye el comando, cómo se lee su salida y cómo se lee su coste.

Este paquete NO importa service ni runner: es hoja del grafo de dependencias.
"""

from __future__ import annotations

import shutil
import subprocess

from ..procutil import run_hidden
import time
from dataclasses import dataclass, field, asdict

BILLING_MODES = {"subscription", "api", "local"}
COST_KINDS = {"billed", "equivalent", "none"}
MODELS_TTL_S = 3600


@dataclass
class ModelInfo:
    id: str
    label: str
    source: str  # help | api | probe | cli | local | seed | measured
    tier: str | None = None
    family: str | None = None       # v3.1 (T5.4): fable | opus | sonnet | haiku | otros
    alias_of: str | None = None
    validated: bool | None = None   # True sonda/medición OK · False rechazado · None sin sondar
    validated_at: str | None = None
    note: str = ""


@dataclass
class RunResult:
    status: str            # ok | error | timeout
    exit_code: int | None
    cost_usd: float | None
    session_id: str | None
    output_text: str
    raw: dict | None
    structured: dict | list | None = None


@dataclass
class Capabilities:
    mcp: bool = False
    allowed_tools: bool = False
    permission_mode: bool = False
    json_schema: bool = False
    effort: bool = False
    fallback_model: bool = False
    system_prompt: bool = False
    add_dir: bool = False
    budget: bool = False
    inherit_mcp: bool = False


class HarnessAdapter:
    """Clase base. Los adapters concretos sobreescriben lo marcado."""

    name: str = ""
    billing: str = "api"
    capabilities: Capabilities = Capabilities()
    executable: str | None = None  # nombre del binario para shutil.which
    required_settings: list[str] = []  # settings keys this adapter needs in env()

    def __init__(self):
        self._models: list[ModelInfo] = []
        self._models_at: float = 0.0
        self._models_source: str = "none"
        self._version: str | None = None

    # ---- presencia --------------------------------------------------------
    def find_exe(self) -> str | None:
        return shutil.which(self.executable) if self.executable else None

    def installed(self) -> bool:
        return self.find_exe() is not None

    def version(self) -> str | None:
        if self._version is None and self.installed():
            try:
                out = run_hidden([self.find_exe(), "--version"], capture_output=True,
                                 text=True, timeout=20)
                self._version = (out.stdout or out.stderr).strip().splitlines()[0][:80]
            except (subprocess.SubprocessError, OSError, IndexError):
                self._version = None
        return self._version

    # ---- modelos ----------------------------------------------------------
    def fetch_models(self, settings: dict | None = None) -> tuple[list[ModelInfo], str]:
        """Pregunta al harness. Devuelve (modelos, fuente). Sobreescribir."""
        return [], "none"

    def list_models(self, refresh: bool = False, settings: dict | None = None) -> list[ModelInfo]:
        stale = (time.time() - self._models_at) > MODELS_TTL_S
        if refresh or stale or not self._models:
            models, source = self.fetch_models(settings)
            if models or refresh or not self._models:
                self._models, self._models_source = models, source
                self._models_at = time.time()
        return self._models

    def validate_model(self, model: str) -> bool:
        """True si el harness acepta el nombre. Por defecto: está en la lista."""
        return any(m.id == model for m in self.list_models())

    # ---- ejecución --------------------------------------------------------
    def build_command(self, job: dict) -> list[str]:
        raise NotImplementedError

    def stdin_payload(self, job: dict, prompt: str) -> bytes | None:
        """Qué va por stdin (el prompt, por defecto). None si el adapter lo pasa
        como argumento en build_command."""
        return prompt.encode("utf-8")

    def env(self, settings: dict) -> dict:
        """Variables de entorno extra para el subproceso (p.ej. API keys leídas de
        settings, T1.7). `settings` trae los valores REALES. Por defecto ninguna."""
        return {}

    def preamble_args(self, preamble: str) -> list[str] | None:
        """Cómo entregar el preámbulo del agente genérico (T1.9). None = no hay
        flag: el runner lo antepone al prompt por stdin."""
        return None

    def parse_output(self, stdout: str, exit_code: int) -> RunResult:
        raise NotImplementedError

    def read_cost(self, result: RunResult) -> tuple[float | None, str]:
        """(coste, kind). subscription → 'equivalent'; api → 'billed'; local → 'none'."""
        if result.cost_usd is None:
            return None, "none"
        if self.billing == "subscription":
            return result.cost_usd, "equivalent"
        if self.billing == "api":
            return result.cost_usd, "billed"
        return None, "none"

    # ---- descripción para la API -------------------------------------------
    def describe(self, settings: dict | None = None, refresh: bool = False) -> dict:
        models = self.list_models(refresh=refresh, settings=settings) if self.installed() else []
        return {
            "name": self.name,
            "installed": self.installed(),
            "version": self.version(),
            "billing": self.billing,
            "capabilities": asdict(self.capabilities),
            "models": [asdict(m) for m in models],
            "models_source": self._models_source,
            "models_refreshed_at": (time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self._models_at))
                                    if self._models_at else None),
        }
