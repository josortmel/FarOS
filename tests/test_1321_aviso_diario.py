"""#1321, decisión 134 de Pepe (19-nov): «Una vez al día por persona y clave — quiero saber en qué estáis pero no
necesito recibirlo 500 veces». El aviso de «primera vez» de Claves salta la primera vez que QUIÉN pide ESTA clave
CADA DÍA; el purpose sigue apuntado en Entregas, pero ya no hace saltar otro aviso."""

import pytest

from faros import credentials as cr
from faros import db
from faros import service as s

SECRETO = "otra-" + "a1b2c3d4e5f6" * 3


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def avisos(monkeypatch):
    from faros import notify

    enviados = []
    monkeypatch.setattr(notify, "telegram", lambda conn, text: enviados.append(text) or True)
    return enviados


@pytest.fixture
def dia(monkeypatch):
    """Fija el reloj de credentials: dia["hoy"] = "2026-11-19" y cada entrega lleva esa fecha."""
    reloj = {"hoy": "2026-11-19", "n": 0}

    def ahora():
        reloj["n"] += 1
        return f"{reloj['hoy']}T10:{reloj['n'] % 60:02d}:00"

    monkeypatch.setattr(cr, "_now", ahora)
    return reloj


def _clave(conn, nombre="X_API_KEY"):
    c = cr.create(conn, "owner", nombre, "otra", SECRETO)
    cr.update(conn, s.HOUSE_OWNER, c["id"], {"deliverable": True})


def test_tres_purposes_el_mismo_dia_dan_un_solo_aviso(conn, avisos, dia):
    _clave(conn)
    for p in ("abrir Kavita", "mirar la biblioteca", "subir un libro"):
        cr.deliver(conn, "X_API_KEY", requested_by="eco", purpose=p)
    assert len(avisos) == 1
    filas = conn.execute("SELECT purpose FROM credential_deliveries ORDER BY id").fetchall()
    assert [f["purpose"] for f in filas] == [
        "abrir Kavita",
        "mirar la biblioteca",
        "subir un libro",
    ]  # Entregas, entero


def test_al_dia_siguiente_vuelve_a_avisar_una_vez(conn, avisos, dia):
    _clave(conn)
    cr.deliver(conn, "X_API_KEY", requested_by="eco", purpose="abrir Kavita")
    dia["hoy"] = "2026-11-20"
    cr.deliver(conn, "X_API_KEY", requested_by="eco", purpose="abrir Kavita")  # mismo purpose: antes no avisaba
    cr.deliver(conn, "X_API_KEY", requested_by="eco", purpose="otra cosa")
    assert len(avisos) == 2 and "abrir Kavita" in avisos[1]


def test_otra_persona_u_otra_clave_el_mismo_dia_si_avisan(conn, avisos, dia):
    _clave(conn)
    _clave(conn, "Y_TOKEN")
    cr.deliver(conn, "X_API_KEY", requested_by="eco", purpose="p")
    cr.deliver(conn, "X_API_KEY", requested_by="prima", purpose="p")
    cr.deliver(conn, "Y_TOKEN", requested_by="eco", purpose="p")
    assert len(avisos) == 3
