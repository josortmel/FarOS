"""#1346: el núcleo de la regla de las piezas (faros/piezas.py), contra los casos de Prima
(casos_1346.md, en el laboratorio de Prima). Aquí lo que no necesita base: partir,
huella, validar. Lo de verify/complete/MCP/historial va en test_1346_verify.py."""

import pytest

from faros import piezas
from faros.piezas import PiezasError

T3 = "C1 [salida]: A.\nC2 [fichero]: B.\nC3: C."
DUENO = "Pepe"


def todas(ruta, **cambio):
    p = {
        "C1": {"estado": "cumple", "prueba": "3 passed in 0.1s"},
        "C2": {"estado": "cumple", "prueba": str(ruta)},
        "C3": {"estado": "cumple", "prueba": "#1346"},
    }
    p.update(cambio)
    return p


def validar(criterio=T3, verdict="pass", p=None, agent="bob", **kw):
    return piezas.validar(criterio, verdict, p, agent, DUENO, **kw)


@pytest.fixture
def ruta(tmp_path):
    f = tmp_path / "prueba.txt"
    f.write_text("x")
    return f


def codigo_y_mensaje(exc):
    return exc.value.code, str(exc.value)


# ---- C1: pass sin piezas o con una pieza sin entrada


def test_c1a_pass_con_todas_las_piezas(ruta):
    r = validar(p=todas(ruta))
    assert [x["etiqueta"] for x in r["piezas"]] == ["C1", "C2", "C3"]


def test_c1b_pass_sin_piezas_nombra_todas():
    with pytest.raises(PiezasError) as e:
        validar(p=None)
    code, msg = codigo_y_mensaje(e)
    assert code == "verify.piezas_missing" and all(x in msg for x in ("C1", "C2", "C3"))


def test_c1c_falta_una_y_solo_nombra_esa(ruta):
    p = todas(ruta)
    del p["C2"]
    with pytest.raises(PiezasError) as e:
        validar(p=p)
    code, msg = codigo_y_mensaje(e)
    assert code == "verify.piezas_missing"
    assert "C2" in msg and "C1" not in msg and "C3" not in msg


def test_c1d_pieza_inventada(ruta):
    with pytest.raises(PiezasError) as e:
        validar(p=todas(ruta, C9={"estado": "cumple", "prueba": "x"}))
    code, msg = codigo_y_mensaje(e)
    assert code == "verify.piezas_unknown" and "C9" in msg


# ---- C2: no_cumple / no_comprobable no entran en un pass; el fail sigue como hoy


@pytest.mark.parametrize("pieza,estado", [("C2", "no_cumple"), ("C3", "no_comprobable")])
def test_c2ab_pass_con_pieza_que_no_cumple(ruta, pieza, estado):
    with pytest.raises(PiezasError) as e:
        validar(p=todas(ruta, **{pieza: {"estado": estado, "prueba": "no"}}))
    code, msg = codigo_y_mensaje(e)
    assert code == "verify.pieza_no_cumple" and pieza in msg


def test_c2c_fail_con_no_cumple_se_acepta():
    r = validar(verdict="fail", p={"C2": {"estado": "no_cumple", "prueba": "la ruta no existe"}})
    assert r["piezas"][0]["estado"] == "no_cumple"


def test_duda4_fail_sin_piezas_como_hoy():
    assert validar(verdict="fail", p=None)["piezas"] == []


def test_duda4_fail_valida_las_etiquetas_si_vienen():
    with pytest.raises(PiezasError) as e:
        validar(verdict="fail", p={"C9": {"estado": "no_cumple", "prueba": "x"}})
    assert e.value.code == "verify.piezas_unknown"


# ---- C3: incierto


def test_c3a_incierto_con_su_pieza(ruta):
    r = validar(verdict="incierto", p=todas(ruta, C2={"estado": "incierto", "prueba": "no puedo abrir la ruta"}))
    assert "C2 incierto: no puedo abrir la ruta" in r["historial"]


