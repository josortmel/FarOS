"""#1081 — el criterio de verificación se revisa al crear la tarea, no al cerrarla (decisión 75, «Primero los
criterios»). Determinista y nunca bloqueante: avisos que nombran el punto de la regla incumplido. Los casos
malo → bueno son los de la regla (banco #595, 22-oct)."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from faros import api, criterios, db

TOKEN = "t" * 32
H = {"X-FarOS-Token": TOKEN}


def _puntos(criterio, nivel="peer"):
    return [a["punto"] for a in criterios.revisar(criterio, nivel)]


def test_g1_la_suite_sin_comando_avisa_y_con_el_comando_no():
    assert _puntos("la suite completa está en verde") == [4]
    assert _puntos("la suite sin los tests del juez de pago está en verde (`python -m pytest -q -m 'not judge'`)") == []


def test_c3_suite_completa_en_verde_avisa_y_el_comando_exacto_con_rojo_no():
    assert _puntos("suite completa en verde") == [4]
    assert _puntos("`python -m pytest -q tests/test_431_cambio_calendario.py` en verde en el commit y rojo en el padre") == []


def test_arreglado_con_test_sin_rojo_avisa():
    assert _puntos("el fallo queda arreglado y su test pasa con `pytest tests/test_x.py`") == [6]
    assert _puntos("arreglado: `pytest tests/test_x.py` falla en el padre por la aserción y pasa en el commit") == []


def test_lo_que_el_juez_no_puede_tocar_avisa_solo_con_juez_automatico():
    assert _puntos("funciona en producción con la clave nueva", "auto") == [2]
    assert _puntos("funciona en producción con la clave nueva", "peer") == []


def test_sin_criterio_avisa_si_alguien_tiene_que_verificar():
    assert _puntos("", "peer") == [2]
    assert _puntos(None, "self") == []


def test_un_criterio_de_texto_bien_escrito_no_avisa():
    assert _puntos("Explica cada paper, ticket y decisión citados; los nombres de los agentes no hay que definirlos.") == []


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "load_token", lambda: TOKEN)
    db.connect(tmp_path / "t.db").close()
    c = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    yield TestClient(api.create_app(c))
    c.close()


def test_crear_por_la_api_devuelve_el_aviso_sin_bloquear(cli):
    r = cli.post("/api/tickets", headers=H, json={"agent": "alice", "title": "x", "verification_level": "peer",
                                                  "verify_criteria": "la suite en verde"})
    assert r.status_code == 200
    assert [a["punto"] for a in r.json()["criterion_warnings"]] == [4]
    assert r.json()["ticket"]["verify_criteria"] == "la suite en verde"   # se guarda igual
    bueno = cli.post("/api/tickets", headers=H, json={"agent": "alice", "title": "y", "verification_level": "peer",
                                                      "verify_criteria": "`pytest -q tests/test_y.py` en verde"})
    assert bueno.json()["criterion_warnings"] == []


def test_cambiar_el_criterio_tambien_lo_revisa(cli):
    t = cli.post("/api/tickets", headers=H, json={"agent": "alice", "title": "z"}).json()["ticket"]
    r = cli.patch(f"/api/tickets/{t['id']}", headers=H, json={"agent": "alice", "verify_criteria": "tests pasan"})
    assert r.status_code == 200 and [a["punto"] for a in r.json()["criterion_warnings"]] == [4]
    sin = cli.patch(f"/api/tickets/{t['id']}", headers=H, json={"agent": "alice", "title": "z2"})
    assert "criterion_warnings" not in sin.json()
