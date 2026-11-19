"""Decisiones (bisagras) como entidad (T3.2, decisión 7 del dueño): sustituyen a
los bisagra_*.md. Opciones LITERALES enteras — nunca A/B/C. Append-only: una
decisión decidida no se decide dos veces; se abre otra.

Estados: pending → decided | deferred (→ pending al retomar). Cada evento deja
fila en decision_history (event, actor, note).
"""

from __future__ import annotations

import json
from datetime import date

from .service import TicketError, _now, _validate_agent, _validate_project, AGENTS, HOUSE_OWNER

STATUSES = {"pending", "decided", "deferred"}
DECIDERS = AGENTS  # el dueño decide; la familia puede registrar SU decisión (con quién/por qué)


SUPERFICIES = ("desktop", "mobile", "mcp", "telegram")   # #657: la manda cada cliente; vacía = no lo dijo (#1189: el bot)
SUPERSEDE_KINDS = ("total", "parcial", "nunca_valida")   # #919
DECIDIDO_POR_TIPOS = ("humano", "agente", "equipo")


def _supersede_targets(conn, decision_id, supersedes, kind, agent) -> list[int]:
    """#919: valida ANTES de escribir nada. Solo decisiones ya decididas, sin marca, distintas de esta.
    «nunca_valida» solo la pone el dueño de la casa: dice que nunca debió valer, no que se cambió."""
    if not supersedes:
        return []
    if not isinstance(supersedes, list) or not all(isinstance(x, int) and not isinstance(x, bool) for x in supersedes):
        raise TicketError("supersedes: lista de ids de decisión (enteros)", field="supersedes")
    if len(set(supersedes)) != len(supersedes):
        raise TicketError("supersedes repite un id", field="supersedes")
    if decision_id in supersedes:
        raise TicketError("una decisión no se sustituye a sí misma", field="supersedes")
    if kind not in SUPERSEDE_KINDS:
        raise TicketError(f"supersede_kind: uno de {list(SUPERSEDE_KINDS)}", field="supersede_kind")
    if kind == "nunca_valida" and agent != HOUSE_OWNER:
        raise TicketError("supersede_kind nunca_valida: solo el dueño de la casa", field="supersede_kind")
    for sid in supersedes:
        row = conn.execute("SELECT status, superseded_by FROM decisions WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise TicketError(f"supersedes: la decisión #{sid} no existe", field="supersedes")
        if row["status"] != "decided":
            raise TicketError(f"supersedes: la decisión #{sid} no está decidida ({row['status']})", field="supersedes")
        if row["superseded_by"] is not None:
            raise TicketError(f"supersedes: la decisión #{sid} ya está superada por #{row['superseded_by']}",
                              field="supersedes")
    return list(supersedes)


def _decidido_por(valor) -> str | None:
    if valor is None:
        return None
    if (not isinstance(valor, dict) or set(valor) != {"tipo", "quien"} or valor["tipo"] not in DECIDIDO_POR_TIPOS
            or not isinstance(valor["quien"], str) or not valor["quien"].strip() or len(valor["quien"]) > 80):
        raise TicketError(f"decidido_por: {{tipo: {list(DECIDIDO_POR_TIPOS)}, quien: nombre}}", field="decidido_por")
    return json.dumps({"tipo": valor["tipo"], "quien": valor["quien"].strip()}, ensure_ascii=False)


def _hist(conn, decision_id, event, actor, note=None, surface=None):
    conn.execute(
        "INSERT INTO decision_history (decision_id, event, actor, changed_at, note, surface)"
        " VALUES (?, ?, ?, ?, ?, ?)", (decision_id, event, actor, _now(), note, surface))


def _normalize_options(options) -> list[dict]:
    if not isinstance(options, list) or not options:
        raise TicketError("options: lista no vacía de opciones literales [{label, text}]")
    out, labels = [], set()
    for o in options:
        if isinstance(o, str):
            o = {"label": o[:60], "text": o}
        if not isinstance(o, dict) or not (o.get("text") or "").strip():
            raise TicketError(f"opción inválida: {o!r} (necesita text)")
        label = (o.get("label") or o["text"][:60]).strip()
        if len(label) <= 2 and label.upper() in {"A", "B", "C", "D", "1", "2", "3"}:
            raise TicketError(f"opción {label!r}: las opciones son literales enteras, nunca A/B/C")
        if label in labels:
            raise TicketError(f"opción duplicada: {label!r}")
        labels.add(label)
        out.append({"label": label, "text": o["text"].strip()})
    return out


def get_decision(conn, decision_id) -> dict:
    row = conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
    if row is None:
        raise TicketError(f"decisión #{decision_id} no existe")
    d = dict(row)
    d["options"] = json.loads(d["options"] or "[]")
    for k in ("marked_by", "decidido_por"):   # #919: JSON guardado como texto
        if d.get(k):
            d[k] = json.loads(d[k])
    d["history"] = [dict(r) for r in conn.execute(
        "SELECT event, actor, changed_at, note, surface FROM decision_history WHERE decision_id=? ORDER BY id",
        (decision_id,))]
    if d["workflow_id"]:
        w = conn.execute("SELECT name FROM workflows WHERE id=?", (d["workflow_id"],)).fetchone()
        d["workflow_name"] = w["name"] if w else None
    return d


def ask(conn, agent, title, options, context="", workflow_id=None, project_id=None) -> dict:
    _validate_agent(agent)
    if not (title or "").strip():
        raise TicketError("title vacío")
    opts = _normalize_options(options)
    if workflow_id is not None and conn.execute(
            "SELECT id FROM workflows WHERE id=?", (workflow_id,)).fetchone() is None:
        raise TicketError(f"workflow #{workflow_id} no existe")
    _validate_project(conn, project_id)
    cur = conn.execute(
        "INSERT INTO decisions (workflow_id, project_id, title, context, options, asked_by, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (workflow_id, project_id, title.strip(), context or "", json.dumps(opts, ensure_ascii=False),
         agent, _now()))
    _hist(conn, cur.lastrowid, "asked", agent)
    conn.commit()
    return get_decision(conn, cur.lastrowid)


