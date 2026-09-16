"""Tests de settings (T0.4)."""

import pytest

from faros import db, settings
from faros.service import TicketError


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def test_defaults_and_mask(conn, monkeypatch):
    monkeypatch.delenv("AGENTICOS_VERIFY_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    all_ = settings.get_all(conn)
    assert all_["casa_slots"] == [["09:00", "12:00"], ["14:00", "18:00"]]
    assert all_["verifier_model"] == "haiku" and all_["anthropic_api_key"] == ""
    assert all_["backup_retention_days"] == 14


def test_patch_persists_and_masks_secret(conn):
    out = settings.patch(conn, "owner", {"anthropic_api_key": "sk-ant-secreto", "verifier_model": "sonnet"})
    assert out["anthropic_api_key"] == settings.MASK and out["verifier_model"] == "sonnet"
    assert settings.get(conn, "anthropic_api_key") == "sk-ant-secreto"  # real, solo daemon
    # reenviar la máscara no pisa el secreto
    settings.patch(conn, "owner", {"anthropic_api_key": settings.MASK})
    assert settings.get(conn, "anthropic_api_key") == "sk-ant-secreto"
    # vaciar sí lo borra
    settings.patch(conn, "owner", {"anthropic_api_key": ""})
    assert settings.get_all(conn)["anthropic_api_key"] == ""


def test_env_fallback_when_empty(conn, monkeypatch):
    monkeypatch.setenv("AGENTICOS_VERIFY_MODEL", "opus")
    assert settings.get(conn, "verifier_model") == "opus"
    settings.patch(conn, "alice", {"verifier_model": "haiku"})
    assert settings.get(conn, "verifier_model") == "haiku"  # DB gana sobre env


def test_validation_errors(conn):
    with pytest.raises(TicketError, match="desconocidos"):
        settings.patch(conn, "owner", {"nope": 1})
    with pytest.raises(TicketError, match="franja"):
        settings.patch(conn, "owner", {"casa_slots": [["9:00", "12:00"]]})
    with pytest.raises(TicketError, match="invertida"):
        settings.patch(conn, "owner", {"casa_slots": [["12:00", "09:00"]]})
    with pytest.raises(TicketError, match="rango"):
        settings.patch(conn, "owner", {"backup_retention_days": 0})
    with pytest.raises(TicketError, match="claves desconocidas"):
        settings.patch(conn, "owner", {"today_window": {"tomorrow": True}})
    with pytest.raises(TicketError, match="permiso"):
        settings.patch(conn, "code", {"verifier_model": "haiku"})


def test_today_window_merges_partial(conn):
    out = settings.patch(conn, "owner", {"today_window": {"in_progress": False}})
    assert out["today_window"] == {"overdue": True, "due_today": True, "in_progress": False}


def test_los_ajustes_aceptan_el_nombre_nuevo_y_el_viejo(tmp_path, monkeypatch):
    """#303 — FAROS_* tiene que funcionar donde antes solo funcionaba AGENTICOS_*.

    POR QUE EXISTE: la tabla de DEFAULTS declara el nombre de la variable de
    entorno de cada ajuste, y el consumidor hacia `os.environ.get(env)` A PELO
    con el nombre VIEJO clavado. Cuatro ajustes —el modelo del verificador, los
    dos de Telegram y el repo raiz— solo aceptaban AGENTICOS_*.

    Y el modo de fallo es el peor que puede tener un ajuste: quien siguiera
    nuestra propia documentacion y pusiera FAROS_VERIFY_MODEL no configuraba
    nada Y NO SE ENTERABA. Sin error, sin aviso: caia al valor por defecto en
    silencio. Un ajuste que se ignora sin decirlo es peor que uno que revienta.

    El rename de hoy llego a lo que SE VE —la marca, la interfaz, las
    descripciones— y no a lo que SE CONFIGURA. Esto cierra esa mitad.

    Se comprueban LAS DOS familias y ADEMAS que la nueva manda sobre la vieja,
    que es la regla que ya sigue faros/env.py: lo nuevo gana, lo viejo sigue
    valiendo.
    """
    import os
    from faros import settings as st

    conn = db.connect(tmp_path / "s.db")
    for k in ("AGENTICOS_VERIFY_MODEL", "FAROS_VERIFY_MODEL"):
        monkeypatch.delenv(k, raising=False)

    # solo el viejo: sigue valiendo
    monkeypatch.setenv("AGENTICOS_VERIFY_MODEL", "viejo")
    assert st.get(conn, "verifier_model") == "viejo"

    # solo el nuevo: TIENE que valer (esto fallaba antes)
    monkeypatch.delenv("AGENTICOS_VERIFY_MODEL")
    monkeypatch.setenv("FAROS_VERIFY_MODEL", "nuevo")
    assert st.get(conn, "verifier_model") == "nuevo", (
        "FAROS_* no configura nada: el ajuste cae al default EN SILENCIO")

    # los dos: manda el nuevo
    monkeypatch.setenv("AGENTICOS_VERIFY_MODEL", "viejo")
    assert st.get(conn, "verifier_model") == "nuevo"
    conn.close()
