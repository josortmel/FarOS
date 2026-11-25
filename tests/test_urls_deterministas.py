"""#378, parte 2: la comprobación determinista de URL de la revista, ya dentro de faros/, probada sin red.

Cada caso es una regla del docstring de faros/urls_deterministas.py. La página se simula con una función abrir_url
falsa, así que los tests no salen a internet.
"""

import urllib.error
from datetime import date

from faros.urls_deterministas import comprobar_linea, comprobar_nota, recuento

HOY = date(2026, 10, 19)


def pagina(
    title="DeepSeek V4.1 Flash beats rivals",
    cuerpo_extra="",
    fecha="2026-10-17",
    final="https://the-decoder.com/deepseek-flash/",
):
    cuerpo = (
        f"<html><head><title>{title}</title>"
        f'<meta property="article:published_time" content="{fecha}T10:00:00Z"></head>'
        f"<body><p>Scores 77.0 % on DeepSWE at 0.20 dollars per task.</p>{cuerpo_extra}</body></html>"
    )

    def abrir(url):
        return final, title, cuerpo

    return abrir


def linea(
    url="https://the-decoder.com/deepseek-flash/",
    fecha="2026-10-17",
    texto="qué cambia para nosotros: 77,0 % en DeepSWE",
    titulo="DeepSeek V4.1 Flash, más barato por tarea",
):
    return f"[nos toca] {titulo} — {url} — {fecha} — The Decoder — {texto}"


def no_abrir(url):
    raise AssertionError("no debía abrir la URL")


def test_1207_sesenta_urls_lentas_caben_en_el_presupuesto_y_en_su_orden(monkeypatch):
    # El 13-nov, 60 URL en serie con la red lenta pasaron de 600 s y la revista se cayó. A escala: 60 líneas que
    # tardan 0,5 s cada una (30 s en serie) y un presupuesto de 3 s.
    import time

    from faros import urls_deterministas as u

    monkeypatch.setattr(u, "PRESUPUESTO_S", 3, raising=False)
    buena = pagina()

    def lenta(url):
        time.sleep(0.5)
        return buena(url)

    lineas = [
        linea(url=f"https://the-decoder.com/deepseek-flash-{i}/", titulo=f"DeepSeek V4.1 Flash número {i}")
        for i in range(60)
    ]
    lineas[57] = linea(url="https://the-decoder.com/", titulo="Portada al final")  # se decide sin abrir nada
    t0 = time.monotonic()
    out = u.comprobar_nota("\n".join(lineas), HOY, lenta)
    tarda = time.monotonic() - t0
    assert tarda < 4.5, tarda  # antes: 30 s
    assert [r["titulo"] for r in out] == [
        f"DeepSeek V4.1 Flash número {i}" if i != 57 else "Portada al final" for i in range(60)
    ]
    assert out[57]["resultado"] == "no_hecho" and "portada" in out[57]["motivo"]
    tarde = [r for r in out if r["resultado"] == "incierto"]
    assert tarde and all(u.A_TIEMPO in r["motivo"] for r in tarde)
    assert {r["resultado"] for r in out} <= {"hecho", "incierto", "no_hecho"}
    assert sum(r["resultado"] == "hecho" for r in out) >= 20  # 6 a la vez durante 3 s: unas 36


def test_1207_con_tiempo_de_sobra_sale_lo_mismo_que_en_serie():
    from faros import urls_deterministas as u

    nota = "\n".join([linea(), linea(url="https://the-decoder.com/"), "texto suelto", linea(fecha="2099-01-01")])
    en_serie = [r for r in (u.comprobar_linea(l, HOY, pagina()) for l in nota.splitlines()) if r]
    assert u.comprobar_nota(nota, HOY, pagina(), paralelas=6, presupuesto_s=30) == en_serie


def test_buena_es_hecho():
    r = comprobar_linea(linea(), HOY, pagina())
    assert r["resultado"] == "hecho", r


def test_portada_no_abre_y_es_no_hecho():
    r = comprobar_linea(linea(url="https://the-decoder.com/"), HOY, no_abrir)
    assert r["resultado"] == "no_hecho" and "portada" in r["motivo"]


