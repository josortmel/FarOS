"""#157: on 2-sep a peer verification opened the house base with the new db.py while the old
daemon was still running, and that migrated production live. It went well by design
(backup, integrity ok), but the order is: test on a copy → restart the daemon → it migrates.

Now db.connect does not migrate THE HOUSE BASE unless the daemon asks for it
(house_migration_ok=True). Any other process that opens it with newer code stops and says
why. A copy, a test base, or the house base already at the current schema open as before.
"""

import sqlite3

import pytest

from faros import db


def _old_house(tmp_path, monkeypatch, name="faros.db"):
    """A house base at schema 8 (one below), in a fake FAROS home."""
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    path = tmp_path / name
    db.connect(path).close()
    c = sqlite3.connect(str(path))
    c.execute("DELETE FROM schema_version WHERE version >= ?", (db.SCHEMA_VERSION,))
    c.execute("INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (8, '2026-10-16T00:00:00')")
    c.commit()
    c.close()
    return path


def _version(path):
    c = sqlite3.connect(str(path))
    try:
        return c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    finally:
        c.close()


@pytest.mark.parametrize("name", ["faros.db", "agenticos.db"])
def test_another_process_does_not_migrate_the_house(tmp_path, monkeypatch, name):
    path = _old_house(tmp_path, monkeypatch, name)
    with pytest.raises(db.HouseMigrationRefused) as e:
        db.connect(path)
    assert "daemon" in str(e.value) and "copia" in str(e.value)
    assert _version(path) == 8


def test_the_default_path_is_the_house_too(tmp_path, monkeypatch):
    path = _old_house(tmp_path, monkeypatch)
    monkeypatch.setattr(db, "DEFAULT_DB", path)
    monkeypatch.setattr(db._env, "get", lambda k, *a: None)
    with pytest.raises(db.HouseMigrationRefused):
        db.connect()
    assert _version(path) == 8


def test_the_daemon_migrates_the_house(tmp_path, monkeypatch):
    path = _old_house(tmp_path, monkeypatch)
    db.connect(path, house_migration_ok=True).close()
    assert _version(path) == db.SCHEMA_VERSION


def test_a_copy_outside_the_house_migrates_as_always(tmp_path, monkeypatch):
    house = _old_house(tmp_path / "casa", monkeypatch)
    copia = tmp_path / "copia.db"
    copia.write_bytes(house.read_bytes())
    db.connect(copia).close()
    assert _version(copia) == db.SCHEMA_VERSION
    assert _version(house) == 8


def test_the_house_at_the_current_schema_opens_as_always(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    path = tmp_path / "faros.db"
    db.connect(path, house_migration_ok=True).close()
    db.connect(path).close()
    assert _version(path) == db.SCHEMA_VERSION


def test_a_new_house_is_created_by_anyone(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.connect(tmp_path / "faros.db").close()
    assert _version(tmp_path / "faros.db") == db.SCHEMA_VERSION
