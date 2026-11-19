"""#389 — el detector de la forma 4 pregunta al catálogo como t(), no por la etiqueta suelta.

Contaba como «sin clave» nodos que el catálogo YA cubre con un patrón ({n}, «{s}»), no
reconocía tb() como «ya pide traducción» y no veía la etiqueta separada del dato por
marcado (4c). Canario en las dos direcciones: sale sin clave y deja de salir con ella.
"""

import importlib.util
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "buscar_etiqueta_pegada", RAIZ / "scripts" / "dev" / "buscar_etiqueta_pegada.py")
det = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(det)


def _etqs(texto, cat):
    return [e for _, e in det.hallazgos_en(texto, cat)]


def test_389_pegada_sin_clave_sale_y_con_su_patron_deja_de_salir():
    js = "toast(`Bisagra #${d.id} decidida: «${opt}». Registrada con su motivo.`, 'ok');"
    assert _etqs(js, {}) == ["Bisagra #"]
    con = {"Bisagra #{n} decidida: «{s}». Registrada con su motivo.": "x"}
    assert _etqs(js, con) == []


def test_389_el_nodo_acaba_en_la_etiqueta_html():
    js = "el.innerHTML = `<b>Tareas de hoy: ${n}</b> <i>otra cosa</i>`;"
    assert _etqs(js, {}) == ["Tareas de hoy:"]
    assert _etqs(js, {"Tareas de hoy: {n}": "x"}) == []


def test_389_lo_que_ya_pide_traduccion_no_sale():
    for f in ("tr", "t2", "tb", "t"):
        js = f"x = {f}(`Pendientes de hoy ${{n}}`);"
        assert _etqs(js, {}) == [], f


def test_389_4c_marcado_entre_etiqueta_y_dato():
    js = "el.innerHTML = `Pendientes de hoy <b>${n}</b>`;"
    assert _etqs(js, {}) == ["Pendientes de hoy"]
    # el nodo de texto es la etiqueta sola: con su clave, deja de salir
    assert _etqs(js, {"Pendientes de hoy": "x"}) == []
