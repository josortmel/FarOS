"""#868 tras la segunda pasada: el recorrido no retiene el candado de escritura mientras lee ficheros,
los cierres con muchas rutas van a UN worker, y los pequeños endurecimientos (P2-2, P2-3, borrado, log)."""

import logging
import os
import sqlite3
import threading
import time

import pytest

from faros import db, jobs, library as L
from test_850_library import mundo, _w, _filas  # noqa: F401
from test_850_ganchos import m, _ticket, _run_con_ticket  # noqa: F401


@pytest.fixture(autouse=True)
def _cesion_real(mundo, monkeypatch):
    """Aquí SÍ rige la pausa de verdad (la del código, no la de los demás tests)."""
    monkeypatch.setattr(L, "CEDER_S", L.CEDER_S_DEFECTO)


def test_el_recorrido_no_bloquea_a_otro_escritor(mundo, monkeypatch):
    """Un segundo escritor con el busy_timeout real (5 s) mientras un recorrido lee ficheros lentos:
    ningún «database is locked» y ninguna espera larga."""
    for i in range(80):
        _w(mundo.ses / "lentos" / f"f{i:02d}.md", f"# Lento {i}\n\ncuerpo")
    real = L._leer

    def lento(path, tope=None):
        time.sleep(0.03)                                   # disco lento: leer un fichero cuesta 30 ms
        return real(path, tope)
    monkeypatch.setattr(L, "_leer", lento)
    parar, esperas, errores = threading.Event(), [], []

    def escritor():
        c = db.connect(mundo.t / "faros.db")               # el busy_timeout de verdad (5000 ms)
        n = 0
        while not parar.is_set():
            n += 1
            t0 = time.perf_counter()
            try:
                c.execute("INSERT INTO projects (name, created_at) VALUES (?, 'x')", (f"p{n}",))
                c.commit()
            except sqlite3.OperationalError as e:
                errores.append(str(e))
            esperas.append(time.perf_counter() - t0)
            time.sleep(0.004)
        c.close()
    hilo = threading.Thread(target=escritor)
    hilo.start()
    time.sleep(0.05)
    t0 = time.perf_counter()
    res = L.recorrer(mundo.conn, mundo.env)
    dur = time.perf_counter() - t0
    parar.set()
    hilo.join()
    print(f"\nrecorrido de {res['registradas']} ficheros en {dur:.1f} s; {len(esperas)} escrituras del otro escritor, "
          f"espera máxima {max(esperas) * 1000:.0f} ms, errores {len(errores)}")
    assert res["registradas"] > 80 and dur > 1.5
    assert errores == []
    assert max(esperas) < 0.75, f"el escritor esperó {max(esperas):.2f} s"


# --------------------------------------------------------------- P2-2: un solo worker

def test_muchos_cierres_con_muchas_rutas_usan_un_solo_hilo_y_registran_todo(m, caplog):
    rutas = [str(_w(m.ses / "masa" / f"n{i:03d}.md", f"# Nota {i}")) for i in range(60)]
    L.parar_worker()
    hilos = set()
    for k in range(20):
        t = _ticket(m.conn, title=f"cierre {k}")
        L.al_cerrar(m.conn, t["id"], "file_path", " ".join(rutas[k * 3:k * 3 + 8]), m.env)
        hilos |= {h.ident for h in threading.enumerate() if h.name == "biblioteca-cierre"}
    L.esperar_pendientes()
    assert len(hilos) == 1, hilos
    assert "locked" not in caplog.text and "Traceback" not in caplog.text
    assert len(_filas(m.conn, origen="cierre")) == len({r for k in range(20) for r in rutas[k * 3:k * 3 + 8]})
    assert m.conn.execute("SELECT count(DISTINCT ticket_id) FROM library_links").fetchone()[0] == 20


