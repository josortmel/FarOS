"""Ajustes de la app en DB (T0.4). Sustituyen a las variables de entorno.

DEFAULTS declara cada clave con su valor por defecto, si es secreta y cómo se
valida. get_all() enmascara los secretos ('••••' si hay valor, '' si no) — es
lo que ve la API y la UI. get() devuelve el valor real: solo lo usa el daemon
(verificador, runner, backup, notificaciones). Las env vars siguen como
fallback mientras la clave esté vacía (transición sin sorpresas).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

from .service import TicketError, _validate_agent

MASK = "••••"

# key → {default, secret, env (fallback), kind}
DEFAULTS: dict[str, dict] = {
    "casa_slots": {"default": [["09:00", "12:00"], ["14:00", "18:00"]], "secret": False,
                   "kind": "slots", "env": None},
    "verifier_model": {"default": "haiku", "secret": False, "kind": "str",
                       "env": "AGENTICOS_VERIFY_MODEL"},
    "telegram_token": {"default": "", "secret": True, "kind": "str",
                       "env": "AGENTICOS_TG_BOT_TOKEN"},
    "telegram_chat_id": {"default": "", "secret": False, "kind": "str",
                         "env": "AGENTICOS_TG_CHAT_ID"},
    "anthropic_api_key": {"default": "", "secret": True, "kind": "str",
                          "env": "ANTHROPIC_API_KEY"},
    "deepseek_api_key": {"default": "", "secret": True, "kind": "str",
                         "env": "DEEPSEEK_API_KEY"},
    "today_window": {"default": {"overdue": True, "due_today": True, "in_progress": True},
                     "secret": False, "kind": "today_window", "env": None},
    "backup_retention_days": {"default": 14, "secret": False, "kind": "int", "env": None},
    "verifier_enabled": {"default": True, "secret": False, "kind": "bool", "env": None},
    # v3.1 (T4.2): raíz de repo por defecto para documents() cuando el workflow no la trae.
    "repo_root_default": {"default": "", "secret": False, "kind": "str", "env": "AGENTICOS_REPO_ROOT"},
    # v3.1 (T5.4): resultado del último refresco del catálogo de modelos {at, models:{id:{validated,validated_at}}}
    "model_catalog": {"default": {}, "secret": False, "kind": "json", "env": None},
}

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _validate(key: str, value):
    spec = DEFAULTS[key]
    kind = spec["kind"]
    if kind == "json":
        if value is None:
            return {}
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                raise TicketError(f"{key}: JSON inválido")
        if not isinstance(value, dict):
            raise TicketError(f"{key}: objeto JSON")
        return value
    if kind == "str":
        if value is None:
            return ""
        if not isinstance(value, str):
            raise TicketError(f"{key}: se esperaba texto")
        return value.strip()
    if kind == "int":
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise TicketError(f"{key}: se esperaba entero")
        if n < 1 or n > 3650:
            raise TicketError(f"{key}: fuera de rango [1, 3650]")
        return n
    if kind == "bool":
        if value in (True, False, 0, 1, "true", "false", "0", "1"):
            return value in (True, 1, "true", "1")
        raise TicketError(f"{key}: se esperaba booleano")
    if kind == "slots":
        if not isinstance(value, list) or not value:
            raise TicketError("casa_slots: lista de [inicio, fin] no vacía")
        out = []
        for slot in value:
            if (not isinstance(slot, (list, tuple)) or len(slot) != 2
                    or not all(isinstance(x, str) and _HHMM.match(x) for x in slot)):
                raise TicketError(f"casa_slots: franja inválida {slot!r} (['HH:MM','HH:MM'])")
            if slot[0] >= slot[1]:
                raise TicketError(f"casa_slots: franja vacía o invertida {slot!r}")
            out.append([slot[0], slot[1]])
        return out
    if kind == "today_window":
        if not isinstance(value, dict):
            raise TicketError("today_window: objeto {overdue, due_today, in_progress}")
        keys = {"overdue", "due_today", "in_progress"}
        unknown = set(value) - keys
        if unknown:
            raise TicketError(f"today_window: claves desconocidas {sorted(unknown)}")
        merged = {**DEFAULTS[key]["default"], **{k: bool(v) for k, v in value.items()}}
        return merged
    raise TicketError(f"{key}: tipo sin validador ({kind})")


def _stored(conn) -> dict:
    return {r["key"]: json.loads(r["value"]) if r["value"] is not None else None
            for r in conn.execute("SELECT key, value FROM settings")}


def get(conn, key: str):
    """Valor REAL (para el daemon). Vacío → env var → default."""
    if key not in DEFAULTS:
        raise TicketError(f"setting desconocido: {key!r}")
    stored = _stored(conn)
    if key in stored and stored[key] not in (None, ""):
        return stored[key]
    env = DEFAULTS[key]["env"]
    if env:
        # #303: LAS DOS FAMILIAS DE NOMBRE, como en todo lo demas del rename.
        # Aqui se leia `os.environ.get(env)` a pelo, con el nombre VIEJO clavado
        # en la tabla de arriba. O sea que quien siguiera nuestra documentacion y
        # pusiera FAROS_VERIFY_MODEL no configuraba nada — y no se enteraba: caia
        # al valor por defecto EN SILENCIO, que es el peor modo de fallo posible
        # para un ajuste.
        # Son cuatro: el modelo del verificador, los dos de Telegram y el repo
        # raiz. El rename llego a lo que SE VE y no a lo que SE CONFIGURA.
        for nombre in (env.replace("AGENTICOS_", "FAROS_", 1), env):
            valor = os.environ.get(nombre)
            if valor:
                return valor
    return DEFAULTS[key]["default"]


def get_all(conn) -> dict:
    """Lo que ve la API: secretos enmascarados, nunca en claro."""
    out = {}
    for key, spec in DEFAULTS.items():
        val = get(conn, key)
        if spec["secret"]:
            out[key] = MASK if val else ""
        else:
            out[key] = val
    return out


def patch(conn, agent: str, changes: dict) -> dict:
    """Solo familia. Valida cada clave; un valor '••••' en un secreto significa
    'no tocar' (la UI reenvía lo que ve)."""
    _validate_agent(agent, family_only=True)
    if not changes:
        return get_all(conn)
    unknown = set(changes) - set(DEFAULTS)
    if unknown:
        raise TicketError(f"settings desconocidos: {sorted(unknown)} (válidos: {sorted(DEFAULTS)})")
    now = datetime.now().isoformat(timespec="seconds")
    for key, value in changes.items():
        spec = DEFAULTS[key]
        if spec["secret"] and value == MASK:
            continue
        clean = _validate(key, value)
        conn.execute(
            "INSERT INTO settings (key, value, secret, updated_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(clean), 1 if spec["secret"] else 0, now))
    conn.commit()
    return get_all(conn)
