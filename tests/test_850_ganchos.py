"""#868 (Biblioteca, tarea 3): ganchos de cierre y de run, y el recorrido en su hilo (Spec v2 §3)."""

import threading
import time

import pytest
from test_850_library import _filas, _w, mundo  # noqa: F401

from faros import db, jobs
from faros import library as L
from faros import service as s


@pytest.fixture
def m(mundo, monkeypatch):
    """El mundo temporal con los ganchos apuntando a SU entorno (nunca a la casa)."""
    monkeypatch.setattr(L, "entorno_cacheado", lambda: mundo.env)
    return mundo


def _ticket(conn, **kw):
    args = dict(agent="owner", title="Hacer algo", auto_accept=True, verification_level="self")
    args.update(kw)
    return s.propose_ticket(conn, **args)["ticket"]


def _links(conn, item_id):
    return [
        tuple(r)
        for r in conn.execute("SELECT ticket_id, job_id, run_id FROM library_links WHERE item_id=?", (item_id,))
    ]


# --------------------------------------------------------------- al_cerrar


def test_al_cerrar_registra_y_enlaza_segun_el_tipo(m):
    f = m.ses / "s1" / "informe.md"
    t = _ticket(m.conn)
    assert L.al_cerrar(m.conn, t["id"], "file_path", f"hecho en {f}", m.env) == 1
    fila = _filas(m.conn, titulo="Informe uno")[0]
    assert fila["origen"] == "cierre" and _links(m.conn, fila["id"]) == [(t["id"], None, None)]
    for tipo in ("memory_id", "peer_attestation", "proof"):
        assert L.al_cerrar(m.conn, t["id"], tipo, f"mira {f}", m.env) == 0
    assert L.al_cerrar(m.conn, t["id"], "command_output", f"escribió {m.ses / 'Hilo_textos' / 'x.md'}", m.env) == 0
    n = len(_filas(m.conn))
    assert L.al_cerrar(m.conn, t["id"], "file_path", f"hecho en {f}", m.env) == 1  # idempotente
    assert len(_filas(m.conn)) == n and len(_links(m.conn, fila["id"])) == 1


def test_al_cerrar_run_output_registra_la_carpeta_del_run(m):
    t = _ticket(m.conn)
    assert L.al_cerrar(m.conn, t["id"], "run_output", "5 terminó bien", m.env) == 1
    assert _filas(m.conn, titulo="run_5")[0]["clase"] == "resultado"


def test_al_cerrar_sin_raices_no_hace_nada_y_no_lanza(m):
    vacio = L.Entorno(raices=[], publicado_dir=m.t / "pub")
    assert L.al_cerrar(m.conn, 1, "file_path", str(m.ses / "s1" / "informe.md"), vacio) == 0
    assert L.al_cerrar(None, 1, "file_path", "x", m.env) == 0
    assert L.al_cerrar(m.conn, 1, "file_path", None, m.env) == 0
    assert _filas(m.conn) == []


def test_cerrar_un_ticket_enlaza_su_evidencia(m):
    f = m.ses / "s1" / "informe.md"
    t = _ticket(m.conn)
    out = s.complete_ticket(m.conn, "owner", t["id"], "file_path", str(f))
    assert out["status"] == "verified"
    fila = _filas(m.conn, titulo="Informe uno")[0]
    assert _links(m.conn, fila["id"]) == [(t["id"], None, None)]


def test_cerrar_no_falla_ni_cambia_si_library_lanza(m, monkeypatch):
    f = str(m.ses / "s1" / "informe.md")
    ref = s.complete_ticket(m.conn, "owner", _ticket(m.conn)["id"], "file_path", f)
    monkeypatch.setattr(L, "al_cerrar", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("library rota")))
    t = _ticket(m.conn, title="Otro")
    out = s.complete_ticket(m.conn, "owner", t["id"], "file_path", f)
    assert out["status"] == ref["status"] == "verified"
    assert {k: out[k] for k in ("evidence_type", "evidence", "verified_by")} == {
        k: ref[k] for k in ("evidence_type", "evidence", "verified_by")
    }
    monkeypatch.undo()
    monkeypatch.setattr(L, "registrar", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("registrar roto")))
    monkeypatch.setattr(L, "entorno_cacheado", lambda: m.env)
    t2 = _ticket(m.conn, title="Y otro")
    assert s.complete_ticket(m.conn, "owner", t2["id"], "file_path", f)["status"] == "verified"
    monkeypatch.setattr(L, "entorno_cacheado", lambda: (_ for _ in ()).throw(OSError("sin casa")))
    t3 = _ticket(m.conn, title="Y otro más")
    assert s.complete_ticket(m.conn, "owner", t3["id"], "file_path", f)["status"] == "verified"