def test_el_worker_cierra_su_conexion_al_parar(m, monkeypatch):
    class Espia:                                           # delega todo; recuerda si le llamaron a close()
        def __init__(self, c):
            self._c, self.cerrada = c, False

        def close(self):
            self.cerrada = True
            self._c.close()

        def __getattr__(self, n):
            return getattr(self._c, n)
    abiertas = []
    real = db.connect
    monkeypatch.setattr(db, "connect", lambda *a, **k: abiertas.append(Espia(real(*a, **k))) or abiertas[-1])
    L.parar_worker()
    rutas = [str(_w(m.ses / "w" / f"n{i}.md")) for i in range(8)]
    L.al_cerrar(m.conn, _ticket(m.conn)["id"], "file_path", " ".join(rutas), m.env)
    L.esperar_pendientes()
    assert len(abiertas) == 1 and not abiertas[0].cerrada
    L.parar_worker()
    assert abiertas[0].cerrada
    assert not any(h.name == "biblioteca-cierre" and h.is_alive() for h in threading.enumerate())


# --------------------------------------------------------------- P2-3, borrado, log, al_terminar_run

def test_run_output_con_miles_de_digitos_no_tira_las_demas_rutas(m):
    f = str(m.ses / "s1" / "informe.md")
    r = L.rutas_de_evidencia("run_output", "9" * 4301 + f" y {f}", m.env)
    assert f in r
    assert L.rutas_de_evidencia("run_output", "5 ok", m.env) == [str(m.art / "run_5")]
    assert L.rutas_de_evidencia("run_output", "1234567890123 ok", m.env) == []          # 13 dígitos: no es un run


@pytest.mark.parametrize("nombre", ["../victima.html", "..\\victima.html", "victima.txt", "x/../../victima.html",
                                    "C:/windows/x.html", "1-" + "a" * 100 + ".html", "-x.html", ""])
def test_borrar_publicado_solo_borra_nombres_validos(m, nombre):
    m.env.publicado_dir.mkdir(parents=True, exist_ok=True)
    victima = m.env.publicado_dir.parent / "victima.html"
    victima.write_text("no me borres")
    L._borrar_publicado(m.env, nombre)
    assert victima.exists()
    ok = m.env.publicado_dir / "7-valido.html"
    ok.write_text("x")
    L._borrar_publicado(m.env, "7-valido.html")
    assert not ok.exists()


def test_fila_manipulada_no_borra_fuera_de_publicado(m):
    L.recorrer(m.conn, m.env)
    viva = _filas(m.conn, titulo="Página Viva ñandú")[0]
    victima = m.t / "victima.html"
    victima.write_text("intacta")
    m.conn.execute("UPDATE library_items SET publicado='../victima.html' WHERE id=?", (viva["id"],))
    m.conn.commit()
    m.env.raices[:] = []                                   # la raíz se va: el ausente inmediato intenta borrar la copia
    m.env.raices_ilegibles = False
    L.recorrer(m.conn, m.env)
    assert victima.exists()


def test_finish_run_registra_el_fallo_del_gancho_en_el_log(m, monkeypatch, caplog):
    _, _, run = _run_con_ticket(m.conn)
    monkeypatch.setattr(L, "al_terminar_run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rota la biblioteca")))
    with caplog.at_level(logging.ERROR):
        assert jobs.finish_run(m.conn, run["id"], "ok", result_summary="ok")["status"] == "ok"
    assert "rota la biblioteca" in caplog.text and "biblioteca" in caplog.text.lower()


def test_al_terminar_run_sin_raices_ni_siquiera_intenta_registrar(m, monkeypatch):
    _w(m.art / "run_9" / "out.txt", "x")
    llamadas = []
    monkeypatch.setattr(L, "registrar", lambda *a, **k: llamadas.append(1))
    vacio = L.Entorno(raices=[], publicado_dir=m.t / "pub", artifacts_dir=m.art)
    assert L.al_terminar_run(m.conn, 9, 1, 1, vacio) is None and llamadas == []


def test_el_recorrido_cede_el_candado_tras_cada_escritura(mundo, monkeypatch):
    pausas = []
    real = time.sleep
    monkeypatch.setattr(L.time, "sleep", lambda s: pausas.append(s) or real(0))
    res = L.recorrer(mundo.conn, mundo.env)
    assert res["registradas"] >= 10 and len(pausas) >= res["registradas"]
    assert all(p >= 0.01 for p in pausas[:res["registradas"]])


