"""#1098 — la mitad de FarOS de decisiones_vigentes: busca también en la respuesta y en su motivo (la 53 decía
«nano» solo en la respuesta), todas las palabras con contenido antes que alguna, sin palabras vacías, sin las
RETIRADAS, y si no hay nada relevante lo dice en vez de rellenar."""

import pytest

from faros import db
from faros import decisions as dec
from faros.service import HOUSE_OWNER

OPTS = [{"label": "sí", "text": "sí, hacerlo"}, {"label": "no", "text": "no hacerlo"}]


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _decidida(conn, titulo, contexto="", respuesta=None, motivo="porque sí"):
    d = dec.ask(conn, "alice", titulo, OPTS, context=contexto)
    dec.decide(conn, HOUSE_OWNER, d["id"], decision=respuesta or "sí, hacerlo", rationale=motivo)
    return d["id"]


def _ids(r):
    return [int(v["id"].split(":")[-1]) for v in r["vigentes"]]


def _tramos(conn, tema):
    """Los ids que da la búsqueda en tramos (_candidatas_faros). Desde la 3.3.43 la vigentes NO la usa (vuelta
    atrás de Prima, 18-nov): se queda probada aquí porque el arreglo se construye sobre ella."""
    return [r["id"] for r in dec._candidatas_faros(conn, tema, None, 30)]


def test_la_respuesta_cuenta(conn):
    d = _decidida(conn, "Modelo del chat", respuesta="gpt-5.4-nano para el prototipo")
    _decidida(conn, "Otra cosa del chat", respuesta="sí")
    assert _tramos(conn, "nano del chat")[0] == d  # arreglo: la de una palabra puede venir detrás


def test_el_motivo_cuenta(conn):
    d = _decidida(conn, "Plantillas", motivo="así la célula no pisa lo cambiado a mano")
    assert _tramos(conn, "célula pisa") == [d]


def test_todas_las_palabras_antes_que_alguna(conn):
    solo_una = _decidida(conn, "Claves de la tele")
    las_dos = _decidida(conn, "Claves que se guardan en la casa")
    ids = _tramos(conn, "claves en la casa")
    assert ids[0] == las_dos and ids.index(solo_una) > ids.index(las_dos)  # arreglo: la mitad completa detrás


def test_las_palabras_vacias_no_casan_con_todo(conn):
    _decidida(conn, "La tele de la casa")
    assert _tramos(conn, "de la") == []


def test_una_retirada_no_esta_vigente(conn):
    _decidida(conn, "Subir commits de EcoDB a GitHub", respuesta="RETIRADA: la sustituye la 30")
    buena = _decidida(conn, "Subir los commits locales de EcoDB a GitHub al cierre")
    assert _tramos(conn, "subir commits EcoDB GitHub") == [buena]


def test_sin_nada_relevante_avisa_y_no_rellena(conn):
    _decidida(conn, "Qué se pone en la tele")
    assert _tramos(conn, "receta de lentejas") == []


def test_una_base_v14_llega_a_la_17_con_la_fts_nueva(tmp_path):
    ruta = tmp_path / "v14.db"
    c = db.connect(str(ruta))
    d = _decidida(c, "Modelo del chat", respuesta="nano")
    for t in ("decisions_fts2_ai", "decisions_fts2_au"):
        c.execute(f"DROP TRIGGER IF EXISTS {t}")
    c.execute("DROP TABLE decisions_fts2")
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS decisions_fts USING fts5(title, context)")
    c.execute("DELETE FROM schema_version")
    c.execute("INSERT INTO schema_version (version, applied_at) VALUES (14, '2026-11-17T10:00:00')")
    c.commit()
    c.close()
    c = db.connect(str(ruta))
    assert db.schema_version(c) == db.SCHEMA_VERSION == 17
    assert c.execute("SELECT name FROM sqlite_master WHERE name = 'decisions_fts'").fetchone() is None
    assert _ids(dec.vigentes_faros(c, "nano")) == [d]
    c.close()


def test_la_casa_v16_pierde_la_fts_vieja_temporal(tmp_path):
    """La casa está en v16 (3.3.43/3.3.44): la decisions_fts vieja y sus triggers eran temporales; la v17 los quita
    y las decisiones nuevas siguen entrando en la vigentes por decisions_fts2."""
    ruta = tmp_path / "v16.db"
    c = db.connect(str(ruta))
    d = _decidida(c, "Modelo del chat")
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS decisions_fts USING fts5(title, context)")
    c.execute(
        "CREATE TRIGGER IF NOT EXISTS decisions_fts_ai AFTER INSERT ON decisions BEGIN"
        " INSERT INTO decisions_fts (rowid, title, context) VALUES (new.id, new.title, new.context); END"
    )
    c.execute("DELETE FROM schema_version")
    c.execute("INSERT INTO schema_version (version, applied_at) VALUES (16, '2026-11-18T10:46:00')")
    c.commit()
    c.close()
    c = db.connect(str(ruta))
    assert db.schema_version(c) == 17
    assert (
        c.execute(
            "SELECT name FROM sqlite_master WHERE name IN ('decisions_fts', 'decisions_fts_ai', 'decisions_fts_au')"
        ).fetchall()
        == []
    )
    assert _ids(dec.vigentes_faros(c, "modelo chat")) == [d]
    nueva = _decidida(c, "Otra del chat")
    assert nueva in _ids(dec.vigentes_faros(c, "chat"))
    c.close()


def test_arreglo_el_or_completa_lo_que_deja_el_and(conn):
    """#1098 (arreglo): si el AND trae pocas, el tramo OR completa hasta el tope con las que tienen al menos la
    mitad de las palabras; antes solo entraba con el AND vacío y una fila con 3 de 4 palabras se perdía."""
    todas = _decidida(conn, "Claves de FarOS: quién las pide y dónde se guardan")
    casi = _decidida(conn, "Claves bajo demanda: quién las pide por MCP")  # 3 de 4: claves, quién, pide
    poca = _decidida(conn, "Claves de la tele")  # 1 de 4: no llega a la mitad
    ids = _tramos(conn, "claves quién pide guardan")
    assert ids[0] == todas and casi in ids and poca not in ids


def test_arreglo_la_vigentes_usa_los_tramos(conn):
    d = _decidida(conn, "Modelo del chat", respuesta="gpt-5.4-nano para el prototipo")
    assert _ids(dec.vigentes_faros(conn, "nano")) == [d]
