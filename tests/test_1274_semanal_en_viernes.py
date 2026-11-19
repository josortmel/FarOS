"""La revista semanal se fecha el VIERNES en que sale, no el lunes de su semana.

Lo cazó Pepe el 16-nov («la revista semanal de la semana pasada no se ha metido en la
biblioteca»): sí estaba, pero con fecha del lunes 9 en vez del viernes 13, debajo de las
diarias del 10 al 13, y parecía que faltaba. Les pasaba a todas (Prima: la 45 con el 2-nov,
la 44 con el 26-oct). library._fecha usaba fromisocalendar(año, semana, 1). Y como una pasada
se salta lo que no ha cambiado, las que ya estaban no se corregían solas: ahora sí.
"""

from faros import library as L
from test_850_library import mundo  # noqa: F401  (el mismo árbol temporal)


def test_la_semana_46_es_el_viernes_13():
    assert L._fecha("semana-2026-46.md", 0) == "2026-11-13"
    assert L._fecha("semana-2026-43.md", 0) == "2026-10-23"


def test_una_fecha_en_el_nombre_sigue_mandando():
    assert L._fecha("2026-11-12.md", 0) == "2026-11-12"


def test_una_pasada_vuelve_a_fechar_la_que_ya_estaba_mal(mundo):
    L.recorrer(mundo.conn, mundo.env)
    ruta = "%semana-2026-43.md"
    mundo.conn.execute("UPDATE library_items SET fecha='2026-10-19' WHERE ruta LIKE ? AND ruta NOT LIKE ?",
                       (ruta, "%lectura_semana-2026-43.md"))
    L.recorrer(mundo.conn, mundo.env)
    fila = mundo.conn.execute("SELECT fecha FROM library_items WHERE ruta LIKE ? AND ruta NOT LIKE ?",
                              (ruta, "%lectura_semana-2026-43.md")).fetchone()
    assert fila[0] == "2026-10-23"