def decide(conn, agent, decision_id, option=None, decision=None, rationale="", surface=None,
           supersedes=None, supersede_kind="total", supersede_reason=None, decidido_por=None,
           valid_until=None) -> dict:
    """option = label de una opción literal; decision = texto libre (el dueño puede
    decidir fuera de las opciones — se registra tal cual). surface (#657): desde dónde se decidió.
    #919: supersedes = ids de decisiones anteriores que esta cambia (se marcan «superada por» esta);
    decidido_por {tipo, quien}; valid_until YYYY-MM-DD para lo que solo vale hasta un día."""
    _validate_agent(agent, family_only=True)
    if surface is not None and surface not in SUPERFICIES:
        raise TicketError(f"surface desconocida: {surface!r} (válidas: {list(SUPERFICIES)} o ninguna)",
                          field="surface")
    d = get_decision(conn, decision_id)
    if d["status"] == "decided":
        raise TicketError(f"decisión #{decision_id} ya está decidida por {d['decided_by']} "
                          f"({d['decided_at']}): {d['decision']!r} — no se decide dos veces, abre otra")
    if option:
        match = next((o for o in d["options"] if o["label"] == option), None)
        if match is None:
            raise TicketError(f"opción desconocida: {option!r} (válidas: {[o['label'] for o in d['options']]})")
        chosen = match["label"]
    elif (decision or "").strip():
        chosen = decision.strip()
    else:
        raise TicketError("decide requiere option (label) o decision (texto libre)")
    if not (rationale or "").strip():
        raise TicketError("decide requiere rationale (por qué)")
    if supersede_reason is not None and (not isinstance(supersede_reason, str) or len(supersede_reason) > 300):
        raise TicketError("supersede_reason: una línea de hasta 300 caracteres", field="supersede_reason")
    if not supersedes and supersede_reason:
        raise TicketError("supersede_reason necesita supersedes", field="supersede_reason")
    targets = _supersede_targets(conn, decision_id, supersedes, supersede_kind or "total", agent)
    quien_decide = _decidido_por(decidido_por)
    valid_iso = None
    if valid_until:
        try:
            valid_iso = date.fromisoformat(str(valid_until)[:10]).isoformat()
        except ValueError:
            raise TicketError(f"valid_until inválido: {valid_until!r} (YYYY-MM-DD)", field="valid_until")
    now = _now()
    conn.execute(
        "UPDATE decisions SET status='decided', decided_by=?, decision=?, rationale=?, decided_at=?,"
        " decidido_por=?, valid_until=? WHERE id=?",
        (agent, chosen, rationale.strip(), now, quien_decide, valid_iso, decision_id))
    _hist(conn, decision_id, "decided", agent, f"{chosen} — {rationale.strip()}", surface=surface)
    marca = json.dumps({"by": agent, "at": now, "via": f"decide #{decision_id}"}, ensure_ascii=False)
    for sid in targets:
        conn.execute(
            "UPDATE decisions SET superseded_by=?, supersede_kind=?, supersede_reason=?, marked_by=?"
            " WHERE id=? AND superseded_by IS NULL",
            (decision_id, supersede_kind or "total", supersede_reason, marca, sid))
        _hist(conn, sid, "superseded", agent, f"superada por #{decision_id} ({supersede_kind or 'total'})"
              + (f": {supersede_reason}" if supersede_reason else ""), surface=surface)
    conn.commit()
    return get_decision(conn, decision_id)


