"""#866 (Biblioteca, tarea 1): esquema v11 con copia previa forzada."""

import sqlite3

import pytest

from faros import db


def _bases_de_copia(path):
    return sorted((path.parent / "backups").glob("faros_pre_migration_*.db")) \
        if (path.parent / "backups").exists() else []


def _v10(path):
    """Una base v10 real: la actual sin las tablas de la Biblioteca y con la versión 10."""
    conn = db.connect(path)
    conn.execute("INSERT INTO projects (name, created_at) VALUES ('p', '2026-11-01')")
    for t in ("library_links", "library_items", "library_fts"):
        conn.execute(f"DROP TABLE {t}")
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version VALUES (10, '2026-10-29T00:00:00')")
    conn.commit()
    conn.close()
    for f in (path.parent / "backups").glob("*") if (path.parent / "backups").exists() else []:
        f.unlink()


def test_version_es_11():
    # >= y no ==: el v12 (#919, decisiones vigentes) va después; lo que guarda este test es que la v11 existe.
    assert db.SCHEMA_VERSION >= 11


def test_base_nueva_sin_copia(tmp_path):
    path = tmp_path / "faros.db"
    conn = db.connect(path)
    assert db.schema_version(conn) == db.SCHEMA_VERSION
    conn.close()
    assert _bases_de_copia(path) == []


def test_v10_no_estructural_migra_a_11_con_copia(tmp_path):
    path = tmp_path / "faros.db"
    _v10(path)
    conn = db.connect(path)
    assert db.schema_version(conn) == db.SCHEMA_VERSION
    assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM library_items").fetchone()[0] == 0
    assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    conn.close()
    copias = _bases_de_copia(path)
    assert len(copias) == 1
    # la copia es la v10: sin tablas de la Biblioteca, version 10
    c = sqlite3.connect(copias[0].resolve().as_uri() + "?mode=ro", uri=True)
    nombres = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
    assert "library_items" not in nombres
    assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 10
    assert c.execute("SELECT count(*) FROM projects").fetchone()[0] == 1
    c.close()


def test_reabrir_no_hace_segunda_copia(tmp_path):
    path = tmp_path / "faros.db"
    _v10(path)
    db.connect(path).close()
    db.connect(path).close()
    assert len(_bases_de_copia(path)) == 1


def _llamadas_a_backup(monkeypatch):
    """Quién llama a backup_before_migration, en orden (no cuenta ficheros: cuenta llamadas)."""
    import sys
    quien, real = [], db.backup_before_migration

    def espia(conn, path):
        quien.append(sys._getframe(1).f_code.co_name)
        return real(conn, path)
    monkeypatch.setattr(db, "backup_before_migration", espia)
    return quien


def _base_v2(path, con_version=True):
    from test_migration import SCHEMA_V2
    c = sqlite3.connect(path)
    c.executescript(SCHEMA_V2)
    if con_version:
        c.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        c.execute("INSERT INTO schema_version VALUES (2, '2026-09-01')")
    c.execute("INSERT INTO projects (id, name, created_at) VALUES (1, 'Casa', '2026-09-01')")
    c.commit()
    c.close()


def test_estructural_con_version_una_sola_llamada_y_desde_connect(tmp_path, monkeypatch):
    quien = _llamadas_a_backup(monkeypatch)
    path = tmp_path / "faros.db"
    _base_v2(path)
    db.connect(path).close()
    assert quien == ["connect"]          # la forzada; la rama estructural no repite
    assert len(_bases_de_copia(path)) == 1


def test_v0_sin_schema_version_copia_una_vez_por_la_rama_estructural(tmp_path, monkeypatch):
    quien = _llamadas_a_backup(monkeypatch)
    path = tmp_path / "faros.db"
    _base_v2(path, con_version=False)
    db.connect(path).close()
    assert quien == ["_migrate"]
    assert len(_bases_de_copia(path)) == 1


def test_base_nueva_no_llama_al_backup(tmp_path, monkeypatch):
    quien = _llamadas_a_backup(monkeypatch)
    db.connect(tmp_path / "faros.db").close()
    assert quien == []


@pytest.mark.parametrize("carpeta", ["a#b", "x%41y", "a%23b", "con espacio"])
def test_rutas_raras_leen_la_version_correcta_y_no_crean_ficheros(tmp_path, carpeta):
    d = tmp_path / carpeta
    d.mkdir()
    path = d / "faros.db"
    _v10(path)
    antes = {f.name for f in d.iterdir()}
    conn = db.connect(path)
    assert db.schema_version(conn) == db.SCHEMA_VERSION
    conn.close()
    assert len(_bases_de_copia(path)) == 1
    nuevos = {f.name for f in d.iterdir()} - antes
    assert nuevos <= {"backups", "faros.db-wal", "faros.db-shm"}, nuevos
    # y la version en disco se lee bien con esa ruta
    assert db._version_en_disco(path) == db.SCHEMA_VERSION


def test_dos_copias_en_el_mismo_instante_no_se_pisan(tmp_path, monkeypatch):
    import datetime as dt

    class Fijo(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 4, 10, 0, 0, 123456)
    monkeypatch.setattr(db, "datetime", Fijo)
    path = tmp_path / "faros.db"
    conn = db.connect(path)
    a = db.backup_before_migration(conn, path)
    b = db.backup_before_migration(conn, path)
    conn.close()
    assert a != b and a.exists() and b.exists()


