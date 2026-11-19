"""#869 (Biblioteca, tarea 4): /api/library/* y library_* del MCP (Spec v2 §4 y §6; contrato con el renderer, 4-nov)."""

import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from faros import api, library as L, mcp_server
from test_850_library import mundo, _w, _filas  # noqa: F401  (el mismo árbol temporal)

TOKEN = "t-850"
H = {"X-FarOS-Token": TOKEN}
BASE = "https://host.example.test/biblioteca/"
CSP = ("sandbox allow-popups allow-popups-to-escape-sandbox; default-src 'none'; img-src data:; "
       "font-src data:; style-src 'unsafe-inline'")


@pytest.fixture
def cl(mundo, monkeypatch):
    monkeypatch.setattr(api, "load_token", lambda: TOKEN)
    mundo.env.abrir_base = BASE
    c = sqlite3.connect(str(mundo.t / "faros.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    L.recorrer(mundo.conn, mundo.env)
    cliente = TestClient(api.create_app(c, biblioteca=mundo.env))
    cliente.mundo, cliente.db = mundo, c
    yield cliente
    c.close()


def _id(cl, **filtro):
    return _filas(cl.mundo.conn, **filtro)[0]["id"]


# --------------------------------------------------------------- auth y forma

@pytest.mark.parametrize("metodo,ruta", [("get", "/api/library/items"), ("get", "/api/library/items/1"),
                                         ("get", "/api/library/items/1/content"), ("post", "/api/library/items"),
                                         ("get", "/api/library/revista/2026-10-29")])
def test_sin_token_401(cl, metodo, ruta):
    r = getattr(cl, metodo)(ruta)
    assert r.status_code == 401 and r.json()["code"] == "auth.invalid_token"


def test_listado_es_un_array_plano_con_abrir_en_cada_fila(cl):
    r = cl.get("/api/library/items?limite=500", headers=H)
    assert r.status_code == 200
    filas = r.json()
    assert isinstance(filas, list) and len(filas) > 10
    assert all("abrir" in f and {"id", "clase", "titulo", "fecha", "estado"} <= set(f) for f in filas)
    assert all(f["estado"] == "presente" for f in filas)


def test_criterio_2_misma_vista_que_la_pestana(cl):
    rev = cl.get("/api/library/items?clase=revista", headers=H).json()
    carpeta = list(cl.mundo.rev.glob("????-??-??.md")) + list(cl.mundo.rev.glob("semana-*.md"))
    assert len(rev) == len(carpeta) == 3
    assert [f["fecha"] for f in rev] == sorted((f["fecha"] for f in rev), reverse=True)
    assert len(cl.get("/api/library/items?clase=lectura", headers=H).json()) == 2


def test_busqueda_filtros_y_errores_400(cl):
    ok = cl.get("/api/library/items", headers=H, params={"q": "migraciones"}).json()
    assert [f["titulo"] for f in ok] == ["Informe uno"]
    assert cl.get("/api/library/items", headers=H, params={"q": 'a"b'}).status_code == 200
    assert len(cl.get("/api/library/items", headers=H, params={"clase": "revista", "desde": "2026-10-29"}).json()) == 1
    for params, campo in (({"q": "a"}, "q"), ({"clase": "inventada"}, "clase"), ({"desde": "ayer"}, "desde"),
                          ({"hasta": "2026-1-1"}, "hasta"), ({"desde": "٢٠٢٦-١٠-٢٩"}, "desde"),
                          ({"desde": "2026-10-29\n"}, "desde")):
        r = cl.get("/api/library/items", headers=H, params=params)
        assert r.status_code == 400 and r.json()["field"] == campo, (params, r.text)
    assert cl.get("/api/library/items", headers=H, params={"limite": "mucho"}).status_code == 422
    assert len(cl.get("/api/library/items", headers=H, params={"limite": 2}).json()) == 2
    assert len(cl.get("/api/library/items", headers=H, params={"limite": 10**9}).json()) <= 500


def test_el_listado_no_incluye_lo_que_ya_no_pasa_permitida(cl):
    i = _id(cl, titulo="Informe uno")
    cl.db.execute("UPDATE library_items SET ruta=? WHERE id=?", (str(cl.mundo.t / "fuera" / "x.md"), i))
    cl.db.commit()
    assert i not in [f["id"] for f in cl.get("/api/library/items?limite=500", headers=H).json()]


# --------------------------------------------------------------- abrir y content

def test_abrir_en_lo_interactivo_y_null_en_lo_demas(cl):
    viva = _filas(cl.mundo.conn, titulo="Página Viva ñandú")[0]
    fila = cl.get(f"/api/library/items/{viva['id']}", headers=H).json()
    assert fila["abrir"] == BASE + viva["publicado"] and fila["id"] == viva["id"]
    c = cl.get(f"/api/library/items/{viva['id']}/content", headers=H)
    assert c.status_code == 200 and c.json() == {"abrir": fila["abrir"]}          # la misma URL en /content
    assert c.headers["content-security-policy"] == CSP
    assert cl.get(f"/api/library/items/{_id(cl, titulo='Informe uno')}", headers=H).json()["abrir"] is None
    en_lista = {f["id"]: f["abrir"] for f in cl.get("/api/library/items?limite=500", headers=H).json()}
    assert en_lista[viva["id"]] == fila["abrir"]


def test_interactivo_retenido_o_sin_copia_da_abrir_null(cl):
    viva = _filas(cl.mundo.conn, titulo="Página Viva ñandú")[0]
    cl.db.execute("UPDATE library_items SET publicado='' WHERE id=?", (viva["id"],))
    cl.db.commit()
    assert cl.get(f"/api/library/items/{viva['id']}", headers=H).json()["abrir"] is None
    assert cl.get(f"/api/library/items/{viva['id']}/content", headers=H).json() == {"abrir": None}
    cl.db.execute("UPDATE library_items SET publicado='../../etc/passwd' WHERE id=?", (viva["id"],))
    cl.db.commit()
    assert cl.get(f"/api/library/items/{viva['id']}", headers=H).json()["abrir"] is None


def test_sin_abrir_base_configurada_abrir_es_null(cl):
    cl.mundo.env.abrir_base = None
    viva = _filas(cl.mundo.conn, titulo="Página Viva ñandú")[0]
    assert cl.get(f"/api/library/items/{viva['id']}", headers=H).json()["abrir"] is None


def test_content_de_un_md_es_html_con_csp_y_enlaces_seguros(cl):
    i = _id(cl, titulo="Revista I+D — 2026-10-29") if _filas(cl.mundo.conn, titulo="Revista I+D — 2026-10-29") else None
    md = _w(cl.mundo.ses / "s1" / "enlaces.md", "# Con enlaces\n\n[a](https://a.test/x) <script>alert(1)</script> [j](javascript:alert(1))")
    j = L.registrar(cl.mundo.conn, md, env=cl.mundo.env)
    r = cl.get(f"/api/library/items/{j}/content", headers=H)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["content-security-policy"] == CSP
    assert r.headers["x-content-type-options"] == "nosniff"
    assert 'target="_blank" rel="noopener noreferrer"' in r.text and "<script" not in r.text.lower()
    assert 'href="javascript' not in r.text
    assert "default-src 'none'" in r.text.split("</head>")[0] and "sandbox" not in r.text.split("</head>")[0]
    assert r.text.index("Content-Security-Policy") < r.text.index("<title>")        # la meta es la primera


def test_content_de_un_html_no_interactivo_y_de_una_carpeta(cl):
    est = cl.get(f"/api/library/items/{_id(cl, titulo='Estatica')}/content", headers=H)
    assert est.status_code == 200 and "hola" in est.text and est.headers["content-security-policy"] == CSP
    run = cl.get(f"/api/library/items/{_id(cl, titulo='run_5')}/content", headers=H)
    assert run.status_code == 200 and "out.txt" in run.text and "<pre>" in run.text


def test_revista_con_html_publicado_se_sirve_maquetada_con_la_misma_csp(cl):
    i = L.registrar(cl.mundo.conn, cl.mundo.rev / "2026-10-29.md", env=cl.mundo.env)
    r = cl.get(f"/api/library/items/{i}/content", headers=H)
    assert r.status_code == 200 and "<h1>Revista</h1>" in r.text            # el revista-2026-10-29.html del fixture
    assert r.headers["content-security-policy"] == CSP
    otra = cl.get(f"/api/library/items/{L.registrar(cl.mundo.conn, cl.mundo.rev / '2026-10-28.md', env=cl.mundo.env)}/content", headers=H)
    assert "Revista 28" in otra.text                                         # sin maquetado: el .md convertido


def test_la_semanal_con_html_publicado_se_sirve_maquetada(cl):
    """semana-AAAA-SS.md no es una fecha: salía en bruto, con las notas internas (Prima, 5-nov)."""
    html = cl.mundo.env.html_revista_dir / "revista-semana-2026-43.html"
    html.write_text("<html><title>Semana</title><body><h1>Maquetada</h1></body></html>", encoding="utf-8")
    try:
        i = L.registrar(cl.mundo.conn, cl.mundo.rev / "semana-2026-43.md", env=cl.mundo.env)
        assert "<h1>Maquetada</h1>" in cl.get(f"/api/library/items/{i}/content", headers=H).text
    finally:
        html.unlink()
        L.registrar(cl.mundo.conn, cl.mundo.rev / "semana-2026-43.md", env=cl.mundo.env)


def test_html_publicado_solo_si_es_el_guardado_para_esa_fila(cl):
    i = L.registrar(cl.mundo.conn, cl.mundo.rev / "2026-10-29.md", env=cl.mundo.env)
    secreto = _w(cl.mundo.t / "otro" / "revista-2026-10-29.html", "<html>SECRETO AJENO</html>")
    cl.db.execute("UPDATE library_items SET html_publicado=? WHERE id=?", (str(secreto), i))
    cl.db.commit()
    r = cl.get(f"/api/library/items/{i}/content", headers=H)
    assert "SECRETO AJENO" not in r.text and r.status_code == 200            # cae al .md
    cl.db.execute("UPDATE library_items SET html_publicado=? WHERE id=?",
                  (str(cl.mundo.env.html_revista_dir / "revista-2026-10-29.html"), i))
    cl.db.commit()
    assert "<h1>Revista</h1>" in cl.get(f"/api/library/items/{i}/content", headers=H).text


# --------------------------------------------------------------- 403 (R13)

def test_403_siempre_el_mismo_y_con_csp(cl):
    ausente = _id(cl, titulo="Informe uno")
    cl.db.execute("UPDATE library_items SET estado='ausente' WHERE id=?", (ausente,))
    fuera = _id(cl, titulo="Estatica")
    cl.db.execute("UPDATE library_items SET ruta=? WHERE id=?", (str(cl.mundo.t / "fuera" / "x.html"), fuera))
    negra = _id(cl, titulo="run_6")
    cl.db.execute("UPDATE library_items SET ruta=? WHERE id=?", (str(cl.mundo.ses / "beta_textos" / "x.md"), negra))
    cl.db.commit()
    cuerpos = set()
    for i in (ausente, fuera, negra, 999999):
        for ruta in (f"/api/library/items/{i}", f"/api/library/items/{i}/content",
                     f"/api/library/items/{i}/content?formato=texto"):
            r = cl.get(ruta, headers=H)
            assert r.status_code == 403, (ruta, r.status_code)
            assert r.headers["content-security-policy"] == CSP
            cuerpos.add(r.text)
    assert len(cuerpos) == 1, cuerpos                                       # ni existe/no existe ni el motivo
    assert "fuera" not in cuerpos.pop() and True


def test_el_403_no_dice_rutas_ni_excepciones(cl):
    r = cl.post("/api/library/items", headers=H, json={"ruta": str(cl.mundo.t / "nada" / "x.md"), "clase": "faro"})
    assert r.status_code == 403 and str(cl.mundo.t) not in r.text and "Traceback" not in r.text


# --------------------------------------------------------------- formato texto (library_get)

def test_formato_texto_con_tope(cl):
    i = _id(cl, titulo="Informe uno")
    r = cl.get(f"/api/library/items/{i}/content", headers=H, params={"formato": "texto"})
    assert r.json()["texto"].startswith("# Informe uno") and r.json()["truncado"] is False
    corto = cl.get(f"/api/library/items/{i}/content", headers=H, params={"formato": "texto", "max": 5}).json()
    assert corto["texto"] == "# Inf" and corto["truncado"] is True and corto["total"] > 5
    h = cl.get(f"/api/library/items/{_id(cl, titulo='Estatica')}/content", headers=H, params={"formato": "texto"}).json()
    assert h["texto"] == "Estatica hola" or "hola" in h["texto"]
    assert "<" not in h["texto"]
    assert cl.get(f"/api/library/items/{i}/content", headers=H, params={"formato": "xml"}).status_code == 400
    assert cl.get(f"/api/library/items/{i}/content", headers=H, params={"formato": "texto", "max": 0}).status_code == 422


# --------------------------------------------------------------- POST

def test_post_registra_y_es_idempotente(cl):
    f = _w(cl.mundo.ses / "s1" / "nuevo.md", "# Nuevo desde la API")
    t = cl.db.execute("INSERT INTO tickets (kind, title, created_by, created_at) VALUES ('manual','t','alice','x')").lastrowid
    cl.db.commit()
    a = cl.post("/api/library/items", headers=H, json={"ruta": str(f), "clase": "faro", "ticket": t})
    assert a.status_code == 201 and a.json()["titulo"] == "Nuevo desde la API" and a.json()["origen"] == "manual"
    b = cl.post("/api/library/items", headers=H, json={"ruta": str(f), "clase": "faro", "ticket": t})
    assert b.json()["id"] == a.json()["id"]
    assert cl.db.execute("SELECT count(*) FROM library_items WHERE titulo='Nuevo desde la API'").fetchone()[0] == 1
    assert cl.db.execute("SELECT count(*) FROM library_links WHERE ticket_id=?", (t,)).fetchone()[0] == 1


@pytest.mark.parametrize("cuerpo,campo", [
    ({"ruta": "x", "clase": "faro", "extra": 1}, "extra"),
    ({"clase": "faro"}, "ruta"), ({"ruta": 5, "clase": "faro"}, "ruta"), ({"ruta": "", "clase": "faro"}, "ruta"),
    ({"ruta": "x" * 2000, "clase": "faro"}, "ruta"), ({"ruta": "x"}, "clase"), ({"ruta": "x", "clase": "otra"}, "clase"),
    ({"ruta": "x", "clase": "faro", "ticket": 999999}, "ticket"), ({"ruta": "x", "clase": "faro", "ticket": True}, "ticket"),
    ({"ruta": "x", "clase": "faro", "ticket": "1"}, "ticket"), ({"ruta": "x", "clase": "faro", "ticket": -1}, "ticket"),
])
def test_post_400_con_su_campo(cl, cuerpo, campo):
    r = cl.post("/api/library/items", headers=H, json=cuerpo)
    assert r.status_code == 400 and r.json()["field"] == campo, r.text


@pytest.mark.parametrize("ruta,clase", [
    ("{ses}/s1/informe.md", "revista"),                       # la clase no es la de la raíz
    ("{ses}/beta_textos/x.md", "faro"), ("{ses}/s1/datos.json", "faro"), ("{ses}/s1/../../Sesiones_evil/x.md", "faro"),
    ("{t}/Faro/Sesiones_evil/x.md", "faro"), ("{ses}/s1/informe.md::$DATA", "faro"), ("{ses}/s1/a\x00b.md", "faro"),
    ("//evil/share/x.md", "faro"), ("{ses}/s1", "faro"), ("{ses}/no_existe.md", "faro"),
])
def test_post_403_lo_que_no_pasa_permitida(cl, ruta, clase):
    ruta = ruta.format(ses=cl.mundo.ses, t=cl.mundo.t)
    r = cl.post("/api/library/items", headers=H, json={"ruta": ruta, "clase": clase})
    assert r.status_code == 403, r.text
    assert cl.db.execute("SELECT count(*) FROM library_items WHERE ruta LIKE '%sesiones_evil%'").fetchone()[0] == 0


# --------------------------------------------------------------- revista/{dia}

@pytest.mark.parametrize("dia", ["2026-1-1", "2026-10-29\n", "٢٠٢٦-١٠-٢٩", "semana-2026-4", "2026-10-29x", "semana-2026-43\n",
                                 "SEMANA-2026-43", " 2026-10-29", "2026-10-29.md", "..", "%2e%2e", "2026-10-29%00"])
def test_revista_dia_fuera_de_la_regex_400(cl, dia):
    from urllib.parse import quote
    r = cl.get("/api/library/revista/" + quote(dia, safe=""), headers=H)
    if dia == "..":
        assert r.status_code == 404                      # el cliente normaliza la ruta: ni llega a la regex
        return
    assert r.status_code == 400 and r.json()["field"] == "dia", (dia, r.text)


def test_revista_dia_registra_si_hace_falta_y_devuelve_el_md_con_su_html(cl):
    cl.db.execute("DELETE FROM library_items WHERE titulo LIKE 'Revista%'")
    cl.db.commit()
    a = cl.get("/api/library/revista/2026-10-29", headers=H)
    assert a.status_code == 200
    fila = a.json()
    assert fila["clase"] == "revista" and fila["ruta"].endswith("2026-10-29.md") and fila["html_publicado"]
    assert cl.get("/api/library/revista/2026-10-29", headers=H).json()["id"] == fila["id"]
    s = cl.get("/api/library/revista/semana-2026-43", headers=H)
    assert s.status_code == 200 and s.json()["fecha"] == "2026-10-23"
    assert cl.get("/api/library/revista/2031-01-01", headers=H).status_code == 404


# --------------------------------------------------------------- MCP (Spec §6)

def test_mcp_library_tools_existen_y_prohiben_argumentos_desconocidos():
    tools = {t.name: t for t in mcp_server.mcp._tool_manager.list_tools()}
    assert {"library_search", "library_get", "library_add"} <= set(tools)
    assert all(t.fn_metadata.arg_model.model_config.get("extra") == "forbid" for t in tools.values())
    with pytest.raises(Exception) as e:
        asyncio.run(mcp_server.mcp.call_tool("library_add", {"ruta": "x", "clase": "faro", "inventado": 1}))
    assert "inventado" in str(e.value)


def test_mcp_library_son_clientes_finos(monkeypatch):
    hechas = []
    monkeypatch.setattr(mcp_server, "_call", lambda m, p, **kw: hechas.append((m, p, kw)) or {"ok": True})
    asyncio.run(mcp_server.mcp.call_tool("library_search", {"q": "algo", "clase": "faro"}))
    asyncio.run(mcp_server.mcp.call_tool("library_get", {"id": 7}))
    asyncio.run(mcp_server.mcp.call_tool("library_get", {"id": 7, "max_chars": 10**6}))
    asyncio.run(mcp_server.mcp.call_tool("library_get", {"id": 7, "max_chars": 5}))
    asyncio.run(mcp_server.mcp.call_tool("library_add", {"ruta": "r", "clase": "faro"}))
    asyncio.run(mcp_server.mcp.call_tool("library_add", {"ruta": "r", "clase": "faro", "ticket": 3}))
    assert hechas == [
        ("GET", "/api/library/items", {"params": {"q": "algo", "clase": "faro", "limite": 20}}),
        ("GET", "/api/library/items/7/content", {"params": {"formato": "texto", "max": 8000}}),
        ("GET", "/api/library/items/7/content", {"params": {"formato": "texto", "max": 8000}}),   # tope de 8000
        ("GET", "/api/library/items/7/content", {"params": {"formato": "texto", "max": 5}}),
        ("POST", "/api/library/items", {"json": {"ruta": "r", "clase": "faro"}}),
        ("POST", "/api/library/items", {"json": {"ruta": "r", "clase": "faro", "ticket": 3}}),
    ]


def test_el_detalle_lleva_los_enlaces_a_tickets_jobs_y_runs(cl):
    i = _id(cl, titulo="Informe uno")
    assert cl.get(f"/api/library/items/{i}", headers=H).json()["enlaces"] == []
    L.registrar(cl.mundo.conn, cl.mundo.ses / "s1" / "informe.md", env=cl.mundo.env, ticket_id=7)
    L.registrar(cl.mundo.conn, cl.mundo.ses / "s1" / "informe.md", env=cl.mundo.env, ticket_id=8, job_id=2, run_id=3)
    enl = cl.get(f"/api/library/items/{i}", headers=H).json()["enlaces"]
    assert enl == [{"ticket_id": 7, "job_id": None, "run_id": None}, {"ticket_id": 8, "job_id": 2, "run_id": 3}]
    assert "enlaces" not in cl.get("/api/library/items?limite=1", headers=H).json()[0]


@pytest.mark.parametrize("ticket", [2**63, 10**30, 2**63 - 1 + 1])
def test_vt9_ticket_desmesurado_es_400_no_500(cl, ticket):
    f = _w(cl.mundo.ses / "s1" / "gordo.md", "# x")
    r = cl.post("/api/library/items", headers=H, json={"ruta": str(f), "clase": "faro", "ticket": ticket})
    assert r.status_code == 400 and r.json()["field"] == "ticket" and r.json()["code"] == "ticket.invalid"


@pytest.mark.parametrize("i", ["9223372036854775807", "9223372036854775808", "99999999999999999999", "0", "-1"])
def test_id_fuera_de_rango_es_el_mismo_403(cl, i):
    esperado = cl.get("/api/library/items/987654", headers=H)
    assert esperado.status_code == 403
    for ruta in (f"/api/library/items/{i}", f"/api/library/items/{i}/content", f"/api/library/items/{i}/content?formato=texto"):
        r = cl.get(ruta, headers=H)
        assert r.status_code == 403 and r.text == esperado.text, (ruta, r.status_code, r.text)

import json as _json  # noqa: E402
from test_850_library import TOKEN as _T  # noqa: E402,F401


# --------------------------------------------------------------- lote final de revisión (entorno vivo, paginación, supervivientes)

def test_una_raiz_retirada_deja_de_servir_sin_reiniciar(mundo, tmp_path, monkeypatch):
    casa = tmp_path / "casa"
    (casa / "biblioteca").mkdir(parents=True)
    monkeypatch.setenv("FAROS_HOME", str(casa))
    monkeypatch.setattr(api, "load_token", lambda: TOKEN)
    L._cache_entorno.update(clave=None, env=None)
    rj = casa / "biblioteca" / "raices.json"
    rj.write_text(mundo.rj.read_text(encoding="utf-8"), encoding="utf-8")
    c = sqlite3.connect(str(mundo.t / "faros.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    cliente = TestClient(api.create_app(c))                      # SIN biblioteca=: el entorno de la casa, vivo
    env = L.entorno_de_la_casa()
    env.publicado_dir = mundo.env.publicado_dir
    L.recorrer(mundo.conn, env)
    i = _filas(mundo.conn, titulo="Informe uno")[0]["id"]
    assert cliente.get(f"/api/library/items/{i}", headers=H).status_code == 200
    quedan = [r for r in _json.loads(rj.read_text(encoding="utf-8")) if not r["ruta"].endswith("Sesiones")]
    rj.write_text(_json.dumps(quedan), encoding="utf-8")
    assert cliente.get(f"/api/library/items/{i}", headers=H).status_code == 403
    assert cliente.get(f"/api/library/items/{i}/content", headers=H).status_code == 403
    assert i not in [f["id"] for f in cliente.get("/api/library/items?limite=500", headers=H).json()]
    rj.write_text("{roto", encoding="utf-8")                      # ilegible: se queda con las últimas buenas
    assert cliente.get(f"/api/library/items/{i}", headers=H).status_code == 403
    c.close()


def test_limite_y_offset_fuera_de_rango_son_400(cl):
    for params, campo in (({"limite": 0}, "limite"), ({"limite": 501}, "limite"), ({"limite": -3}, "limite"),
                          ({"offset": -1}, "offset"), ({"offset": 2**63}, "offset")):
        r = cl.get("/api/library/items", headers=H, params=params)
        assert r.status_code == 400 and r.json()["field"] == campo, (params, r.text)
    assert cl.get("/api/library/items", headers=H, params={"limite": 500, "offset": 0}).status_code == 200
    assert cl.get("/api/library/items", headers=H, params={"offset": "x"}).status_code == 422


def test_paginacion_sin_repetidos_ni_huecos_aunque_haya_filas_viejas(cl):
    ses = cl.mundo.ses
    for i in range(120):
        _w(ses / "paginas" / f"p{i:03d}.md", f"# Pagina {i}\n\nmigraciones")
    L.recorrer(cl.mundo.conn, cl.mundo.env)
    # filas viejas repartidas: ya no pasan permitida (la ruta se sale de las raíces)
    ids = [r["id"] for r in cl.db.execute("SELECT id FROM library_items WHERE ruta LIKE '%paginas%' ORDER BY id")]
    for i in ids[::3]:
        cl.db.execute("UPDATE library_items SET ruta=? WHERE id=?", (str(cl.mundo.t / "fuera" / f"{i}.md"), i))
    cl.db.commit()
    for params in ({}, {"clase": "faro"}, {"q": "migraciones"}):
        vistos, offset = [], 0
        while True:
            pag = cl.get("/api/library/items", headers=H, params={**params, "limite": 7, "offset": offset}).json()
            vistos += [f["id"] for f in pag]
            if len(pag) < 7:
                break                                              # página corta = final
            offset += 7
        todo = [f["id"] for f in cl.get("/api/library/items", headers=H, params={**params, "limite": 500}).json()]
        assert vistos == todo and len(set(vistos)) == len(vistos), params
        assert not set(vistos) & set(ids[::3])
    assert len(vistos) > 50


def test_q_se_corta_a_200_caracteres(cl, monkeypatch):
    vistos = []
    real = L.buscar
    monkeypatch.setattr(L, "buscar", lambda conn, q=None, **k: vistos.append(q) or real(conn, q, **k))
    cl.get("/api/library/items", headers=H, params={"q": "migraciones " * 400})
    assert len(vistos[0]) == 200


def test_abrir_exige_interactivo_y_copia_en_disco(cl):
    viva = _filas(cl.mundo.conn, titulo="Página Viva ñandú")[0]
    assert cl.get(f"/api/library/items/{viva['id']}", headers=H).json()["abrir"]
    (cl.mundo.env.publicado_dir / viva["publicado"]).unlink()                      # is_file
    assert cl.get(f"/api/library/items/{viva['id']}", headers=H).json()["abrir"] is None
    info = _filas(cl.mundo.conn, titulo="Informe uno")[0]
    cl.mundo.env.publicado_dir.mkdir(parents=True, exist_ok=True)
    (cl.mundo.env.publicado_dir / "5-falso.html").write_text("x")
    cl.db.execute("UPDATE library_items SET publicado='5-falso.html' WHERE id=?", (info["id"],))   # interactivo = 0
    cl.db.commit()
    assert cl.get(f"/api/library/items/{info['id']}", headers=H).json()["abrir"] is None


def test_html_publicado_con_enlace_a_otra_carpeta_no_se_sirve(cl):
    import os
    i = L.registrar(cl.mundo.conn, cl.mundo.rev / "2026-10-29.md", env=cl.mundo.env)
    fuera = _w(cl.mundo.t / "fuera_rev" / "x.html", "<html>DE FUERA</html>")
    destino = cl.mundo.env.html_revista_dir / "revista-2026-10-29.html"
    destino.unlink()
    try:
        os.symlink(fuera, destino)
    except OSError:
        pytest.skip("sin permiso para symlinks")
    cl.db.execute("UPDATE library_items SET html_publicado=? WHERE id=?", (str(destino), i))
    cl.db.commit()
    r = cl.get(f"/api/library/items/{i}/content", headers=H)
    assert r.status_code == 200 and "DE FUERA" not in r.text
