"""API HTTP del daemon — superficie de paridad GUI↔MCP (SPEC §5).

Contrato con el frontend (cerrado con Lienzo, 1-sep):
- TicketError en /transition → 409 {"error":"transition_rejected","reason":...}
- TicketError en el resto → 400 {"error":"invalid_request","reason":...}
- SSE: un solo tipo de evento `board.changed` con {detail, ids}
- GET /api/meta para poblar selects (nada hardcodeado en el renderer)
"""

from __future__ import annotations

import asyncio
import json
import secrets
import logging
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse, JSONResponse

from . import service as s, runner, workflows as w, harness as h, settings as st, db as dbmod
from . import env as _env
from . import __version__


class _AuthError(Exception):
    """401 con la misma forma plana que el resto de errores (#270).

    No se usa HTTPException porque FastAPI envuelve su `detail` en
    {"detail": ...} y el renderer lee reason/error/field (api.js:20), no
    detail: el 401 se leía como "HTTP 401" y un `code` ahí dentro no habría
    llegado nunca a la interfaz."""


log = logging.getLogger("faros.api")

TOKEN_FILE = runner.DATA_DIR / "token"

META = {
    "harnesses": h.names(),
    "models": [
        {"alias": "haiku", "label": "Haiku 4.5 (barato/rápido)", "tier": "cheap"},
        {"alias": "sonnet", "label": "Sonnet 5 (equilibrado)", "tier": "mid"},
        {"alias": "opus", "label": "Opus 4.8 (potente)", "tier": "high"},
        {"alias": "fable", "label": "Fable 5 (máximo)", "tier": "max"},
    ],
    "permission_modes": sorted(s.PERMISSION_MODES),
    "evidence_types": sorted(s.EVIDENCE_TYPES),
    "verification_levels": sorted(s.VERIFICATION_LEVELS),
    "priorities": sorted(s.PRIORITIES),
    "agents": sorted(s.AGENTS),
    # #302: QUIEN de esos agentes es el duenio. Sin esto el renderer no tenia
    # forma de saberlo y lo llevaba clavado en store.js — con el nombre de
    # nuestra casa dentro. En un clon limpio eso no es solo una fuga de
    # nombre: el roster neutro no contiene ese nombre, asi que el daemon
    # RECHAZA todo lo que la app intente escribir. Medido:
    #   "agente desconocido o sin permiso: 'el dueño'"
    # o sea que la primera tarea que cree un desconocido falla.
    "owner": s.HOUSE_OWNER,
    "defaults": {"model": "haiku", "timeout_s": 1800, "permission_mode": "dontAsk",
                 "schedule": {"type": "manual"}, "verification_level": "auto"},
}


def load_token() -> str:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    token = secrets.token_hex(32)
    TOKEN_FILE.write_text(token, encoding="utf-8")
    return token


class EventBus:
    """Broadcaster SSE minimalista: una Queue por suscriptor."""

    def __init__(self):
        self.subscribers: set[asyncio.Queue] = set()

    def emit(self, detail: str, ids: dict | None = None):
        payload = {"detail": detail, "ids": ids or {}}
        for q in list(self.subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    async def stream(self):
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.subscribers.add(q)
        try:
            yield "event: board.changed\ndata: {\"detail\": \"connected\", \"ids\": {}}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=25)
                    yield f"event: board.changed\ndata: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            self.subscribers.discard(q)


def _solo_campos_que_acepta(fn, body: dict, *, saltar: int = 0,
                            ya_puestos: tuple = ()) -> dict:
    """Rechaza con 400 los campos que `fn` no acepta, en vez de reventar con 500.

    POR QUE EXISTE (#302, lo encontro Lienzo probando el motor a mano): SIETE
    endpoints hacen `fn(conn, agent, **body)`. Desempaquetar el body entero
    significa que UNA sola clave de mas —un campo mal escrito, un cliente de
    otra version— sale por TypeError y el usuario recibe:

        500 Internal Server Error

    sin codigo, sin campo, sin nada que arreglar. Y quien llama a estos
    endpoints es un AGENTE por MCP, que se equivoca de nombre de campo
    exactamente igual que una persona. Un 500 no le dice que corregir; un 400
    con `field` si, y es lo que esta escrito en nuestro propio contrato:
    "que me lo diga en el propio formulario en vez de tirarlo".

    SE MIRA LA FIRMA, NO SE CAZA EL TypeError. Cazar el TypeError y traducirlo
    a 400 convertiria tambien los TypeError DE VERDAD —bugs nuestros dentro de
    la funcion— en errores de usuario, y los esconderia justo donde mas duelen.
    Aqui se comprueba ANTES de llamar: lo que la firma no acepta, se rechaza
    con su nombre; lo que pase de ahi, si revienta, es un fallo nuestro y debe
    salir como 500.

    `saltar` son los parametros posicionales que ya pone el llamador (conn,
    agent, y a veces un id), que no pueden venir del body.
    """
    import inspect
    params = list(inspect.signature(fn).parameters.values())
    propios = [p for p in params[saltar:]
               if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                             inspect.Parameter.KEYWORD_ONLY)]

    # 1) FALTA UN OBLIGATORIO. Esta mitad salio despues de arreglar la otra, y
    #    es el mismo 500: `create_job() missing 1 required positional argument:
    #    'prompt'` sale como Internal Server Error. El que llama no se entera de
    #    que le falta el prompt. Se comprueba aunque la funcion acepte **kwargs,
    #    porque **kwargs no salva a un obligatorio.
    faltan = [p.name for p in propios
              if p.default is inspect.Parameter.empty
              and p.name not in body and p.name not in ya_puestos]
    if faltan:
        k = faltan[0]
        raise s.TicketError(f"falta el campo obligatorio: '{k}'",
                            field=k, code="request.missing_field")

    # 2) SOBRA UNO QUE NO EXISTE. Si la funcion acepta **kwargs no hay nada que
    #    filtrar: se los queda ella a proposito.
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params):
        return body
    aceptados = {p.name for p in propios}
    sobran = [k for k in body if k not in aceptados]
    if sobran:
        k = sobran[0]
        raise s.TicketError(
            f"campo desconocido: '{k}' (acepta: {', '.join(sorted(aceptados))})",
            field=k, code="request.unknown_field")
    return body