def defer(conn, agent, decision_id, until=None, note="") -> dict:
    _validate_agent(agent, family_only=True)
    d = get_decision(conn, decision_id)
    if d["status"] == "decided":
        raise TicketError(f"decisión #{decision_id} ya está decidida")
    until_iso = None
    if until:
        try:
            until_iso = date.fromisoformat(str(until)[:10]).isoformat()
        except ValueError:
            raise TicketError(f"until inválido: {until!r} (YYYY-MM-DD)")
    conn.execute("UPDATE decisions SET status='deferred', deferred_until=? WHERE id=?",
                 (until_iso, decision_id))
    _hist(conn, decision_id, "deferred", agent, (f"hasta {until_iso}. " if until_iso else "") + (note or ""))
    conn.commit()
    return get_decision(conn, decision_id)


def reopen(conn, agent, decision_id, note="") -> dict:
    _validate_agent(agent, family_only=True)
    d = get_decision(conn, decision_id)
    if d["status"] != "deferred":
        raise TicketError(f"decisión #{decision_id} está {d['status']} — solo se retoman las aplazadas")
    conn.execute("UPDATE decisions SET status='pending', deferred_until=NULL WHERE id=?", (decision_id,))
    _hist(conn, decision_id, "reopened", agent, note or None)
    conn.commit()
    return get_decision(conn, decision_id)


