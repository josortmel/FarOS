"""Registro de harness (T1.2). Un adapter por ejecutable de agente headless.

Registrar uno nuevo: subclase de HarnessAdapter + entrada en _FACTORIES. El
registro es perezoso: un adapter cuyo ejecutable no está instalado se
describe como installed=False, no rompe el arranque.
"""

from __future__ import annotations

from .base import HarnessAdapter, ModelInfo, RunResult, Capabilities, BILLING_MODES, COST_KINDS

_FACTORIES: dict[str, type] = {}
_INSTANCES: dict[str, HarnessAdapter] = {}


def register(cls: type) -> type:
    _FACTORIES[cls.name] = cls
    _INSTANCES.pop(cls.name, None)
    return cls


def names() -> list[str]:
    return sorted(_FACTORIES)


def get(name: str) -> HarnessAdapter:
    if name not in _FACTORIES:
        raise KeyError(f"harness sin adapter: {name!r} (registrados: {names()})")
    if name not in _INSTANCES:
        _INSTANCES[name] = _FACTORIES[name]()
    return _INSTANCES[name]


def all_adapters() -> list[HarnessAdapter]:
    out = []
    for n in names():
        try:
            out.append(get(n))
        except Exception:  # ejecutable ausente: se describe como no instalado
            out.append(_Missing(n))
    return out


class _Missing(HarnessAdapter):
    def __init__(self, name):
        super().__init__()
        self.name = name
        self.billing = getattr(_FACTORIES[name], "billing", "api")
        self.capabilities = getattr(_FACTORIES[name], "capabilities", Capabilities())

    def installed(self):
        return False

    def version(self):
        return None


from . import claude_cli as _claude  # noqa: E402  (registro al importar)
register(_claude.ClaudeCliAdapter)

try:  # T1.4 (Prima): adapter opencode; si no existe aún, no pasa nada
    from . import opencode as _opencode  # noqa: E402
    register(_opencode.OpenCodeAdapter)
except ImportError:
    pass
