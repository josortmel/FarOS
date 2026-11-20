"""#1076 (verificación #857 de Prima, criterio 4): buscar() pone primero la entrada cuyo título ES la
consulta, después las que tienen TODAS las palabras y al final las que tienen alguna."""

import pytest

from faros import db
from faros import library as L


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _item(conn, ruta, titulo, extracto):
    cur = conn.execute(
        "INSERT INTO library_items (clase, ruta, titulo, origen, creado, actualizado)"
        " VALUES ('faro', ?, ?, 'scan', '2026-11-10', '2026-11-10')",
        (ruta, titulo),
    )
    conn.execute(
        "INSERT INTO library_fts (rowid, titulo, extracto) VALUES (?, ?, ?)", (cur.lastrowid, titulo, extracto)
    )
    return cur.lastrowid


def test_el_titulo_exacto_va_primero_aunque_otro_repita_las_palabras(conn):
    titulo = "KnowTwin — Execution Plan v4 (9-field schema)"
    _item(conn, "s/orchestration.md", "orchestration", ("KnowTwin execution plan v4 9-field schema " * 20))
    plan = _item(conn, "s/plan.md", titulo, "el plan")
    assert L.buscar(conn, titulo)[0]["id"] == plan


def test_las_que_tienen_todas_las_palabras_van_antes_que_las_de_alguna(conn):
    alguna = _item(conn, "a.md", "nota", "horario " * 50)
    todas = _item(conn, "b.md", "otra nota", "horario tienda")
    ids = [r["id"] for r in L.buscar(conn, "horario tienda")]
    assert ids == [todas, alguna]


def test_paginacion_sobre_los_dos_tramos(conn):
    a = _item(conn, "a.md", "uno", "horario tienda")
    b = _item(conn, "b.md", "dos", "horario")
    assert [r["id"] for r in L.buscar(conn, "horario tienda", limite=1)] == [a]
    assert [r["id"] for r in L.buscar(conn, "horario tienda", limite=1, offset=1)] == [b]


# ---- criterio 11: la documentación de un proyecto, primero cuando la consulta es su nombre ----

from pathlib import Path  # noqa: E402


def test_el_proyecto_sale_de_la_carpeta_o_de_la_raiz():
    assert L._proyecto_de({"clase": "documentacion"}, "/x/MiRepo/CLAUDE.md") == "MiRepo"
    assert L._proyecto_de({"clase": "documentacion", "proyecto": "Otro"}, "/x/MiRepo/CLAUDE.md") == "Otro"
    assert L._proyecto_de({"clase": "faro"}, "/x/MiRepo/informe.md") is None


def test_la_raiz_lleva_su_proyecto_desde_raices_json(tmp_path):
    f = tmp_path / "raices.json"
    f.write_text(
        '[{"clase": "documentacion", "ruta": "%s", "tipo": "fichero", "proyecto": "Faro"}]'
        % (tmp_path / "doc.md").as_posix(),
        encoding="utf-8",
    )
    assert L.cargar_raices(f)[0]["proyecto"] == "Faro"


def test_rellenar_y_su_claude_md_va_delante_de_los_informes_que_lo_nombran(conn):
    informe = _item(conn, "informes/x.md", "informe", "MiRepo " * 40)
    conn.execute(
        "UPDATE library_items SET clase = 'documentacion' WHERE id = ?",
        (_item(conn, "/x/mirepo/claude.md", "Notas del repo", "MiRepo una vez"),),
    )
    env = L.Entorno(raices=[], publicado_dir=Path("."))
    assert L.rellenar_proyectos(conn, env) == 1
    ids = [r["id"] for r in L.buscar(conn, "MiRepo")]
    assert ids[0] != informe and ids[-1] == informe


def test_1124_el_proyecto_de_la_raiz_manda_aunque_la_fila_ya_tenga_el_de_la_carpeta(conn, tmp_path):
    ruta = (tmp_path / "Documentacion" / "manual.md").as_posix()
    conn.execute(
        "UPDATE library_items SET clase = 'documentacion' WHERE id = ?",
        (_item(conn, L._norm(ruta), "Manual", "texto"),),
    )
    sin = L.Entorno(raices=[{"clase": "documentacion", "ruta": ruta, "tipo": "fichero"}], publicado_dir=Path("."))
    assert L.rellenar_proyectos(conn, sin) == 1
    assert conn.execute("SELECT proyecto FROM library_items").fetchone()["proyecto"] == L._proyecto_de(
        {"clase": "documentacion"}, L._norm(ruta)
    )
    con = L.Entorno(
        raices=[{"clase": "documentacion", "ruta": ruta, "tipo": "fichero", "proyecto": "faro"}],
        publicado_dir=Path("."),
    )
    assert L.rellenar_proyectos(conn, con) == 1
    assert conn.execute("SELECT proyecto FROM library_items").fetchone()["proyecto"] == "faro"
    assert L.rellenar_proyectos(conn, con) == 0  # sin cambios no reescribe
    assert L.rellenar_proyectos(conn, sin) == 0  # quitar el proyecto de la raíz no lo borra


# ---- criterio 12: las carpetas run_<id> que entraron sin enlace se enlazan con su job y su ticket ----


def test_enlazar_runs_usa_la_tabla_runs(conn):
    t = None  # un run sin tarea: el enlace lleva su job y su run
    job = conn.execute(
        "INSERT INTO agent_jobs (name, harness, model, prompt, ticket_id, created_at) VALUES ('j', 'claude-cli', 'haiku', 'p', ?, 'x')",
        (t,),
    ).lastrowid
    run = conn.execute(
        "INSERT INTO runs (job_id, ticket_id, status, started_at) VALUES (?, ?, 'ok', 'x')", (job, t)
    ).lastrowid
    item = conn.execute(
        "INSERT INTO library_items (clase, ruta, titulo, origen, creado, actualizado)"
        " VALUES ('resultado', ?, 'run', 'scan', 'x', 'x')",
        (f"c:/artifacts/run_{run}",),
    ).lastrowid
    assert L.enlazar_runs(conn) == 1
    assert tuple(
        conn.execute("SELECT job_id, ticket_id, run_id FROM library_links WHERE item_id = ?", (item,)).fetchone()
    ) == (job, t, run)
    assert L.enlazar_runs(conn) == 0  # no se repite