def test_c3b_incierto_sin_pieza_incierta(ruta):
    with pytest.raises(PiezasError) as e:
        validar(verdict="incierto", p=todas(ruta))
    assert e.value.code == "verify.incierto_sin_pieza"


def test_c3c_pass_con_pieza_incierta(ruta):
    with pytest.raises(PiezasError) as e:
        validar(p=todas(ruta, C3={"estado": "incierto", "prueba": "?"}))
    code, msg = codigo_y_mensaje(e)
    assert code == "verify.pieza_incierta" and "C3" in msg


# ---- C4: etiquetas escritas, no por posición


def test_c4a_dos_piezas():
    assert [p.etiqueta for p in piezas.partir("C1: A.\nC2: B.")] == ["C1", "C2"]


def test_c4b_etiqueta_repetida():
    with pytest.raises(PiezasError) as e:
        piezas.partir("C1: A.\nC1: B.")
    code, msg = codigo_y_mensaje(e)
    assert code == "criteria.duplicate_label" and "C1" in msg


def test_c4d_salto_en_la_numeracion():
    assert [p.etiqueta for p in piezas.partir("C1: A.\nC3: B.")] == ["C1", "C3"]


def test_c4e_sufijos():
    assert [p.etiqueta for p in piezas.partir("C11a [salida]: A.\nC11b [salida]: B.")] == ["C11a", "C11b"]


def test_marcas_y_texto_de_varias_lineas():
    ps = piezas.partir("C1 [persona: Prima]: Lo prueba\nen su móvil.\nC2 [fichero]: B.")
    assert ps[0].marca == "persona" and ps[0].persona == "Prima" and ps[0].texto == "Lo prueba\nen su móvil."
    assert ps[1].marca == "fichero"


def test_texto_antes_de_la_primera_etiqueta_es_una_pieza_mas():
    ps = piezas.partir("Preámbulo suelto.\nC1: A.")
    assert [p.etiqueta for p in ps] == ["C0", "C1"]


# ---- C5: huella y cambios


def test_c5a_sin_cambios(ruta):
    r = validar(p=todas(ruta), huella_vieja=piezas.huella(T3))
    assert "cambios del criterio" not in (r["historial"] or "")


def test_c5b_texto_cambiado_sin_porque(ruta):
    nuevo = T3.replace("B.", "B y algo más.")
    with pytest.raises(PiezasError) as e:
        validar(criterio=nuevo, p=todas(ruta), huella_vieja=piezas.huella(T3))
    code, msg = codigo_y_mensaje(e)
    assert code == "verify.piezas_cambiadas" and "C2 cambiada" in msg


def test_c5c_con_porque_entra_y_queda_en_el_historial(ruta):
    nuevo = T3.replace("B.", "B y algo más.")
    r = validar(criterio=nuevo, p=todas(ruta), huella_vieja=piezas.huella(T3), porque={"C2": "Pepe pidió más"})
    assert "C2 cambiada: Pepe pidió más" in r["historial"]


def test_c5d_pieza_borrada(ruta):
    nuevo = "C1 [salida]: A.\nC2 [fichero]: B."
    p = todas(ruta)
    del p["C3"]
    with pytest.raises(PiezasError) as e:
        validar(criterio=nuevo, p=p, huella_vieja=piezas.huella(T3))
    assert "C3 borrada" in str(e.value)


def test_c5e_renombrar_es_cambio(ruta):
    nuevo = T3.replace("C3:", "C4:")
    p = todas(ruta)
    p["C4"] = p.pop("C3")
    with pytest.raises(PiezasError) as e:
        validar(criterio=nuevo, p=p, huella_vieja=piezas.huella(T3))
    assert "C3 borrada" in str(e.value) and "C4 nueva" in str(e.value)


