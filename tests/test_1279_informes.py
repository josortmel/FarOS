"""#1279: la Biblioteca tiene la clase «informe» (los informes para Pepe en Documents/Informes, una carpeta por
informe). Esquema 14: el CHECK de library_items.clase la admite; una base v13 se reconstruye conservando ids,
texto buscable y enlaces, con copia previa."""

import json
import sqlite3

from faros import db, library as L

CLASES_V13 = "('revista','lectura','faro','resultado','documentacion')"


def _v13(path):
    """Una base v13 de verdad: library_items con el CHECK de antes, una fila con su texto y su enlace."""
    conn = db.connect(path)
    viejo = db.LIBRARY_ITEMS_V14.replace(CLASES_V13[:-1] + ",'informe')", CLASES_V13)
    assert "'informe'" not in viejo
    conn.execute("PRAGMA foreign_keys = OFF")
    db._rebuild(conn, "library_items", viejo)
    conn.execute("INSERT INTO library_items (id, clase, ruta, titulo, origen, creado, actualizado)"
                 " VALUES (1470, 'faro', 'C:/x/Informes/a.html', 'Análisis Urquijo', 'scan', '2026-11-16', '2026-11-16')")
    conn.execute("INSERT INTO library_fts (rowid, titulo, extracto) VALUES (1470, 'Análisis Urquijo', 'fondo de private equity')")
    conn.execute("INSERT INTO library_links (item_id, ticket_id) VALUES (1470, 1279)")
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version VALUES (13, '2026-11-11T00:00:00')")
    conn.commit()
    conn.close()


def test_una_base_v13_migra_a_14_conservando_todo_y_con_copia(tmp_path):
    path = tmp_path / "faros.db"
    _v13(path)
    conn = db.connect(path)
    assert db.schema_version(conn) == db.SCHEMA_VERSION  # v14 de #1279; desde v15 (#1098) sigue migrando hasta la última
    fila = conn.execute("SELECT id, clase, titulo FROM library_items").fetchone()
    assert tuple(fila) == (1470, "faro", "Análisis Urquijo")
    assert conn.execute("SELECT rowid FROM library_fts WHERE library_fts MATCH 'equity'").fetchone()[0] == 1470
    assert conn.execute("SELECT ticket_id FROM library_links WHERE item_id = 1470").fetchone()[0] == 1279
    conn.execute("UPDATE library_items SET clase = 'informe' WHERE id = 1470")   # antes: CHECK constraint failed
    assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
    copias = sorted((tmp_path / "backups").glob("faros_pre_migration_*.db"))
    assert len(copias) == 1
    c = sqlite3.connect(copias[0].resolve().as_uri() + "?mode=ro", uri=True)
    assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 13
    c.close()


def test_una_base_nueva_ya_admite_informe(tmp_path):
    conn = db.connect(tmp_path / "faros.db")
    conn.execute("INSERT INTO library_items (clase, ruta, titulo, origen, creado, actualizado)"
                 " VALUES ('informe', 'x', 'x', 'scan', 'h', 'h')")
    conn.close()
    assert not list((tmp_path / "backups").glob("*")) if (tmp_path / "backups").exists() else True


def test_un_informe_interactivo_se_publica_para_abrirlo_aparte_como_los_de_faro(tmp_path):
    informes = tmp_path / "Informes"
    viva = informes / "2026-11-15_fondo" / "Analisis.html"
    viva.parent.mkdir(parents=True)
    viva.write_text("<html><head><title>Análisis vivo</title></head><body><script>1</script></body></html>",
                    encoding="utf-8")
    rj = tmp_path / "raices.json"
    rj.write_text(json.dumps([{"clase": "informe", "ruta": str(informes), "tipo": "carpeta",
                               "patrones": ["*.html", "*.md"], "recursivo": True}]), encoding="utf-8")
    env = L.Entorno(raices=L.cargar_raices(rj), publicado_dir=tmp_path / "pub", token="TOKEN-DE-PRUEBA-0123456789")
    conn = db.connect(tmp_path / "faros.db")
    i = L.registrar(conn, viva, env=env)
    fila = conn.execute("SELECT clase, interactivo, publicado FROM library_items WHERE id=?", (i,)).fetchone()
    assert fila["clase"] == "informe" and fila["interactivo"] == 1
    assert fila["publicado"] and (env.publicado_dir / fila["publicado"]).read_text(encoding="utf-8").count("<script>") == 1
    conn.close()


def test_un_informe_en_su_subcarpeta_entra_con_clase_informe_y_lo_de_fuera_no(tmp_path):
    informes = tmp_path / "Informes"
    dentro = informes / "2026-11-17_bot_telegram" / "informe.md"
    dentro.parent.mkdir(parents=True)
    dentro.write_text("# El bot de FarOS\n\nlo que hace", encoding="utf-8")
    html = informes / "2026-11-15_fondo" / "Analisis.html"
    html.parent.mkdir(parents=True)
    html.write_text("<html><head><title>Análisis del fondo</title></head><body>fondo</body></html>", encoding="utf-8")
    fuera = tmp_path / "Otros" / "suelto.md"
    fuera.parent.mkdir(parents=True)
    fuera.write_text("# Suelto", encoding="utf-8")
    (informes / "2026-11-17_bot_telegram" / "datos.json").write_text("{}", encoding="utf-8")
    rj = tmp_path / "raices.json"
    rj.write_text(json.dumps([{"clase": "informe", "ruta": str(informes), "tipo": "carpeta",
                               "patrones": ["*.html", "*.md"], "recursivo": True}]), encoding="utf-8")
    raices = L.cargar_raices(rj)
    assert [r["clase"] for r in raices] == ["informe"]
    env = L.Entorno(raices=raices, publicado_dir=tmp_path / "pub")
    conn = db.connect(tmp_path / "faros.db")
    cede, L.CEDER_S = L.CEDER_S, 0.0
    try:
        L.recorrer(conn, env)
    finally:
        L.CEDER_S = cede
    filas = {r["ruta"]: r["clase"] for r in conn.execute("SELECT ruta, clase FROM library_items")}
    assert set(filas.values()) == {"informe"}
    assert sorted(p.lower() for p in filas) == sorted(str(p).lower() for p in (dentro, html))
    assert [r["titulo"] for r in L.buscar(conn, clase="informe", q="fondo")] == ["Análisis del fondo"]
    assert not L.permitida(fuera, raices)
    conn.close()