def test_sin_fecha_es_no_hecho():
    l = "[nos toca] Algo — https://x.org/a/b — The Decoder — por qué importa: nada"
    r = comprobar_linea(l, HOY, no_abrir)
    assert r["resultado"] == "no_hecho" and "fecha" in r["motivo"]


def test_fecha_futura_es_no_hecho():
    r = comprobar_linea(linea(fecha="2026-10-25"), HOY, no_abrir)
    assert r["resultado"] == "no_hecho" and "futura" in r["motivo"]


def test_404_es_no_hecho():
    def abrir(url):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    r = comprobar_linea(linea(), HOY, abrir)
    assert r["resultado"] == "no_hecho" and "404" in r["motivo"]


def test_403_es_incierto():
    def abrir(url):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    assert comprobar_linea(linea(), HOY, abrir)["resultado"] == "incierto"


def test_redirige_a_portada_es_no_hecho():
    r = comprobar_linea(linea(), HOY, pagina(final="https://the-decoder.com/"))
    assert r["resultado"] == "no_hecho" and "portada" in r["motivo"]


def test_cifra_que_no_esta_en_la_pagina_es_no_hecho():
    r = comprobar_linea(linea(texto="qué cambia para nosotros: 81,5 % en DeepSWE"), HOY, pagina())
    assert r["resultado"] == "no_hecho" and "81.5" in r["motivo"]


def test_arxiv_listado_hasta_tres_dias_despues_vale():
    # enviado el 15 (fecha de la página), listado y escrito en el ítem el 18
    r = comprobar_linea(linea(fecha="2026-10-18", texto="por qué importa: algo"), HOY, pagina(fecha="2026-10-15"))
    assert r["resultado"] == "hecho", r


def test_fecha_del_item_antes_que_la_pagina_es_no_hecho():
    r = comprobar_linea(linea(fecha="2026-10-12", texto="por qué importa: algo"), HOY, pagina(fecha="2026-10-15"))
    assert r["resultado"] == "no_hecho" and "no coincide" in r["motivo"]


def test_titulo_sin_palabras_en_comun_es_incierto():
    r = comprobar_linea(linea(texto="por qué importa: algo"), HOY, pagina(title="Página distinta del todo"))
    assert r["resultado"] == "incierto"


def test_semanal_sin_fecha_no_cae_por_la_fecha():
    l = "[SE QUEDÓ] DeepSeek V4.1 Flash — https://the-decoder.com/deepseek-flash/ — señal: 77.0 % en DeepSWE"
    r = comprobar_linea(l, HOY, pagina())
    assert r["resultado"] == "hecho", r


def test_semanal_un_codigo_http_citado_no_cuenta_como_cifra():
    l = "[AÚN NO SE SABE] DeepSeek V4.1 Flash — https://the-decoder.com/deepseek-flash/ — señal: la fuente devolvió 404 ayer"
    assert comprobar_linea(l, HOY, pagina())["resultado"] == "hecho"


def test_semanal_los_numeros_de_una_url_secundaria_no_cuentan_como_cifras():
    # Prima, 24-nov: el id de arXiv de la segunda fuente se exigía en la página principal
    l = (
        "[SE QUEDÓ] DeepSeek V4.1 Flash — https://the-decoder.com/deepseek-flash/ — señal: segunda fuente "
        "independiente, https://arxiv.org/abs/2611.30185 , dice lo mismo"
    )
    assert comprobar_linea(l, HOY, pagina())["resultado"] == "hecho"


def test_las_urls_sin_esquema_tampoco_ponen_cifras():
    l = linea(
        texto="qué cambia para nosotros: 77,0 % en DeepSWE; lo cuenta theregister.com/2026/11/23/flash/ "
        "y github.com/deepseek-ai/flash/issues/123"
    )
    assert comprobar_linea(l, HOY, pagina())["resultado"] == "hecho"


def test_una_cifra_de_verdad_sigue_contando_aunque_la_linea_lleve_una_url():
    l = (
        "[SE QUEDÓ] DeepSeek V4.1 Flash — https://the-decoder.com/deepseek-flash/ — señal: 88,5 % según "
        "https://arxiv.org/abs/2611.30185"
    )
    r = comprobar_linea(l, HOY, pagina())
    assert r["resultado"] == "no_hecho" and r["motivo"].endswith(": 88.5"), r