def test_c5f_espacios_y_crlf_no_son_cambio():
    """Duda 1, lectura (b), acordada: texto normalizado."""
    assert piezas.huella(T3) == piezas.huella(T3.replace("\n", "  \r\n") + "\r\n\r\n")


def test_c5_marca_cambiada_es_cambio():
    assert piezas.cambios_desde(piezas.huella(T3), T3.replace("C3:", "C3 [salida]:")) == {"C3": "cambiada"}


def test_c5_sin_huella_no_hay_comprobacion(ruta):
    assert validar(criterio=T3.replace("B.", "otra"), p=todas(ruta), huella_vieja=None)["piezas"]


# ---- C6: criterio viejo sin etiquetas


def test_c6a_partido_por_lineas():
    ps = piezas.partir("Que arranque.\n\nQue no rompa nada.")
    assert [(p.etiqueta, p.texto, p.partido_por_faros) for p in ps] == [
        ("C1", "Que arranque.", True),
        ("C2", "Que no rompa nada.", True),
    ]


def test_c6b_determinista():
    t = "Que arranque.\n\nQue no rompa nada."
    assert piezas.partir(t) == piezas.partir(t) and piezas.huella(t) == piezas.huella(t)


def test_c6c_otra_particion_se_rechaza():
    with pytest.raises(PiezasError) as e:
        validar(criterio="Que arranque.\nQue no rompa nada.", p={"C1": {"estado": "cumple", "prueba": "#1"}})
    assert e.value.code == "verify.piezas_missing" and "C2" in str(e.value)


def test_c6d_una_linea():
    assert len(piezas.partir("Que funcione.")) == 1


# ---- C7: [persona: X] solo la cierra X

CP = "C1 [persona: carol]: Lo prueba Carol en su móvil."


def test_c7a_la_cierra_su_persona():
    assert validar(criterio=CP, p={"C1": {"estado": "cumple", "prueba": "visto en su móvil"}}, agent="carol")


# C7d cambiado por Prima (24-nov): el dueño de la casa con piezas recibe aviso, no error (decisión 136); ver
# test_prima_24nov_*. Los demás siguen sin poder cerrarla.
@pytest.mark.parametrize("quien", ["bob", "alice"])
def test_c7bcd_otro_no_la_cierra(quien):
    """C7-b otro, C7-c la dueña de la tarea, C7-d el dueño de la casa CON piezas."""
    with pytest.raises(PiezasError) as e:
        validar(criterio=CP, p={"C1": {"estado": "cumple", "prueba": "x"}}, agent=quien)
    code, msg = codigo_y_mensaje(e)
    assert code == "verify.persona_only" and "carol" in msg


# ---- C8: forma de la prueba


def test_c8a_fichero_que_existe(ruta):
    assert validar(p=todas(ruta))


def test_c8b_fichero_que_no_existe(ruta, tmp_path):
    with pytest.raises(PiezasError) as e:
        validar(p=todas(ruta, C2={"estado": "cumple", "prueba": str(tmp_path / "no_esta.txt")}))
    code, msg = codigo_y_mensaje(e)
    assert code == "verify.prueba_forma" and "C2" in msg and "ruta" in msg


@pytest.mark.parametrize("vacia", ["", "   \n  "])
def test_c8c_salida_vacia(ruta, vacia):
    with pytest.raises(PiezasError) as e:
        validar(p=todas(ruta, C1={"estado": "cumple", "prueba": vacia}))
    assert e.value.code == "verify.prueba_forma" and "C1" in str(e.value)


def test_c8d_solo_forma_no_contenido(ruta):
    """Una salida inventada pasa: el daemon no sabe si es verdad. Para eso está otro lector."""
    assert validar(p=todas(ruta, C1={"estado": "cumple", "prueba": "17 passed"}))


def test_c8e_una_carpeta_vale_como_fichero(ruta, tmp_path):
    """Duda 3, lectura (b), acordada: como la Biblioteca desde #1154."""
    assert validar(p=todas(ruta, C2={"estado": "cumple", "prueba": str(tmp_path)}))


