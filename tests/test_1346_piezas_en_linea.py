"""#1346 (25-nov, Prima e Hilo): etiquetas en la misma línea. La primera verificación real con piezas
(#1397) tenía «C1 [salida]: … C2 [salida]: … C3: … C4 [salida]: …» en UNA línea y partir() veía una
sola pieza C1 con las otras dentro: 14 de 121 tareas abiertas, cuatro con un [persona: X] escondido.
Decisión de Prima: una etiqueta también abre pieza detrás de «. » o «; » (C<n>, [corchete] opcional y
«:» justo detrás); un aviso para lo que siga con forma de etiqueta dentro de una pieza; y la migración
v19 vuelve a tomar la huella sin que cuente como «el criterio cambió»."""

import json

import pytest

from faros import criterios, db, piezas
from faros import service as s
from faros.service import TicketError

# Las formas de las 14 reales (sin su texto: el repo es público).
EN_LINEA = "C1 [salida]: `x --json` da MERGED. C2 [salida]: `y` en pass. C3: el runbook cuenta lo de hoy. C4 [salida]: CI en verde."
PERSONA = "C1: lo hace el script; C2 [salida]: la salida pegada. C3 [persona: carol]: Carol lo prueba en su móvil."


def _etiquetas(criterio):
    return [p.etiqueta for p in piezas.partir(criterio)]


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


# ---- partir


def test_etiquetas_detras_de_punto_abren_pieza():
    ps = piezas.partir(EN_LINEA)
    assert [p.etiqueta for p in ps] == ["C1", "C2", "C3", "C4"]
    assert [p.marca for p in ps] == ["salida", "salida", "", "salida"]
    assert ps[0].texto == "`x --json` da MERGED."
    assert ps[3].texto == "CI en verde."


def test_detras_de_punto_y_coma_y_persona_escondida():
    ps = piezas.partir(PERSONA)
    assert [p.etiqueta for p in ps] == ["C1", "C2", "C3"]
    assert ps[2].marca == "persona" and ps[2].persona == "carol"


def test_control_mencion_entre_parentesis_no_parte():
    """Como en el criterio de #1346: «(C1:, C2:…)» nombra etiquetas, no las abre."""
    c = "C4 [salida]: Las etiquetas van escritas en el texto (C1:, C2:…), no por posición.\nC5: otra."
    assert _etiquetas(c) == ["C4", "C5"]


@pytest.mark.parametrize(
    "texto",
    [
        "el caso C14 queda para después. C7d cambiado a aviso. C15 nueva.",
        "Lo de C3 [salida] sigue igual. C2 sin dos puntos.",
    ],
)
def test_control_casos_nombrados_sin_dos_puntos_no_parten(texto):
    """Control de Prima: nombrar un caso sin «:» detrás no es una etiqueta."""
    c = "C1: " + texto
    assert _etiquetas(c) == ["C1"]


def test_detras_de_cualquier_espacio_no_parte():
    """Solo detrás de «. » o «; », no detrás de una coma ni de una palabra."""
    assert _etiquetas("C1: mira esto, C2: no es pieza") == ["C1"]
    assert _etiquetas("C1: mira el C2: no es pieza") == ["C1"]


def test_lo_de_siempre_sigue_igual():
    t = "C1 [salida]: A.\nC2 [fichero]: B.\nC3: C."
    assert _etiquetas(t) == ["C1", "C2", "C3"]
    assert piezas.huella(t) == piezas.huella_v18(t)


def test_repetida_en_linea_es_repetida():
    with pytest.raises(piezas.PiezasError) as e:
        piezas.partir("C1: a. C1: b.")
    assert e.value.code == "criteria.duplicate_label"


# ---- el aviso


def test_aviso_si_queda_forma_de_etiqueta_dentro_de_una_pieza():
    c = "C1: mira esto, C2 [salida]: la salida."
    avisos = [a for a in criterios.revisar(c, "peer") if a["punto"] == "piezas"]
    assert avisos and "C2 [salida]:" in avisos[0]["aviso"]


def test_sin_aviso_cuando_todo_esta_partido():
    assert not [a for a in criterios.revisar(EN_LINEA, "peer") if a["punto"] == "piezas"]


# ---- la regla, de punta a punta


def _tarea(conn, criterio, owner="alice"):
    return s.propose_ticket(
        conn, owner, "Tarea", owner=owner, auto_accept=True, verification_level="peer", verify_criteria=criterio
    )["ticket"]["id"]


def test_la_persona_escondida_ya_no_la_cierra_otro(conn):
    tid = _tarea(conn, PERSONA)
    s.start_ticket(conn, "alice", tid)
    s.complete_ticket(conn, "alice", tid, "command_output", "hecho")
    p = {
        "C1": {"estado": "cumple", "prueba": "#1"},
        "C2": {"estado": "cumple", "prueba": "1 passed"},
        "C3": {"estado": "cumple", "prueba": "lo vi yo"},
    }
    with pytest.raises(TicketError) as e:
        s.verify_ticket(conn, "bob", tid, "pass", piezas=p)
    assert "carol" in str(e.value)


# ---- la migración v19


def test_v19_vuelve_a_tomar_la_huella_sin_cambios_de_criterio(conn):
    tid = _tarea(conn, EN_LINEA)
    s.start_ticket(conn, "alice", tid)
    # la huella que dejó la 3.3.54: una sola pieza
    conn.execute("UPDATE tickets SET piezas_huella = ? WHERE id = ?", (json.dumps(piezas.huella_v18(EN_LINEA)), tid))
    db._v19_huella_en_linea(conn)
    assert list(json.loads(s._get(conn, tid)["piezas_huella"])) == ["C1", "C2", "C3", "C4"]
    s.complete_ticket(conn, "alice", tid, "command_output", "hecho")
    p = {e: {"estado": "cumple", "prueba": "1 passed"} for e in ["C1", "C2", "C3", "C4"]}
    # sin porqués: el texto no ha cambiado, solo cómo se parte
    assert s.verify_ticket(conn, "bob", tid, "pass", piezas=p)["status"] == "verified"
    notas = [h["note"] or "" for h in s.ticket_history(conn, tid)["history"]]
    assert sum("partida de nuevo" in n for n in notas) == 1
    db._v19_huella_en_linea(conn)  # idempotente
    notas = [h["note"] or "" for h in s.ticket_history(conn, tid)["history"]]
    assert sum("partida de nuevo" in n for n in notas) == 1


def test_v19_no_toca_la_huella_si_el_criterio_cambio_de_verdad(conn):
    tid = _tarea(conn, EN_LINEA)
    s.start_ticket(conn, "alice", tid)
    vieja = piezas.huella_v18("C1: lo que decía al empezar")
    conn.execute("UPDATE tickets SET piezas_huella = ? WHERE id = ?", (json.dumps(vieja), tid))
    db._v19_huella_en_linea(conn)
    assert json.loads(s._get(conn, tid)["piezas_huella"]) == vieja


def test_esquema_19():
    assert db.SCHEMA_VERSION == 19