def test_linea_que_no_es_item_se_ignora_y_recuento():
    nota = "\n".join(
        ["# Revista I+D — 2026-10-19", linea(), linea(url="https://the-decoder.com/"), "Fuentes consultadas"]
    )
    out = comprobar_nota(nota, HOY, pagina())
    assert len(out) == 2
    assert recuento(out) == {"hecho": 1, "no_hecho": 1, "incierto": 0}


# ------------------------------------------------------------------ #483 (22-oct): la ventana y las repetidas, por código
# Contrato de Prima: entra la nota anterior (su fecha y sus URL); un ítem publicado antes que ella, o con una URL que ya
# salió en ella, es no_hecho sin que opine el juez. Caso real: la revista del 9-oct (run 35) pasó con 3 ítems de dos días
# antes y 2 repetidos de la nota del 8.

ANTERIOR = date(2026, 10, 16)


def test_item_anterior_a_la_nota_anterior_es_no_hecho_sin_abrir():
    r = comprobar_linea(linea(fecha="2026-10-15"), HOY, no_abrir, desde=ANTERIOR)
    assert r["resultado"] == "no_hecho" and r["fuera_de_ventana"] is True
    assert "2026-10-16" in r["motivo"]


def test_item_del_mismo_dia_que_la_nota_anterior_entra():
    r = comprobar_linea(linea(fecha="2026-10-16"), HOY, pagina(fecha="2026-10-16"), desde=ANTERIOR)
    assert r["resultado"] == "hecho" and not r.get("fuera_de_ventana")


def test_arxiv_enviado_antes_pero_listado_despues_entra():
    l = (
        "[nos toca] REM: memoria de experiencia — https://arxiv.org/abs/2610.27377 — 2026-10-15 — "
        "(listado del 17 oct) arXiv cs.AI /new — qué cambia para nosotros: política de memoria"
    )
    r = comprobar_linea(
        l,
        HOY,
        pagina(title="REM: memoria experiencia", fecha="2026-10-15", final="https://arxiv.org/abs/2610.27377"),
        desde=ANTERIOR,
    )
    assert r["resultado"] == "hecho" and not r.get("fuera_de_ventana")


def test_arxiv_listado_antes_de_la_nota_anterior_es_no_hecho():
    l = (
        "[nos toca] REM: memoria de experiencia — https://arxiv.org/abs/2610.27377 — 2026-10-13 — "
        "(listado del 15 oct) arXiv cs.AI /new — qué cambia para nosotros: política de memoria"
    )
    r = comprobar_linea(l, HOY, no_abrir, desde=ANTERIOR)
    assert r["resultado"] == "no_hecho" and r["fuera_de_ventana"] is True


def test_arxiv_listado_pegado_a_la_fecha_lee_la_fecha_y_entra():
    # #835: el prompt del job 13 pide «fecha de envío seguida de (listado del DD mes)», sin « — » en medio.
    # Run 64 (29-oct): esta forma salía no_hecho «no lleva fecha» y la revista quitó 5 papers buenos.
    l = (
        "[mueve el campo] Un paper — https://arxiv.org/abs/2610.35950 — 2026-10-15 (listado del 17 oct) — "
        "arXiv cs.AI — por qué importa: algo."
    )
    r = comprobar_linea(
        l, HOY, pagina(title="Un paper", fecha="2026-10-15", final="https://arxiv.org/abs/2610.35950"), desde=ANTERIOR
    )
    assert r["resultado"] == "hecho" and not r.get("fuera_de_ventana")


def test_arxiv_con_enviado_delante_de_la_fecha_lee_la_fecha_y_entra():
    # #921: run 67 (2-nov) escribió «enviado 2026-10-29 (listado del 2 nov)»; salía no_hecho «no lleva fecha»
    # y la revista quitó un paper listado dentro de la ventana.
    for prefijo in ("enviado ", "enviado el ", "Enviado "):
        l = (
            "[nos toca] Un paper — https://arxiv.org/abs/2610.35950 — " + prefijo + "2026-10-15 (listado del 17 oct) — "
            "arXiv cs.AI — qué cambia para nosotros: algo."
        )
        r = comprobar_linea(
            l,
            HOY,
            pagina(title="Un paper", fecha="2026-10-15", final="https://arxiv.org/abs/2610.35950"),
            desde=ANTERIOR,
        )
        assert r["resultado"] == "hecho" and not r.get("fuera_de_ventana"), (prefijo, r)


