"""#1134 — un segundo daemon no toca la casa. El 11-nov la app, con la máquina cargada, lanzó tres daemons
más; cada uno abrió la base, hizo la copia diaria y arrancó scheduler y barredor antes de fallar al enlazar
el puerto. Ahora el daemon coge el puerto LO PRIMERO y, si ya es de otro, se va sin abrir nada."""

import asyncio
import socket

from faros import daemon


def _ocupado():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s, s.getsockname()[1]


def test_coger_puerto_libre_y_ocupado():
    s, port = _ocupado()
    try:
        assert daemon.coger_puerto(port) is None
    finally:
        s.close()
    libre = daemon.coger_puerto(port)
    assert libre is not None
    libre.close()


def test_con_el_puerto_ocupado_se_va_sin_abrir_la_base_ni_crear_la_casa(tmp_path, monkeypatch):
    casa = tmp_path / "casa"
    monkeypatch.setattr(daemon, "DATA_DIR", casa)

    def no(*a, **k):
        raise AssertionError("abrió la base con el puerto ocupado")

    monkeypatch.setattr(daemon.db, "connect", no)
    s, port = _ocupado()
    try:
        asyncio.run(daemon.main(port, str(tmp_path / "x.db")))
    finally:
        s.close()
    assert not casa.exists() and not (tmp_path / "x.db").exists()
