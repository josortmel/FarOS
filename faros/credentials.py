"""Claves (#460, 7-oct-2026): una lista ABIERTA de secretos de servicios externos.

El dueño, al pedir la pantalla: «imagínate que creamos varios bots de Telegram…
debería aceptar las cosas de una forma no tan única y dura». Así que no es un campo
por servicio: es una lista donde cabe cualquier número de claves de cada tipo, con
su nombre y, si se quiere, de dónde viene. Sus decisiones (7-oct):

  1. Se guardan TODAS y FarOS usa UNA de cada cosa: la marcada «en uso» de su tipo.
  2. Al guardar se PRUEBA según el tipo y SOLO se guarda si vale (DeepSeek y
     Anthropic: su lista de modelos; Telegram: getMe). Ninguna prueba cuesta nada.
  3. El tipo «otra» se admite: no se sabe probar, así que queda «sin probar».

CÓMO LA USA FarOS SIN QUE NADIE MÁS CAMBIE. La marcada «en uso» de cada tipo se
COPIA en su ajuste de siempre (deepseek_api_key, anthropic_api_key, telegram_token).
Quien la lee hoy (opencode, el verificador, las notificaciones) sigue leyendo
settings.get y no se entera. Y una base con esta tabla sigue sirviendo a un daemon
anterior: sus ajustes siguen ahí, con la clave en uso.

El valor no sale nunca de aquí en claro: la API enmascara («••••»), el backup lo
vacía, y un error de prueba no lleva el texto de la excepción (en Telegram la URL
lleva el token dentro).
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from . import settings as st
from .service import HOUSE_OWNER, TicketError, _validate_agent

# la URL de Telegram lleva el token: httpx a INFO la escribiría en el log
logging.getLogger("httpx").setLevel(logging.WARNING)

MASK = st.MASK
# tipo → el ajuste que alimenta la marcada «en uso» (None: no la usa FarOS)
USO = {"deepseek": "deepseek_api_key", "anthropic": "anthropic_api_key", "telegram": "telegram_token", "otra": None}
NOMBRE_TIPO = {"deepseek": "DeepSeek", "anthropic": "Anthropic", "telegram": "Telegram", "otra": "Otra"}
_ERROR = {403: "forbidden", 404: "not_found", 409: "conflict", 422: "invalid_request"}


class ClaveError(TicketError):
    """TicketError con estado HTTP propio (como RosterError): 404, 409 o 422."""

    def __init__(self, message: str, status: int = 400, field: str | None = None, code: str | None = None, **extra):
        super().__init__(message, field=field, code=code)
        self.status = status
        self.error = _ERROR.get(status, "invalid_request")
        self.extra = extra


# ------------------------------------------------------------------ la prueba


def _probar_red(kind: str, value: str) -> tuple[bool, str]:
    """Prueba sin coste contra el servicio. Devuelve (vale, detalle legible).
    Nunca mete en el detalle el texto de una excepción: puede llevar la clave."""
    try:
        if kind == "deepseek":
            h = {"Authorization": f"Bearer {value}"}
            r = httpx.get("https://api.deepseek.com/models", headers=h, timeout=10)
            if r.status_code == 200:
                n = len((r.json() or {}).get("data") or [])
                return True, f"{n} modelos{_sin_saldo_deepseek(h)}"
        elif kind == "anthropic":
            r = httpx.get(
                "https://api.anthropic.com/v1/models",
                headers={"X-Api-Key": value, "anthropic-version": "2023-06-01"},
                timeout=10,
            )
            if r.status_code == 200:
                n = len((r.json() or {}).get("data") or [])
                return True, f"{n} modelos"
        elif kind == "telegram":
            r = httpx.get(f"https://api.telegram.org/bot{value}/getMe", timeout=10)
            if r.status_code == 200 and (r.json() or {}).get("ok"):
                user = (r.json().get("result") or {}).get("username")
                return True, f"@{user}" if user else "bot válido"
        else:
            return False, "este tipo no se sabe probar"
    except (httpx.HTTPError, ValueError) as e:
        return False, f"no se pudo comprobar ({type(e).__name__})"
    if r.status_code in (401, 403):
        return False, f"el servicio la rechaza (HTTP {r.status_code})"
    return False, f"respuesta inesperada del servicio (HTTP {r.status_code})"


def _sin_saldo_deepseek(headers) -> str:
    """/models da 200 aunque la cuenta no tenga saldo, y entonces cada llamada da 402 (Prima,
    7-oct). La clave VALE (se guarda), pero el detalle lo dice. Si no se puede saber, nada."""
    try:
        r = httpx.get("https://api.deepseek.com/user/balance", headers=headers, timeout=10)
        if r.status_code == 200 and (r.json() or {}).get("is_available") is False:
            return " · sin saldo"
    except (httpx.HTTPError, ValueError):
        pass
    return ""


PROBAR = _probar_red  # los tests lo sustituyen: la suite no sale a la red


# ------------------------------------------------------------------ #499: entrega bajo demanda


def _etiqueta(valor, campo: str, maximo: int) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise ClaveError(f"falta {campo}", field=campo, code=f"credential.{campo}_required")
    valor = valor.strip()
    if len(valor) > maximo or any(c in valor for c in "\r\n\0"):
        raise ClaveError(
            f"{campo}: una línea de hasta {maximo} caracteres", field=campo, code=f"credential.{campo}_invalid"
        )
    return valor


def deliver(conn, name, requested_by, purpose) -> dict:
    """#499: el VALOR de una clave, por nombre, a quien la pide para un proceso hijo (el lanzador
    tools/faros_con_claves.py). Es la única salida del valor y rompe a propósito la regla del #460, así
    que va acotada: solo las que el dueño marcó entregables, y cada petición (dada o negada) queda en
    credential_deliveries SIN el valor. No protege de un proceso del mismo usuario que lea el token
    (dicho a Pepe el 16-oct)."""
    requested_by = _etiqueta(requested_by, "requested_by", 80)
    purpose = _etiqueta(purpose, "purpose", 200)
    _sin_valores(conn, requested_by=requested_by, purpose=purpose)
    nombre = name.strip() if isinstance(name, str) else ""
    r = (
        conn.execute("SELECT * FROM credentials WHERE name = ? COLLATE NOCASE", (nombre,)).fetchone()
        if nombre
        else None
    )
    if r is None:
        err = ClaveError(
            f"no hay ninguna clave que se llame «{nombre}»", 404, field="name", code="credential.not_found"
        )
    elif not r["deliverable"]:
        err = ClaveError(
            f"«{r['name']}» no está marcada como entregable (lo decide {HOUSE_OWNER} en Claves)",
            403,
            field="name",
            code="credential.not_deliverable",
        )
    elif not r["value"]:
        err = ClaveError(
            f"«{r['name']}» está vacía (restaurada de una copia): hay que volver a pegarla",
            409,
            field="name",
            code="credential.value_empty",
        )
    else:
        err = None
    nombre_reg = r["name"] if r is not None else nombre[:60]
    # #499, revisión de Prima (16-oct): como no se puede impedir, que se vea. Aviso a Pepe (dada o negada).
    # #1321, decisión 134 de Pepe (19-nov): «una vez al día por persona y clave — quiero saber en qué estáis pero no
    # necesito recibirlo 500 veces». Antes era quién + para qué, y cada purpose nuevo avisaba; el purpose sigue
    # apuntado en credential_deliveries, pero ya no cuenta para el aviso.
    ahora = _now()
    nueva = (
        conn.execute(
            "SELECT 1 FROM credential_deliveries WHERE name = ? COLLATE NOCASE AND requested_by = ?"
            " AND substr(at, 1, 10) = ? LIMIT 1",
            (nombre_reg, requested_by, ahora[:10]),
        ).fetchone()
        is None
    )
    conn.execute(
        "INSERT INTO credential_deliveries (credential_id, name, requested_by, purpose, at, ok, reason)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            r["id"] if r is not None else None,
            nombre_reg,
            requested_by,
            purpose,
            ahora,
            0 if err else 1,
            str(err) if err else "",
        ),
    )
    conn.commit()
    if nueva:
        _avisar_primera_vez(conn, nombre_reg, requested_by, purpose, err)
    if err:
        raise err
    return {"name": r["name"], "value": r["value"]}


_VALOR_MIN = 8  # por debajo, coincidir dentro de un texto es casualidad, no una clave pegada


def _sin_valores(conn, **campos) -> None:
    """#499 (Prima): requested_by y purpose son texto libre de quien llama. Si alguien pega una clave en
    ellos por descuido, quedaría en el registro. Se rechaza ANTES de registrar nada, sin repetir el texto."""
    for (valor,) in conn.execute("SELECT value FROM credentials WHERE length(value) >= ?", (_VALOR_MIN,)):
        for campo, texto in campos.items():
            if valor in texto:
                raise ClaveError(
                    f"{campo} contiene el valor de una clave guardada: no se registra ni se entrega",
                    field=campo,
                    code="credential.value_in_label",
                )


def _avisar_primera_vez(conn, nombre, requested_by, purpose, err) -> None:
    from . import notify

    estado = f"NEGADA ({err.code})" if err else "entregada"
    try:
        notify.telegram(
            conn,
            "\n".join(
                [
                    "FarOS · Claves: primera vez hoy",
                    f"«{requested_by}» ha pedido la clave «{nombre}» para «{purpose}» → {estado}.",
                    "Hoy no te aviso más de esta persona con esta clave; el resto queda en Ajustes › Claves › Entregas.",
                ]
            ),
        )
    except Exception:  # un aviso que no sale no puede tumbar la entrega
        pass


def _cambio(conn, cid, name, field, antes, despues, by) -> None:
    """#1078: una fila del historial de una clave. antes/despues son etiquetas, nunca el valor."""
    conn.execute(
        "INSERT INTO credential_changes (credential_id, name, field, antes, despues, changed_by, at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cid, name, field, antes, despues, by, _now()),
    )