def test_arxiv_listado_pegado_antes_de_la_nota_anterior_es_no_hecho_por_ventana():
    l = (
        "[mueve el campo] Un paper — https://arxiv.org/abs/2610.35950 — 2026-10-13 (listado del 15 oct) — "
        "arXiv cs.AI — por qué importa: algo."
    )
    r = comprobar_linea(l, HOY, no_abrir, desde=ANTERIOR)
    assert r["resultado"] == "no_hecho" and r["fuera_de_ventana"] is True


def test_fecha_sin_parentesis_sigue_igual():
    r = comprobar_linea(linea(fecha="2026-10-17"), HOY, pagina(fecha="2026-10-17"), desde=ANTERIOR)
    assert r["resultado"] == "hecho"
    r = comprobar_linea(linea(fecha="17-10-2026"), HOY, no_abrir)
    assert r["resultado"] == "no_hecho" and "no lleva fecha" in r["motivo"]


def test_url_repetida_de_la_nota_anterior_es_no_hecho_sin_abrir():
    r = comprobar_linea(linea(), HOY, no_abrir, desde=ANTERIOR, urls_previas={"https://the-decoder.com/deepseek-flash"})
    assert r["resultado"] == "no_hecho" and "nota anterior" in r["motivo"]


def test_sin_nota_anterior_nada_cambia():
    r = comprobar_linea(linea(fecha="2026-10-01"), HOY, pagina(fecha="2026-10-01"))
    assert r["resultado"] == "hecho" and "fuera_de_ventana" not in r