def test_fallo_de_copia_aborta_con_error_claro_y_la_base_intacta(tmp_path):
    path = tmp_path / "faros.db"
    _v10(path)
    (tmp_path / "backups").write_text("soy un fichero, no una carpeta")
    with pytest.raises(db.BackupFallido) as e:
        db.connect(path)
    assert "backups" in str(e.value) and "NO se ha aplicado" in str(e.value)
    c = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 10
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='library_items'").fetchone()
    c.close()


def test_fallo_de_copia_en_la_rama_estructural(tmp_path):
    path = tmp_path / "faros.db"
    _base_v2(path, con_version=False)
    (tmp_path / "backups").write_text("x")
    with pytest.raises(db.BackupFallido, match="NO se ha aplicado"):
        db.connect(path)
    # Rama v0: executescript(SCHEMA) ya corrió (solo CREATE IF NOT EXISTS), pero nada se migró:
    # sin fila de versión y agent_jobs sigue con su forma v2.
    c = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    assert c.execute("SELECT count(*) FROM schema_version").fetchone()[0] == 0
    assert "name" not in {r[1] for r in c.execute("PRAGMA table_info(agent_jobs)")}
    assert c.execute("SELECT count(*) FROM projects").fetchone()[0] == 1
    c.close()


def test_la_copia_forzada_deja_una_linea_de_log_con_la_ruta(tmp_path, caplog):
    import logging
    path = tmp_path / "faros.db"
    _v10(path)
    with caplog.at_level(logging.INFO, logger="faros.db"):
        db.connect(path).close()
    lineas = [r.getMessage() for r in caplog.records if "copia previa" in r.getMessage()]
    assert len(lineas) == 1 and str(path) in lineas[0]


def test_estructural_solo_una_copia(tmp_path):
    from test_migration import SCHEMA_V2  # noqa: F401  (base v2 existente)
    path = tmp_path / "faros.db"
    c = sqlite3.connect(path)
    c.executescript(SCHEMA_V2)
    c.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    c.execute("INSERT INTO schema_version VALUES (2, '2026-09-01')")
    c.execute("INSERT INTO projects (id, name, created_at) VALUES (1, 'Casa', '2026-09-01')")
    c.commit()
    c.close()
    conn = db.connect(path)
    assert db.schema_version(conn) == db.SCHEMA_VERSION
    conn.close()
    assert len(_bases_de_copia(path)) == 1


def test_en_memoria_no_copia():
    conn = db.connect(":memory:")
    assert db.schema_version(conn) == db.SCHEMA_VERSION
    conn.close()


def test_indice_unico_de_links(tmp_path):
    conn = db.connect(tmp_path / "faros.db")
    conn.execute("INSERT INTO library_items (clase, ruta, titulo, origen, creado, actualizado)"
                 " VALUES ('faro', 'r', 't', 'scan', 'x', 'x')")
    for _ in range(2):
        conn.execute("INSERT OR IGNORE INTO library_links (item_id, ticket_id) VALUES (1, 5)")
        conn.execute("INSERT OR IGNORE INTO library_links (item_id, run_id) VALUES (1, NULL)")
    assert conn.execute("SELECT count(*) FROM library_links").fetchone()[0] == 2


@pytest.mark.parametrize("campo,valor", [("clase", "otra"), ("origen", "otro"), ("estado", "roto")])
def test_check_rechaza_fuera_de_lista(tmp_path, campo, valor):
    conn = db.connect(tmp_path / "faros.db")
    datos = {"clase": "faro", "origen": "scan", "estado": "presente", campo: valor}
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO library_items (clase, ruta, titulo, origen, estado, creado, actualizado)"
                     " VALUES (?, 'r', 't', ?, ?, 'x', 'x')",
                     (datos["clase"], datos["origen"], datos["estado"]))


def test_ruta_unica_y_fts(tmp_path):
    conn = db.connect(tmp_path / "faros.db")
    conn.execute("INSERT INTO library_items (clase, ruta, titulo, origen, creado, actualizado)"
                 " VALUES ('faro', 'r', 't', 'scan', 'x', 'x')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO library_items (clase, ruta, titulo, origen, creado, actualizado)"
                     " VALUES ('faro', 'r', 't2', 'scan', 'x', 'x')")
    conn.execute("INSERT OR REPLACE INTO library_fts (rowid, titulo, extracto) VALUES (1, 'hola', 'mundo')")
    assert conn.execute("SELECT rowid FROM library_fts WHERE library_fts MATCH 'mundo'").fetchone()[0] == 1


def test_tras_backup_fallido_la_conexion_esta_cerrada_y_no_queda_copia_a_medias(tmp_path, monkeypatch):
    """BC5: se puede borrar la base (conn cerrada) y backups/ no tiene un .db parcial."""
    import os
    import types

    class Falla(sqlite3.Connection):
        def backup(self, target, **kw):
            target.execute("CREATE TABLE parcial (a)")      # deja algo escrito en el destino...
            target.commit()
            raise sqlite3.OperationalError("disco lleno")    # ...y falla a mitad

    real = sqlite3
    falso = types.SimpleNamespace(**{k: getattr(real, k) for k in dir(real) if not k.startswith("__")})
    falso.connect = lambda *a, **k: real.connect(*a, factory=Falla, **k)
    monkeypatch.setattr(db, "sqlite3", falso)
    path = tmp_path / "faros.db"
    monkeypatch.undo()                    # la base v10 se prepara con el sqlite3 de verdad
    _v10(path)
    monkeypatch.setattr(db, "sqlite3", falso)
    with pytest.raises(db.BackupFallido, match="disco lleno"):
        db.connect(path)
    os.remove(path)                       # en Windows falla si la conexión sigue abierta
    assert not list((tmp_path / "backups").glob("*.db"))