def _si_no(v) -> str:
    return "sí" if v else "no"


def changes(conn, limit: int = 200) -> list[dict]:
    """#1078: el historial de cambios de las claves, el más nuevo primero."""
    rows = conn.execute(
        "SELECT id, credential_id, name, field, antes, despues, changed_by, at FROM credential_changes"
        " ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def deliveries(conn, limit: int = 200) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, requested_by, purpose, at, ok, reason FROM credential_deliveries ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [{**dict(r), "ok": bool(r["ok"])} for r in rows]


# ------------------------------------------------------------------ helpers


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row(r) -> dict:
    """Lo que ve la API: todo menos el valor."""
    return {
        "id": r["id"],
        "name": r["name"],
        "kind": r["kind"],
        "origin": r["origin"],
        "in_use": bool(r["in_use"]),
        "use": USO[r["kind"]],
        "value": MASK if r["value"] else "",
        "empty": not r["value"],  # vacía = restaurada de una copia: hay que volver a pegarla
        "tested_at": r["tested_at"],
        "test_ok": None if r["test_ok"] is None else bool(r["test_ok"]),
        "test_detail": r["test_detail"],
        "created_by": r["created_by"],
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
        "deliverable": bool(r["deliverable"]),
    }


def _get(conn, cid: int):
    r = conn.execute("SELECT * FROM credentials WHERE id = ?", (cid,)).fetchone()
    if r is None:
        raise ClaveError(f"no existe la clave {cid}", 404, code="credential.not_found")
    return r


def _name(conn, name, cid: int | None = None) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ClaveError("falta el nombre de la clave", field="name", code="credential.name_required")
    name = name.strip()
    if len(name) > 60:
        raise ClaveError("el nombre no puede pasar de 60 caracteres", field="name", code="credential.name_too_long")
    otra = conn.execute(
        "SELECT id FROM credentials WHERE name = ? COLLATE NOCASE AND id IS NOT ?", (name, cid)
    ).fetchone()
    if otra:
        raise ClaveError(f"ya hay una clave que se llama «{name}»", 409, field="name", code="credential.name_taken")
    return name


def _value(value) -> str:
    if not isinstance(value, str) or not value.strip() or value == MASK:
        raise ClaveError("falta el valor de la clave", field="value", code="credential.value_required")
    return value.strip()


def valor(conn, name: str) -> str:
    """El valor REAL de una clave por nombre — solo para el daemon; la API nunca lo devuelve
    (`_row` lo enmascara). Devuelve '' si no existe: quien llama decide qué hacer sin clave.
    #600: el ejecutor lo usa con la credencial «EcoDB» para comprobar memorias citadas."""
    r = conn.execute("SELECT value FROM credentials WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    return (r["value"] or "") if r else ""


def _origin(origin) -> str:
    if origin is None:
        return ""
    if not isinstance(origin, str):
        raise ClaveError("el origen es texto", field="origin", code="credential.origin_invalid")
    if len(origin.strip()) > 300:
        raise ClaveError(
            "el origen no puede pasar de 300 caracteres", field="origin", code="credential.origin_too_long"
        )
    return origin.strip()


def prueba_previa(kind, value):
    """La prueba de red, SIN tocar la base: la API la corre en un hilo (hasta 10 s) y pasa
    el resultado a create/update/retest, que escriben en el hilo del daemon (la conexión
    sqlite no se comparte entre hilos). None si no toca probar o la entrada no vale
    (la validación de después lo dirá)."""
    if kind not in USO or USO[kind] is None or not isinstance(value, str) or not value.strip() or value == MASK:
        return None
    return PROBAR(kind, value.strip())


def _probar(kind: str, value: str, probado=None) -> tuple[int | None, str]:
    """(test_ok, detalle). «otra» no se prueba: (None, ''). probado = la prueba ya hecha."""
    if USO[kind] is None:
        return None, ""
    ok, detail = probado if probado is not None else PROBAR(kind, value)
    return (1 if ok else 0), detail


def _rechazada(kind: str, detail: str):
    return ClaveError(
        f"la clave de {NOMBRE_TIPO[kind]} no vale: {detail}. No se ha guardado.",
        422,
        field="value",
        code="credential.test_failed",
        detail=detail,
    )


def _espejo(conn, kind: str) -> None:
    """Copia la marcada «en uso» de este tipo en su ajuste (o lo vacía si no hay)."""
    key = USO[kind]
    if key is None:
        return
    r = conn.execute("SELECT value FROM credentials WHERE kind = ? AND in_use = 1", (kind,)).fetchone()
    st._guardar(conn, key, r["value"] if r else "")


def _marcar(conn, cid: int, kind: str, by: str) -> None:
    for o in conn.execute(
        "SELECT id, name FROM credentials WHERE kind = ? AND in_use = 1 AND id != ?", (kind, cid)
    ).fetchall():
        _cambio(conn, o["id"], o["name"], "en uso", "sí", "no", by)
    r = conn.execute("SELECT name, in_use FROM credentials WHERE id = ?", (cid,)).fetchone()
    if not r["in_use"]:
        _cambio(conn, cid, r["name"], "en uso", "no", "sí", by)
    conn.execute("UPDATE credentials SET in_use = 0 WHERE kind = ? AND in_use = 1", (kind,))
    conn.execute("UPDATE credentials SET in_use = 1 WHERE id = ?", (cid,))


# ------------------------------------------------------------------ operaciones


def listing(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM credentials ORDER BY kind, in_use DESC, name COLLATE NOCASE").fetchall()
    return [_row(r) for r in rows]


def create(conn, agent: str, name, kind, value, origin=None, in_use=None, probado=None) -> dict:
    """Alta. Si el tipo se sabe probar y la prueba falla, NO se guarda (422).
    in_use sin decir: la primera de su tipo queda en uso (si no, nadie la usaría)."""
    _validate_agent(agent, family_only=True)
    if kind not in USO:
        raise ClaveError(
            f"tipo desconocido: {kind!r} (deepseek, anthropic, telegram u otra)",
            field="kind",
            code="credential.kind_invalid",
        )
    name, value, origin = _name(conn, name), _value(value), _origin(origin)
    if in_use and USO[kind] is None:
        raise ClaveError(
            "FarOS no usa las claves de tipo «otra»: no se pueden marcar en uso",
            field="in_use",
            code="credential.not_usable",
        )
    ok, detail = _probar(kind, value, probado)
    if ok == 0:
        raise _rechazada(kind, detail)
    if in_use is None:
        in_use = (
            USO[kind] is not None
            and not conn.execute("SELECT 1 FROM credentials WHERE kind = ? AND in_use = 1", (kind,)).fetchone()
        )
    now = _now()
    cur = conn.execute(
        "INSERT INTO credentials (name, kind, value, origin, in_use, tested_at, test_ok, test_detail,"
        " created_by, created_at, updated_at) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)",
        (name, kind, value, origin, now if ok is not None else None, ok, detail, agent, now, now),
    )
    cid = cur.lastrowid
    _cambio(conn, cid, name, "alta", None, kind, agent)
    if in_use:
        _marcar(conn, cid, kind, agent)
        _espejo(conn, kind)
    conn.commit()
    return _row(_get(conn, cid))


def update(conn, agent: str, cid: int, changes: dict, probado=None) -> dict:
    """name, origin, value (se prueba antes; «••••» = no tocar), in_use y deliverable (#499, solo el
    dueño de la casa). El tipo no cambia."""
    _validate_agent(agent, family_only=True)
    r = _get(conn, cid)
    kind = r["kind"]
    unknown = set(changes) - {"name", "origin", "value", "in_use", "deliverable"}
    if unknown:
        raise ClaveError(
            f"no se puede cambiar {sorted(unknown)} (sí: name, origin, value, in_use, deliverable)",
            field=sorted(unknown)[0],
            code="credential.field_not_editable",
        )
    sets, args, cambios = [], [], []
    if "deliverable" in changes:
        if agent != HOUSE_OWNER:
            raise ClaveError(
                f"solo {HOUSE_OWNER} decide qué claves se entregan bajo demanda",
                403,
                field="deliverable",
                code="credential.deliverable_owner_only",
            )
        sets.append("deliverable = ?")
        args.append(1 if changes["deliverable"] else 0)
        if bool(changes["deliverable"]) != bool(r["deliverable"]):
            cambios.append(("entregable", _si_no(r["deliverable"]), _si_no(changes["deliverable"])))
    if "name" in changes:
        nuevo_nombre = _name(conn, changes["name"], cid)
        sets.append("name = ?")
        args.append(nuevo_nombre)
        if nuevo_nombre != r["name"]:
            cambios.append(("nombre", r["name"], nuevo_nombre))
    if "origin" in changes:
        nuevo_origen = _origin(changes["origin"])
        sets.append("origin = ?")
        args.append(nuevo_origen)
        if nuevo_origen != r["origin"]:
            cambios.append(("origen", r["origin"], nuevo_origen))
    nuevo_valor = "value" in changes and changes["value"] != MASK
    if nuevo_valor:
        value = _value(changes["value"])
        ok, detail = _probar(kind, value, probado)
        if ok == 0:
            raise _rechazada(kind, detail)
        sets += ["value = ?", "tested_at = ?", "test_ok = ?", "test_detail = ?"]
        args += [value, _now() if ok is not None else None, ok, detail]
        if value != r["value"]:
            cambios.append(("valor", None, "cambiado"))
    if "in_use" in changes and changes["in_use"] and USO[kind] is None:
        raise ClaveError(
            "FarOS no usa las claves de tipo «otra»: no se pueden marcar en uso",
            field="in_use",
            code="credential.not_usable",
        )
    if sets:
        conn.execute(f"UPDATE credentials SET {', '.join(sets)}, updated_at = ? WHERE id = ?", (*args, _now(), cid))
    nombre = _get(conn, cid)["name"]
    for campo, de, a in cambios:
        _cambio(conn, cid, nombre, campo, de, a, agent)
    if "in_use" in changes:
        if changes["in_use"]:
            _marcar(conn, cid, kind, agent)
        else:
            if r["in_use"]:
                _cambio(conn, cid, nombre, "en uso", "sí", "no", agent)
            conn.execute("UPDATE credentials SET in_use = 0 WHERE id = ?", (cid,))
    if nuevo_valor or "in_use" in changes:
        _espejo(conn, kind)
    conn.commit()
    return _row(_get(conn, cid))


def retest(conn, agent: str, cid: int, probado=None) -> dict:
    """Prueba otra vez el valor guardado y apunta el resultado (no borra nada si falla)."""
    _validate_agent(agent, family_only=True)
    r = _get(conn, cid)
    if USO[r["kind"]] is None:
        raise ClaveError("las claves de tipo «otra» no se saben probar", 422, code="credential.untestable")
    if not r["value"]:
        raise ClaveError(
            "esta clave está vacía (viene de una copia de seguridad): pega el valor otra vez",
            422,
            code="credential.value_empty",
        )
    ok, detail = _probar(r["kind"], r["value"], probado)
    conn.execute(
        "UPDATE credentials SET tested_at = ?, test_ok = ?, test_detail = ?, updated_at = ? WHERE id = ?",
        (_now(), ok, detail, _now(), cid),
    )
    conn.commit()
    return _row(_get(conn, cid))


def delete(conn, agent: str, cid: int) -> dict:
    _validate_agent(agent, family_only=True)
    r = _get(conn, cid)
    _cambio(conn, cid, r["name"], "borrada", None, None, agent)
    conn.execute("DELETE FROM credentials WHERE id = ?", (cid,))
    if r["in_use"]:
        _espejo(conn, r["kind"])  # ya no hay ninguna en uso de ese tipo: el ajuste se vacía
    conn.commit()
    return {"id": cid, "deleted": True, "was_in_use": bool(r["in_use"])}


def desde_ajuste(conn, agent: str, key: str, value: str, probado=None) -> None:
    """PATCH /api/settings de una clave con uso (el script de Prima, patch_settings del MCP):
    escribe en la marcada «en uso» de su tipo, con la misma prueba que la pantalla.
    '' = ya no se usa ninguna (la lista no pierde nada). Sin marcada, crea una."""
    kind = next(k for k, v in USO.items() if v == key)
    marcada = conn.execute("SELECT * FROM credentials WHERE kind = ? AND in_use = 1", (kind,)).fetchone()
    if value == "":
        if marcada:
            _cambio(conn, marcada["id"], marcada["name"], "en uso", "sí", "no", agent)
            conn.execute("UPDATE credentials SET in_use = 0 WHERE id = ?", (marcada["id"],))
        return
    if marcada and marcada["value"] == value:
        return
    ok, detail = _probar(kind, value, probado)
    if ok == 0:
        raise _rechazada(kind, detail)
    now = _now()
    if marcada:
        conn.execute(
            "UPDATE credentials SET value = ?, tested_at = ?, test_ok = ?, test_detail = ?,"
            " updated_at = ? WHERE id = ?",
            (value, now, ok, detail, now, marcada["id"]),
        )
        _cambio(conn, marcada["id"], marcada["name"], "valor", None, "cambiado", agent)
        return
    base, n, name = f"{NOMBRE_TIPO[kind]} (ajustes)", 2, f"{NOMBRE_TIPO[kind]} (ajustes)"
    while conn.execute("SELECT 1 FROM credentials WHERE name = ? COLLATE NOCASE", (name,)).fetchone():
        name, n = f"{base} {n}", n + 1
    cur = conn.execute(
        "INSERT INTO credentials (name, kind, value, origin, in_use, tested_at, test_ok, test_detail,"
        " created_by, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
        (name, kind, value, "guardada por los ajustes", now, ok, detail, agent, now, now),
    )
    _cambio(conn, cur.lastrowid, name, "alta", None, kind, agent)
