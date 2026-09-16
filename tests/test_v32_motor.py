"""v3.2 (7-sep-2026) — motor: betatesting v3.1 de owner.
T1.1 weekday (recurrente semanal anclada a un día) · T1.3 rework ancla hoy ·
T1.4 cerradas con hora en la agenda · T1.5 TicketError.field → 400 {field}.
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from faros import db, service as s, agenda as cal, api
from faros.service import TicketError


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _recurrente(conn, last_done=None, cadence=7, **kw):
    t = s.propose_ticket(conn, "owner", kw.pop("title", "Revision de un pasaje"), owner="carol",
                         cadence_days=cadence, auto_accept=True, **kw)["ticket"]
    if last_done:
        conn.execute("UPDATE tickets SET last_done_at=? WHERE id=?", (last_done, t["id"]))
        conn.commit()
    return s._as_dict(conn, s._get(conn, t["id"]))


# ------------------------------------------------------------------ T1.1 weekday

def test_weekday_next_due_lands_on_that_weekday():
    # last_done lunes 2026-08-31 → semanal del viernes: el viernes de ESA semana (4-sep)
    d = {"cadence_days": 7, "weekday": 4, "last_done_at": "2026-08-31T10:00:00"}
    assert s.next_due_date(d) == "2026-09-04"
    # last_done viernes 4-sep → el viernes siguiente (11-sep), no el mismo día
    d["last_done_at"] = "2026-09-04T09:30:00"
    assert s.next_due_date(d) == "2026-09-11"
    # estampada un martes 8-sep (día distinto al ancla): sigue cayendo en viernes
    d["last_done_at"] = "2026-09-08T16:00:00"
    assert s.next_due_date(d) == "2026-09-11"


def test_weekday_cadence_14_skips_a_week():
    d = {"cadence_days": 14, "weekday": 2, "last_done_at": "2026-09-01T10:00:00"}  # martes
    assert s.next_due_date(d) == "2026-09-09"  # miércoles de la semana siguiente


def test_weekday_without_last_done_is_first_occurrence_from_today():
    today = date.today()
    wd = (today.weekday() + 3) % 7
    d = {"cadence_days": 7, "weekday": wd, "last_done_at": None}
    assert s.next_due_date(d) == (today + timedelta(days=3)).isoformat()
    d["weekday"] = today.weekday()
    assert s.next_due_date(d) == today.isoformat()  # hoy mismo si es el día


def test_weekday_override_still_wins():
    d = {"cadence_days": 7, "weekday": 4, "last_done_at": "2026-08-31T10:00:00",
         "next_due_override": "2026-09-30"}
    assert s.next_due_date(d) == "2026-09-30"


def test_without_weekday_behaviour_unchanged():
    d = {"cadence_days": 7, "weekday": None, "last_done_at": "2026-08-31T10:00:00"}
    assert s.next_due_date(d) == "2026-09-07"


def test_weekday_validation(conn):
    with pytest.raises(TicketError, match="weekday solo aplica") as e:
        s.propose_ticket(conn, "owner", "puntual", weekday=2)
    assert e.value.field == "weekday"
    with pytest.raises(TicketError, match="múltiplo de 7"):
        s.propose_ticket(conn, "owner", "cada 3", cadence_days=3, weekday=2)
    with pytest.raises(TicketError, match="fuera de rango"):
        s.propose_ticket(conn, "owner", "semanal", cadence_days=7, weekday=7)
    t = _recurrente(conn, weekday=4, preferred_time="09:00")
    assert t["weekday"] == 4
    t = s.update_ticket(conn, "owner", t["id"], weekday=1)
    assert t["weekday"] == 1
    t = s.update_ticket(conn, "owner", t["id"], weekday="")
    assert t["weekday"] is None


def test_weekday_agenda_projects_on_its_day_and_overdue_in_its_day(conn):
    # semanal del viernes, estampada por última vez el 21-ago → vencida (28-ago) al
    # mirar la semana 7-11 sep: se ve el VIERNES 11 como real vencida, no el lunes 7.
    t = _recurrente(conn, "2026-08-21T09:00:00", 7, weekday=4, preferred_time="09:00",
                    duration_min=60)
    assert t["next_due"] == "2026-08-28"
    res = cal.occurrences(conn, "2026-09-07", "2026-09-11")
    mine = [i for i in res["items"] if i["ticket_id"] == t["id"]]
    assert [i["date"] for i in mine] == ["2026-09-11"]
    assert mine[0]["ghost"] is False and mine[0]["overdue_since"] == "2026-08-28"
    assert mine[0]["start"] == "09:00"
    # quincenal del lunes vencida (finding bob T4.1): last_done viernes 21-ago → next_due
    # lunes 31-ago; el paso de 14 cae el 14-sep, fuera de la semana 7-13 → se ve el LUNES 7
    q = _recurrente(conn, "2026-08-21T10:00:00", 14, title="Graph health", weekday=0,
                    preferred_time="10:00")
    assert q["next_due"] == "2026-08-31"
    res = cal.occurrences(conn, "2026-09-07", "2026-09-13")
    qs = [i for i in res["items"] if i["ticket_id"] == q["id"]]
    assert [(i["date"], i["ghost"], i.get("overdue_since")) for i in qs] == [("2026-09-07", False, "2026-08-31")]
    # y en un rango de dos semanas: la vencida el 7 + la proyección quincenal del 14 como ghost
    res = cal.occurrences(conn, "2026-09-07", "2026-09-20")
    qs = [i for i in res["items"] if i["ticket_id"] == q["id"]]
    assert [(i["date"], i["ghost"]) for i in qs] == [("2026-09-07", False), ("2026-09-14", True)]
    # sin weekday, la vencida sigue apilándose al inicio del rango (comportamiento v3.1)
    u = _recurrente(conn, "2026-08-21T09:00:00", 7, title="sin ancla", preferred_time="10:00")
    res = cal.occurrences(conn, "2026-09-07", "2026-09-11")
    theirs = [i for i in res["items"] if i["ticket_id"] == u["id"]]
    assert theirs[0]["date"] == "2026-09-07" and theirs[0]["overdue_since"] == "2026-08-28"


# ------------------------------------------------------------------ T1.3 rework ancla

def _done_ticket(conn, **kw):
    t = s.propose_ticket(conn, "owner", "tarea de carol", owner="carol", verification_level="peer",
                         auto_accept=True, **kw)["ticket"]
    s.start_ticket(conn, "carol", t["id"])
    s.complete_ticket(conn, "carol", t["id"], "file_path", "x.md")
    return t["id"]


def test_rework_anchors_dateless_ticket_today(conn):
    tid = _done_ticket(conn)
    # la ancla automática de creación le puso scheduled_at: la quitamos para reproducir
    # el caso de owner (tickets de workflow / migrados sin fecha)
    conn.execute("UPDATE tickets SET scheduled_at=NULL, due_at=NULL WHERE id=?", (tid,)); conn.commit()
    res = s.rework_ticket(conn, "owner", tid, "accepted", "falta la captura de la vista Día")
    assert res["status"] == "accepted" and res["anchored"] is True
    assert res["due_at"] == date.today().isoformat()
    h = s.ticket_history(conn, tid)["history"]
    assert any("[ancla] devuelta sin fecha" in (r["note"] or "") for r in h)
    assert any("[rechazo]" in (r["note"] or "") for r in h)
    board = s.board(conn, view="today")
    assert tid in [t["id"] for t in board["columns"]["accepted"]]
    board = s.board(conn, view="week")
    assert tid in [t["id"] for t in board["columns"]["accepted"]]


def test_rework_keeps_existing_dates(conn):
    tid = _done_ticket(conn, scheduled_at="2026-09-10T11:00", duration_min=30)
    res = s.rework_ticket(conn, "owner", tid, "in_progress", "hay que repetir el test")
    assert res["anchored"] is False
    assert res["scheduled_at"] == "2026-09-10T11:00" and res["due_at"] is None


# ------------------------------------------------------------------ T1.4 cerradas con hora

def test_closed_items_have_start(conn):
    today = date.today().isoformat()
    # (1) manual verificada sin ninguna hora → hora del cierre
    tid = _done_ticket(conn)
    conn.execute("UPDATE tickets SET scheduled_at=NULL WHERE id=?", (tid,)); conn.commit()
    s.verify_ticket(conn, "bob", tid, verdict="pass")
    t = s._as_dict(conn, s._get(conn, tid))
    res = cal.occurrences(conn, today, today)
    it = next(i for i in res["items"] if i["ticket_id"] == tid)
    assert it["kind"] == "closed" and it["start"] == t["closed_at"][11:16]
    # (2) manual con scheduled_at del día → esa hora
    tid2 = _done_ticket(conn, scheduled_at=f"{today}T11:00", duration_min=30)
    s.verify_ticket(conn, "bob", tid2, verdict="pass")
    res = cal.occurrences(conn, today, today)
    it2 = next(i for i in res["items"] if i["ticket_id"] == tid2)
    assert it2["start"] == "11:00" and it2["end"] == "11:30"
    # (3) ninguna cerrada del día sin start
    assert all(i["start"] for i in res["items"] if i["kind"] == "closed")
    # (4) finding #26 (bob): una cerrada de «todo el día» sigue sin hora
    tid3 = _done_ticket(conn, scheduled_at=f"{today}T00:00", all_day=1)
    s.verify_ticket(conn, "bob", tid3, verdict="pass")
    res = cal.occurrences(conn, today, today)
    it3 = next(i for i in res["items"] if i["ticket_id"] == tid3)
    assert it3["kind"] == "closed" and it3["start"] is None


# ------------------------------------------------------------------ T1.5 field en el 400

def test_create_ticket_error_field(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setattr(api, "load_token", lambda: "t-test")  # sin tocar el token real
    db.connect(tmp_path / "t.db").close()  # crea el esquema
    conn = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)  # TestClient = otro hilo
    conn.row_factory = sqlite3.Row
    client = TestClient(api.create_app(conn))
    h = {"X-AgenticOS-Token": "t-test"}
    r = client.post("/api/tickets", json={"title": "x"}, headers=h)
    assert r.status_code == 400 and r.json()["field"] == "agent"
    r = client.post("/api/tickets", json={"agent": "owner", "title": "  "}, headers=h)
    assert r.status_code == 400 and r.json()["field"] == "title"
    r = client.post("/api/tickets", json={"agent": "owner", "title": "ok", "owner": "Nadie"}, headers=h)
    assert r.status_code == 400 and r.json()["field"] == "owner"
    r = client.post("/api/tickets", json={"agent": "owner", "title": "ok", "due_at": "ayer"}, headers=h)
    assert r.status_code == 400 and r.json()["field"] == "due_at"
    r = client.post("/api/tickets", json={"agent": "owner", "title": "ok", "owner": "owner"}, headers=h)
    assert r.status_code == 200 and r.json()["ticket"]["scheduled_at"]
    # finding #27 (dave): la carga de la UI con opcionales a null no puede dar 500
    r = client.post("/api/tickets", json={"agent": "owner", "title": "nulos", "description": None,
                                          "owner": None, "verify_criteria": None, "due_at": None}, headers=h)
    assert r.status_code == 200 and r.json()["ticket"]["description"] == ""
    # T2.5 (dave): /api/meta trae docs_dir absoluto.
    # El test CONSTRUYE su propia raíz en vez de depender del docs/ que haya al
    # lado (16-sep): antes asertaba contra el checkout de desarrollo, así que en
    # el repo público —que no lleva docs/— este test salía ROJO sin que hubiera
    # ningún defecto. Un test no puede depender de ficheros ambientales que no
    # controla; lo que se prueba es la RESOLUCIÓN, no el arbol de quien lo corre.
    from pathlib import Path as _P
    from faros import settings as _st
    fake_root = tmp_path / "repo"
    (fake_root / "docs").mkdir(parents=True)
    (fake_root / "docs" / "gmail_oauth.md").write_text("# guia", encoding="utf-8")
    _st.patch(conn, "owner", {"repo_root_default": str(fake_root)})
    m = client.get("/api/meta", headers=h).json()
    assert m["docs_dir"] and m["docs_dir"].endswith("docs")
    assert (_P(m["docs_dir"]) / "gmail_oauth.md").exists()


def test_health_dice_la_casa_y_no_dice_quien_eres(tmp_path, monkeypatch):
    """#301 — /api/health tiene que decir DONDE escribe, y sin decir quien eres.

    POR QUE EXISTE, y es la mitad de su valor: hoy le dimos a tres personas un
    `curl /api/health` como prueba de que estaban aisladas, y NO lo era —
    decia la version y callaba la casa. Una paso esa comprobacion con un 3.3.0
    impecable mientras su daemon escribia en la base de produccion. Un
    aislamiento que no se puede comprobar no es un aislamiento, es una
    creencia.

    Y cuando anadi el campo, LO ROMPI: el import se colo en silencio y el
    endpoint empezo a devolver 500. La suite entera siguio en verde, porque
    NINGUN test tocaba /api/health. El unico endpoint sin token, el primero
    que mira cualquiera para saber si la app vive, y no lo probaba nadie.

    Tres cosas, y la tercera es de privacidad: health es el UNICO endpoint sin
    autenticar, asi que lo lee cualquiera que alcance el puerto, y la ruta
    entera lleva el nombre de usuario dentro.
    """
    import os, sqlite3
    monkeypatch.setattr(api, "load_token", lambda: "t-test")
    db.connect(tmp_path / "t.db").close()
    conn = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    client = TestClient(api.create_app(conn))

    r = client.get("/api/health")          # sin token a proposito: es el unico que no lo pide
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["ok"] is True
    assert "home" in cuerpo, "health no dice la casa: no sirve para comprobar aislamiento"
    assert cuerpo["home"], "la casa viene vacia"

    # OJO CON ESTA ASERCION, que ya la escribi mal una vez: mi primera version
    # falseaba USERPROFILE con monkeypatch y comprobaba que el tmp_path no
    # estuviera en la respuesta. Pasaba SIEMPRE, incluso con el campo sin
    # redactar, porque DATA_DIR se calcula al importar el modulo y nunca
    # contiene el tmp_path. Una asercion que no puede fallar no es una
    # asercion. Lo cace calibrando: quite la redaccion y el test siguio verde.
    # Se comprueba contra el perfil REAL, que es el que se filtraria.
    perfil = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    if perfil:
        assert perfil not in cuerpo["home"], (
            f"health filtra la ruta del usuario sin redactar: {cuerpo['home']}")


def test_un_campo_mal_escrito_no_es_un_500(tmp_path, monkeypatch):
    """#302 — SIETE endpoints hacian `fn(conn, agent, **body)`.

    Lo encontro Lienzo probando el motor a mano: mando `question` en vez de
    `title` y el daemon respondio

        500 Internal Server Error

    sin codigo, sin campo, sin nada que arreglar. En el stderr habia un
    TypeError, pero el stderr no lo ve quien llama.

    POR QUE IMPORTA MAS DE LO QUE PARECE: a estos endpoints los llama un AGENTE
    por MCP, y un agente se equivoca de nombre de campo exactamente igual que
    una persona. Un 500 no le dice que corregir; un 400 con `field` si. Y va
    contra nuestro propio contrato, que esta escrito en la cabecera de api.js:
    "que me lo diga en el propio formulario en vez de tirarlo".

    Son DOS mitades y la segunda salio despues de arreglar la primera:
      - un campo que SOBRA          -> antes TypeError, ahora 400 unknown_field
      - un obligatorio que FALTA    -> antes TypeError, ahora 400 missing_field

    Y el guardia mira la FIRMA, no caza el TypeError: cazarlo convertiria
    tambien los TypeError DE VERDAD —bugs nuestros dentro de la funcion— en
    errores de usuario, y los esconderia justo donde mas duelen.
    """
    import sqlite3
    monkeypatch.setattr(api, "load_token", lambda: "t-test")
    db.connect(tmp_path / "t.db").close()
    conn = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cli = TestClient(api.create_app(conn), raise_server_exceptions=False)
    h = {"X-FarOS-Token": "t-test"}
    opts = [{"label": "opcion primera entera", "text": "opcion primera entera"},
            {"label": "opcion segunda entera", "text": "opcion segunda entera"}]

    # sobra un campo
    r = cli.post("/api/tickets", json={"agent": "owner", "title": "x", "inventado": 1}, headers=h)
    assert r.status_code == 400, r.text
    assert r.json()["field"] == "inventado"
    assert r.json()["code"] == "request.unknown_field"

    # falta un obligatorio (el caso literal de Lienzo: mando 'question' por 'title')
    r = cli.post("/api/decisions", json={"agent": "owner", "question": "x", "options": opts}, headers=h)
    assert r.status_code == 400, r.text
    assert r.json()["field"] == "title"
    assert r.json()["code"] == "request.missing_field"

    # CALIBRACION: el camino bueno tiene que seguir pasando. Un guardia que
    # rechaza todo tambien dejaria estos dos casos en 400 y el test pasaria.
    r = cli.post("/api/tickets", json={"agent": "owner", "title": "una tarea de verdad"}, headers=h)
    assert r.status_code == 200, r.text
    r = cli.post("/api/decisions",
                 json={"agent": "owner", "title": "una pregunta de verdad", "options": opts}, headers=h)
    assert r.status_code == 200, r.text

