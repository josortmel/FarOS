"""Rutas /api/library/* (Spec v2 §4, contrato con el renderer del 4-nov).

- `GET /api/library/items` devuelve un ARRAY plano de filas; cada fila lleva `abrir`
  (URL del publicado si es interactivo y se publicó; null si no), igual que
  `GET /api/library/items/{id}`: la fila es un enlace de un toque.
- Todo lo que no pasa `permitida`, está ausente o no existe da el MISMO 403 (R13): el cliente no
  puede usar la API para saber qué existe.
- El cliente nunca aporta una ruta de fichero a servir: se sirve la de la fila, tras volver a
  comprobarla; y el HTML maquetado de una revista, solo si es el que la fila tiene guardado.
"""

from __future__ import annotations

import html as _html
import os
import re
from pathlib import Path

from fastapi import Query
from fastapi.responses import HTMLResponse, JSONResponse

from . import library as L
from . import service as s

# R8: sandbox sin allow-scripts ni allow-same-origin, nada de red, solo estilo en línea.
CSP = ("sandbox allow-popups allow-popups-to-escape-sandbox; default-src 'none'; img-src data:; "
       "font-src data:; style-src 'unsafe-inline'")
CSP_META = "default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'"   # sin `sandbox`: en una meta no vale
HEADERS = {"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}

DIA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}|semana-[0-9]{4}-[0-9]{2}")      # ASCII + fullmatch (no \d, no $)
FECHA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
PUBLICADO = L.PUBLICADO_RE
CAMPOS_POST = {"ruta", "clase", "ticket"}
MAX_ID = 2**63 - 1


def _prohibido() -> JSONResponse:
    return JSONResponse(status_code=403, headers=HEADERS,
                        content={"error": "forbidden", "reason": "no disponible", "code": "library.forbidden"})


def _pagina(titulo: str, cuerpo: str) -> str:
    return ('<!doctype html><html><head>'
            f'<meta http-equiv="Content-Security-Policy" content="{CSP_META}">'
            '<meta charset="utf-8">'
            f'<title>{_html.escape(titulo, quote=False)}</title>'
            '<style>body{font:16px/1.55 system-ui,sans-serif;max-width:46rem;margin:1rem auto;padding:0 1rem}'
            'pre{white-space:pre-wrap}table{border-collapse:collapse}td,th{border:1px solid #8884;padding:.2rem .5rem}'
            f'</style></head><body>{cuerpo}</body></html>')


