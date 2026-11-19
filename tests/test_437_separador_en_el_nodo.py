"""#437 — el separador va DENTRO del nodo de texto, y la ficha del plan pasa por tr().

Dos cosas que salieron mirando la interfaz en ingles, no el detector:
- «</code> · copia en el Escritorio: <code>» ya traducia: en el DOM el nodo es
  «· copia en el Escritorio:» y esa clave existe. El detector arrancaba en «copia» y
  preguntaba por un nodo que no existe (falso positivo).
- La ficha del plan decia «phase F1 · tamaño M · sugerido bob · verificación peer»: el
  «·» lo pone .join(' · ') al pintar y queda pegado a la etiqueta siguiente, asi que
  «· verificación» no casa con la clave «verificación». Eso no lo ve un detector
  estatico; el arreglo es que cada etiqueta pase por tr() antes del join.
"""

import importlib.util
import io
import json
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "buscar_etiqueta_pegada", RAIZ / "scripts" / "dev" / "buscar_etiqueta_pegada.py")
det = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(det)

CAT = json.load(io.open(RAIZ / "app" / "renderer" / "locales" / "en.json", encoding="utf-8"))


def _etqs(texto, cat):
    return [e for _, e in det.hallazgos_en(texto, cat)]


def test_437_el_separador_es_parte_del_nodo():
    js = "x = `Guía: <code>${a}</code> · copia en el Escritorio: <code>${b}</code>`;"
    assert _etqs(js, {"Guía:": "x"}) == ["copia en el Escritorio:"]
    # la clave con el separador es la que casa con el DOM: deja de salir
    assert _etqs(js, {"Guía:": "x", "· copia en el Escritorio:": "x"}) == []
    # sin el separador no casa con el nodo de verdad: sigue saliendo
    assert _etqs(js, {"Guía:": "x", "copia en el Escritorio:": "x"}) == ["copia en el Escritorio:"]


def test_437_un_dato_condicional_no_es_un_hallazgo():
    # el ${…} pinta marcado o nada: el nodo es la etiqueta sola, y esta en el catalogo
    js = "b.innerHTML = `Mesa de bugs${open ? ` <span>${open}</span>` : ''}`;"
    assert _etqs(js, {"Mesa de bugs": "x"}) == []


def test_437_la_ficha_del_plan_pasa_cada_etiqueta_por_tr():
    src = io.open(RAIZ / "app" / "renderer" / "js" / "ui" / "panel.js", encoding="utf-8").read()
    ficha = src[src.index("function renderPlanSections"):src.index(".join(' · ')")]
    etiquetas = re.findall(r"\$\{tr\('([^']+)'\)\} <b>", ficha)
    assert etiquetas == ["fase", "tamaño", "sugerido", "verificación"]
    assert not re.search(r"`[a-zñáéíóú]+ <b>", ficha), "queda una etiqueta sin tr() en la ficha"
    for e in etiquetas:
        assert e in CAT, e
