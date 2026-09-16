"""Descubrimiento de modelos para claude-cli — módulo scratch.

Medido 2026-09-02 por Prima (#89 T1.3). Hilo integra en claude_cli.py.
Documentación de mediciones: docs/mediciones/modelos_claude_cli.md
"""

from __future__ import annotations

import json
import re
import subprocess

from ..procutil import run_hidden
from dataclasses import dataclass, asdict
from typing import Optional

KNOWN_ALIASES = ("opus", "sonnet", "fable", "haiku")

UNRECOGNIZED_MARKER = "[claude-code:unrecognized_model]"


@dataclass
class Model:
    name: str
    source: str  # "help" | "probe" | "api"
    accessible: Optional[bool] = None

    def to_dict(self) -> dict:
        return asdict(self)


def parse_help_aliases() -> list[Model]:
    """Aliases documentados en --help. Siempre disponible, coste $0."""
    try:
        r = run_hidden(
            ["claude", "--help"],
            capture_output=True, text=True, timeout=10
        )
        aliases = list(KNOWN_ALIASES)
        m = re.search(r"alias.*?'(\w+)'.*?'(\w+)'.*?'(\w+)'", r.stdout)
        if m:
            for g in m.groups():
                if g not in aliases:
                    aliases.append(g)
        return [Model(name=a, source="help") for a in aliases]
    except Exception:
        return [Model(name=a, source="help") for a in KNOWN_ALIASES]


def validate_model(name: str, timeout: int = 15) -> bool:
    """Sonda de coste $0. True si el nombre está en el catálogo del CLI.

    Mecanismo: ejecuta `claude -p --model <name> "x"` y busca el marcador
    [claude-code:unrecognized_model] en la salida combinada. El CLI valida
    contra su catálogo interno ANTES de cualquier llamada API, así que el
    coste es exactamente $0.00 (verificado).

    Limitación: devuelve True para modelos deprecated/retired que están
    en el catálogo pero la API rechaza. Para distinguir accesible de
    inaccesible se necesita un intento de inferencia (con --max-budget-usd
    mínimo) o la lista de /v1/models.
    """
    try:
        r = run_hidden(
            ["claude", "-p", "--model", name, "x"],
            capture_output=True, text=True, timeout=timeout
        )
        combined = (r.stderr or "") + (r.stdout or "")
        return UNRECOGNIZED_MARKER not in combined
    except Exception:
        return False


def list_models_api(api_key: str) -> list[Model]:
    """GET /v1/models con API key. Coste: $0 (endpoint de listado, no inferencia)."""
    import urllib.request

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/models",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return [
                Model(name=m["id"], source="api", accessible=True)
                for m in data.get("data", [])
            ]
    except Exception:
        return []


def list_models(
    api_key: str | None = None,
    refresh: bool = False,
) -> list[Model]:
    """Lista combinada de modelos disponibles.

    Sin key: devuelve los 4 aliases documentados (source=help).
    Con key: GET /v1/models como fuente de verdad (source=api),
    más los aliases como shortcuts.

    refresh se reserva para un futuro caché; hoy siempre recalcula.
    """
    models = parse_help_aliases()
    if api_key:
        api_models = list_models_api(api_key)
        if api_models:
            seen = {m.name for m in api_models}
            for m in models:
                if m.name not in seen:
                    api_models.append(m)
            return api_models
    return models