def test_registrar_reintenta_si_otro_escritor_confirmo_entre_la_lectura_y_la_escritura(mundo, monkeypatch):
    f = mundo.ses / "s1" / "informe.md"
    real = L._registrar_dentro
    fallos = []

    def a_veces(*a, **k):
        if len(fallos) < 2:
            fallos.append(1)
            raise sqlite3.OperationalError("database is locked")
        return real(*a, **k)
    monkeypatch.setattr(L, "_registrar_dentro", a_veces)
    assert L.registrar(mundo.conn, f, env=mundo.env) is not None and len(fallos) == 2
    # dentro de una transacción ajena NO se reintenta (no se puede)
    fallos.clear()
    mundo.conn.execute("INSERT INTO projects (name, created_at) VALUES ('ajena', 'x')")
    assert L.registrar(mundo.conn, mundo.ses / "s1" / "estatica.html", env=mundo.env) is None and len(fallos) == 1
    mundo.conn.rollback()
    # y si sigue bloqueada tras los reintentos, devuelve None sin lanzar
    monkeypatch.setattr(L, "_registrar_dentro", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    assert L.registrar(mundo.conn, f, env=mundo.env) is None


def test_un_fallo_en_un_fichero_no_le_cuesta_una_falta(mundo, monkeypatch):
    L.recorrer(mundo.conn, mundo.env)
    f = mundo.ses / "s1" / "informe.md"
    os.utime(f, (f.stat().st_atime + 99, f.stat().st_mtime + 99))                 # cambió: irá por registrar
    real = L.registrar

    def rompe(conn, ruta, *a, **k):
        if str(ruta).endswith("informe.md"):
            raise RuntimeError("fallo raro")
        return real(conn, ruta, *a, **k)
    monkeypatch.setattr(L, "registrar", rompe)
    for _ in range(3):
        L.recorrer(mundo.conn, mundo.env)
    fila = _filas(mundo.conn, titulo="Informe uno")[0]
    assert (fila["estado"], fila["faltas"]) == ("presente", 0)


def test_un_bloque_real_de_6000_caracteres_conserva_su_formato_y_el_peor_caso_es_rapido():
    bloque = "**negrita** y *cursiva* y [enlace](https://a.test/x) " * 120                 # ~6 KB
    assert 6000 < len(bloque) < 10000
    h = L.md_a_html(bloque)
    assert "<strong>negrita</strong>" in h and "<em>cursiva</em>" in h and '<a href="https://a.test/x"' in h
    for p in ("[a](", "**a ", "*a ", "_a ", "[", "__a "):
        c = (p * 3000)[:9900]
        t0 = time.time()
        L.md_a_html(c)
        assert time.time() - t0 < 0.3, p
    assert "<strong>" not in L.md_a_html("**x** " + "y" * 10001)


def _escritor(mundo, parar, esperas, errores):
    c = db.connect(mundo.t / "faros.db")
    n = 0
    while not parar.is_set():
        n += 1
        t0 = time.perf_counter()
        try:
            c.execute("INSERT INTO projects (name, created_at) VALUES (?, 'x')", (f"q{n}",))
            c.commit()
        except sqlite3.OperationalError as e:
            errores.append(str(e))
        esperas.append(time.perf_counter() - t0)
        time.sleep(0.004)
    c.close()


def test_mezcla_de_cambiados_y_sin_cambios_no_lee_con_el_candado_cogido(mundo, monkeypatch):
    """Escrituras rápidas pendientes (ficheros sin cambios) y, entre ellas, UN fichero que hay que leer y
    es lentísimo (0,5 s): esa lectura no puede ocurrir con la transacción abierta."""
    ficheros = [_w(mundo.ses / "mezcla" / f"f{i:03d}.md", f"# Mezcla {i}") for i in range(120)]
    L.recorrer(mundo.conn, mundo.env)
    lento = ficheros[60]
    os.utime(lento, (lento.stat().st_atime + 50, lento.stat().st_mtime + 50))
    real = L._leer
    # #1354: the property itself, not its symptom. Measuring how long a second writer
    # waits (< 0.3 s) failed on the slow CI machine (0.63 s, then 0.33 s with Python 3.11)
    # and could not tell a slow runner from the bug. At the moment of the slow read, can
    # another connection take the write lock at once? A yes or a no. (An open deferred
    # transaction is fine: registrar reads inside a SAVEPOINT and only writes afterwards.)
    candado_libre_al_leer = []

    def leer(p, t=None):
        if str(p).endswith("f060.md"):
            otra = sqlite3.connect(mundo.t / "faros.db", timeout=0)
            try:
                otra.execute("BEGIN IMMEDIATE")
                otra.rollback()
                candado_libre_al_leer.append(True)
            except sqlite3.OperationalError:
                candado_libre_al_leer.append(False)
            finally:
                otra.close()
            time.sleep(0.5)
        return real(p, t)
    monkeypatch.setattr(L, "_leer", leer)
    real_stat = os.stat
    monkeypatch.setattr(L.os, "stat", lambda p, *a, **k: (time.sleep(0.002), real_stat(p, *a, **k))[1])
    parar, esperas, errores = threading.Event(), [], []
    hilo = threading.Thread(target=_escritor, args=(mundo, parar, esperas, errores))
    hilo.start()
    time.sleep(0.05)
    L.recorrer(mundo.conn, mundo.env)
    parar.set(); hilo.join()
    print(f"mezcla: espera máxima {max(esperas) * 1000:.0f} ms, errores {len(errores)}")
    # Every read found the write lock free. There may be more than one: if the other writer
    # commits during the read, registrar retries (up to 4 times), which the slow CI machine
    # did every time (4 reads, all free).
    assert candado_libre_al_leer and all(candado_libre_al_leer), candado_libre_al_leer
    assert errores == []


def test_un_barrido_de_ficheros_sin_cambios_no_retiene_el_candado_todo_el_barrido(mundo, monkeypatch):
    for i in range(500):
        _w(mundo.ses / "quietos" / f"q{i:03d}.md", f"# Quieto {i}")
    L.recorrer(mundo.conn, mundo.env)
    real = os.stat
    monkeypatch.setattr(L.os, "stat", lambda p, *a, **k: (time.sleep(0.003), real(p, *a, **k))[1])
    pausas = []
    rs = time.sleep
    monkeypatch.setattr(L.time, "sleep", lambda s: pausas.append(s) or rs(s))
    parar, esperas, errores = threading.Event(), [], []
    hilo = threading.Thread(target=_escritor, args=(mundo, parar, esperas, errores))
    hilo.start()
    time.sleep(0.05)
    t0 = time.perf_counter()
    res = L.recorrer(mundo.conn, mundo.env)
    dur = time.perf_counter() - t0
    parar.set(); hilo.join()
    print(f"sin cambios: {res['vistas']} vistos en {dur:.1f} s, espera máxima {max(esperas) * 1000:.0f} ms")
    assert res["vistas"] > 500 and dur > 1.5
    assert errores == [] and max(esperas) < 0.45, max(esperas)           # CANDADO_MAX_S (0.2 s) + márgenes, no los ~2 s del barrido
    assert len([p for p in pausas if p >= 0.01]) >= 5                      # y cede el candado tras cada commit


# --------------------------------------------------------------- tercera pasada: NEW-A, NEW-B, NEW-D

def test_new_a_un_fichero_que_falla_no_cobra_faltas_a_sus_vecinos(mundo, monkeypatch):
    monkeypatch.setattr(L, "CEDER_S", 0.0)
    for i in range(400):
        _w(mundo.ses / "cuatrocientos" / f"c{i:03d}.md", f"# Cuatrocientos {i}")
    L.recorrer(mundo.conn, mundo.env)
    ids_publicadas = {r["id"]: r["publicado"] for r in _filas(mundo.conn) if r["publicado"]}
    malo = str(mundo.ses / "cuatrocientos" / "c200.md").lower()
    real = os.stat

    def stat(p, *a, **k):
        if str(p).lower().endswith("c200.md"):
            raise PermissionError("bloqueado por el antivirus")
        return real(p, *a, **k)
    monkeypatch.setattr(L.os, "stat", stat)
    for _ in range(4):
        L.recorrer(mundo.conn, mundo.env)
        for r in _filas(mundo.conn):
            assert (r["estado"], r["faltas"]) == ("presente", 0), (r["ruta"], r["estado"], r["faltas"])
        for i, nombre in ids_publicadas.items():
            assert (mundo.env.publicado_dir / nombre).is_file()


def test_new_d_registrar_que_se_rinde_no_hace_desaparecer_al_fichero(mundo, monkeypatch):
    monkeypatch.setattr(L, "CEDER_S", 0.0)
    L.recorrer(mundo.conn, mundo.env)
    f = mundo.ses / "s1" / "informe.md"
    os.utime(f, (f.stat().st_atime + 70, f.stat().st_mtime + 70))                 # cambió: irá por registrar
    real = L.registrar
    monkeypatch.setattr(L, "registrar", lambda conn, ruta, *a, **k: None if str(ruta).endswith("informe.md") else real(conn, ruta, *a, **k))
    for _ in range(4):
        L.recorrer(mundo.conn, mundo.env)
        fila = _filas(mundo.conn, titulo="Informe uno")[0]
        assert (fila["estado"], fila["faltas"]) == ("presente", 0)
    f.unlink()                                                                    # y si de verdad desaparece, sí se cobra
    monkeypatch.undo()
    for _ in range(2):
        L.recorrer(mundo.conn, mundo.env)
    assert _filas(mundo.conn, titulo="Informe uno")[0]["estado"] == "ausente"


def test_new_b_parar_el_worker_vacia_la_cola_y_no_pierde_el_hilo_vivo(m, monkeypatch, caplog):
    L.parar_worker()
    real = L.registrar
    monkeypatch.setattr(L, "registrar", lambda *a, **k: (time.sleep(0.15), real(*a, **k))[1])
    tickets = []
    for k in range(4):
        t = _ticket(m.conn, title=f"cola {k}")
        tickets.append(t["id"])
        rutas = [str(_w(m.ses / "cola" / f"k{k}_{i}.md")) for i in range(6)]
        L.al_cerrar(m.conn, t["id"], "file_path", " ".join(rutas), m.env)      # 6 rutas: va al worker
    hilo = L._worker_hilo[0]
    assert hilo is not None and hilo.is_alive()
    t0 = time.perf_counter()
    with caplog.at_level(logging.WARNING):
        L.parar_worker(timeout=0.3)                                            # no da tiempo a todo (24 x 0,15 s)
    assert time.perf_counter() - t0 < 1.0                                      # el plazo se aplica UNA vez
    assert "quedan" in caplog.text and str(tickets[-1]) in caplog.text         # cuántos y qué tickets
    assert L._worker_hilo[0] is hilo and hilo.is_alive()                       # no se olvida un hilo vivo
    n = threading.active_count()
    L.al_cerrar(m.conn, _ticket(m.conn, title="otro")["id"], "file_path",
                " ".join(str(_w(m.ses / "cola" / f"z{i}.md")) for i in range(6)), m.env)
    assert threading.active_count() == n                                       # y no nace un segundo worker
    monkeypatch.setattr(L, "registrar", real)
    L.parar_worker(timeout=15)
    assert not hilo.is_alive()
    assert len(_filas(m.conn, origen="cierre")) == 24 + 6                      # nada se perdió


def test_new_b_con_tiempo_suficiente_se_drena_todo(m, monkeypatch, caplog):
    L.parar_worker()
    for k in range(3):
        t = _ticket(m.conn, title=f"drena {k}")
        L.al_cerrar(m.conn, t["id"], "file_path", " ".join(str(_w(m.ses / "drena" / f"d{k}_{i}.md")) for i in range(6)), m.env)
    with caplog.at_level(logging.WARNING):
        L.parar_worker(timeout=10)
    assert "quedan" not in caplog.text and len(_filas(m.conn, origen="cierre")) == 18
