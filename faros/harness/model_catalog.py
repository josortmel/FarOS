"""Catálogo de modelos concretos (v3.1, T5.4) — el dueño, queja 6ª: «no puedo elegir bien el
modelo … lo que puedo elegir es la familia del modelo pero no el modelo».

Fuentes, en este orden:
  1. Aliases del --help del CLI (fable/opus/sonnet/haiku) — coste $0, siempre.
  2. SEMILLA medida por Prima el 4-sep-2026 (docs/mediciones/modelos_concretos.md,
     tools/probe_models.py): IDs concretos que `claude -p` ACEPTA con inferencia
     real. Va en código porque el daemon empaquetado no lleva docs/.
  3. /v1/models si hay API key (settings.anthropic_api_key) — lista de la cuenta.
  4. Refresco manual (POST /api/harnesses/claude-cli/models/refresh): re-sonda
     cada ID con el marcador [claude-code:unrecognized_model] (coste $0) y guarda
     el resultado en settings.model_catalog con fecha. Un ID que la sonda
     rechaza se marca validated=False y el modal lo enseña tachado.

Un job puede guardar cualquier ID del catálogo (validated != False) o un alias;
un ID desconocido se rechaza con la lista (evita el typo que solo falla al
correr a las 08:00 del lunes).
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, asdict
from datetime import datetime

MEASURED_AT = "2026-09-04"
MEASURED_BY = "Prima (tools/probe_models.py)"

# id → (familia, etiqueta, tier, nota)
SEED: dict[str, tuple[str, str, str, str]] = {
    "claude-opus-5":              ("opus",   "Opus 5",                "high",  "el opus actual (alias opus)"),
    "claude-opus-4-8":            ("opus",   "Opus 4.8",              "high",  ""),
    "claude-opus-4-6":            ("opus",   "Opus 4.6",              "high",  "escribe mejor"),
    "claude-opus-4-6[1m]":        ("opus",   "Opus 4.6 · 1M contexto", "high", "contexto largo"),
    "claude-sonnet-5":            ("sonnet", "Sonnet 5",              "mid",   "el sonnet actual (alias sonnet)"),
    "claude-sonnet-4-6":          ("sonnet", "Sonnet 4.6",            "mid",   "más barato y sensible; pendiente de sonda"),
    "claude-haiku-4-5-20251001":  ("haiku",  "Haiku 4.5",             "cheap", "alias haiku"),
    "claude-fable-5-1":           ("fable",  "Fable 5.1",             "max",   "alias fable"),
    "claude-fable-5":             ("fable",  "Fable 5",               "max",   ""),
}
# Medido el 4-sep: aceptados con inferencia real. Lo que no está aquí y sí en SEED
# queda validated=None (candidato) hasta la sonda del refresco.
MEASURED_OK = {"claude-opus-5", "claude-opus-4-8", "claude-opus-4-6", "claude-opus-4-6[1m]",
               "claude-sonnet-5", "claude-haiku-4-5-20251001", "claude-fable-5", "claude-fable-5-1"}
ALIASES = {"opus": "claude-opus-5", "sonnet": "claude-sonnet-5",
           "haiku": "claude-haiku-4-5-20251001", "fable": "claude-fable-5-1"}
FAMILY_ORDER = ["fable", "opus", "sonnet", "haiku", "otros"]
FAMILY_LABEL = {"fable": "Fable", "opus": "Opus", "sonnet": "Sonnet", "haiku": "Haiku", "otros": "Otros"}


def family_of(model_id: str) -> str:
    m = re.match(r"claude-(fable|opus|sonnet|haiku)", model_id)
    if m:
        return m.group(1)
    return model_id if model_id in ALIASES else "otros"


@dataclass
class CatalogEntry:
    id: str
    family: str
    label: str
    tier: str | None
    source: str            # help | seed | measured | api | probe
    alias_of: str | None = None
    validated: bool | None = None   # True sonda/medición OK · False rechazado · None sin sondar
    validated_at: str | None = None
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def seed_entries(aliases: list[str]) -> list[CatalogEntry]:
    out = []
    for a in aliases:
        out.append(CatalogEntry(a, family_of(a), f"{FAMILY_LABEL.get(a, a)} (alias → {ALIASES.get(a, '?')})",
                                None, "help", alias_of=ALIASES.get(a), validated=True,
                                validated_at=MEASURED_AT, note="alias: cambia cuando Anthropic sube versión"))
    for mid, (fam, label, tier, note) in SEED.items():
        ok = mid in MEASURED_OK
        out.append(CatalogEntry(mid, fam, label, tier, "measured" if ok else "seed",
                                validated=True if ok else None,
                                validated_at=MEASURED_AT if ok else None, note=note))
    return out


def merge_cache(entries: list[CatalogEntry], cache: dict | None) -> list[CatalogEntry]:
    """Aplica el resultado guardado del último refresco (settings.model_catalog)."""
    if not cache:
        return entries
    by_id = {e.id: e for e in entries}
    for mid, info in (cache.get("models") or {}).items():
        e = by_id.get(mid)
        if e is None:
            e = CatalogEntry(mid, family_of(mid), mid, None, "probe")
            entries.append(e); by_id[mid] = e
        e.validated = info.get("validated")
        e.validated_at = info.get("validated_at") or cache.get("at")
        if e.validated is True and e.source == "seed":
            e.source = "probe"
    return entries


def grouped(entries: list[CatalogEntry]) -> list[dict]:
    fams: dict[str, list[dict]] = {}
    for e in entries:
        fams.setdefault(e.family, []).append(e.to_dict())
    order = [f for f in FAMILY_ORDER if f in fams] + [f for f in fams if f not in FAMILY_ORDER]
    return [{"family": f, "label": FAMILY_LABEL.get(f, f), "models": fams[f]} for f in order]


def is_acceptable(model: str, entries: list[CatalogEntry]) -> tuple[bool, str]:
    """(ok, motivo). Acepta alias, IDs del catálogo no rechazados y la forma
    claude-<x>[1m] (deja pasar un ID nuevo de Anthropic sin esperar al refresco)."""
    by_id = {e.id: e for e in entries}
    e = by_id.get(model)
    if e is not None:
        if e.validated is False:
            return False, f"{model!r} está en el catálogo pero la sonda lo rechazó ({e.validated_at})"
        return True, ""
    if re.fullmatch(r"claude-[a-z0-9.-]+(\[1m\])?", model):
        return True, "forma válida, sin sondar"
    known = ", ".join(sorted(x.id for x in entries if x.validated is not False))
    return False, f"modelo desconocido {model!r}. Conocidos: {known}"


# ------------------------------------------------------------------ refresco (sonda $0)

_refresh_lock = threading.Lock()
_refresh_state: dict = {"running": False, "started_at": None, "finished_at": None, "result": None}


def refresh_state() -> dict:
    return dict(_refresh_state)


def run_probe(exe: str, ids: list[str], probe) -> dict:
    """Sonda síncrona: {models: {id: {validated, validated_at}}, at}."""
    at = datetime.now().isoformat(timespec="minutes")
    res = {}
    for mid in ids:
        ok = bool(probe(exe, mid))
        res[mid] = {"validated": ok, "validated_at": at}
    return {"at": at, "models": res}


def refresh_async(exe: str, ids: list[str], probe, on_done) -> bool:
    """Lanza la sonda en un hilo (cada `probe` ya corre sin ventana, procutil).
    on_done(result) guarda en settings. False si ya había una en marcha."""
    if not _refresh_lock.acquire(blocking=False):
        return False

    def _work():
        try:
            _refresh_state.update(running=True, started_at=datetime.now().isoformat(timespec="seconds"),
                                  finished_at=None)
            result = run_probe(exe, ids, probe)
            try:
                on_done(result)
            finally:
                _refresh_state.update(result=result)
        finally:
            _refresh_state.update(running=False, finished_at=datetime.now().isoformat(timespec="seconds"))
            _refresh_lock.release()

    threading.Thread(target=_work, name="model-catalog-refresh", daemon=True).start()
    return True
