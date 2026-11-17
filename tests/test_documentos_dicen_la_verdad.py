"""Las cifras que afirman los documentos publicos se CUENTAN, no se declaran.

POR QUE EXISTE ESTE FICHERO, con fechas:
  · el README y el CHANGELOG decian «234 tests». Eran 236.
  · se corrigio a mano a 236 a las 12:1x.
  · a las 12:2x ya eran 237, porque alguien anadio un test entre medias.
La cifra se pudrio en MINUTOS, y es la tercera vez hoy que se arregla a mano.

Es el mismo animal que nos ha mordido trece veces: prosa correcta cuando se escribio y
falsa despues, que no se degrada a la vista — se queda igual mientras el resto se mueve.
La unica defensa que sobrevive a un dia malo es que la afirmacion se CALCULE.

No comprueba que la cifra sea bonita: comprueba que el documento y el objeto dicen lo
mismo. Si alguien anade un test, este falla y le dice el numero nuevo.
"""
import re
import pathlib
import subprocess
import sys

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]
DOCS = ["README.md", "README.es.md", "CHANGELOG.md"]
# «234 of them», «236,», «234 tests» — la cifra junto a la palabra tests en los dos idiomas
PATRON = re.compile(r"[Tt]ests?\s*[—-]\s*(\d{2,4})\b|(\d{2,4})\s+tests\b")


def _cuantos_tests_hay():
    """Se le pregunta a pytest, que es el objeto. No se cuenta a mano ni se hereda."""
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=RAIZ, capture_output=True, text=True,
    )
    m = re.search(r"(\d+)\s*/\s*\d+\s+tests collected", r.stdout) \
        or re.search(r"(\d+)\s+tests? collected", r.stdout)
    assert m, f"no supe leer el recuento de pytest:\n{r.stdout[-500:]}"
    return int(m.group(1))


def _cifras_que_afirma(texto):
    return [int(a or b) for a, b in PATRON.findall(texto)]


def _lo_que_sigue_vivo(doc, texto):
    """1-oct (#401): en el CHANGELOG solo cuenta lo que no se ha publicado. Las secciones
    de versiones salidas son historia: la 3.3.0 se publico con 240 tests, y este test
    obligo a reescribir esa linea cuatro veces el 30-sep hasta que decia 281."""
    if doc != "CHANGELOG.md":
        return texto
    return re.split(r"(?m)^## \d", texto, maxsplit=1)[0]


@pytest.mark.parametrize("doc", DOCS)
def test_la_cifra_de_tests_del_documento_es_la_real(doc):
    p = RAIZ / doc
    if not p.exists():
        pytest.skip(f"{doc} no existe en este arbol")
    afirmadas = _cifras_que_afirma(_lo_que_sigue_vivo(doc, p.read_text(encoding="utf-8", errors="replace")))
    if not afirmadas:
        # 6-oct (#439): un sed con la cifra vacía dejó «Tests —  of them» en los dos README
        # y este test se SALTÓ en vez de fallar: el hueco pasaba por verde. Los README
        # siempre la afirman; solo el CHANGELOG puede no tenerla.
        assert doc == "CHANGELOG.md", f"{doc} ha perdido su cifra de tests"
        pytest.skip(f"{doc} no afirma ninguna cifra de tests")
    real = _cuantos_tests_hay()
    malas = [n for n in afirmadas if n != real]
    assert not malas, (
        f"{doc} dice {malas} y los tests son {real}. "
        f"No lo corrijas a mano sin volver a contar: la cifra cambia cada vez que alguien "
        f"anade un test."
    )