def test_el_gancho_corre_despues_del_commit(m, monkeypatch):
    vistos = []
    real = L.al_cerrar

    def espia(conn, tid, *a, **k):
        vistos.append(
            (conn.in_transaction, conn.execute("SELECT status FROM tickets WHERE id=?", (tid,)).fetchone()[0])
        )
        return real(conn, tid, *a, **k)

    monkeypatch.setattr(L, "al_cerrar", espia)
    t = _ticket(m.conn)
    s.complete_ticket(m.conn, "owner", t["id"], "file_path", str(m.ses / "s1" / "informe.md"))
    assert vistos == [(False, "verified")]


def test_cierre_con_50_rutas_no_espera_y_acaba_registrando_las_50(m):
    rutas = [str(_w(m.ses / "masa" / f"n{i}.md", f"# Nota {i}")) for i in range(50)]
    t = _ticket(m.conn)
    t0 = time.perf_counter()
    s.complete_ticket(m.conn, "owner", t["id"], "file_path", " ".join(rutas))
    dt = time.perf_counter() - t0
    L.esperar_pendientes()
    total = time.perf_counter() - t0
    print(
        f"cierre con 50 rutas: la peticion {dt * 1000:.0f} ms; con el registro en segundo plano {total * 1000:.0f} ms"
    )
    assert len(_filas(m.conn, origen="cierre")) == 50
    assert m.conn.execute("SELECT count(*) FROM library_links WHERE ticket_id=?", (t["id"],)).fetchone()[0] == 50
    assert dt < 0.2


def test_con_pocas_rutas_el_registro_es_en_linea(m):
    rutas = [str(_w(m.ses / "poca" / f"n{i}.md", f"# Nota {i}")) for i in range(L.EN_LINEA_HASTA)]
    t = _ticket(m.conn)
    assert L.al_cerrar(m.conn, t["id"], "file_path", " ".join(rutas), m.env) == L.EN_LINEA_HASTA
    assert L._cola.unfinished_tasks == 0


def test_en_memoria_no_hay_hilo_aunque_sean_muchas(mundo, monkeypatch):
    c = db.connect(":memory:")
    rutas = [str(_w(mundo.ses / "mem" / f"n{i}.md")) for i in range(10)]
    assert L.al_cerrar(c, 1, "file_path", " ".join(rutas), mundo.env) == 10
    c.close()


# --------------------------------------------------------------- finish_run


def _run_con_ticket(conn, **jkw):
    t = _ticket(conn, kind="agentic") if False else _ticket(conn)
    j = jobs.create_job(conn, agent="owner", name="un job", prompt="haz algo", ticket_id=t["id"], **jkw)
    return t, j, jobs.create_run(conn, j["id"])


def test_finish_run_registra_la_carpeta_con_su_job_y_su_ticket(m):
    t, j, run = _run_con_ticket(m.conn)
    _w(m.art / f"run_{run['id']}" / "out.txt", "salida")
    jobs.finish_run(m.conn, run["id"], "ok", result_summary="ok")
    fila = _filas(m.conn, titulo=f"run_{run['id']}")[0]
    assert fila["clase"] == "resultado" and fila["origen"] == "run"
    assert _links(m.conn, fila["id"]) == [(t["id"], j["id"], run["id"])]


def test_finish_run_sin_carpeta_no_registra_y_no_falla(m):
    _, _, run = _run_con_ticket(m.conn)
    done = jobs.finish_run(m.conn, run["id"], "ok", result_summary="ok")
    assert done["status"] == "ok" and _filas(m.conn, clase="resultado") == []


def test_finish_run_no_falla_si_library_lanza(m, monkeypatch):
    _, _, run = _run_con_ticket(m.conn)
    _w(m.art / f"run_{run['id']}" / "out.txt", "salida")
    monkeypatch.setattr(L, "registrar", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rota")))
    assert jobs.finish_run(m.conn, run["id"], "ok", result_summary="ok")["status"] == "ok"
    monkeypatch.setattr(L, "al_terminar_run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rota")))
    _, _, run2 = _run_con_ticket(m.conn)
    assert jobs.finish_run(m.conn, run2["id"], "ok", result_summary="ok")["status"] == "ok"