def register(app, conn, dep, get_env) -> None:
    def abrir(row, env) -> str | None:
        pub = row["publicado"]
        if (not row["interactivo"] or not pub or not PUBLICADO.fullmatch(pub) or not env.abrir_base
                or not (env.publicado_dir / pub).is_file()):
            return None
        return env.abrir_base.rstrip("/") + "/" + pub

    def fila(row, env) -> dict:
        d = dict(row)
        d["abrir"] = abrir(row, env)
        return d

    def permitido(item_id: int):
        env = get_env()
        if not 0 < item_id <= MAX_ID:           # fuera de rango de SQLite = como cualquier id que no existe
            return None, env
        row = conn.execute("SELECT * FROM library_items WHERE id=?", (item_id,)).fetchone()
        if row is None or row["estado"] != "presente" or not L.permitida(row["ruta"], env.raices):
            return None, env
        return row, env

    def html_de_revista(row, env) -> str | None:
        """El revista-<día>.html maquetado, solo si es el que la fila tiene guardado."""
        guardado, base = row["html_publicado"], env.html_revista_dir
        m = L.REVISTA_MD.fullmatch(os.path.basename(row["ruta"]))
        if not guardado or base is None or not m or row["clase"] != "revista":
            return None
        esperado = Path(base) / f"revista-{m.group(1)}.html"
        real = os.path.realpath(esperado)
        if (os.path.normcase(os.path.realpath(guardado)) != os.path.normcase(real)
                or os.path.normcase(os.path.dirname(real)) != os.path.normcase(os.path.realpath(base))
                or not os.path.isfile(real) or L.en_lista_negra(real)):
            return None
        return L._leer(real)

    @app.get("/api/library/items", dependencies=dep)
    async def library_items(q: str | None = None, clase: str | None = None, desde: str | None = None,
                            hasta: str | None = None, limite: int = Query(50), offset: int = Query(0)):
        env = get_env()
        if not 1 <= limite <= 500:
            raise s.TicketError("limite debe estar entre 1 y 500", field="limite", code="limite.invalid")
        if not 0 <= offset <= MAX_ID:
            raise s.TicketError("offset debe ser >= 0", field="offset", code="offset.invalid")
        if clase is not None and clase not in L.CLASES:
            raise s.TicketError(f"clase desconocida: {clase!r}", field="clase", code="clase.invalid")
        for campo, v in (("desde", desde), ("hasta", hasta)):
            if v is not None and not FECHA.fullmatch(v):
                raise s.TicketError(f"{campo} debe ser AAAA-MM-DD", field=campo, code=f"{campo}.invalid")
        try:
            filas = L.buscar(conn, (q or "")[:200], clase=clase, desde=desde, hasta=hasta, limite=limite,
                             offset=offset, filtro=lambda r: L.permitida(r["ruta"], env.raices))
        except ValueError as e:
            raise s.TicketError(str(e), field="q", code="q.invalid")
        return [fila(r, env) for r in filas]

    @app.get("/api/library/items/{item_id}", dependencies=dep)
    async def library_item(item_id: int):
        row, env = permitido(item_id)
        if row is None:
            return _prohibido()
        d = fila(row, env)
        d["enlaces"] = [dict(r) for r in conn.execute(
            "SELECT ticket_id, job_id, run_id FROM library_links WHERE item_id=? ORDER BY rowid", (item_id,))]
        return d

    @app.get("/api/library/items/{item_id}/content", dependencies=dep)
    async def library_content(item_id: int, formato: str = "html", max: int = Query(8000, ge=1, le=200000)):
        row, env = permitido(item_id)
        if row is None:
            return _prohibido()
        if formato not in ("html", "texto"):
            raise s.TicketError("formato debe ser html o texto", field="formato", code="formato.invalid")
        if formato == "texto":
            texto = L.leer_contenido(row["ruta"], env)
            if texto is None:
                return _prohibido()
            if row["ruta"].lower().endswith((".html", ".htm")):
                texto = L._texto_de_html(texto)
            return JSONResponse(headers=HEADERS, content={
                "id": row["id"], "titulo": row["titulo"], "clase": row["clase"], "total": len(texto),
                "texto": texto[:max], "truncado": len(texto) > max})
        if row["interactivo"]:
            return JSONResponse(headers=HEADERS, content={"abrir": abrir(row, env)})
        maquetado = html_de_revista(row, env)
        if maquetado is not None:
            return HTMLResponse(maquetado, headers=HEADERS)
        texto = L.leer_contenido(row["ruta"], env)
        if texto is None:
            return _prohibido()
        nombre = row["ruta"].lower()
        if nombre.endswith((".html", ".htm")):
            return HTMLResponse(texto, headers=HEADERS)
        if nombre.endswith((".md", ".markdown")):
            cuerpo = L.md_a_html(texto)
        else:                                           # carpeta de un run, .txt...: texto plano
            cuerpo = "<pre>" + _html.escape(texto, quote=False) + "</pre>"
        return HTMLResponse(_pagina(row["titulo"], cuerpo), headers=HEADERS)

    @app.post("/api/library/items", dependencies=dep, status_code=201)
    async def library_add(body: dict):
        env = get_env()
        sobran = sorted(set(body) - CAMPOS_POST)
        if sobran:
            raise s.TicketError(f"campos desconocidos: {sobran}", field=sobran[0], code="request.unknown_field")
        ruta, clase, ticket = body.get("ruta"), body.get("clase"), body.get("ticket")
        if not isinstance(ruta, str) or not 0 < len(ruta) <= 1024:
            raise s.TicketError("ruta debe ser un texto de 1 a 1024 caracteres", field="ruta", code="ruta.invalid")
        if clase not in L.CLASES:
            raise s.TicketError(f"clase debe ser una de {list(L.CLASES)}", field="clase", code="clase.invalid")
        if ticket is not None:
            if (not isinstance(ticket, int) or isinstance(ticket, bool) or not 1 <= ticket <= MAX_ID
                    or conn.execute("SELECT 1 FROM tickets WHERE id=?", (ticket,)).fetchone() is None):
                raise s.TicketError("ticket no existe", field="ticket", code="ticket.invalid")
        item_id = L.registrar(conn, ruta, clase, "manual", ticket_id=ticket, env=env)
        if item_id is None:
            return _prohibido()
        return JSONResponse(status_code=201, content=fila(
            conn.execute("SELECT * FROM library_items WHERE id=?", (item_id,)).fetchone(), env))

    @app.get("/api/library/revista/{dia}", dependencies=dep)
    async def library_revista(dia: str):
        env = get_env()
        if not DIA.fullmatch(dia):
            raise s.TicketError("dia debe ser AAAA-MM-DD o semana-AAAA-WW", field="dia", code="dia.invalid")
        raiz = next((r for r in env.raices if r["clase"] == "revista" and r["tipo"] == "carpeta"), None)
        ruta = Path(raiz["ruta"]) / f"{dia}.md" if raiz else None
        if ruta is None or not ruta.is_file() or not L.permitida(ruta, env.raices):
            return JSONResponse(status_code=404, content={"error": "not_found", "reason": "sin revista de ese día",
                                                          "code": "library.not_found"})
        clave = os.path.normcase(os.path.realpath(ruta))
        row = conn.execute("SELECT * FROM library_items WHERE ruta=? AND estado='presente'", (clave,)).fetchone()
        if row is None:         # la única escritura en un GET: ruta construida con la regex, nunca del cliente
            item_id = L.registrar(conn, ruta, "revista", "manual", env=env)
            row = conn.execute("SELECT * FROM library_items WHERE id=?", (item_id,)).fetchone() if item_id else None
        if row is None:
            return _prohibido()
        return fila(row, env)