def create_app(conn, bus: EventBus | None = None) -> FastAPI:
    app = FastAPI(title="FarOS daemon", version=__version__)
    bus = bus or EventBus()
    app.state.bus = bus
    app.state.conn = conn
    token = load_token()

    def auth(request: Request):
        if request.url.path == "/api/health":
            return
        # Se aceptan LAS DOS cabeceras durante la transicion (#286). La nueva
        # manda; la vieja sigue valiendo porque hay builds empaquetados, scripts
        # y arneses de prueba que mandan X-AgenticOS-Token y que no se reconstruyen
        # a la vez que el codigo. Cambiar un solo extremo = 401 en todo.
        sent = (request.headers.get("X-FarOS-Token")
                or request.headers.get("X-AgenticOS-Token"))
        if sent != token:
            # v3.3 (#270): forma PLANA, la misma que el resto de errores.
            # HTTPException envuelve en {"detail": ...} y el renderer no lee
            # `detail` (lee reason/error/field, ver app/renderer/js/api.js:20),
            # así que un 401 se leía como "HTTP 401" y el `code` no habría
            # llegado nunca. Por eso se emite la respuesta directamente.
            raise _AuthError()

    dep = [Depends(auth)]

    @app.exception_handler(_AuthError)
    async def auth_error_handler(request: Request, exc: "_AuthError"):
        return JSONResponse(status_code=401,
                            content={"error": "unauthorized",
                                     "code": "auth.invalid_token",
                                     "reason": "token inválido o ausente"})

    @app.exception_handler(s.TicketError)
    async def ticket_error_handler(request: Request, exc: s.TicketError):
        # v3.3 (#270): `code` viaja en las dos ramas. `reason` se queda como
        # estaba (español, para el log y como fallback si no hay traducción).
        code = getattr(exc, "code", None)
        if request.url.path.endswith(("/transition", "/decide")):
            # transición ilegal o bisagra ya decidida: conflicto de estado, no input inválido
            content = {"error": "transition_rejected", "reason": str(exc)}
            if code:
                content["code"] = code
            return JSONResponse(status_code=409, content=content)
        content = {"error": "invalid_request", "reason": str(exc)}
        if getattr(exc, "field", None):
            content["field"] = exc.field  # v3.2 (T1.5): la UI lo pinta bajo el campo
        if code:
            content["code"] = code
        return JSONResponse(status_code=400, content=content)

    # ------------------------------------------------------------ salud y meta

    @app.get("/api/health")
    async def health():
        # T0.7: versión visible. La app añade la suya (package.json) en la UI.
        #
        # #301 (Lienzo, 16-sep): TAMBIÉN la casa. Hoy dimos a tres personas un
        # `curl /api/health` como prueba de que estaban aisladas, y NO lo era:
        # decía la versión y callaba dónde escribe. Uno de los tres pasó esa
        # comprobación con un 3.3.0 impecable mientras el daemon escribía en la
        # base de producción, porque sin FAROS_HOME la casa cae a la de siempre.
        # Un aislamiento que no se puede comprobar no es un aislamiento: es una
        # creencia. Con esta línea, "creo que estoy aislado" pasa a ser un dato.
        #
        # REDACTADA, y no es paranoia de más: health es el ÚNICO endpoint sin
        # token (ver el middleware), así que lo que salga por aquí lo lee
        # cualquiera que alcance el puerto. La ruta entera lleva el nombre de
        # usuario dentro; con el perfil sustituido por ~ sigue distinguiendo
        # la casa real de una de prueba igual de bien, y no dice quién eres.
        #
        # (Aquí iba un ejemplo literal de la ruta. Lo quito porque el barrido
        # de términos propios del nacimiento lo cazó. Era un falso positivo
        # —la ruta del ejemplo ya iba redactada— pero prefiero quitar el
        # literal a añadirle una excepción al detector: un detector con
        # excepciones deja de ser un detector, y el comentario se explica
        # igual de bien sin el ejemplo.)
        return {"ok": True,
                "home": _env.redact_home(str(dbmod.DATA_DIR)),
                "version": {"daemon": __version__,
                            "schema": dbmod.schema_version(conn)}}

    # ------------------------------------------------------------ settings (v3, T0.4)

    @app.get("/api/settings", dependencies=dep)
    async def settings_get():
        return st.get_all(conn)

    @app.patch("/api/settings", dependencies=dep)
    async def settings_patch(body: dict):
        agent = body.pop("agent", None)
        if not agent:
            raise s.TicketError("falta agent", field="agent", code="request.agent_required")
        res = st.patch(conn, agent, body)
        bus.emit("settings.changed", {"keys": sorted(body)})
        return res

    @app.get("/api/meta", dependencies=dep)
    async def meta():
        # v3.2 (T2.5, Lienzo): `docs_dir` absoluto para que el renderer abra guías con
        # openPath (la de Gmail). Misma resolución que workflows.documents(): settings
        # → último workflow con repo_root → el paquete solo si trae docs/ (desarrollo).
        from pathlib import Path as _P
        root = st.get(conn, "repo_root_default") or ""
        if not root:
            row = conn.execute(
                "SELECT repo_root FROM workflows WHERE repo_root IS NOT NULL AND repo_root != ''"
                " ORDER BY id DESC LIMIT 1").fetchone()
            root = row[0] if row else ""
        if not root:
            cand = _P(__file__).resolve().parent.parent
            root = str(cand) if (cand / "docs").is_dir() else ""
        docs = _P(root) / "docs" if root else None
        return {**META, "docs_dir": str(docs) if docs is not None and docs.is_dir() else None}

    # ------------------------------------------------------------ harness (v3, T1.2)

    @app.get("/api/harnesses", dependencies=dep)
    async def harnesses(refresh: bool = False):
        """Cada harness con billing, capabilities y los modelos que él mismo
        declara (nada de listas duras)."""
        return [a.describe(refresh=refresh) for a in h.all_adapters()]

    @app.get("/api/harnesses/{name}/models", dependencies=dep)
    async def harness_models(name: str, refresh: bool = False):
        try:
            adapter = h.get(name)
        except (KeyError, RuntimeError) as exc:
            raise s.TicketError(str(exc))
        d = adapter.describe(settings=st.get_all(conn), refresh=refresh)
        from .harness import model_catalog as mc
        return {"harness": name, "models": d["models"], "source": d["models_source"],
                "refreshed_at": d["models_refreshed_at"],
                "families": mc.grouped([mc.CatalogEntry(**{k: m.get(k) for k in
                                        ("id", "family", "label", "tier", "source", "alias_of",
                                         "validated", "validated_at", "note")}) for m in d["models"]]),
                "probe": mc.refresh_state()}

    @app.post("/api/harnesses/{name}/models/refresh", dependencies=dep)
    async def harness_models_refresh(name: str, body: dict | None = None):
        """v3.1 (T5.4): re-sonda cada ID concreto con el marcador del CLI (coste $0,
        sin ventana) en un hilo; el resultado se guarda en settings.model_catalog."""
        try:
            adapter = h.get(name)
        except (KeyError, RuntimeError) as exc:
            raise s.TicketError(str(exc))
        from .harness import model_catalog as mc
        if not hasattr(adapter, "probe_ids"):
            raise s.TicketError(f"el harness {name!r} no admite sonda de modelos")
        agent = (body or {}).get("agent") or "system"
        ids = adapter.probe_ids()

        def _save(result):
            st.patch(conn, agent if agent in s.AGENTS else s.HOUSE_OWNER, {"model_catalog": result})
            adapter.list_models(refresh=True, settings=st.get_all(conn))

        started = mc.refresh_async(adapter.exe, ids, lambda exe, m: adapter.probe(m), _save)
        return {"started": started, "ids": ids, "probe": mc.refresh_state()}

    # ------------------------------------------------------------ proyectos

    @app.get("/api/projects", dependencies=dep)
    async def projects(include_archived: bool = False):
        return s.list_projects(conn, include_archived=include_archived)

    @app.post("/api/projects", dependencies=dep)
    async def create_project(body: dict):
        p = s.create_project(conn, body.get("agent", s.HOUSE_OWNER), body.get("name", ""),
                             color=body.get("color"))
        bus.emit("project.changed", {"project_id": p["id"]})
        return p

    @app.patch("/api/projects/{project_id}", dependencies=dep)
    async def update_project(project_id: int, body: dict):
        p = s.update_project(conn, body.pop("agent", s.HOUSE_OWNER), project_id,
                             **_solo_campos_que_acepta(s.update_project, body, saltar=3))
        bus.emit("project.changed", {"project_id": project_id})
        return p

    # ------------------------------------------------------------ board

    @app.get("/api/board", dependencies=dep)
    async def board(project_id: int | None = None, view: str = "all"):
        return s.board(conn, project_id=project_id, view=view)

    @app.get("/api/my_board", dependencies=dep)
    async def my_board(agent: str):
        return s.my_board(conn, agent)

    # ------------------------------------------------------------ tickets

    @app.get("/api/tickets", dependencies=dep)
    async def tickets(status: str | None = None, owner: str | None = None,
                      project_id: int | None = None, kind: str | None = None,
                      query: str | None = None, include_closed: bool = False,
                      workflow_id: int | None = None, ready: bool | None = None):
        out = s.list_tickets(conn, status=status, owner=owner, query=query,
                             include_closed=include_closed, project_id=project_id, kind=kind)
        if workflow_id is not None:
            out = [t for t in out if t.get("workflow_id") == workflow_id]
        if ready:  # review Eco SNAG-1: la cola despachable del coordinador en una llamada
            out = [t for t in out
                   if t["status"] in ("accepted", "blocked")
                   and not s._unmet_deps(conn, t["id"])]
        return out

    @app.post("/api/tickets", dependencies=dep)
    async def create_ticket(body: dict):
        agent = body.pop("agent", None)
        if not agent:
            raise s.TicketError("falta agent (quién crea)", field="agent", code="request.agent_required")
        res = s.propose_ticket(conn, agent,
                               **_solo_campos_que_acepta(s.propose_ticket, body, saltar=2))
        bus.emit("ticket.created", {"ticket_id": res["ticket"]["id"]})
        return res

    @app.get("/api/tickets/{ticket_id}", dependencies=dep)
    async def get_ticket(ticket_id: int):
        return s._as_dict(conn, s._get(conn, ticket_id))

    @app.post("/api/tickets/{ticket_id}/transition", dependencies=dep)
    async def transition(ticket_id: int, body: dict):
        agent = body.get("agent")
        action = body.get("action")
        if not agent or not action:
            raise s.TicketError("transition requiere agent y action", code="request.agent_required")
        if action == "accept":
            t = s.accept_ticket(conn, agent, ticket_id)
        elif action == "start":
            t = s.start_ticket(conn, agent, ticket_id, note=body.get("note", ""))
        elif action == "block":
            t = s.block_ticket(conn, agent, ticket_id, body.get("reason") or body.get("note", ""))
        elif action == "complete":
            t = s.complete_ticket(conn, agent, ticket_id,
                                  body.get("evidence_type"), body.get("evidence"))
        elif action == "verify":
            t = s.verify_ticket(conn, agent, ticket_id, body.get("verdict", "pass"),
                                note=body.get("note", ""))
        elif action == "reject":
            t = s.reject_ticket(conn, agent, ticket_id, body.get("reason") or body.get("note", ""))
        elif action in ("unblock", "start"):
            # unblock del kanban = start desde blocked (el motor ya lo soporta)
            t = s.start_ticket(conn, agent, ticket_id, note=body.get("note", ""))
        elif action == "reopen":
            t = s.reopen_ticket(conn, agent, ticket_id, note=body.get("note", ""))
        elif action == "backlog":
            t = s.backlog_ticket(conn, agent, ticket_id, note=body.get("note", ""))
        elif action == "rework":
            # v3.1: devolver una done/verified a accepted|in_progress con motivo obligatorio.
            t = s.rework_ticket(conn, agent, ticket_id, body.get("to") or "accepted",
                                body.get("reason") or body.get("note", ""))
        else:
            raise s.TicketError(f"action desconocida: {action!r} (accept|start|block|"
                                "complete|verify|reject|unblock|reopen|backlog|rework)")
        bus.emit("ticket.transition", {"ticket_id": ticket_id, "status": t["status"]})
        return _proto(agent, t)

    @app.get("/api/tickets/{ticket_id}/history", dependencies=dep)
    async def history(ticket_id: int):
        return s.ticket_history(conn, ticket_id)

    # ------------------------------------------------------------ calendario (v3, T2.1-T2.2)

    from . import agenda as cal, decisions as dec

    @app.get("/api/calendar", dependencies=dep)
    async def calendar_range(from_: str | None = Query(None, alias="from"),
                             to: str | None = None, agent: str | None = None,
                             project_id: int | None = None):
        """?from=YYYY-MM-DD&to=YYYY-MM-DD (alias: `from` es palabra reservada en Python)."""
        if not from_ or not to:
            raise s.TicketError("calendar requiere from y to (YYYY-MM-DD)")
        return cal.occurrences(conn, from_, to, agent=agent, project_id=project_id)

    @app.post("/api/tickets/{ticket_id}/schedule", dependencies=dep)
    async def ticket_schedule(ticket_id: int, body: dict):
        agent = body.get("agent")
        if not agent:
            raise s.TicketError("falta agent", field="agent", code="request.agent_required")
        res = cal.schedule_ticket(conn, agent, ticket_id, scheduled_at=body.get("scheduled_at"),
                                  duration_min=body.get("duration_min"), all_day=body.get("all_day"),
                                  note=body.get("note", ""))
        bus.emit("ticket.updated", {"ticket_id": ticket_id})
        return res

    @app.post("/api/tickets/{ticket_id}/shift_next", dependencies=dep)
    async def ticket_shift_next(ticket_id: int, body: dict):
        agent = body.get("agent")
        if not agent:
            raise s.TicketError("falta agent", field="agent", code="request.agent_required")
        res = cal.shift_next_occurrence(conn, agent, ticket_id, body.get("date"), note=body.get("note", ""))
        bus.emit("ticket.updated", {"ticket_id": ticket_id})
        return res

    # ------------------------------------------------------------ búsqueda global (v3, T3.9)

    from . import search as search_mod

    @app.get("/api/search", dependencies=dep)
    async def search_all(q: str = ""):
        return search_mod.search(conn, q)

    # ------------------------------------------------------------ decisiones (v3, T3.2-T3.3)

    @app.get("/api/decisions", dependencies=dep)
    async def decisions_list(status: str | None = None, workflow_id: int | None = None,
                             project_id: int | None = None):
        return dec.list_decisions(conn, status=status, workflow_id=workflow_id, project_id=project_id)

    @app.post("/api/decisions", dependencies=dep)
    async def decisions_ask(body: dict):
        agent = body.pop("agent", None)
        if not agent:
            raise s.TicketError("falta agent", field="agent", code="request.agent_required")
        d = dec.ask(conn, agent, **_solo_campos_que_acepta(dec.ask, body, saltar=2))
        bus.emit("decision.changed", {"decision_id": d["id"], "status": d["status"]})
        return d

    @app.get("/api/decisions/{decision_id}", dependencies=dep)
    async def decision_get(decision_id: int):
        return dec.get_decision(conn, decision_id)

    @app.post("/api/decisions/{decision_id}/decide", dependencies=dep)
    async def decision_decide(decision_id: int, body: dict):
        d = dec.decide(conn, body.get("agent"), decision_id,
                       option=body.get("option"), decision=body.get("decision"),
                       rationale=body.get("rationale", ""))
        bus.emit("decision.changed", {"decision_id": decision_id, "status": d["status"]})
        return d

    @app.post("/api/decisions/{decision_id}/defer", dependencies=dep)
    async def decision_defer(decision_id: int, body: dict):
        d = dec.defer(conn, body.get("agent"), decision_id, until=body.get("until"),
                      note=body.get("note", ""))
        bus.emit("decision.changed", {"decision_id": decision_id, "status": d["status"]})
        return d

    # ------------------------------------------------------------ Agentes (v3, T1.11)

    from . import jobs as jm, mcp_inherit, schedule as sched_mod

    @app.get("/api/jobs", dependencies=dep)
    async def jobs_list(status: str | None = None, project_id: int | None = None,
                        include_archived: bool = False):
        return jm.list_jobs(conn, status=status, project_id=project_id,
                            include_archived=include_archived)

    @app.post("/api/jobs", dependencies=dep)
    async def jobs_create(body: dict):
        agent = body.pop("agent", None)
        if not agent:
            raise s.TicketError("falta agent (quién crea)", field="agent", code="request.agent_required")
        harness_name = body.pop("harness", "claude-cli")
        j = jm.create_job(conn, agent, harness_name=harness_name,
                          **_solo_campos_que_acepta(jm.create_job, body, saltar=2,
                                                    ya_puestos=("harness_name",)))
        bus.emit("job.changed", {"job_id": j["id"]})
        return j

    @app.post("/api/jobs/{job_id}/pause", dependencies=dep)
    async def job_pause(job_id: int, body: dict):
        j = jm.set_status(conn, body.get("agent", s.HOUSE_OWNER), job_id, "paused")
        bus.emit("job.changed", {"job_id": job_id}); return j

    @app.post("/api/jobs/{job_id}/resume", dependencies=dep)
    async def job_resume(job_id: int, body: dict):
        j = jm.set_status(conn, body.get("agent", s.HOUSE_OWNER), job_id, "active")
        bus.emit("job.changed", {"job_id": job_id}); return j

    @app.post("/api/jobs/{job_id}/archive", dependencies=dep)
    async def job_archive(job_id: int, body: dict):
        j = jm.set_status(conn, body.get("agent", s.HOUSE_OWNER), job_id, "archived")
        bus.emit("job.changed", {"job_id": job_id}); return j

    @app.post("/api/jobs/{job_id}/duplicate", dependencies=dep)
    async def job_duplicate(job_id: int, body: dict):
        j = jm.duplicate_job(conn, body.get("agent", s.HOUSE_OWNER), job_id, name=body.get("name"))
        bus.emit("job.changed", {"job_id": j["id"]}); return j

    @app.get("/api/agents/schedule", dependencies=dep)
    async def agents_schedule(from_: str | None = Query(None, alias="from"),
                              to: str | None = None, days: int = 7):
        """Línea de tiempo de próximas ejecuciones (calendario PROPIO de Agentes,
        decisión 4: nunca en el Tablero)."""
        from datetime import datetime as _dt, timedelta as _td
        start = _dt.fromisoformat(from_) if from_ else _dt.now()
        end = _dt.fromisoformat(to) if to else start + _td(days=days)
        items = []
        for j in jm.list_jobs(conn, status="active"):
            cursor = start
            for _ in range(200):
                nf = sched_mod.next_fire(j["schedule"], j["created_at"], cursor)
                if nf is None or nf > end:
                    break
                items.append({"job_id": j["id"], "name": j["name"], "fire_at": nf.isoformat(timespec="minutes"),
                              "harness": j["harness"], "model": j["model"],
                              "schedule": sched_mod.human(j["schedule"])})
                cursor = nf
        items.sort(key=lambda x: x["fire_at"])
        return {"from": start.isoformat(timespec="minutes"), "to": end.isoformat(timespec="minutes"),
                "items": items}

    @app.get("/api/agents/calendar", dependencies=dep)
    async def agents_calendar(from_: str | None = Query(None, alias="from"), to: str | None = None):
        """v3.1 (T5.1): calendario PROPIO de Agentes — jobs once/recurring/weekly
        proyectados + runs pasados, mismo shape que /api/calendar. Nunca en el Tablero."""
        if not from_ or not to:
            raise s.TicketError("calendar requiere from y to (YYYY-MM-DD)")
        from . import agents_agenda
        return agents_agenda.occurrences(conn, from_, to)

    @app.get("/api/agents/results", dependencies=dep)
    async def agents_results():
        """La lectura de la mañana: último run + veredicto por job."""
        return jm.results(conn)

    @app.get("/api/agents/running", dependencies=dep)
    async def agents_running():
        return jm.list_runs(conn, status="running", limit=100)

    @app.get("/api/mcp/inherited", dependencies=dep)
    async def mcp_inherited():
        return mcp_inherit.inherited_servers()

    # ------------------------------------------------------------ registro MCP (v3.1, T5.6)

    from . import mcp_registry as reg

    @app.get("/api/mcp", dependencies=dep)
    async def mcp_list(q: str | None = None, enabled: int | None = None):
        """Registro propio de MCP: buscador por nombre/comando/url/nota; secretos enmascarados."""
        return reg.list_servers(conn, q=q, enabled_only=bool(enabled))

    @app.post("/api/mcp", dependencies=dep)
    async def mcp_create(body: dict):
        agent = body.pop("agent", None)
        if not agent:
            raise s.TicketError("falta agent", field="agent", code="request.agent_required")
        res = reg.create(conn, agent, body)
        bus.emit("mcp.changed", {"id": res["id"]})
        return res

    @app.post("/api/mcp/import_local", dependencies=dep)
    async def mcp_import_local(body: dict):
        agent = body.get("agent")
        if not agent:
            raise s.TicketError("falta agent", field="agent", code="request.agent_required")
        res = reg.import_local(conn, agent)
        bus.emit("mcp.changed", {"imported": res["imported"], "updated": res["updated"]})
        return res

    @app.get("/api/mcp/{server_id}", dependencies=dep)
    async def mcp_get(server_id: int):
        return reg.get(conn, server_id)

    @app.patch("/api/mcp/{server_id}", dependencies=dep)
    async def mcp_update(server_id: int, body: dict):
        agent = body.pop("agent", None)
        if not agent:
            raise s.TicketError("falta agent", field="agent", code="request.agent_required")
        res = reg.update(conn, agent, server_id, body)
        bus.emit("mcp.changed", {"id": server_id})
        return res

    @app.delete("/api/mcp/{server_id}", dependencies=dep)
    async def mcp_delete(server_id: int, agent: str):
        res = reg.delete(conn, agent, server_id)
        bus.emit("mcp.changed", {"id": server_id, "deleted": True})
        return res

    @app.post("/api/runs/{run_id}/verdict", dependencies=dep)
    async def run_verdict(run_id: int, body: dict):
        """Veredicto manual de un run (una persona, no el juez) — misma ruta que el juez."""
        agent = body.get("agent")
        if not agent:
            raise s.TicketError("falta agent", field="agent", code="request.agent_required")
        s._validate_agent(agent, family_only=True)
        r = jm.set_verdict(conn, run_id, body.get("verdict", "pass"), body.get("reason", ""), by=agent)
        bus.emit("verify.done", {"run_id": run_id, "job_id": r["job_id"], "verdict": r["verdict"]})
        return r

    @app.post("/api/verify/runs", dependencies=dep)
    async def verify_runs_now():
        from . import verifier
        return await verifier.sweep_runs(conn, emit=bus.emit)

    # ------------------------------------------------------------ jobs y runs

    @app.get("/api/jobs/{job_id}", dependencies=dep)
    async def get_job(job_id: int):
        return jm.get_job(conn, job_id)

    @app.patch("/api/jobs/{job_id}", dependencies=dep)
    async def patch_job(job_id: int, body: dict):
        agent = body.pop("agent", None)
        if not agent:
            raise s.TicketError("falta agent", field="agent", code="request.agent_required")
        j = jm.update_job(conn, agent, job_id,
                          **_solo_campos_que_acepta(jm.update_job, body, saltar=3))
        bus.emit("job.changed", {"job_id": job_id})
        if j.get("ticket_id"):
            bus.emit("ticket.updated", {"ticket_id": j["ticket_id"]})
        return j

    @app.post("/api/jobs/{job_id}/launch", dependencies=dep)
    async def launch(job_id: int):
        # create_run valida AQUÍ (enabled, no solape, ticket abierto): el rechazo
        # le llega al botón como 400 con reason, no a un log.
        run = s.create_run(conn, job_id)
        asyncio.create_task(_run_and_log(job_id, run))
        return {"launched": True, "job_id": job_id, "run_id": run["id"]}

    async def _run_and_log(job_id: int, run: dict):
        try:
            await runner.execute_job(conn, job_id, emit=bus.emit, run=run)
        except Exception:
            log.exception("run #%s del job #%s reventó", run["id"], job_id)

    @app.get("/api/runs", dependencies=dep)
    async def runs(ticket_id: int | None = None, status: str | None = None, limit: int = 50,
                   job_id: int | None = None):
        return jm.list_runs(conn, job_id=job_id, ticket_id=ticket_id, status=status, limit=limit)

    @app.get("/api/runs/{run_id}", dependencies=dep)
    async def get_run(run_id: int):
        return s.get_run(conn, run_id)

    @app.post("/api/runs/{run_id}/cancel", dependencies=dep)
    async def cancel_run(run_id: int):
        result = await runner.cancel_run(conn, run_id)
        bus.emit("run.finished", {"run_id": run_id, "status": "cancelled"})
        return result

    @app.get("/api/runs/{run_id}/output", dependencies=dep)
    async def run_output(run_id: int):
        run = s.get_run(conn, run_id)
        if not run.get("output_path") or not Path(run["output_path"]).exists():
            return {"run_id": run_id, "output": None}
        return {"run_id": run_id,
                "output": Path(run["output_path"]).read_text(encoding="utf-8")}

    # ------------------------------------------------------------ workflows (v2)

    def _proto(agent, payload):
        """Los peers escriben directo PERO siempre reportan.
        La respuesta se lo recuerda en cada escritura de un rol de workflow."""
        if agent in s.WORKFLOW_AGENTS and isinstance(payload, dict):
            payload = {**payload,
                       "protocol": "reporta al coordinador por relay que has apuntado esto"}
        return payload

    @app.post("/api/workflows/import", dependencies=dep)
    async def import_plan(body: dict):
        agent = body.get("agent")
        if not agent:
            raise s.TicketError("falta agent", field="agent", code="request.agent_required")
        res = w.import_plan(conn, agent, body.get("plan") or {}, repo_root=body.get("repo_root"))
        bus.emit("workflow.imported", {"workflow_id": res["id"]})
        return res

    @app.get("/api/workflows", dependencies=dep)
    async def workflows_list(status: str | None = None, q: str | None = None):
        return w.list_workflows(conn, status=status, q=q)

    @app.get("/api/workflows/{workflow_id}", dependencies=dep)
    async def workflow_detail(workflow_id: int):
        return w.workflow_status(conn, workflow_id)

    @app.patch("/api/workflows/{workflow_id}", dependencies=dep)
    async def workflow_patch(workflow_id: int, body: dict):
        res = w.update_workflow(conn, body.get("agent"), workflow_id,
                                status=body.get("status"), repo_root=body.get("repo_root"),
                                reason=body.get("reason"), close=bool(body.get("close")))
        bus.emit("workflow.milestone" if res["status"] == "closed" else "ticket.updated",
                 {"workflow_id": workflow_id})
        return res

    @app.post("/api/workflows/{workflow_id}/dispatch_batch", dependencies=dep)
    async def dispatch_batch(workflow_id: int, body: dict):
        """Lote atómico (T3.8, Eco): todas o ninguna; el 400 nombra la culpable."""
        res = w.dispatch_batch(conn, body.get("agent"), workflow_id, body.get("items") or [])
        for d in res.get("dispatched", []):
            bus.emit("ticket.dispatched", {"ticket_id": d.get("ticket_id") or d.get("id"),
                                           "to": d.get("to") or d.get("dispatched_to")})
        return res

    @app.get("/api/workflows/{workflow_id}/documents", dependencies=dep)
    async def workflow_documents(workflow_id: int):
        """T3.6: spec, plan, reviews y mediciones del workflow, abribles desde la app."""
        return w.documents(conn, workflow_id)

    @app.get("/api/workflows/{workflow_id}/handoffs", dependencies=dep)
    async def workflow_handoffs(workflow_id: int):
        """Todos los handoffs del workflow, más reciente primero (T3.5)."""
        return w.list_handoffs(conn, workflow_id)

    @app.post("/api/tickets/{ticket_id}/dispatch", dependencies=dep)
    async def dispatch(ticket_id: int, body: dict):
        res = w.dispatch_ticket(conn, body.get("agent"), ticket_id,
                                body.get("to"), note=body.get("note", ""))
        bus.emit("ticket.dispatched", {"ticket_id": ticket_id, "to": body.get("to")})
        return res

    @app.post("/api/tickets/{ticket_id}/deps", dependencies=dep)
    async def add_dep(ticket_id: int, body: dict):
        res = w.add_dep(conn, body.get("agent"), ticket_id, body.get("depends_on"))
        bus.emit("ticket.updated", {"ticket_id": ticket_id})
        return res

    @app.delete("/api/tickets/{ticket_id}/deps/{depends_on}", dependencies=dep)
    async def remove_dep(ticket_id: int, depends_on: int, agent: str):
        res = w.remove_dep(conn, agent, ticket_id, depends_on)
        bus.emit("ticket.updated", {"ticket_id": ticket_id})
        return res

    @app.get("/api/workflows/{workflow_id}/findings", dependencies=dep)
    async def findings_list(workflow_id: int, status: str | None = None):
        return w.list_findings(conn, workflow_id, status=status)

    @app.post("/api/workflows/{workflow_id}/findings", dependencies=dep)
    async def finding_add(workflow_id: int, body: dict):
        agent = body.pop("agent", None)
        res = w.add_finding(conn, agent, workflow_id,
                            **_solo_campos_que_acepta(w.add_finding, body, saltar=3))
        bus.emit("finding.added", {"workflow_id": workflow_id, "finding_id": res["id"]})
        return _proto(agent, res)

    @app.post("/api/findings/{finding_id}/dispatch", dependencies=dep)
    async def finding_dispatch(finding_id: int, body: dict):
        res = w.dispatch_finding(conn, body.get("agent"), finding_id,
                                 note=body.get("note", ""), phase=body.get("phase"))
        bus.emit("finding.dispatched", {"finding_id": finding_id,
                                        "correction_ticket_id": res["correction_ticket_id"]})
        return res

    @app.post("/api/findings/{finding_id}/dismiss", dependencies=dep)
    async def finding_dismiss(finding_id: int, body: dict):
        res = w.dismiss_finding(conn, body.get("agent"), finding_id, body.get("note", ""))
        bus.emit("finding.resolved", {"finding_id": finding_id, "status": "dismissed"})
        return res

    @app.get("/api/workflows/{workflow_id}/activity", dependencies=dep)
    async def workflow_activity(workflow_id: int, since: str | None = None, limit: int = 30):
        return w.recent_activity(conn, workflow_id, limit=limit, since=since)

    @app.get("/api/workflows/{workflow_id}/handoff_draft", dependencies=dep)
    async def workflow_handoff_draft(workflow_id: int):
        return w.handoff_draft(conn, workflow_id)

    @app.post("/api/workflows/{workflow_id}/handoffs", dependencies=dep)
    async def workflow_handoff_write(workflow_id: int, body: dict):
        res = w.write_handoff(conn, body.get("agent"), workflow_id, body.get("content", ""))
        bus.emit("ticket.updated", {"workflow_id": workflow_id})
        return res

    @app.get("/api/workflows/{workflow_id}/milestones/{milestone_id}/memory_draft",
             dependencies=dep)
    async def milestone_memory_draft(workflow_id: int, milestone_id: int):
        return w.memory_draft(conn, workflow_id, milestone_id)

    @app.post("/api/workflows/{workflow_id}/milestones/{milestone_id}/mark_saved",
              dependencies=dep)
    async def milestone_mark_saved(workflow_id: int, milestone_id: int, body: dict):
        return w.mark_milestone_saved(conn, body.get("agent"), workflow_id,
                                      milestone_id, body.get("memory_id"))

    @app.patch("/api/tickets/{ticket_id}", dependencies=dep)
    async def ticket_patch(ticket_id: int, body: dict):
        agent = body.pop("agent", None)
        if not agent:
            raise s.TicketError("falta agent", field="agent", code="request.agent_required")
        res = s.update_ticket(conn, agent, ticket_id,
                              **_solo_campos_que_acepta(s.update_ticket, body, saltar=3))
        bus.emit("ticket.updated", {"ticket_id": ticket_id})
        return res

    # ------------------------------------------------------------ verificador

    @app.post("/api/verify/sweep", dependencies=dep)
    async def verify_sweep():
        from . import verifier
        result = await verifier.sweep(conn, emit=bus.emit)
        return result

    # ------------------------------------------------------------ SSE

    @app.get("/api/events", dependencies=dep)
    async def events():
        return StreamingResponse(bus.stream(), media_type="text/event-stream")

    return app