def test_run_que_cierra_su_ticket_deja_un_solo_enlace(m):
    t, j, run = _run_con_ticket(m.conn, verify_level="none")
    _w(m.art / f"run_{run['id']}" / "out.txt", "salida")
    jobs.finish_run(m.conn, run["id"], "ok", result_summary="ok")
    assert m.conn.execute("SELECT status FROM tickets WHERE id=?", (t["id"],)).fetchone()[0] == "verified"
    fila = _filas(m.conn, titulo=f"run_{run['id']}")[0]
    assert _links(m.conn, fila["id"]) == [(t["id"], j["id"], run["id"])]


# --------------------------------------------------------------- recorrido en su hilo


def test_recorrer_con_raices_ilegibles_no_toca_nada(m):
    L.recorrer(m.conn, m.env)
    n = len(L.buscar(m.conn, limite=500))
    assert n > 10
    res = L.recorrer(m.conn, L.Entorno(raices=[], publicado_dir=m.t / "pub", raices_ilegibles=True))
    assert res["ausentes"] == 0 and len(L.buscar(m.conn, limite=500)) == n


def test_recorrer_parado_a_medias_no_cuenta_faltas(m):
    L.recorrer(m.conn, m.env)
    (m.ses / "s1" / "informe.md").unlink()
    res = L.recorrer(m.conn, m.env, detener=lambda: True)
    assert res["ausentes"] == 0
    assert all(r["faltas"] == 0 for r in _filas(m.conn))


def test_barredor_arranca_en_segundo_plano_con_su_conexion_y_para_limpio(m, monkeypatch):
    conexiones = []
    real = db.connect
    monkeypatch.setattr(
        db, "connect", lambda *a, **k: conexiones.append(threading.current_thread().name) or real(*a, **k)
    )
    b = L.Barredor(m.t / "faros.db", cada_s=3600, entorno=lambda: m.env)
    t0 = time.perf_counter()
    b.start()
    assert time.perf_counter() - t0 < 0.5  # start() no espera al recorrido
    for _ in range(100):
        if b.pasadas:
            break
        time.sleep(0.1)
    assert b.pasadas == 1 and conexiones == ["biblioteca-recorrido"]
    t1 = time.perf_counter()
    b.stop()
    assert time.perf_counter() - t1 < 3 and not b._hilo.is_alive()
    assert len(L.buscar(m.conn, limite=500)) > 10  # y lo registró en la base compartida


def test_barredor_sobrevive_a_un_fallo_y_repite(m):
    intentos = []

    def entorno():
        intentos.append(1)
        if len(intentos) == 1:
            raise RuntimeError("raices.json roto")
        return m.env

    b = L.Barredor(m.t / "faros.db", cada_s=0.05, entorno=entorno)
    b.start()
    for _ in range(100):
        if b.pasadas >= 2:
            break
        time.sleep(0.1)
    b.stop()
    assert len(intentos) >= 3 and b.pasadas >= 2


def test_barredor_para_a_media_pasada(m, monkeypatch):
    for i in range(300):
        _w(m.ses / "muchos" / f"f{i}.md", f"# f{i}")
    b = L.Barredor(m.t / "faros.db", cada_s=3600, entorno=lambda: m.env)
    b.start()
    time.sleep(0.15)
    t0 = time.perf_counter()
    b.stop()
    assert time.perf_counter() - t0 < 3 and not b._hilo.is_alive()
    assert all(r["estado"] == "presente" for r in _filas(m.conn))


def test_entorno_cacheado_se_relee_si_cambia_raices_json(tmp_path, monkeypatch):
    casa = tmp_path / "casa"
    (casa / "biblioteca").mkdir(parents=True)
    monkeypatch.setenv("FAROS_HOME", str(casa))
    L._cache_entorno.update(clave=None, env=None)
    assert L.entorno_cacheado().raices == []
    a = L.entorno_cacheado()
    assert L.entorno_cacheado() is a
    d = tmp_path / "docs"
    d.mkdir()
    (casa / "biblioteca" / "raices.json").write_text(
        '[{"clase":"faro","ruta":%s,"tipo":"carpeta","patrones":["*.md"],"recursivo":true}]'
        % __import__("json").dumps(str(d)),
        encoding="utf-8",
    )
    assert len(L.entorno_cacheado().raices) == 1