def test_propuesta_de_prima_es_aviso_no_rechazo(ruta):
    r = validar(p=todas(ruta, C3={"estado": "cumple", "prueba": "se lo expliqué a Pepe en el uno a uno"}))
    assert any("C3" in a and "referencia" in a for a in r["avisos"])


def test_propuesta_de_prima_sin_aviso_con_referencia(ruta):
    assert not validar(p=todas(ruta))["avisos"]


# ---- C10: aviso si el criterio no está en piezas


def test_c10_aviso_sin_piezas():
    assert piezas.aviso_sin_piezas("Que funcione.")
    assert piezas.aviso_sin_piezas(T3) is None
    assert piezas.aviso_sin_piezas("") is None


# ---- C13: sin criterio, como hoy


@pytest.mark.parametrize("criterio", [None, "", "  \n\r\n "])
def test_c13_sin_criterio(criterio):
    assert validar(criterio=criterio, p=None)["piezas"] == []


# ---- C14: el dueño de la casa sin piezas


def test_c14a_pepe_sin_piezas_pasa():
    r = validar(p=None, agent=DUENO)
    assert r["historial"] == "verificada por Pepe sin piezas" and not r["avisos"]


def test_c14b_otro_sin_piezas_no():
    with pytest.raises(PiezasError):
        validar(p=None, agent="bob")


def test_c14c_pepe_con_piezas_se_validan(ruta, tmp_path):
    with pytest.raises(PiezasError):
        validar(p=todas(ruta, C2={"estado": "cumple", "prueba": str(tmp_path / "no.txt")}), agent=DUENO)


def test_duda2_pepe_sin_piezas_sobre_una_persona_ajena_pasa_con_aviso():
    r = validar(criterio=CP, p=None, agent=DUENO)
    assert "quedan sin su persona: C1 [persona: carol]" in r["historial"]
    assert any("AVISO" in a and "carol" in a for a in r["avisos"])


def test_prima_24nov_pepe_con_piezas_sobre_una_persona_ajena_tambien_es_aviso():
    # Prima, revisión del 24-nov (1): para el dueño de la casa, la [persona] ajena es aviso en los dos caminos.
    r = validar(criterio=CP, p={"C1": {"estado": "cumple", "prueba": "me lo enseñó en su móvil"}}, agent=DUENO)
    assert any("AVISO" in a and "carol" in a for a in r["avisos"])
    assert "sin su persona" in r["historial"]


def test_prima_24nov_otro_con_piezas_sobre_una_persona_ajena_sigue_siendo_error():
    with pytest.raises(PiezasError) as e:
        validar(criterio=CP, p={"C1": {"estado": "cumple", "prueba": "me lo enseñó en su móvil"}}, agent="bob")
    assert e.value.code == "verify.persona_only"


# ---- C15 (Prima, 23-nov): los bots sin piezas, provisional hasta #1379

BOTS = {"system", "runner-bot", "verifier-bot"}


@pytest.mark.parametrize("bot", ["verifier-bot", "runner-bot"])
def test_c15_un_bot_sin_piezas_pasa_y_se_apunta(bot):
    r = validar(p=None, agent=bot, sistema=BOTS)
    assert r["historial"].startswith(f"verificada por {bot} sin piezas")


@pytest.mark.parametrize("quien", ["Fiel", "bob", "verifier_bot", "Verifier-Bot"])
def test_c15_quien_no_es_de_sistema_no_usa_la_exencion(quien):
    with pytest.raises(PiezasError) as e:
        validar(p=None, agent=quien, sistema=BOTS)
    assert e.value.code == "verify.piezas_missing"


def test_c15_sin_lista_de_sistema_no_hay_exencion():
    with pytest.raises(PiezasError):
        validar(p=None, agent="verifier-bot")