def list_decisions(conn, status=None, workflow_id=None, project_id=None) -> list[dict]:
    where, params = [], []
    if status:
        if status not in STATUSES:
            raise TicketError(f"status inválido: {status!r}")
        where.append("d.status=?"); params.append(status)
    if workflow_id is not None:
        where.append("d.workflow_id=?"); params.append(workflow_id)
    if project_id is not None:
        where.append("d.project_id=?"); params.append(project_id)
    sql = ("SELECT d.*, w.name AS wf_name FROM decisions d"
           " LEFT JOIN workflows w ON w.id = d.workflow_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE d.status WHEN 'pending' THEN 0 WHEN 'deferred' THEN 1 ELSE 2 END, d.id DESC"
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    ph = ",".join("?" * len(ids))
    hist_rows = conn.execute(
        f"SELECT decision_id, event, actor, changed_at, note, surface FROM decision_history"
        f" WHERE decision_id IN ({ph}) ORDER BY id", ids).fetchall()
    hist_map: dict[int, list[dict]] = {did: [] for did in ids}
    for h in hist_rows:
        hist_map[h["decision_id"]].append(
            {"event": h["event"], "actor": h["actor"],
             "changed_at": h["changed_at"], "note": h["note"], "surface": h["surface"]})
    out = []
    for r in rows:
        d = dict(r)
        d["options"] = json.loads(d["options"] or "[]")
        d["history"] = hist_map.get(d["id"], [])
        d["workflow_name"] = d.pop("wf_name", None)
        out.append(d)
    return out


def pending_for_workflow(conn, workflow_id) -> list[dict]:
    return [d for d in list_decisions(conn, workflow_id=workflow_id) if d["status"] != "decided"]


# ---- #919 H2: decisiones vigentes ----------------------------------------------------------------
# FarOS lee EcoDB, no al revés (diseño #847): su mitad sale de `decisions`, la de EcoDB de su endpoint
# GET /api/v1/decisiones/vigentes con la clave «EcoDB» de Claves. Las cadenas de FarOS no pueden hacer
# ciclos (decide() solo sustituye decisiones que aún no lo están), así que aquí basta con seguirlas.

def _ref(did) -> str:
    return f"faros:decision:{did}"


def _fila_faros(d: dict, sustituye: list[str]) -> dict:
    linea = d["title"] + (f": {d['decision']}" if d.get("decision") else "")
    fila = {"id": _ref(d["id"]), "fecha": (d.get("decided_at") or "")[:10], "tipo": "decision",
            "agente": d.get("asked_by"), "decidido_por": json.loads(d["decidido_por"]) if d.get("decidido_por") else None,
            "linea": " ".join(linea.split())[:200], "fuente": "FarOS"}
    if sustituye:
        fila["sustituye_a"] = sustituye
    if d.get("valid_until"):
        fila["valid_until"] = d["valid_until"][:10]
    return fila


# #1098: palabras que no deciden nada; sin quitarlas, «de» o «la» casaban con todo (la FTS unía con OR).
_VACIAS = {"de", "la", "el", "en", "y", "a", "que", "los", "las", "del", "por", "con", "para", "un", "una",
           "se", "no", "lo", "al", "es", "o", "u", "como", "qué", "quién", "cuándo", "cómo", "cuál"}


def _palabras_tema(tema: str) -> list[str]:
    import re
    vistas, salida = set(), []
    for w in re.findall(r"\w+", (tema or "").lower()):
        if len(w) > 1 and w not in _VACIAS and w not in vistas:
            vistas.add(w)
            salida.append(w)
    return salida[:8]


def _candidatas_faros(conn, tema: str, agente: str | None, tope: int) -> list:
    """#1098: en tramos, como la Biblioteca. Primero las que tienen TODAS las palabras con contenido; si no hay,
    las que tienen al menos la mitad. Busca en título, contexto, respuesta y motivo (decisions_fts2). Una
    decisión cuya respuesta es «RETIRADA» no está vigente y no sale."""
    palabras = _palabras_tema(tema)
    if not palabras:
        return []
    base = ("SELECT d.*, bm25(decisions_fts2) AS bm25 FROM decisions_fts2 JOIN decisions d"
            " ON d.id = decisions_fts2.rowid WHERE decisions_fts2 MATCH ? AND d.status = 'decided'"
            " AND upper(trim(coalesce(d.decision, ''))) NOT LIKE 'RETIRADA%'")
    extra, params = "", []
    if agente:
        extra = " AND d.asked_by = ?"; params.append(agente)
    todas = " AND ".join(f'"{w}"*' for w in palabras)
    salen = list(conn.execute(base + extra + " ORDER BY bm25 LIMIT ?", [todas, *params, tope]).fetchall())
    # #1098 (arreglo, 18-nov): el tramo OR COMPLETA lo que deja el AND hasta el tope; antes solo entraba si el
    # AND no traía nada, y una fila con 4 de 5 palabras se perdía detrás de 2 que las tenían todas.
    ya = {f["id"] for f in salen}
    alguna = " OR ".join(f'"{w}"*' for w in palabras)
    minimo = (len(palabras) + 1) // 2
    for f in conn.execute(base + extra + " ORDER BY bm25 LIMIT ?", [alguna, *params, tope * 3]).fetchall():
        if len(salen) >= tope:
            break
        if f["id"] in ya:
            continue
        texto = " ".join(str(f[k] or "") for k in ("title", "context", "decision", "rationale")).lower()
        if sum(1 for w in palabras if w in texto) >= minimo:
            salen.append(f)
    return salen


def vigentes_faros(conn, tema: str, agente: str | None = None, incluir_superadas: bool = False,
                   limit: int = 10, hoy: date | None = None) -> dict:
    """Las decisiones DECIDIDAS de FarOS que valen hoy sobre `tema` (FTS sobre título, contexto, respuesta y
    motivo; #1098: AND antes que OR, sin palabras vacías, sin las RETIRADAS)."""
    hoy = hoy or date.today()
    salida, superadas, ya = [], [], set()
    for c in _candidatas_faros(conn, tema, agente, limit * 3):
        actual, camino = dict(c), []
        while actual.get("superseded_by") is not None:
            sig = conn.execute("SELECT * FROM decisions WHERE id=?", (actual["superseded_by"],)).fetchone()
            if sig is None:
                break
            camino.append(_ref(actual["id"]))
            actual = dict(sig)
        if incluir_superadas and camino:
            superadas.append({**_fila_faros(dict(c), []), "superada_por": _ref(c["superseded_by"]),
                              "supersede_kind": c["supersede_kind"] or "total", "motivo": c["supersede_reason"]})
        caducada = bool(actual.get("valid_until")) and date.fromisoformat(actual["valid_until"][:10]) < hoy
        if actual["id"] in ya or caducada or actual["status"] != "decided":
            continue
        ya.add(actual["id"])
        salida.append(_fila_faros(actual, camino))
        if len(salida) >= limit:
            break
    r = {"vigentes": salida, "errores": []}
    if not salida:
        r["avisos"] = ["sin decisiones relevantes en FarOS para este tema"]
    if incluir_superadas:
        r["superadas"] = superadas
    return r


def vigentes_ecodb(clave: str, tema: str, agente: str | None = None, incluir_superadas: bool = False,
                   limit: int = 10, base: str | None = None, timeout: float = 60.0) -> dict:
    """La mitad de EcoDB, tal cual la da su endpoint. Si no hay clave o EcoDB no responde, la mitad
    va vacía con un aviso: FarOS no depende de que EcoDB esté arrancado."""
    import urllib.error
    import urllib.parse
    import urllib.request
    from .prueba import ECODB_URL
    vacia = {"vigentes": [], "errores": [], "avisos": []}
    if incluir_superadas:
        vacia["superadas"] = []
    if not clave:
        vacia["avisos"].append("EcoDB: sin la clave «EcoDB» en Claves, solo salen las de FarOS")
        return vacia
    q = {"tema": tema, "limit": limit, "incluir_superadas": "true" if incluir_superadas else "false"}
    if agente:
        q["agente"] = agente
    url = f"{(base or ECODB_URL).rstrip('/')}/api/v1/decisiones/vigentes?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {clave}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        vacia["avisos"].append(f"EcoDB respondió {e.code}: solo salen las de FarOS")
    except Exception as e:  # noqa: BLE001 — sin EcoDB, FarOS sigue respondiendo con lo suyo
        vacia["avisos"].append(f"EcoDB no respondió ({type(e).__name__}): solo salen las de FarOS")
    return vacia


def combinar(f: dict, e: dict, limit: int, incluir_superadas: bool) -> dict:
    """#919: junta la mitad de FarOS y la de EcoDB. Cada mitad llega ordenada por SU relevancia (FTS y
    búsqueda semántica no se pueden comparar), así que se alternan, FarOS primero, y se cortan en
    `limit`. Cada fila dice su «fuente» y su «rango» dentro de ella. Ordenar por fecha tiraba lo viejo
    y relevante detrás de lo nuevo sin relación (Prima, 10-nov)."""
    mitades = [[{**x, "rango": i} for i, x in enumerate(m, 1)] for m in (f["vigentes"], e.get("vigentes", []))]
    alternadas = [x for par in zip(*[m + [None] * (max(map(len, mitades)) - len(m)) for m in mitades])
                  for x in par if x is not None]
    r = {"vigentes": alternadas[:limit],
         "errores": f["errores"] + e.get("errores", []), "avisos": f.get("avisos", []) + e.get("avisos", [])}
    if incluir_superadas:
        r["superadas"] = f.get("superadas", []) + e.get("superadas", [])
    return r