def test_main_con_anterior_lee_su_fecha_y_sus_url(tmp_path, monkeypatch, capsys):
    import json

    from faros import urls_deterministas as u

    previa = tmp_path / "2026-10-16.md"
    previa.write_text(linea() + "\n", encoding="utf-8")
    hoy_nota = tmp_path / "2026-10-19.md"
    hoy_nota.write_text(
        linea() + "\n" + linea(url="https://ejemplo.com/otra", fecha="2026-10-14") + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(u, "abrir", no_abrir)
    u.main([str(hoy_nota), "--anterior", str(previa)])
    salida = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert [s.get("resultado") for s in salida[:2]] == ["no_hecho", "no_hecho"]
    assert salida[-1] == {"hecho": 0, "no_hecho": 2, "incierto": 0}


def test_changelog_misma_url_otra_version_no_es_repetida():
    # el changelog es UN fichero para todas las versiones: la URL se repite cada día y el tema no (caso real, 8 y 9-oct)
    from faros.urls_deterministas import urls_de_nota

    cl = "https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md"
    previa = urls_de_nota(
        f"[nos toca] Claude Code v2.1.294: hooks — {cl} — 2026-10-08 — Anthropic — qué cambia para nosotros: x"
    )
    sin_title = lambda url: (url, "", "## 2.1.295\n- algo\n")  # noqa: E731
    r = comprobar_linea(
        f"[nos toca] Claude Code v2.1.295 — {cl} — 2026-10-09 — Anthropic — qué cambia para nosotros: y",
        date(2026, 10, 9),
        sin_title,
        desde=date(2026, 10, 8),
        urls_previas=previa,
    )
    assert r["resultado"] == "hecho"
    r = comprobar_linea(
        f"[nos toca] Claude Code v2.1.294: otra vez — {cl} — 2026-10-09 — Anthropic — qué cambia para nosotros: y",
        date(2026, 10, 9),
        no_abrir,
        desde=date(2026, 10, 8),
        urls_previas=previa,
    )
    assert r["resultado"] == "no_hecho" and "nota anterior" in r["motivo"]


# --- #720: URL secundarias como filas hijas, y la URL se valida antes de pedirla ---

SEMANAL = (
    "[SE QUEDÓ] LiteLLM 1.107.0 comprometido en PyPI — https://pypi.org/project/litellm/1.107.0/ — señal: "
    "la página de PyPI la muestra en cuarentena; el análisis de SafeDep "
    "(safedep.io/litellm-pypi-compromise-credential-stealer-backdoor/) detalla el robo, y el blog de LiteLLM que "
    "abrí (litellm.ai/blog) no menciona el incidente; ver también https://pypi.org/project/litellm/1.107.0/."
)


def test_secundarias_se_extraen_con_y_sin_esquema_sin_repetir_la_principal():
    from faros.urls_deterministas import urls_secundarias

    assert urls_secundarias(SEMANAL) == [
        "https://safedep.io/litellm-pypi-compromise-credential-stealer-backdoor/",
        "https://litellm.ai/blog",
    ]


def test_nota_saca_una_fila_hija_por_secundaria():
    abiertas = []

    def abrir(url):
        abiertas.append(url)
        if "litellm.ai" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return url, "PyPI litellm 1.107.0", "<html></html>"

    out = comprobar_nota(SEMANAL, HOY, abrir)
    assert [r.get("secundaria", False) for r in out] == [False, True, True]
    hijas = out[1:]
    assert hijas[0]["resultado"] == "hecho" and hijas[0]["padre"] == "https://pypi.org/project/litellm/1.107.0/"
    assert hijas[1]["resultado"] == "no_hecho" and "404" in hijas[1]["motivo"]
    assert recuento(out)["no_hecho"] >= 1


def test_secundaria_lenta_es_incierto_y_no_para_la_nota():
    import socket

    def abrir(url):
        if "safedep" in url:
            raise socket.timeout("timed out")
        return url, "PyPI litellm 1.107.0", "<html></html>"

    out = comprobar_nota(SEMANAL, HOY, abrir)
    assert len(out) == 3
    assert out[1]["resultado"] == "incierto" and "timeout" in out[1]["motivo"].lower()
    assert out[2]["resultado"] == "hecho"


def test_secundaria_con_forma_invalida_se_rechaza_sin_pedirla():
    from faros.urls_deterministas import comprobar_secundaria

    for mala in (
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8080/api/x",
        "https://user:clave@ejemplo.com/a",
        "http://localhost/x",
        "http://10.0.0.5/admin",
    ):
        r = comprobar_secundaria(mala, "https://p.org/a", "t", no_abrir)
        assert r["resultado"] == "no_hecho" and "no válida" in r["motivo"], (mala, r)


def test_principal_con_ip_privada_es_no_hecho_sin_pedirla():
    r = comprobar_linea(linea(url="http://192.168.1.10/informe"), HOY, no_abrir)
    assert r["resultado"] == "no_hecho" and "no válida" in r["motivo"]


def test_url_segura_mira_a_que_resuelve_el_host():
    from faros.urls_deterministas import url_segura

    publica = lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))]  # noqa: E731
    interna = lambda host, port: [(2, 1, 6, "", ("10.1.2.3", 0))]  # noqa: E731
    assert url_segura("https://ejemplo.com/a", publica) is None
    assert "no pública" in url_segura("https://ejemplo.com/a", interna)


def test_abrir_bloquea_antes_de_pedir_si_el_host_resuelve_a_interna():
    import pytest

    from faros.urls_deterministas import abrir

    interna = lambda host, port: [(2, 1, 6, "", ("127.0.0.1", 0))]  # noqa: E731
    with pytest.raises(ValueError, match="bloqueada"):
        abrir("https://trampa.ejemplo.com/x", resolver=interna)


def test_dns_caido_es_incierto_no_no_hecho():
    """Revisión de Hilo (23-oct): un fallo al RESOLVER el host es de red, no prueba de que la URL no exista.
    Antes salía no_hecho (ValueError de URL bloqueada) y un corte de DNS tumbaba la revista por un ítem real."""
    import socket

    from faros import urls_deterministas as u

    def caido(h, p):
        raise socket.gaierror("temporary failure in name resolution")

    r = u.comprobar_secundaria(
        "https://example.com/x", "https://p.com/a", "t", abrir_url=lambda x: u.abrir(x, resolver=caido)
    )
    assert r["resultado"] == "incierto"
