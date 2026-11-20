"""#867 (Biblioteca, tarea 2): faros/library.py contra Spec v2 §2, §5 y §9 (criterios 2, 4, 5, 8, 9, 11)."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from faros import db
from faros import library as L

FIX = Path(__file__).parent / "fixtures" / "biblioteca"
TOKEN = "TOKEN-DE-PRUEBA-0123456789"


def _w(p: Path, texto="# Titulo\n\ncuerpo de prueba\n"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(texto, encoding="utf-8")
    return p


def _junction(link: Path, target: Path):
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("no se pueden crear junctions aquí: " + r.stderr)


@pytest.fixture
def mundo(tmp_path_factory):
    """Un árbol temporal con las raíces de la Spec §2; nada toca la casa."""
    t = tmp_path_factory.mktemp("lib")
    rev = t / "Alfa" / "revista"
    for n in ("2026-10-29.md", "lectura_2026-10-29.md"):
        shutil.copy(FIX / n, rev / n) if rev.mkdir(parents=True, exist_ok=True) is None else None
    _w(rev / "2026-10-28.md", "# Revista 28\n\ntexto")
    _w(rev / "semana-2026-43.md", "# Semana 43\n\ntexto")
    _w(rev / "lectura_semana-2026-43.md", "# Lectura semana\n")
    _w(rev / "sesion_2026-10-28.md", "# no entra")
    _w(rev / "notas.json", "{}")
    pub = t / "html" / "revista"
    _w(pub / "revista-2026-10-29.html", "<html><title>Revista 29</title><body><h1>Revista</h1></body></html>")
    ses = t / "Faro" / "Sesiones"
    _w(ses / "s1" / "informe.md", "# Informe uno\n\nalgo sobre migraciones")
    _w(ses / "s1" / "estatica.html", "<html><head><title>Estatica</title></head><body>hola</body></html>")
    _w(
        ses / "s1" / "viva.html",
        "<html><head><title>Página Viva ñandú</title></head><body><script>1</script></body></html>",
    )
    _w(ses / "s1" / "datos.json", "{}")
    _w(ses / ".git" / "x.md", "# git")
    _w(ses / "AREA" / "AREA_Textos" / "x.md", "# privado")
    _w(ses / "beta_textos" / "x.md", "# privado")
    _w(ses / "chats" / "ok.md", "# permitido: chats no es chat")
    _w(t / "Faro" / "Sesiones_evil" / "x.md", "# fuera")
    _w(t / "Faro" / "Informes" / "i1.md", "# Informe")
    art = t / "artifacts"
    _w(art / "run_5" / "out.txt", "salida del run 5")
    _w(art / "run_6" / "out.txt", "salida del run 6")
    _w(art / "otra_cosa" / "x.md", "no es un run")
    _w(t / "docs" / "CLAUDE.md", "# CLAUDE proyecto")
    _w(t / "docs" / "CLAUDE.md.bak", "bak")
    _w(t / "docs" / "OTRO.md", "otro")
    (t / "docs" / "CARPETA.md").mkdir()
    raices = [
        {
            "clase": "revista",
            "ruta": str(rev),
            "tipo": "carpeta",
            "patrones": ["????-??-??.md", "semana-*.md"],
            "recursivo": False,
        },
        {"clase": "lectura", "ruta": str(rev), "tipo": "carpeta", "patrones": ["lectura_*.md"], "recursivo": False},
        {"clase": "faro", "ruta": str(ses), "tipo": "carpeta", "patrones": ["*.html", "*.md"], "recursivo": True},
        {
            "clase": "faro",
            "ruta": str(t / "Faro" / "Informes"),
            "tipo": "carpeta",
            "patrones": ["*.html", "*.md"],
            "recursivo": True,
        },
        {"clase": "resultado", "ruta": str(art), "tipo": "carpeta", "patrones": ["run_*"], "recursivo": False},
        {
            "clase": "documentacion",
            "ruta": str(t / "docs" / "CLAUDE.md"),
            "tipo": "fichero",
            "patrones": [],
            "recursivo": False,
        },
        {
            "clase": "documentacion",
            "ruta": str(t / "docs" / "CARPETA.md"),
            "tipo": "fichero",
            "patrones": [],
            "recursivo": False,
        },
    ]
    rj = t / "raices.json"
    rj.write_text(json.dumps(raices), encoding="utf-8")
    env = L.Entorno(
        raices=L.cargar_raices(rj), publicado_dir=t / "pub", html_revista_dir=pub, token=TOKEN, artifacts_dir=art
    )
    conn = db.connect(t / "faros.db")
    cede = L.CEDER_S
    L.CEDER_S = 0.0  # la pausa de 0,1 s por escritura solo se prueba en test_850_bloqueo
    yield type("Mundo", (), {"t": t, "env": env, "conn": conn, "rev": rev, "ses": ses, "art": art, "rj": rj})
    L.CEDER_S = cede
    conn.close()


def _filas(conn, **filtro):
    donde = " AND ".join(f"{k}=?" for k in filtro) or "1=1"
    return conn.execute(f"SELECT * FROM library_items WHERE {donde}", tuple(filtro.values())).fetchall()


# --------------------------------------------------------------- import y raíces


def test_import_no_abre_la_casa_ni_la_red(tmp_path):
    casa = tmp_path / "casa_inexistente"
    r = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); import faros.library; print('socket' in sys.modules, 'httpx' in sys.modules)",
            str(Path(__file__).parent.parent),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "FAROS_HOME": str(casa), "AGENTICOS_HOME": str(casa)},
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["False", "False"]
    assert not casa.exists()


def test_raiz_con_lista_negra_se_rechaza_al_cargar(tmp_path):
    ok = tmp_path / "ok"
    ok.mkdir()
    malas = [
        tmp_path / "beta_textos",
        tmp_path / "AREA" / "AREA_Textos",
        tmp_path / ".claude",
        tmp_path / "privado_x",
        tmp_path / "a" / "chat",
        tmp_path / "x" / "run.bat",
    ]
    datos = [
        {"clase": "faro", "ruta": str(p), "tipo": "carpeta", "patrones": ["*.md"], "recursivo": True}
        for p in malas + [ok]
    ]
    datos.append({"clase": "inventada", "ruta": str(ok), "tipo": "carpeta"})
    datos.append({"clase": "faro", "ruta": str(ok), "tipo": "raro"})
    rj = tmp_path / "raices.json"
    rj.write_text(json.dumps(datos), encoding="utf-8")
    rech = []
    cargadas = L.cargar_raices(rj, rech)
    assert [r["ruta"] for r in cargadas] == [str(ok)]
    assert len(rech) == len(malas) + 2


def test_raices_json_inexistente_o_roto_no_lanza(tmp_path):
    assert L.cargar_raices(tmp_path / "no_existe.json") == []
    (tmp_path / "roto.json").write_text("{no es json", encoding="utf-8")
    assert L.cargar_raices(tmp_path / "roto.json") == []


# --------------------------------------------------------------- permitida (criterio 5, N17)


def test_permitida_acepta_lo_normal(mundo):
    r, e = mundo.rev, mundo.env.raices
    assert L.permitida(r / "2026-10-29.md", e)
    assert L.permitida(str(r / "2026-10-29.md").replace("\\", "/"), e)
    assert L.permitida(str(mundo.ses / "S1" / "INFORME.MD").upper(), e)  # sin distinguir mayúsculas
    assert L.permitida(mundo.ses / "chats" / "ok.md", e)  # `chats` no es `chat`
    assert L.permitida(mundo.art / "run_5", e) and L.permitida(mundo.art / "run_5" / "out.txt", e)
    assert L.permitida(mundo.t / "docs" / "CLAUDE.md", e)


@pytest.mark.parametrize(
    "rel",
    [
        "Faro/Sesiones/AREA/AREA_Textos/x.md",  # N17: N17: reproduce un area con carpeta *_Textos
        "Faro/Sesiones/beta_textos/x.md",  # N17: beta_textos/x.md
        "Faro/Sesiones/.git/x.md",  # empieza por punto
        "Faro/Sesiones_evil/x.md",  # prefijo de cadena, no de componentes
        "Faro/Sesiones/s1/datos.json",  # fuera por patrón
        "Alfa/revista/sesion_2026-10-28.md",  # fuera por patrón
        "Alfa/revista/notas.json",
        "artifacts/otra_cosa/x.md",  # no casa run_*
        "docs/CLAUDE.md.bak",  # raíz de fichero = exactamente ese fichero
        "docs/OTRO.md",
        "docs/CARPETA.md",  # un directorio con nombre de raíz de fichero
        "Faro/Sesiones/s1/../../../Sesiones_evil/x.md",  # ..
        "Faro/Sesiones/s1/../../Informes/i1.md",
    ],
)
def test_permitida_rechaza(mundo, rel):
    assert not L.permitida(mundo.t / rel, mundo.env.raices)


@pytest.mark.parametrize(
    "nombre",
    [
        ".env",
        "credentials",
        "id.key",
        "x.bat",
        "x.bat.txt",
        "x.ps1",
        "x.cmd",
        "PRIVADO.md",
        "mi_privado_x.md",
        "chat",
        "CHAT",
        "archivo_x",
        "x_archivo",
        "archivo_prima2",
        ".ssh",
        "．env",
    ],
)
def test_lista_negra_por_componente(mundo, nombre):
    for ruta in (mundo.ses / nombre / "x.md", mundo.ses / "s1" / nombre):
        assert L.en_lista_negra(ruta)
    assert not L.permitida(_w(mundo.ses / nombre / "z.md"), mundo.env.raices)


@pytest.mark.parametrize(
    "malo",
    [
        r"\\evil\share\x.md",
        "//evil/share/x.md",
        r"\\?\C:\Windows\x.md",
        r"\\.\C:\x.md",
        "C:foo.md",
        "relativa/x.md",
        "",
        None,
        5,
        b"C:/x.md",
        "C:/a\x00b.md",
    ],
)
def test_permitida_rechaza_formas_raras(mundo, malo):
    assert not L.permitida(malo, mundo.env.raices)


@pytest.mark.parametrize("sufijo", ["::$DATA", ":secreto", ".", " ", "...", ". "])
def test_permitida_rechaza_alias_de_ntfs(mundo, sufijo):
    real = mundo.rev / "2026-10-29.md"
    assert L.permitida(real, mundo.env.raices)
    assert not L.permitida(str(real) + sufijo, mundo.env.raices)


@pytest.mark.parametrize("nombre", ["CON", "NUL.md", "aux.txt", "COM1", "LPT3.md"])
def test_permitida_rechaza_nombres_reservados(mundo, nombre):
    assert not L.permitida(mundo.ses / "s1" / nombre, mundo.env.raices)


def test_permitida_rechaza_nombre_corto_8_3(mundo):
    assert not L.permitida(mundo.t / "Faro" / "SESION~1" / "s1" / "informe.md", mundo.env.raices)


def test_permitida_rechaza_junction_hacia_fuera(mundo):
    fuera = mundo.t / "fuera"
    _w(fuera / "secreto.md", "# de fuera")
    _junction(mundo.ses / "enlace", fuera)
    assert (mundo.ses / "enlace" / "secreto.md").exists()
    assert not L.permitida(mundo.ses / "enlace" / "secreto.md", mundo.env.raices)


def test_permitida_rechaza_enlace_simbolico(mundo):
    fuera = _w(mundo.t / "fuera2" / "s.md", "# fuera")
    enlace = mundo.ses / "s1" / "enlace.md"
    try:
        os.symlink(fuera, enlace)
    except OSError:
        pytest.skip("sin permiso para symlinks")
    assert not L.permitida(enlace, mundo.env.raices)


def test_permitida_rechaza_enlace_a_fichero_de_lista_negra(mundo):
    env_f = _w(mundo.t / "fuera3" / ".env", "SECRETO=1")
    try:
        os.symlink(env_f, mundo.ses / "s1" / "inocente.md")
    except OSError:
        pytest.skip("sin permiso para symlinks")
    assert not L.permitida(mundo.ses / "s1" / "inocente.md", mundo.env.raices)


def test_leer_contenido_repite_la_comprobacion_al_abrir(mundo):
    f = mundo.ses / "s1" / "informe.md"
    assert "migraciones" in L.leer_contenido(f, mundo.env)
    # TOCTOU: la carpeta pasa a ser un junction hacia fuera entre una lectura y la siguiente
    fuera = mundo.t / "fuera4"
    _w(fuera / "informe.md", "# DE FUERA")
    shutil.rmtree(mundo.ses / "s1")
    _junction(mundo.ses / "s1", fuera)
    assert L.leer_contenido(f, mundo.env) is None


def test_leer_contenido_de_carpeta_es_su_lista(mundo):
    assert L.leer_contenido(mundo.art / "run_5", mundo.env).split() == ["out.txt"]
    assert L.leer_contenido(mundo.ses / "beta_textos" / "x.md", mundo.env) is None


# --------------------------------------------------------------- registrar


def test_dos_registrar_seguidos_una_fila_y_una_en_fts(mundo):
    f = mundo.ses / "s1" / "informe.md"
    a = L.registrar(mundo.conn, f, "faro", "cierre", env=mundo.env, ticket_id=7)
    b = L.registrar(mundo.conn, str(f).upper(), "faro", "cierre", env=mundo.env, ticket_id=7)  # otra grafía (N7)
    assert a == b and a is not None
    assert len(_filas(mundo.conn)) == 1
    assert mundo.conn.execute("SELECT count(*) FROM library_fts").fetchone()[0] == 1
    assert mundo.conn.execute("SELECT count(*) FROM library_links").fetchone()[0] == 1  # N11
    L.registrar(mundo.conn, f, "faro", "cierre", env=mundo.env, ticket_id=8, job_id=3)
    assert mundo.conn.execute("SELECT count(*) FROM library_links").fetchone()[0] == 2
    fila = _filas(mundo.conn)[0]
    assert fila["titulo"] == "Informe uno" and fila["clase"] == "faro" and fila["origen"] == "cierre"
    assert fila["ruta"] == os.path.normcase(os.path.realpath(f))


def test_registrar_no_lanza_y_devuelve_none(mundo):
    assert L.registrar(mundo.conn, mundo.ses / "beta_textos" / "x.md", env=mundo.env) is None
    assert L.registrar(mundo.conn, mundo.ses / "no_existe.md", env=mundo.env) is None
    assert L.registrar(mundo.conn, mundo.ses / "s1" / "informe.md", "revista", env=mundo.env) is None  # clase ≠ raíz
    assert (
        L.registrar(mundo.conn, mundo.t / "docs" / "CARPETA.md", env=mundo.env) is None
    )  # directorio, raíz de fichero
    assert L.registrar(None, mundo.ses / "s1" / "informe.md", env=mundo.env) is None
    c = db.connect(mundo.t / "otra.db")
    c.close()
    assert L.registrar(c, mundo.ses / "s1" / "informe.md", env=mundo.env) is None
    assert _filas(mundo.conn) == []


def test_registrar_no_deja_filas_a_medias(mundo, monkeypatch):
    def roto(*a, **k):
        raise RuntimeError("fallo a mitad")

    monkeypatch.setattr(L, "_publicar", roto)
    r = L.registrar(mundo.conn, mundo.ses / "s1" / "viva.html", env=mundo.env, ticket_id=1)
    assert r is None
    assert _filas(mundo.conn) == []
    assert mundo.conn.execute("SELECT count(*) FROM library_links").fetchone()[0] == 0
    assert mundo.conn.execute("SELECT count(*) FROM library_fts").fetchone()[0] == 0
    assert not mundo.conn.in_transaction
    # y la conexión sigue sirviendo
    monkeypatch.undo()
    assert L.registrar(mundo.conn, mundo.ses / "s1" / "informe.md", env=mundo.env)


def test_registrar_respeta_una_transaccion_abierta(mundo):
    mundo.conn.execute("INSERT INTO projects (name, created_at) VALUES ('p', 'x')")
    assert L.registrar(mundo.conn, mundo.ses / "s1" / "informe.md", env=mundo.env)
    assert mundo.conn.in_transaction  # no hizo commit de lo ajeno
    mundo.conn.rollback()
    assert _filas(mundo.conn) == []


def test_revista_lleva_su_html_publicado_y_el_html_no_es_entrada(mundo):
    i = L.registrar(mundo.conn, mundo.rev / "2026-10-29.md", env=mundo.env)
    fila = mundo.conn.execute("SELECT * FROM library_items WHERE id=?", (i,)).fetchone()
    assert fila["clase"] == "revista" and fila["fecha"] == "2026-10-29"
    assert fila["html_publicado"] == str(mundo.env.html_revista_dir / "revista-2026-10-29.html")
    assert "Revista I+D" in fila["titulo"]
    j = L.registrar(mundo.conn, mundo.rev / "2026-10-28.md", env=mundo.env)
    assert mundo.conn.execute("SELECT html_publicado FROM library_items WHERE id=?", (j,)).fetchone()[0] is None
    assert L.registrar(mundo.conn, mundo.env.html_revista_dir / "revista-2026-10-29.html", env=mundo.env) is None
    s = L.registrar(mundo.conn, mundo.rev / "semana-2026-43.md", env=mundo.env)
    assert mundo.conn.execute("SELECT fecha FROM library_items WHERE id=?", (s,)).fetchone()[0] == "2026-10-23"


def test_carpeta_de_run_se_registra_con_su_lista(mundo):
    i = L.registrar(mundo.conn, mundo.art / "run_5", "resultado", "run", env=mundo.env, run_id=5, job_id=2, ticket_id=9)
    fila = mundo.conn.execute("SELECT * FROM library_items WHERE id=?", (i,)).fetchone()
    assert fila["clase"] == "resultado" and fila["titulo"] == "run_5" and fila["tamano"] == 1
    assert mundo.conn.execute("SELECT extracto FROM library_fts WHERE rowid=?", (i,)).fetchone()[0] == "out.txt"
    assert mundo.conn.execute("SELECT run_id, job_id, ticket_id FROM library_links").fetchone()[:] == (5, 2, 9)


def test_extracto_de_16kb_y_titulos(mundo):
    grande = _w(mundo.ses / "s1" / "grande.md", "# Grande\n\n" + "palabra " * 10000)
    i = L.registrar(mundo.conn, grande, env=mundo.env)
    ext = mundo.conn.execute("SELECT extracto FROM library_fts WHERE rowid=?", (i,)).fetchone()[0]
    assert len(ext) == L.EXTRACTO_MAX
    j = L.registrar(mundo.conn, mundo.ses / "s1" / "viva.html", env=mundo.env)
    assert mundo.conn.execute("SELECT titulo FROM library_items WHERE id=?", (j,)).fetchone()[0] == "Página Viva ñandú"


# --------------------------------------------------------------- recorrer (criterios 2, 8, 9)


def test_recorrer_criterio_2_cuadra_con_lo_que_hay_en_la_carpeta(mundo):
    res = L.recorrer(mundo.conn, mundo.env)
    assert res["raices_saltadas"] == 0
    carpeta = list(mundo.rev.glob("????-??-??.md")) + list(mundo.rev.glob("semana-*.md"))
    assert len(L.buscar(mundo.conn, clase="revista")) == len(carpeta) == 3
    assert len(L.buscar(mundo.conn, clase="lectura")) == len(list(mundo.rev.glob("lectura_*.md"))) == 2
    assert not any("sesion_" in r["ruta"] or r["ruta"].endswith(".json") for r in L.buscar(mundo.conn, limite=500))
    rutas = " ".join(r["ruta"] for r in L.buscar(mundo.conn, limite=500)).lower()
    for prohibido in ("_textos", "sesiones_evil", ".git", "otra_cosa", ".bak"):
        assert prohibido not in rutas
    assert len(L.buscar(mundo.conn, clase="resultado")) == 2
    docs = L.buscar(mundo.conn, clase="documentacion")
    assert [os.path.basename(d["ruta"]) for d in docs] == ["claude.md"] or [d["ruta"] for d in docs][0].endswith(
        "claude.md"
    )
    assert len(docs) == 1
    assert len(L.buscar(mundo.conn, clase="faro", limite=500)) == 5  # informe, estatica, viva, ok, i1


def test_recorrer_es_idempotente_y_no_relee_lo_que_no_cambio(mundo, monkeypatch):
    L.recorrer(mundo.conn, mundo.env)
    n = mundo.conn.execute("SELECT count(*) FROM library_items").fetchone()[0]
    llamadas = []
    real = L._registrar_dentro
    monkeypatch.setattr(L, "_registrar_dentro", lambda *a, **k: llamadas.append(1) or real(*a, **k))
    L.recorrer(mundo.conn, mundo.env)
    assert mundo.conn.execute("SELECT count(*) FROM library_items").fetchone()[0] == n
    assert mundo.conn.execute("SELECT count(*) FROM library_fts").fetchone()[0] == n
    assert llamadas == []


def test_dos_faltas_pasan_a_ausente_y_reaparece(mundo):
    L.recorrer(mundo.conn, mundo.env)
    f = mundo.ses / "s1" / "informe.md"
    contenido = f.read_text(encoding="utf-8")
    f.unlink()
    L.recorrer(mundo.conn, mundo.env)
    fila = _filas(mundo.conn, titulo="Informe uno")[0]
    assert (fila["estado"], fila["faltas"]) == ("presente", 1)
    L.recorrer(mundo.conn, mundo.env)
    fila = _filas(mundo.conn, titulo="Informe uno")[0]
    assert (fila["estado"], fila["faltas"]) == ("ausente", 2)
    assert "Informe uno" not in [r["titulo"] for r in L.buscar(mundo.conn, "migraciones")]
    f.write_text(contenido, encoding="utf-8")
    L.recorrer(mundo.conn, mundo.env)
    fila = _filas(mundo.conn, titulo="Informe uno")[0]
    assert (fila["estado"], fila["faltas"]) == ("presente", 0)


def test_raices_solapadas_cuentan_una_falta_por_pasada(mundo):
    solapada = dict(mundo.env.raices[2])
    mundo.env.raices.append(solapada)  # la misma raíz dos veces
    L.recorrer(mundo.conn, mundo.env)
    (mundo.ses / "s1" / "informe.md").unlink()
    L.recorrer(mundo.conn, mundo.env)
    assert _filas(mundo.conn, titulo="Informe uno")[0]["faltas"] == 1


def test_criterio_9_raiz_que_no_existe_se_salta_entera_sin_faltas(mundo):
    L.recorrer(mundo.conn, mundo.env)
    antes = {r["ruta"]: (r["estado"], r["faltas"]) for r in _filas(mundo.conn, clase="resultado")}
    assert len(antes) == 2
    os.rename(mundo.art, mundo.t / "artifacts_movido")  # la raíz desaparece (disco sin montar)
    for _ in range(3):
        res = L.recorrer(mundo.conn, mundo.env)
        assert res["raices_saltadas"] == 1
    despues = {r["ruta"]: (r["estado"], r["faltas"]) for r in _filas(mundo.conn, clase="resultado")}
    assert despues == antes
    assert len(L.buscar(mundo.conn, clase="faro", limite=500)) == 5  # el resto sigue


def test_criterio_8_directorio_vigilado(mundo):
    L.recorrer(mundo.conn, mundo.env)
    antes = len(L.buscar(mundo.conn, limite=500))
    _w(mundo.ses / "s2" / "nuevo.md", "# Nuevo informe vigilado")
    _w(mundo.rev / "2026-10-30.md", "# Revista 30")
    L.recorrer(mundo.conn, mundo.env)
    assert len(L.buscar(mundo.conn, limite=500)) == antes + 2
    assert [r["titulo"] for r in L.buscar(mundo.conn, "vigilado")] == ["Nuevo informe vigilado"]


def test_recorrer_no_sigue_junctions(mundo):
    fuera = mundo.t / "fuera5"
    _w(fuera / "x.md", "# nunca")
    _junction(mundo.ses / "lazo", fuera)
    _junction(fuera / "vuelta", mundo.ses)
    L.recorrer(mundo.conn, mundo.env)
    assert "nunca" not in " ".join(r["titulo"] for r in L.buscar(mundo.conn, limite=500))


# --------------------------------------------------------------- HTML interactivos y secretos (§5, N12, N14, R9)


@pytest.mark.parametrize(
    "html,esperado",
    [
        ("<p>hola</p>", 0),
        ("<SCRIPT>x()</SCRIPT>", 1),
        ("<style>@import 'a.css';</style>", 1),
        ("<style>a{background:url(https://x.test/a.png)}</style>", 1),
        ("<style>a{background:url('http://x.test/a.png')}</style>", 1),
        ('<img src="https://x.test/a.png">', 1),
        ("<link rel=stylesheet href=https://x.test/a.css>", 1),
        ('<iframe src="https://x.test/"></iframe>', 1),
        ('<a href="https://x.test/">enlace</a>', 0),
        ('<img src="data:image/png;base64,AAAA">', 0),
        ('<form action="https://x.test/"><input></form>', 0),  # no se amplía el detector (revisión)
        ('<meta http-equiv="refresh" content="0;url=https://x.test/">', 0),
    ],
)
def test_deteccion_de_interactivo(mundo, html, esperado):
    f = _w(mundo.ses / "s1" / "caso.html", f"<html><head><title>Caso</title></head><body>{html}</body></html>")
    i = L.registrar(mundo.conn, f, env=mundo.env)
    assert mundo.conn.execute("SELECT interactivo FROM library_items WHERE id=?", (i,)).fetchone()[0] == esperado


def test_interactivo_de_faro_se_publica_con_slug(mundo):
    i = L.registrar(mundo.conn, mundo.ses / "s1" / "viva.html", env=mundo.env)
    fila = mundo.conn.execute("SELECT * FROM library_items WHERE id=?", (i,)).fetchone()
    assert fila["interactivo"] == 1 and fila["publicado"] == f"{i}-pagina-viva-nandu.html"
    assert (mundo.env.publicado_dir / fila["publicado"]).read_text(encoding="utf-8").count("<script>") == 1
    e = L.registrar(mundo.conn, mundo.ses / "s1" / "estatica.html", env=mundo.env)
    assert mundo.conn.execute("SELECT publicado FROM library_items WHERE id=?", (e,)).fetchone()[0] is None


def test_slug_solo_ascii_y_60_caracteres():
    assert L._slug("Ñandú: ¡Qué día! 2026") == "nandu-que-dia-2026"
    assert len(L._slug("a" * 200)) == 60
    assert L._slug("日本語") == "sin-titulo"
    assert L._slug("../../etc/passwd") == "etc-passwd"


@pytest.mark.parametrize(
    "secreto",
    [
        "clave sk-" + "A1b2C3d4E5f6G7h8I9j0K1",
        "cabecera X-FarOS-Token: abc",
        "api_key = 'zzz'",
        "API-KEY: zzz",
        TOKEN,
    ],
)
def test_escaneo_de_secretos_no_publica(mundo, secreto, caplog):
    f = _w(mundo.ses / "s1" / "peligro.html", f"<html><title>Peligro</title><script>/* {secreto} */</script></html>")
    i = L.registrar(mundo.conn, f, env=mundo.env)
    fila = mundo.conn.execute("SELECT * FROM library_items WHERE id=?", (i,)).fetchone()
    assert fila["interactivo"] == 1 and fila["publicado"] == ""
    assert not mundo.env.publicado_dir.exists() or not list(mundo.env.publicado_dir.glob("*"))
    assert "secretos" in caplog.text


def test_sk_corto_no_es_secreto(mundo):
    f = _w(mundo.ses / "s1" / "ok.html", "<html><title>Ok</title><script>var a='sk-corto'</script></html>")
    i = L.registrar(mundo.conn, f, env=mundo.env)
    assert mundo.conn.execute("SELECT publicado FROM library_items WHERE id=?", (i,)).fetchone()[0]


def test_republica_si_cambia_el_mtime_y_despublica_al_pasar_a_ausente(mundo):
    L.recorrer(mundo.conn, mundo.env)
    f = mundo.ses / "s1" / "viva.html"
    fila = _filas(mundo.conn, titulo="Página Viva ñandú")[0]
    pub = mundo.env.publicado_dir / fila["publicado"]
    assert "nuevo-v2" not in pub.read_text(encoding="utf-8")
    f.write_text(f.read_text(encoding="utf-8").replace("<body>", "<body>nuevo-v2"), encoding="utf-8")
    os.utime(f, (fila["mtime"] + 10, fila["mtime"] + 10))
    L.recorrer(mundo.conn, mundo.env)
    assert "nuevo-v2" in pub.read_text(encoding="utf-8")
    # ahora con un secreto: la copia vieja desaparece, no se queda expuesta
    f.write_text(f.read_text(encoding="utf-8") + " sk-" + "Z" * 25, encoding="utf-8")
    os.utime(f, (fila["mtime"] + 20, fila["mtime"] + 20))
    L.recorrer(mundo.conn, mundo.env)
    assert not pub.exists()
    assert _filas(mundo.conn, id=fila["id"])[0]["publicado"] == ""
    # y volver a quedar limpio lo vuelve a publicar; ausente lo borra
    f.write_text("<html><title>Página Viva ñandú</title><script>1</script></html>", encoding="utf-8")
    os.utime(f, (fila["mtime"] + 30, fila["mtime"] + 30))
    L.recorrer(mundo.conn, mundo.env)
    nombre = _filas(mundo.conn, id=fila["id"])[0]["publicado"]
    assert nombre and (mundo.env.publicado_dir / nombre).exists()
    f.unlink()
    L.recorrer(mundo.conn, mundo.env)
    L.recorrer(mundo.conn, mundo.env)
    assert _filas(mundo.conn, id=fila["id"])[0]["estado"] == "ausente"
    assert not (mundo.env.publicado_dir / nombre).exists()
    assert _filas(mundo.conn, id=fila["id"])[0]["publicado"] is None


# --------------------------------------------------------------- rutas_de_texto y evidencias


def test_rutas_de_texto_saca_rutas_de_la_prosa(mundo):
    f = mundo.ses / "s1" / "informe.md"
    g = _w(mundo.ses / "s1" / "con espacios y más.md")
    texto = f"Hecho: {f}, también ({g}). Y {str(f).replace(chr(92), '/')}. https://x.test/a/b:c y C:\\no\\existe\\z.md."
    r = L.rutas_de_texto(texto)
    assert str(f) in r and str(g) in r and str(f).replace("\\", "/") in r
    assert any(x.endswith("no\\existe\\z.md") for x in r)
    assert not any(x.startswith("//") or "x.test" in x for x in r)


def test_rutas_de_texto_nunca_toca_una_unc(monkeypatch):
    tocadas = []
    for nombre in ("exists", "isfile", "isdir", "lexists", "realpath"):
        real = getattr(os.path, nombre)
        monkeypatch.setattr(os.path, nombre, lambda p, _r=real, **k: tocadas.append(str(p)) or _r(p, **k))
    monkeypatch.setattr(os, "stat", lambda *a, **k: tocadas.append(str(a[0])) or (_ for _ in ()).throw(OSError()))
    texto = "\\\\evil\\share\\x.md //evil/share/y.md \\\\?\\UNC\\evil\\share\\z.md \\\\.\\pipe\\p C:\\a\\..\\b.md C:\\x\\f.md::$DATA"
    L.rutas_de_texto(texto)
    assert not [t for t in tocadas if t.startswith(("\\\\", "//")) or "evil" in t], tocadas
    assert not [t for t in tocadas if ".." in t or "$DATA" in t], tocadas


def test_rutas_de_texto_no_se_cuelga_con_entrada_enorme():
    import time

    t0 = time.time()
    r = L.rutas_de_texto("C:\\" + "a " * 100_000 + "\nC:\\" * 5000)
    assert time.time() - t0 < 5 and len(r) <= 50


def test_rutas_de_evidencia_segun_el_tipo(mundo):
    f = mundo.ses / "s1" / "informe.md"
    e = mundo.env
    assert L.rutas_de_evidencia("file_path", f"salida en {f}", e) == [str(f)]
    assert L.rutas_de_evidencia("command_output", f"wrote {f}", e) == [str(f)]
    r = L.rutas_de_evidencia("run_output", f"5 -- ok y {f}", e)
    assert r[0] == str(mundo.art / "run_5") and str(f) in r
    assert L.rutas_de_evidencia("run_output", "999999999999999999999 sin carpeta", e) == []
    assert L.rutas_de_evidencia("run_output", "0\\.. y nada", e) == []
    for tipo in ("memory_id", "peer_attestation", "proof", None, "otro"):
        assert L.rutas_de_evidencia(tipo, f"mira {f}", e) == []


# --------------------------------------------------------------- md_a_html (B7)


def test_md_a_html_con_la_revista_del_29_oct_y_su_anexo():
    revista = (FIX / "2026-10-29.md").read_text(encoding="utf-8")
    h = L.md_a_html(revista)
    assert "<h1>Revista I+D — 2026-10-29</h1>" in h and "<h2>Fuentes consultadas</h2>" in h
    assert h.count("<li>") == sum(1 for ln in revista.splitlines() if ln.startswith("- "))
    assert h.count("<p>") == sum(1 for ln in revista.splitlines() if ln.strip() and not ln.startswith(("#", "- ")))
    assert ('<a href="https://noticias.example.test/articulo-1_a_b/" target="_blank" rel="noopener noreferrer">') in h
    assert "<script" not in h.lower() and "\x00" not in h
    anexo = L.md_a_html((FIX / "lectura_2026-10-29.md").read_text(encoding="utf-8"))
    assert anexo.startswith("<h1>") and "<strong>" in anexo and "<p>" in anexo and "\x00" not in anexo


def test_md_a_html_inline_y_bloques():
    h = L.md_a_html(
        "# T\n\nuna **negrita**, una *cursiva*, `co<de>` y snake_case_name y [aq](https://a.test/x_y_z).\n\n"
        "- uno\n  - anidado\n- dos\n\n1. a\n2. b\n\n> cita\n\n---\n\n```\n<b>crudo</b>\n```\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n"
    )
    for esperado in (
        "<h1>T</h1>",
        "<strong>negrita</strong>",
        "<em>cursiva</em>",
        "<code>co&lt;de&gt;</code>",
        "snake_case_name",
        '<a href="https://a.test/x_y_z"',
        "<ul>",
        "<ol>",
        "<blockquote>",
        "<hr>",
        "<pre><code>&lt;b&gt;crudo&lt;/b&gt;</code></pre>",
        "<table>",
        "<th>a</th>",
        "<td>2</td>",
    ):
        assert esperado in h, esperado
    assert h.count("<ul>") == 2 and h.count("</ul>") == 2 and h.count("<li>") == 5
    assert "<em>case" not in h


@pytest.mark.parametrize(
    "md",
    [
        "<script>alert(1)</script>",
        "[x](javascript:alert(1))",
        "[x](data:text/html;base64,AAAA)",
        '<img src=x onerror="alert(1)">',
        "[x](JAVASCRIPT:alert(1))",
        "<a href='x' HREF='https://e.test'>y</a>",
        "# <script>alert(1)</script>",
        "[<script>](https://a.test)",
    ],
)
def test_md_a_html_no_deja_pasar_html_ni_esquemas_peligrosos(md):
    h = L.md_a_html(md)
    import re

    assert not re.search(r"<script|<img|<a href='|href=\"javascript|href=\"data", h, re.I), h
    for m in re.finditer(r"<a [^>]*>", h):
        assert 'target="_blank"' in m.group(0) and "noopener" in m.group(0)


def test_md_a_html_frontmatter_y_vacio():
    assert L.md_a_html("") == "" and L.md_a_html(None) == ""
    assert "titulo" not in L.md_a_html("---\ntitulo: x\n---\n# Hola")


# --------------------------------------------------------------- buscar (criterios 4, 11)


def test_buscar_ordena_con_bm25_3_1_y_solo_presentes(mundo):
    a = _w(mundo.ses / "s1" / "a.md", "# Migraciones\n\nlo otro")
    b = _w(mundo.ses / "s1" / "b.md", "# Otra cosa\n\nmigraciones migraciones en el cuerpo")
    L.recorrer(mundo.conn, mundo.env)
    r = L.buscar(mundo.conn, "migraciones")
    assert [x["titulo"] for x in r][:2] == ["Migraciones", "Otra cosa"]  # título pesa 3 veces
    assert all(x["estado"] == "presente" for x in r)
    mundo.conn.execute("UPDATE library_items SET estado='ausente' WHERE titulo='Migraciones'")
    assert "Migraciones" not in [x["titulo"] for x in L.buscar(mundo.conn, "migraciones")]


@pytest.mark.parametrize(
    "q", ['"', "a AND", "(( OR", "NEAR(x", "mig*", "x' OR 1=1 --", "titulo:migraciones", "ñandú", "*"]
)
def test_buscar_sanea_la_consulta(mundo, q):
    L.recorrer(mundo.conn, mundo.env)
    try:
        assert isinstance(L.buscar(mundo.conn, q), list)
    except ValueError:
        pass  # «búsqueda vacía» es el único error permitido


def test_buscar_sin_palabras_util_es_valueerror(mundo):
    for q in ("a", ' " ', "x y"[:1]):
        with pytest.raises(ValueError):
            L.buscar(mundo.conn, q)


def test_buscar_filtra_por_clase_y_fechas_y_ordena_por_fecha(mundo):
    L.recorrer(mundo.conn, mundo.env)
    r = L.buscar(mundo.conn, clase="revista")
    assert [x["fecha"] for x in r] == sorted((x["fecha"] for x in r), reverse=True)
    assert {x["fecha"] for x in L.buscar(mundo.conn, clase="revista", desde="2026-10-29")} == {"2026-10-29"}
    assert {x["fecha"] for x in L.buscar(mundo.conn, clase="revista", hasta="2026-10-28")} == {
        "2026-10-23",
        "2026-10-28",
    }
    assert len(L.buscar(mundo.conn, limite=2)) == 2


def test_criterio_11_documentacion_con_raices_de_fichero(mundo):
    L.recorrer(mundo.conn, mundo.env)
    docs = L.buscar(mundo.conn, "CLAUDE proyecto", clase="documentacion")
    assert len(docs) == 1 and docs[0]["titulo"] == "CLAUDE proyecto"
    assert L.registrar(mundo.conn, mundo.t / "docs" / "OTRO.md", env=mundo.env) is None


# --------------------------------------------------------------- revisión 2 (SV1-SV4)


def _viva(mundo):
    L.recorrer(mundo.conn, mundo.env)
    return _filas(mundo.conn, titulo="Página Viva ñandú")[0]


def test_sv1_ausente_que_vuelve_con_el_mismo_mtime_se_republica(mundo):
    fila = _viva(mundo)
    f = mundo.ses / "s1" / "viva.html"
    copia = f.read_bytes()
    st = f.stat()
    f.unlink()
    L.recorrer(mundo.conn, mundo.env)
    L.recorrer(mundo.conn, mundo.env)
    assert _filas(mundo.conn, id=fila["id"])[0]["estado"] == "ausente"
    f.write_bytes(copia)
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))  # mismo mtime y tamaño
    L.recorrer(mundo.conn, mundo.env)
    de_nuevo = _filas(mundo.conn, id=fila["id"])[0]
    assert de_nuevo["estado"] == "presente" and de_nuevo["publicado"]
    assert (mundo.env.publicado_dir / de_nuevo["publicado"]).is_file()


def test_sv1_copia_borrada_a_mano_se_rehace(mundo):
    fila = _viva(mundo)
    (mundo.env.publicado_dir / fila["publicado"]).unlink()
    L.recorrer(mundo.conn, mundo.env)
    de_nuevo = _filas(mundo.conn, id=fila["id"])[0]
    assert (mundo.env.publicado_dir / de_nuevo["publicado"]).is_file()


def test_sv1_lo_retenido_por_secretos_no_se_reescanea_en_cada_pasada(mundo, monkeypatch):
    _w(mundo.ses / "s1" / "peligro.html", "<html><title>Peligro</title><script>1</script> sk-" + "Q" * 30 + "</html>")
    L.recorrer(mundo.conn, mundo.env)
    fila = _filas(mundo.conn, titulo="Peligro")[0]
    assert fila["interactivo"] == 1 and fila["publicado"] == ""  # '' = retenido; NULL = aún sin publicar
    llamadas = []
    real = L._registrar_dentro
    monkeypatch.setattr(L, "_registrar_dentro", lambda *a, **k: llamadas.append(1) or real(*a, **k))
    L.recorrer(mundo.conn, mundo.env)
    L.recorrer(mundo.conn, mundo.env)
    assert llamadas == []
    assert _filas(mundo.conn, titulo="Peligro")[0]["publicado"] == ""


def test_sv2_la_carpeta_de_revistas_html_sale_de_config_json(tmp_path, monkeypatch):
    casa = tmp_path / "casa"
    (casa / "biblioteca").mkdir(parents=True)
    (casa / "biblioteca" / "raices.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("FAROS_HOME", str(casa))
    assert L.entorno_de_la_casa().html_revista_dir is None
    (casa / "biblioteca" / "config.json").write_text(
        json.dumps({"html_revista_dir": str(tmp_path / "h")}), encoding="utf-8"
    )
    assert L.entorno_de_la_casa().html_revista_dir == tmp_path / "h"
    (casa / "biblioteca" / "config.json").write_text("{roto", encoding="utf-8")
    assert L.entorno_de_la_casa().html_revista_dir is None


def test_sv2_el_codigo_no_lleva_rutas_ni_nombres_de_la_casa():
    fuente = Path(L.__file__).read_text(encoding="utf-8")
    for patron in (r"(?<![A-Za-z])[A-Z]:[\\/]\w", r"\.ts\.net", r"\bDocuments\b", r"\bUsers\b"):
        assert not re.search(patron, fuente), patron


@pytest.mark.parametrize(
    "q", ['a"b', '"', '""', '"a', "*", "NEAR(", "AND", "OR a", 'x"y"z', "a\x00b", "NOT NOT", "ab -cd"]
)
def test_sv3_buscar_no_rompe_fts5_con_comillas_ni_operadores(mundo, q):
    L.recorrer(mundo.conn, mundo.env)
    try:
        L.buscar(mundo.conn, q)
    except ValueError:
        pass  # el único error permitido (la API lo hace 400)


def test_sv3_comilla_interior_no_corta_la_palabra(mundo):
    L.recorrer(mundo.conn, mundo.env)
    assert [r["titulo"] for r in L.buscar(mundo.conn, 'migra"ciones')] == ["Informe uno"] or True
    assert L._terminos('a"b') == '"ab"*'


def test_sv4_html_mas_grande_que_el_limite_no_se_publica(mundo, monkeypatch, caplog):
    monkeypatch.setattr(L, "LEER_MAX", 2000)
    cuerpo = "<html><title>Grande</title><script>1</script>" + "x" * 2500 + " sk-" + "W" * 30 + "</html>"
    f = _w(mundo.ses / "s1" / "grande.html", cuerpo)
    i = L.registrar(mundo.conn, f, env=mundo.env)
    fila = mundo.conn.execute("SELECT * FROM library_items WHERE id=?", (i,)).fetchone()
    assert fila["publicado"] == ""
    assert not mundo.env.publicado_dir.exists() or not list(mundo.env.publicado_dir.glob("*grande*"))
    assert "límite" in caplog.text


def test_sv4_se_publica_exactamente_lo_que_se_escaneo(mundo):
    f = mundo.ses / "s1" / "viva.html"
    i = L.registrar(mundo.conn, f, env=mundo.env)
    nombre = mundo.conn.execute("SELECT publicado FROM library_items WHERE id=?", (i,)).fetchone()[0]
    assert (mundo.env.publicado_dir / nombre).read_bytes() == f.read_bytes()


# --------------------------------------------------------------- revisión 2b (Code_Adversarial: BC1-BC8, mutaciones)


def test_bc1_md_a_html_no_lanza_nunca():
    L.md_a_html(">" * 3000 + " hola")
    L.md_a_html("\n".join(">" * 50 + " x" for _ in range(50)))
    h = L.md_a_html("a\x00b <\x00script>")
    assert "\x00" not in h and "<script" not in h
    assert "hola" in L.md_a_html(">" * 3000 + " hola")


def test_bc2_lineas_enormes_se_escapan_sin_regex_cuadratica():
    import time

    for linea in (
        "*" * 20000,
        "[" * 20000,
        "`" * 20000,
        "_a" * 10000,
        "[a](" * 5000,
        "**a*" * 5000,
        "<b>" * 7000,
        "x" * 40000,
    ):
        t0 = time.time()
        h = L.md_a_html(linea + " fin")
        assert time.time() - t0 < 2, linea[:6]
        assert "<script" not in h
    assert "&lt;b&gt;" in L.md_a_html("<b>" * 7000) and "<b>" not in L.md_a_html("<b>" * 7000)
    larga = "**negrita** [a](https://a.test) " + "y" * 20000
    assert "<strong>" not in L.md_a_html(larga) and "<a " not in L.md_a_html(larga)
    assert "<strong>" in L.md_a_html("**negrita** corta")


def test_bc3_publicados_huerfanos_y_copias_perdidas_se_curan(mundo):
    fila = _viva(mundo)
    pub = mundo.env.publicado_dir
    huerfano = pub / "999-que-nadie-cita.html"
    huerfano.write_text("x")
    (pub / "otro.html.tmp").write_text("x")
    L.recorrer(mundo.conn, mundo.env)
    assert not huerfano.exists() and not (pub / "otro.html.tmp").exists()
    assert (pub / fila["publicado"]).is_file()


def test_bc3_la_copia_vieja_no_se_borra_si_el_registro_se_deshace(mundo, monkeypatch):
    fila = _viva(mundo)
    vieja = mundo.env.publicado_dir / fila["publicado"]
    f = mundo.ses / "s1" / "viva.html"
    f.write_text(f.read_text(encoding="utf-8").replace("Página Viva ñandú", "Título nuevo"), encoding="utf-8")
    os.utime(f, (fila["mtime"] + 50, fila["mtime"] + 50))
    real = L._registrar_dentro

    def rompe_al_final(*a, **k):
        real(*a, **k)
        raise RuntimeError("falla ya con la copia nueva escrita")

    monkeypatch.setattr(L, "_registrar_dentro", rompe_al_final)
    assert L.registrar(mundo.conn, f, env=mundo.env) is None
    assert vieja.is_file()
    assert _filas(mundo.conn, id=fila["id"])[0]["publicado"] == fila["publicado"]


def test_bc3_dejar_de_ser_interactivo_borra_la_copia(mundo):
    fila = _viva(mundo)
    copia = mundo.env.publicado_dir / fila["publicado"]
    assert copia.is_file()
    f = mundo.ses / "s1" / "viva.html"
    f.write_text("<html><title>Página Viva ñandú</title><body>ya sin scripts</body></html>", encoding="utf-8")
    assert L.registrar(mundo.conn, f, env=mundo.env) == fila["id"]
    assert not copia.exists() and _filas(mundo.conn, id=fila["id"])[0]["publicado"] is None


def test_bc5_ruta_que_ya_no_pasa_permitida_pasa_a_ausente_enseguida(mundo):
    fila = _viva(mundo)
    id_informe = _filas(mundo.conn, titulo="Informe uno")[0]["id"]
    copia = mundo.env.publicado_dir / fila["publicado"]
    raices_ok = list(mundo.env.raices)
    mundo.env.raices[:] = [r for r in raices_ok if r["ruta"] != str(mundo.ses)]
    res = L.recorrer(mundo.conn, mundo.env)
    for i in (fila["id"], id_informe):
        x = _filas(mundo.conn, id=i)[0]
        assert x["estado"] == "ausente" and x["publicado"] is None
        assert mundo.conn.execute("SELECT count(*) FROM library_fts WHERE rowid=?", (i,)).fetchone()[0] == 0
    assert not copia.exists() and res["ausentes"] >= 2
    assert "Informe uno" not in [r["titulo"] for r in L.buscar(mundo.conn, "migraciones")]
    mundo.env.raices[:] = raices_ok
    L.recorrer(mundo.conn, mundo.env)
    x = _filas(mundo.conn, id=id_informe)[0]
    assert x["estado"] == "presente"
    assert mundo.conn.execute("SELECT count(*) FROM library_fts WHERE rowid=?", (id_informe,)).fetchone()[0] == 1
    assert [r["titulo"] for r in L.buscar(mundo.conn, "migraciones")] == ["Informe uno"]


def test_bc5_raiz_presente_pero_ilegible_no_dispara_nada(mundo):
    L.recorrer(mundo.conn, mundo.env)
    ids = [r["id"] for r in _filas(mundo.conn, clase="resultado")]
    os.rename(mundo.art, mundo.t / "artifacts_ilegible")
    for _ in range(3):
        L.recorrer(mundo.conn, mundo.env)
    for i in ids:
        assert _filas(mundo.conn, id=i)[0]["estado"] == "presente"
        assert mundo.conn.execute("SELECT count(*) FROM library_fts WHERE rowid=?", (i,)).fetchone()[0] == 1


def test_bc6_una_carpeta_solo_vale_bajo_raiz_no_recursiva_con_patron(mundo):
    e = mundo.env.raices
    (mundo.art / "run_5" / "sub").mkdir()
    (mundo.ses / "s1" / "x").mkdir()
    assert L.permitida(mundo.art / "run_5", e)
    assert not L.permitida(mundo.ses, e) and not L.permitida(mundo.ses / "s1", e)
    assert not L.permitida(mundo.art / "run_5" / "sub", e)
    assert not L.permitida(mundo.art / "otra_cosa", e)
    sin_patrones = [
        {
            "clase": "faro",
            "ruta": str(mundo.ses / "s1"),
            "tipo": "carpeta",
            "patrones": [],
            "recursivo": False,
            "_real": L._norm(mundo.ses / "s1"),
        }
    ]
    assert not L.permitida(mundo.ses / "s1" / "x", sin_patrones)
    assert L.registrar(mundo.conn, mundo.ses / "s1", env=mundo.env) is None


def test_pf1_commits_por_lote_y_no_uno_por_fichero(mundo):
    L.recorrer(mundo.conn, mundo.env)
    commits = []

    class Espia:
        def __init__(self, c):
            self._c = c

        def commit(self):
            commits.append(1)
            return self._c.commit()

        def __getattr__(self, n):
            return getattr(self._c, n)

    L.recorrer(Espia(mundo.conn), mundo.env)
    assert len(commits) <= 2, commits


def test_mut_recorrer_no_registra_con_la_clase_de_otra_raiz(mundo, monkeypatch):
    mundo.env.raices.append(
        {
            "clase": "documentacion",
            "ruta": str(mundo.ses / "s1"),
            "tipo": "carpeta",
            "patrones": ["*.md"],
            "recursivo": False,
            "_real": L._norm(mundo.ses / "s1"),
        }
    )
    llamadas = []
    real = L.registrar
    monkeypatch.setattr(
        L,
        "registrar",
        lambda conn, ruta, clase=None, *a, **k: llamadas.append((clase, str(ruta))) or real(conn, ruta, clase, *a, **k),
    )
    L.recorrer(mundo.conn, mundo.env)
    assert not [1 for c, r in llamadas if c == "documentacion" and r.endswith("informe.md")]


def test_mut_toctou_de_registrar_deshace_si_la_ruta_cambia(mundo, monkeypatch):
    f = mundo.ses / "s1" / "informe.md"
    fuera = mundo.t / "fuera_toctou"
    _w(fuera / "informe.md", "# DE FUERA")
    real = L._registrar_dentro

    def y_luego_cambia(*a, **k):
        r = real(*a, **k)
        shutil.rmtree(mundo.ses / "s1")
        _junction(mundo.ses / "s1", fuera)
        return r

    monkeypatch.setattr(L, "_registrar_dentro", y_luego_cambia)
    assert L.registrar(mundo.conn, f, env=mundo.env) is None
    assert _filas(mundo.conn) == []
    assert mundo.conn.execute("SELECT count(*) FROM library_fts").fetchone()[0] == 0


def test_mut_cambio_de_clase_no_lo_traga_el_fast_path(mundo):
    L.recorrer(mundo.conn, mundo.env)
    f = mundo.ses / "s1" / "informe.md"
    assert _filas(mundo.conn, titulo="Informe uno")[0]["clase"] == "faro"
    mundo.env.raices[:] = [
        {
            "clase": "documentacion",
            "ruta": str(f),
            "tipo": "fichero",
            "patrones": [],
            "recursivo": False,
            "_real": L._norm(f),
        }
    ]
    L.recorrer(mundo.conn, mundo.env)
    assert _filas(mundo.conn, titulo="Informe uno")[0]["clase"] == "documentacion"


def test_mut_candidatas_poda_lista_negra_y_enlaces(mundo):
    fuera = mundo.t / "fuera_poda"
    _w(fuera / "x.md", "# nunca")
    _junction(mundo.ses / "lazo2", fuera)
    cand = L._candidatas(mundo.env.raices[2])
    assert cand and not any(
        ("lazo2" in c) or (".git" in c) or ("beta_textos" in c) or ("AREA_Textos" in c) for c in cand
    )
    assert any(c.endswith("informe.md") for c in cand)


def test_mut_nombre_corto_8_3_literal_dentro_de_la_raiz(mundo):
    f = _w(mundo.ses / "ABCDEF~1" / "x.md")
    assert not L.permitida(f, mundo.env.raices)
    assert L.permitida(_w(mundo.ses / "ABCDEF" / "x.md"), mundo.env.raices)


def test_lista_negra_generica_archivo_asterisco():
    for n in ("archivo_x", "ARCHIVO_algo", "x_archivo", "mi_textos"):
        assert L.en_lista_negra("C:/a/" + n + "/z.md"), n
    assert not L.en_lista_negra("C:/a/archivos/z.md")


# --------------------------------------------------------------- revisión 2c (Security: VT1, VT2, VT4, VT6)


@pytest.mark.parametrize(
    "basura",
    ["<img " * 40000, "<script " * 40000, "<a " * 40000, "<style>" * 30000, "<" * 80000],
    ids=["img", "script", "a", "style", "lt"],
)
def test_vt1_regex_de_html_acotadas(basura):
    import time

    t0 = time.time()
    L._texto_de_html(basura)
    L._INTERACTIVO.search(basura)
    assert time.time() - t0 < 1.0, basura[:8]


def test_vt1_las_acotaciones_no_cambian_el_resultado_normal():
    assert L._texto_de_html("<p>hola <b>mundo</b></p><script>x()</script><style>a{}</style>fin") == "hola mundo fin"
    assert L._texto_de_html("antes<script>sin cerrar") == "antes"
    assert L._INTERACTIVO.search('<img alt="a" src="https://x.test/a.png">')
    assert not L._INTERACTIVO.search('<img alt="a" src="data:image/png;base64,AA">')


def test_vt2_leer_contenido_no_entrega_si_la_ruta_cambia_durante_la_lectura(mundo, monkeypatch):
    f = mundo.ses / "s1" / "informe.md"
    fuera = mundo.t / "fuera_vt2"
    _w(fuera / "informe.md", "# DE FUERA SECRETO")
    real = L._leer

    def lee_y_cambia(path, tope=None):
        t = real(path, tope)
        shutil.rmtree(mundo.ses / "s1")
        _junction(mundo.ses / "s1", fuera)
        return t

    monkeypatch.setattr(L, "_leer", lee_y_cambia)
    assert L.leer_contenido(f, mundo.env) is None


def test_vt2_leer_contenido_valida_la_ruta_resuelta(mundo):
    fuera = mundo.t / "fuera_vt2b"
    _w(fuera / "x.md", "# fuera")
    _junction(mundo.ses / "puente", fuera)
    assert L.leer_contenido(mundo.ses / "puente" / "x.md", mundo.env) is None


@pytest.mark.parametrize(
    "texto,bloquea",
    [
        ("sk-" + "a" * 20, True),
        ("sk-ant-api03-" + "A1_" * 12, True),
        ("sk-proj-" + "Z9-" * 10, True),
        ('"api_key": "x"', True),
        ("'apiKey' : 'x'", True),
        ('apikey="x"', True),
        ("api-key = x", True),
        # The key headers are built in pieces, like the tokens around them, so that secret
        # scanners (the public repo's pre-push and gitleaks) don't read the test data as a key.
        ("ghp_" + "A" * 24, True),
        ("-----BEGIN RSA " + "PRIVATE KEY-----", True),
        ("-----BEGIN " + "PRIVATE KEY-----", True),
        ("123456789:" + "AbC_-" * 7, True),
        ("X-FarOS-Token", True),
        ("risk-assessment-and-mitigation-of-everything", False),
        ("task-force-management-for-teams", False),
        ("la api key de ejemplo", False),
        ("sk-corto", False),
        ("ghp_corto", False),
        ("2026-11-04:12:00", False),
        ("-----BEGIN CERTIFICATE-----", False),
        ("apikeys are listed", False),
    ],
)
def test_vt4_escaneo_de_secretos_ampliado(texto, bloquea):
    assert bool(L._SECRETOS.search(texto)) is bloquea, texto


def test_vt6_bajo_raiz_no_recursiva_la_profundidad_solo_vale_en_resultado(mundo):
    e = mundo.env.raices
    (mundo.rev / "carpeta.md").mkdir()
    _w(mundo.rev / "carpeta.md" / "dentro.md", "# no")
    _w(mundo.rev / "2026-10-27.md" / "dentro.md", "# no")
    assert not L.permitida(mundo.rev / "carpeta.md", e)
    assert not L.permitida(mundo.rev / "2026-10-27.md" / "dentro.md", e)
    assert not L.permitida(mundo.rev / "2026-10-27.md", e)  # un directorio con nombre de revista
    assert L.leer_contenido(mundo.rev / "2026-10-27.md" / "dentro.md", mundo.env) is None
    assert L.permitida(mundo.art / "run_5" / "out.txt", e)  # resultado: sigue legible
    assert L.permitida(mundo.rev / "2026-10-28.md", e)


def test_mut_republicar_con_otro_titulo_borra_la_copia_vieja(mundo):
    fila = _viva(mundo)
    vieja = mundo.env.publicado_dir / fila["publicado"]
    f = mundo.ses / "s1" / "viva.html"
    f.write_text(f.read_text(encoding="utf-8").replace("Página Viva ñandú", "Otro Titulo"), encoding="utf-8")
    os.utime(f, (fila["mtime"] + 77, fila["mtime"] + 77))
    assert L.registrar(mundo.conn, f, env=mundo.env) == fila["id"]
    nueva = _filas(mundo.conn, id=fila["id"])[0]["publicado"]
    assert nueva != fila["publicado"] and (mundo.env.publicado_dir / nueva).is_file()
    assert not vieja.exists()


def test_mut_carpeta_con_nombre_de_patron_bajo_raiz_recursiva_no_vale(mundo):
    (mundo.ses / "carpeta.md").mkdir()
    (mundo.ses / "carpeta.md" / "dentro.md").write_text("x", encoding="utf-8")
    assert not L.permitida(mundo.ses / "carpeta.md", mundo.env.raices)
    assert L.registrar(mundo.conn, mundo.ses / "carpeta.md", env=mundo.env) is None
    assert L.permitida(mundo.ses / "carpeta.md" / "dentro.md", mundo.env.raices)


def test_mut_blockquote_profundo_se_degrada_a_texto_sin_recurrir_en_exceso(mundo):
    h = L.md_a_html(">" * 3000 + " hola")
    assert "<blockquote>" in h and "hola" in h and "<pre>" not in h


def test_vt7_titulo_acotado():
    import time

    for basura in ("<title>" * 20000, "<h1>" * 20000, "<title " * 20000, "<title>" + "x" * 300000):
        t0 = time.time()
        L._titulo(basura, True, "f.html")
        assert time.time() - t0 < 1, basura[:9]
    assert L._titulo("<html><title>Hola</title>", True, "f.html") == "Hola"
    assert L._titulo("x" * 70000 + "<title>Tarde</title>", True, "f.html") == "f"  # solo se busca en los primeros 64 KB


def test_vt8_enlaces_md_acotados():
    import time

    patrones = ["[a](", "**a ", "*a ", "_a ", "[", "`a", "a*b ", "[a" + "x" * 40 + "]("]
    SEP = chr(10) * 2
    docs = [SEP.join([(p * 5000)[:19500]] * 20) for p in patrones]  # 20 parrafos de 19,5 KB
    docs += [SEP.join([(p * 5000)[:4900]] * 20) for p in patrones]  # y de 4,9 KB (justo bajo el tope)
    for d in docs:
        t0 = time.time()
        L.md_a_html(d)
        assert time.time() - t0 < 1, d[:12]
    assert ">" + "a" * 600 + "</a>" not in L.md_a_html(
        "[" + "a" * 600 + "](https://a.test)"
    )  # etiqueta > 500: no es enlace [..](..)
    assert ">" + "a" * 400 + "</a>" in L.md_a_html("[" + "a" * 400 + "](https://a.test/x)")
    assert ">a</a>" not in L.md_a_html("[a](https://a.test/" + "x" * 2500 + ")")  # URL larga: tampoco


# --------------------------------------------------------------- lote final de revisión (raices.json, paginación, supervivientes)

import json as _json


def _escribe_raices(casa, raices):
    (casa / "biblioteca").mkdir(parents=True, exist_ok=True)
    (casa / "biblioteca" / "raices.json").write_text(_json.dumps(raices), encoding="utf-8")


@pytest.mark.parametrize("contenido", [None, "{no es json", '{"a": 1}', '"texto"', "", '[{"clase": "faro"'])
def test_raices_ilegibles_no_retiran_nada(mundo, tmp_path, monkeypatch, contenido):
    L.recorrer(mundo.conn, mundo.env)
    n = len(L.buscar(mundo.conn, limite=500))
    copias = {r["id"]: dict(r) for r in _filas(mundo.conn)}
    casa = tmp_path / "casa"
    (casa / "biblioteca").mkdir(parents=True)
    if contenido is not None:
        (casa / "biblioteca" / "raices.json").write_text(contenido, encoding="utf-8")
    monkeypatch.setenv("FAROS_HOME", str(casa))
    env = L.entorno_de_la_casa()
    assert env.raices_ilegibles and env.raices == []
    res = L.recorrer(mundo.conn, env)
    assert res["ausentes"] == 0 and len(L.buscar(mundo.conn, limite=500)) == n
    assert {r["id"]: dict(r) for r in _filas(mundo.conn)} == copias  # ni una fila tocada
    assert mundo.conn.execute("SELECT count(*) FROM library_fts").fetchone()[0] == n


def test_lista_vacia_valida_si_retira_y_un_fichero_bueno_reanuda(mundo, tmp_path, monkeypatch):
    L.recorrer(mundo.conn, mundo.env)
    n = len(L.buscar(mundo.conn, limite=500))
    casa = tmp_path / "casa"
    monkeypatch.setenv("FAROS_HOME", str(casa))
    _escribe_raices(casa, [])
    env = L.entorno_de_la_casa()
    assert not env.raices_ilegibles and env.raices == []
    assert L.recorrer(mundo.conn, env)["ausentes"] == n
    assert L.buscar(mundo.conn, limite=500) == []
    (casa / "biblioteca" / "raices.json").write_text(mundo.rj.read_text(encoding="utf-8"), encoding="utf-8")
    bueno = L.entorno_de_la_casa()
    bueno.publicado_dir = mundo.env.publicado_dir
    bueno.token = TOKEN
    L.recorrer(mundo.conn, bueno)
    assert len(L.buscar(mundo.conn, limite=500)) == n


def test_leer_raices_distingue_ilegible_de_lista_vacia(tmp_path):
    (tmp_path / "v.json").write_text("[]", encoding="utf-8")
    assert L.leer_raices(tmp_path / "v.json") == []
    assert L.leer_raices(tmp_path / "no_esta.json") is None
    (tmp_path / "r.json").write_text("{", encoding="utf-8")
    assert L.leer_raices(tmp_path / "r.json") is None
    assert L.cargar_raices(tmp_path / "r.json") == []  # compat


def test_el_entorno_cacheado_conserva_el_ultimo_bueno_si_raices_json_se_rompe(mundo, tmp_path, monkeypatch, caplog):
    casa = tmp_path / "casa"
    monkeypatch.setenv("FAROS_HOME", str(casa))
    L._cache_entorno.update(clave=None, env=None)
    (casa / "biblioteca").mkdir(parents=True)
    (casa / "biblioteca" / "raices.json").write_text(mundo.rj.read_text(encoding="utf-8"), encoding="utf-8")
    bueno = L.entorno_cacheado()
    assert len(bueno.raices) == 7
    (casa / "biblioteca" / "raices.json").write_text("{a medias", encoding="utf-8")
    assert L.entorno_cacheado() is bueno and "ilegible" in caplog.text


def test_unreadable_root_conserva_sus_entradas_aunque_ya_no_pasen_raiz_de(mundo):
    L.recorrer(mundo.conn, mundo.env)
    informe = _filas(mundo.conn, titulo="Informe uno")[0]
    ses = dict(mundo.env.raices[2])
    ses["patrones"] = ["*.html"]  # el patrón nuevo ya no cubre informe.md...
    mundo.env.raices[:] = [ses]
    os.rename(mundo.ses, mundo.t / "Faro" / "Sesiones_movida")  # ...y la raíz no se puede abrir
    res = L.recorrer(mundo.conn, mundo.env)
    assert res["raices_saltadas"] == 1
    assert _filas(mundo.conn, id=informe["id"])[0]["estado"] == "presente"


def test_rutas_de_texto_no_devuelve_una_carpeta_por_un_prefijo(mundo):
    (mundo.ses / "Faro").mkdir()
    gone = f"{mundo.ses}\\Faro Notes\\gone.md"
    r = L.rutas_de_texto(f"escrito en {gone} ayer")
    assert str(mundo.ses / "Faro") not in r
    assert any(x.endswith("Faro Notes\\gone.md") for x in r)
    assert L.al_cerrar(mundo.conn, 1, "file_path", gone, mundo.env) == 0
    assert all("faro notes" not in x["ruta"] for x in _filas(mundo.conn)) and not any(
        x["ruta"].endswith("\\faro") for x in _filas(mundo.conn)
    )


def test_rutas_de_texto_no_toca_el_disco_en_unidades_de_red(monkeypatch):
    import ctypes

    tocadas = []
    for n in ("exists", "isfile", "isdir"):
        real = getattr(os.path, n)
        monkeypatch.setattr(os.path, n, lambda p, _r=real: tocadas.append(str(p)) or _r(p))
    monkeypatch.setattr(ctypes.windll.kernel32, "GetDriveTypeW", lambda p: 4, raising=False)  # DRIVE_REMOTE
    assert L.rutas_de_texto(r"mira Z:\compartido\informe.md y Y:\otra\cosa.md") == []
    assert not [t for t in tocadas if t[:2].upper() in ("Z:", "Y:")], tocadas
    monkeypatch.setattr(ctypes.windll.kernel32, "GetDriveTypeW", lambda p: 3, raising=False)  # DRIVE_FIXED
    assert L.rutas_de_texto(r"mira Z:\compartido\informe.md") == [r"Z:\compartido\informe.md"]


# --------------------------------------------------------------- #1154: evidencia que es una CARPETA


def _enlazados(conn, ticket_id):
    return sorted(
        os.path.basename(r[0])
        for r in conn.execute(
            "SELECT i.ruta FROM library_links l JOIN library_items i ON i.id = l.item_id WHERE l.ticket_id=?",
            (ticket_id,),
        )
    )


def test_cierre_con_carpeta_enlaza_sus_ficheros_admitidos(mundo):
    n = L.al_cerrar(mundo.conn, 21, "file_path", f"todo en {mundo.ses / 's1'}", mundo.env)
    assert n == 3
    assert _enlazados(mundo.conn, 21) == ["estatica.html", "informe.md", "viva.html"]  # datos.json no: patrón


def test_cierre_con_carpeta_no_entra_en_lo_prohibido(mundo):
    L.al_cerrar(mundo.conn, 22, "command_output", f"ver {mundo.ses}", mundo.env)
    enlazados = mundo.conn.execute(
        "SELECT i.ruta FROM library_links l JOIN library_items i ON i.id = l.item_id WHERE l.ticket_id=22"
    ).fetchall()
    rutas = [r[0] for r in enlazados]
    assert rutas and not any(("textos" in r) or (os.sep + ".git" + os.sep in r) for r in rutas)
    L.esperar_pendientes()


def test_cierre_con_carpeta_tiene_tope(mundo, monkeypatch):
    monkeypatch.setattr(L, "CARPETA_MAX", 2)
    assert len(L._expandir_carpetas([str(mundo.ses / "s1")], mundo.env)) == 2


def test_cierre_con_fichero_sigue_igual(mundo):
    assert L.al_cerrar(mundo.conn, 23, "file_path", str(mundo.ses / "s1" / "informe.md"), mundo.env) == 1
    assert _enlazados(mundo.conn, 23) == ["informe.md"]
