"""#1346: la regla de las piezas cableada en verify, complete, start, crear/editar, la API (la app) y el
MCP, contra los casos de Prima (casos_1346.md, en el laboratorio de Prima). El núcleo
puro está en test_1346_piezas_nucleo.py. Tarea estándar: dueña alice, nivel peer, criterio T3,
llevada a done por alice; verifica bob."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from faros import api, db
from faros import service as s
from faros.service import TicketError

T3 = "C1 [salida]: A.\nC2 [fichero]: B.\nC3: C."
CP = "C1 [persona: carol]: Lo prueba Carol en su móvil."
TOKEN = "t" * 32
H = {"X-FarOS-Token": TOKEN}


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def ruta(tmp_path):
    f = tmp_path / "prueba.txt"
    f.write_text("x")
    return str(f)


def piezas_t3(ruta, **cambio):
    p = {
        "C1": {"estado": "cumple", "prueba": "3 passed in 0.1s"},
        "C2": {"estado": "cumple", "prueba": ruta},
        "C3": {"estado": "cumple", "prueba": "#1346"},
    }
    p.update(cambio)
    return p


def _tarea(conn, criterio=T3, nivel="peer", owner="alice"):
    return s.propose_ticket(
        conn,
        owner,
        "Tarea de prueba",
        owner=owner,
        auto_accept=True,
        verification_level=nivel,
        verify_criteria=criterio,
    )["ticket"]["id"]


def _hecha(conn, criterio=T3, nivel="peer", owner="alice"):
    tid = _tarea(conn, criterio, nivel, owner)
    s.start_ticket(conn, owner, tid)
    s.complete_ticket(conn, owner, tid, "command_output", "hecho")
    return tid


def _estado(conn, tid):
    return s._get(conn, tid)["status"]


def _notas(conn, tid):
    return [h["note"] or "" for h in s.ticket_history(conn, tid)["history"]]


def _codigo(exc):
    return exc.value.code, str(exc.value)


# ---- C1


def test_c1a_pass_con_todas(conn, ruta):
    tid = _hecha(conn)
    assert s.verify_ticket(conn, "bob", tid, "pass", piezas=piezas_t3(ruta))["status"] == "verified"
    assert any("[piezas] C1 cumple" in n for n in _notas(conn, tid))


def test_c1b_pass_sin_piezas(conn):
    tid = _hecha(conn)
    with pytest.raises(TicketError) as e:
        s.verify_ticket(conn, "bob", tid, "pass")
    code, msg = _codigo(e)
    assert code == "verify.piezas_missing" and all(x in msg for x in ("C1", "C2", "C3"))
    assert _estado(conn, tid) == "done"


def test_c1c_falta_c2(conn, ruta):
    tid = _hecha(conn)
    p = piezas_t3(ruta)
    del p["C2"]
    with pytest.raises(TicketError) as e:
        s.verify_ticket(conn, "bob", tid, "pass", piezas=p)
    code, msg = _codigo(e)
    assert code == "verify.piezas_missing" and "C2" in msg and "C1" not in msg and "C3" not in msg


def test_c1d_pieza_inventada(conn, ruta):
    tid = _hecha(conn)
    with pytest.raises(TicketError) as e:
        s.verify_ticket(conn, "bob", tid, "pass", piezas=piezas_t3(ruta, C9={"estado": "cumple", "prueba": "x"}))
    assert e.value.code == "verify.piezas_unknown" and "C9" in str(e.value)


# ---- C2


@pytest.mark.parametrize("pieza,estado", [("C2", "no_cumple"), ("C3", "no_comprobable")])
def test_c2ab_no_llega_a_verified(conn, ruta, pieza, estado):
    tid = _hecha(conn)
    with pytest.raises(TicketError) as e:
        s.verify_ticket(conn, "bob", tid, "pass", piezas=piezas_t3(ruta, **{pieza: {"estado": estado, "prueba": "no"}}))
    assert pieza in str(e.value) and _estado(conn, tid) == "done"


def test_c2c_fail_con_no_cumple(conn):
    tid = _hecha(conn)
    p = {"C2": {"estado": "no_cumple", "prueba": "la ruta no existe"}}
    assert s.verify_ticket(conn, "bob", tid, "fail", "falta C2", piezas=p)["status"] == "in_progress"
    assert any("verify FAIL: falta C2 [piezas] C2 no_cumple" in n for n in _notas(conn, tid))


def test_duda4_fail_sin_piezas_como_hoy(conn):
    tid = _hecha(conn)
    assert s.verify_ticket(conn, "bob", tid, "fail", "no arranca")["status"] == "in_progress"


# ---- C3


def test_c3a_incierto_se_queda_en_done_sin_external(conn, ruta, monkeypatch):
    from faros import verifier

    llamadas = []
    monkeypatch.setattr(verifier, "sweep", lambda *a, **k: llamadas.append(a), raising=False)
    tid = _hecha(conn)
    r = s.verify_ticket(
        conn,
        "bob",
        tid,
        "incierto",
        piezas=piezas_t3(ruta, C2={"estado": "incierto", "prueba": "no puedo abrir la ruta"}),
    )
    assert r["status"] == "done" and r["verification_level"] == "peer" and r["verified_by"] is None
    assert any(n.startswith("[incierto]") and "C2 incierto: no puedo abrir la ruta" in n for n in _notas(conn, tid))
    assert not llamadas


def test_c3b_incierto_sin_pieza(conn, ruta):
    tid = _hecha(conn)
    with pytest.raises(TicketError) as e:
        s.verify_ticket(conn, "bob", tid, "incierto", piezas=piezas_t3(ruta))
    assert e.value.code == "verify.incierto_sin_pieza"


def test_c3c_pass_con_incierta(conn, ruta):
    tid = _hecha(conn)
    with pytest.raises(TicketError) as e:
        s.verify_ticket(conn, "bob", tid, "pass", piezas=piezas_t3(ruta, C3={"estado": "incierto", "prueba": "?"}))
    assert "C3" in str(e.value)


# ---- C4


def test_c4a_crear_con_etiquetas(conn):
    assert _tarea(conn, "C1: A.\nC2: B.")


def test_c4b_crear_con_repetidas(conn):
    with pytest.raises(TicketError) as e:
        _tarea(conn, "C1: A.\nC1: B.")
    assert e.value.code == "criteria.duplicate_label" and "C1" in str(e.value)


def test_c4c_editar_a_repetidas(conn):
    tid = _tarea(conn)
    with pytest.raises(TicketError) as e:
        s.update_ticket(conn, "alice", tid, verify_criteria="C1: A.\nC2: B.\nC2: otra.")
    assert e.value.code == "criteria.duplicate_label" and "C2" in str(e.value)


# ---- C5


def _empezada_y_editada(conn, nuevo):
    tid = _tarea(conn)
    s.start_ticket(conn, "alice", tid)
    s.update_ticket(conn, "alice", tid, verify_criteria=nuevo)
    s.complete_ticket(conn, "alice", tid, "command_output", "hecho")
    return tid


def test_c5_start_guarda_la_huella(conn):
    tid = _tarea(conn)
    assert s._get(conn, tid)["piezas_huella"] is None
    s.start_ticket(conn, "alice", tid)
    assert set(json.loads(s._get(conn, tid)["piezas_huella"])) == {"C1", "C2", "C3"}


def test_c5a_sin_tocar_el_criterio(conn, ruta):
    tid = _hecha(conn)
    s.verify_ticket(conn, "bob", tid, "pass", piezas=piezas_t3(ruta))
    assert not any("cambios del criterio" in n for n in _notas(conn, tid))


def test_c5b_texto_cambiado_sin_porque(conn, ruta):
    tid = _empezada_y_editada(conn, T3.replace("B.", "B y algo más."))
    with pytest.raises(TicketError) as e:
        s.verify_ticket(conn, "bob", tid, "pass", piezas=piezas_t3(ruta))
    assert e.value.code == "verify.piezas_cambiadas" and "C2 cambiada" in str(e.value)


def test_c5c_con_porque_entra(conn, ruta):
    tid = _empezada_y_editada(conn, T3.replace("B.", "B y algo más."))
    r = s.verify_ticket(conn, "bob", tid, "pass", piezas=piezas_t3(ruta), cambios={"C2": "Pepe pidió más"})
    assert r["status"] == "verified"
    assert any("C2 cambiada: Pepe pidió más" in n for n in _notas(conn, tid))


def test_c5d_borrada(conn, ruta):
    tid = _empezada_y_editada(conn, "C1 [salida]: A.\nC2 [fichero]: B.")
    p = piezas_t3(ruta)
    del p["C3"]
    with pytest.raises(TicketError) as e:
        s.verify_ticket(conn, "bob", tid, "pass", piezas=p)
    assert "C3 borrada" in str(e.value)


def test_c5e_renombrada(conn, ruta):
    tid = _empezada_y_editada(conn, T3.replace("C3:", "C4:"))
    p = piezas_t3(ruta)
    p["C4"] = p.pop("C3")
    with pytest.raises(TicketError) as e:
        s.verify_ticket(conn, "bob", tid, "pass", piezas=p)
    assert "C3 borrada" in str(e.value) and "C4 nueva" in str(e.value)


def test_c5f_espacios_al_final_no_cuentan(conn, ruta):
    tid = _empezada_y_editada(conn, T3.replace("B.", "B.   "))
    assert s.verify_ticket(conn, "bob", tid, "pass", piezas=piezas_t3(ruta))["status"] == "verified"


def test_c5_huella_al_instalar_para_las_que_estan_en_curso(conn):
    """Duda 1a: la migración v18 toma la huella de las tareas ya en curso y lo apunta."""
    tid = _tarea(conn)
    s.start_ticket(conn, "alice", tid)
    conn.execute("UPDATE tickets SET piezas_huella = NULL WHERE id = ?", (tid,))
    db._v18_huella_en_curso(conn)
    assert set(json.loads(s._get(conn, tid)["piezas_huella"])) == {"C1", "C2", "C3"}
    assert any("huella del criterio tomada al instalar" in n for n in _notas(conn, tid))
    db._v18_huella_en_curso(conn)  # idempotente
    assert sum("tomada al instalar" in n for n in _notas(conn, tid)) == 1


def test_esquema_18_con_la_columna(conn):
    assert db.SCHEMA_VERSION >= 18 and "piezas_huella" in db._columns(conn, "tickets")


# ---- C6


def test_c6a_criterio_sin_etiquetas_pide_una_pieza_por_linea(conn):
    tid = _hecha(conn, "Que arranque.\n\nQue no rompa nada.")
    with pytest.raises(TicketError) as e:
        s.verify_ticket(conn, "bob", tid, "pass", piezas={"C1": {"estado": "cumple", "prueba": "#1"}})
    assert e.value.code == "verify.piezas_missing" and "C2" in str(e.value)
    p = {"C1": {"estado": "cumple", "prueba": "#1"}, "C2": {"estado": "cumple", "prueba": "#2"}}
    assert s.verify_ticket(conn, "bob", tid, "pass", piezas=p)["status"] == "verified"


def test_c6c_otra_particion(conn):
    tid = _hecha(conn, "Que arranque.\nQue no rompa nada.")
    with pytest.raises(TicketError) as e:
        s.verify_ticket(
            conn,
            "bob",
            tid,
            "pass",
            piezas={"C1": {"estado": "cumple", "prueba": "#1"}, "C3": {"estado": "cumple", "prueba": "#2"}},
        )
    assert e.value.code == "verify.piezas_unknown" and "partió por líneas" in str(e.value)


# ---- C7


def test_c7a_la_cierra_carol(conn):
    tid = _hecha(conn, CP)
    p = {"C1": {"estado": "cumple", "prueba": "visto en su móvil"}}
    assert s.verify_ticket(conn, "carol", tid, "pass", piezas=p)["status"] == "verified"


def test_c7b_bob_no(conn):
    tid = _hecha(conn, CP)
    with pytest.raises(TicketError) as e:
        s.verify_ticket(conn, "bob", tid, "pass", piezas={"C1": {"estado": "cumple", "prueba": "x"}})
    assert e.value.code == "verify.persona_only" and "carol" in str(e.value)


def test_c7c_la_duena_tampoco_al_sellar_una_self(conn):
    tid = _tarea(conn, CP, nivel="self")
    with pytest.raises(TicketError) as e:
        s.complete_ticket(
            conn, "alice", tid, "command_output", "hecho", piezas={"C1": {"estado": "cumple", "prueba": "x"}}
        )
    assert e.value.code == "verify.persona_only" and "carol" in str(e.value)
    assert _estado(conn, tid) == "accepted"


def test_c7d_el_dueno_de_la_casa_con_piezas_cierra_con_aviso(conn):
    # C7d cambiado por Prima (24-nov): aviso en los dos caminos, con piezas y sin ellas (decisión 136).
    tid = _hecha(conn, CP)
    r = s.verify_ticket(conn, s.HOUSE_OWNER, tid, "pass", piezas={"C1": {"estado": "cumple", "prueba": "x"}})
    assert r["status"] == "verified"
    assert any("carol" in a for a in r.get("avisos_piezas", []))


# ---- C8


def test_c8b_fichero_que_no_existe(conn, ruta, tmp_path):
    tid = _hecha(conn)
    with pytest.raises(TicketError) as e:
        s.verify_ticket(
            conn,
            "bob",
            tid,
            "pass",
            piezas=piezas_t3(ruta, C2={"estado": "cumple", "prueba": str(tmp_path / "no.txt")}),
        )
    assert e.value.code == "verify.prueba_forma" and "ruta" in str(e.value)


def test_c8c_salida_vacia(conn, ruta):
    tid = _hecha(conn)
    with pytest.raises(TicketError) as e:
        s.verify_ticket(conn, "bob", tid, "pass", piezas=piezas_t3(ruta, C1={"estado": "cumple", "prueba": "  "}))
    assert e.value.code == "verify.prueba_forma" and "C1" in str(e.value)


def test_c8e_carpeta_vale(conn, ruta, tmp_path):
    tid = _hecha(conn)
    r = s.verify_ticket(
        conn, "bob", tid, "pass", piezas=piezas_t3(ruta, C2={"estado": "cumple", "prueba": str(tmp_path)})
    )
    assert r["status"] == "verified"


def test_propuesta_de_prima_sale_como_aviso_en_la_respuesta(conn, ruta):
    tid = _hecha(conn)
    r = s.verify_ticket(
        conn, "bob", tid, "pass", piezas=piezas_t3(ruta, C3={"estado": "cumple", "prueba": "se lo dije a Pepe"})
    )
    assert r["status"] == "verified" and any("C3" in a for a in r["avisos_piezas"])


# ---- C9


def test_c9a_complete_de_una_self_sin_piezas(conn):
    tid = _tarea(conn, nivel="self")
    with pytest.raises(TicketError) as e:
        s.complete_ticket(conn, "alice", tid, "command_output", "hecho")
    assert e.value.code == "verify.piezas_missing"


def test_c9b_complete_de_una_self_con_piezas(conn, ruta):
    tid = _tarea(conn, nivel="self")
    r = s.complete_ticket(conn, "alice", tid, "command_output", "hecho", piezas=piezas_t3(ruta))
    assert r["status"] == "verified"
    assert any("[piezas] C1 cumple" in n for n in _notas(conn, tid))


def test_c9c_mcp_manda_piezas_y_cambios(monkeypatch):
    from faros import mcp_server as m

    sent = {}
    monkeypatch.setattr(m, "_call", lambda method, path, **kw: sent.update(method=method, path=path, **kw) or {"ok": 1})
    p = {"C1": {"estado": "cumple", "prueba": "x"}}
    m.transition_ticket("bob", 7, "verify", piezas=p, cambios={"C1": "porque sí"})
    assert sent["json"]["piezas"] == p and sent["json"]["cambios"] == {"C1": "porque sí"}
    m.transition_ticket("bob", 7, "verify")
    assert "piezas" not in sent["json"]


def test_c9d_complete_de_una_peer_sin_piezas_como_hoy(conn):
    tid = _tarea(conn)
    assert s.complete_ticket(conn, "alice", tid, "command_output", "hecho")["status"] == "done"


# ---- C13


@pytest.mark.parametrize("criterio", [None, "", "  \n \r\n"])
def test_c13_sin_criterio_como_hoy(conn, criterio):
    tid = _hecha(conn, criterio)
    assert s.verify_ticket(conn, "bob", tid, "pass")["status"] == "verified"


def test_c13_recurrente_sigue_igual(conn):
    t = s.propose_ticket(conn, "alice", "r", owner="alice", auto_accept=True, cadence_days=1, verify_criteria=T3)
    assert s.complete_ticket(conn, "alice", t["ticket"]["id"], "command_output", "ok")["status"] == "accepted"


# ---- C14


def test_c14a_dueno_sin_piezas(conn):
    tid = _hecha(conn)
    assert s.verify_ticket(conn, s.HOUSE_OWNER, tid, "pass")["status"] == "verified"
    assert any(f"verificada por {s.HOUSE_OWNER} sin piezas" in n for n in _notas(conn, tid))


def test_c14b_otro_sin_piezas(conn):
    tid = _hecha(conn)
    with pytest.raises(TicketError):
        s.verify_ticket(conn, "bob", tid, "pass")


def test_c14c_dueno_con_piezas_malas(conn, ruta, tmp_path):
    tid = _hecha(conn)
    with pytest.raises(TicketError):
        s.verify_ticket(
            conn,
            s.HOUSE_OWNER,
            tid,
            "pass",
            piezas=piezas_t3(ruta, C2={"estado": "cumple", "prueba": str(tmp_path / "no")}),
        )


def test_duda2_dueno_sin_piezas_sobre_persona_ajena_pasa_con_aviso(conn):
    tid = _hecha(conn, CP)
    r = s.verify_ticket(conn, s.HOUSE_OWNER, tid, "pass")
    assert r["status"] == "verified" and any("carol" in a for a in r["avisos_piezas"])
    assert any("quedan sin su persona: C1 [persona: carol]" in n for n in _notas(conn, tid))


# ---- C15 (Prima, 23-nov): los bots sin piezas, provisional hasta #1379


def test_c15_verifier_bot_sin_piezas_en_una_auto(conn):
    tid = _hecha(conn, nivel="auto")
    assert s.verify_ticket(conn, "verifier-bot", tid, "pass", "juez: pass")["status"] == "verified"
    assert any("verificada por verifier-bot sin piezas" in n for n in _notas(conn, tid))


def test_c15_un_miembro_no_usa_la_exencion_de_los_bots(conn):
    tid = _hecha(conn, nivel="auto")
    with pytest.raises(TicketError) as e:
        s.verify_ticket(conn, "dave", tid, "pass")
    assert e.value.code == "verify.piezas_missing"


# ---- C10 y C14-d por la API (la app)


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "load_token", lambda: TOKEN)
    db.connect(tmp_path / "t.db").close()
    c = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    yield TestClient(api.create_app(c))
    c.close()


def _api_hecha(cli, criterio=T3):
    t = cli.post(
        "/api/tickets",
        headers=H,
        json={
            "agent": "alice",
            "title": "x",
            "owner": "alice",
            "verification_level": "peer",
            "verify_criteria": criterio,
            "auto_accept": True,
        },
    ).json()["ticket"]
    for body in ({"action": "start"}, {"action": "complete", "evidence_type": "command_output", "evidence": "ok"}):
        r = cli.post(f"/api/tickets/{t['id']}/transition", headers=H, json={"agent": "alice", **body})
        assert r.status_code == 200, r.text
    return t["id"]


def test_c10_aviso_al_crear_y_al_editar(cli):
    r = cli.post(
        "/api/tickets", headers=H, json={"agent": "alice", "title": "x", "verify_criteria": "Que funcione."}
    ).json()
    assert any(a["punto"] == "piezas" for a in r["criterion_warnings"])
    con = cli.post("/api/tickets", headers=H, json={"agent": "alice", "title": "y", "verify_criteria": T3}).json()
    assert not any(a["punto"] == "piezas" for a in con["criterion_warnings"])
    e = cli.patch(
        f"/api/tickets/{con['ticket']['id']}", headers=H, json={"agent": "alice", "verify_criteria": "Sin piezas."}
    ).json()
    assert any(a["punto"] == "piezas" for a in e["criterion_warnings"])


def test_c9c_c14d_por_la_api(cli, ruta):
    tid = _api_hecha(cli)
    sin = cli.post(
        f"/api/tickets/{tid}/transition", headers=H, json={"agent": "bob", "action": "verify", "verdict": "pass"}
    )
    assert sin.status_code == 400 and sin.json()["code"] == "verify.piezas_missing"
    con = cli.post(
        f"/api/tickets/{tid}/transition",
        headers=H,
        json={"agent": "bob", "action": "verify", "verdict": "pass", "piezas": piezas_t3(ruta)},
    )
    assert con.status_code == 200 and con.json()["status"] == "verified"
    tid2 = _api_hecha(cli)
    pepe = cli.post(
        f"/api/tickets/{tid2}/transition",
        headers=H,
        json={"agent": s.HOUSE_OWNER, "action": "verify", "verdict": "pass"},
    )
    assert pepe.status_code == 200 and pepe.json()["status"] == "verified"


def test_c4b_por_la_api_es_un_400_con_su_codigo(cli):
    r = cli.post("/api/tickets", headers=H, json={"agent": "alice", "title": "x", "verify_criteria": "C1: a\nC1: b"})
    assert r.status_code == 400 and "C1" in r.text
