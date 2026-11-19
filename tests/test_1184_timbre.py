"""#1184: el timbre de franjas. A la hora de inicio de cada tarea con hora, FarOS avisa a su dueño por el
relay. Lee el calendario en cada vuelta (una tarea movida suena a su hora nueva), no repite, no hace sonar lo
viejo tras un reinicio, no se manda a quien no tiene sesión ni al dueño de la casa, y con el relay caído
reintenta mientras siga en la ventana."""

import json
from datetime import date, datetime, time, timedelta

import pytest

from faros import db, service as s, timbre


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


HOY = date.today()


def a_las(h, m=0, seg=10):
    return datetime.combine(HOY, time(h, m, seg))


def tarea(conn, titulo, owner="alice", hora="10:00"):
    t = s.propose_ticket(conn, "owner", titulo, owner=owner, scheduled_at=f"{HOY.isoformat()}T{hora}",
                         auto_accept=True)["ticket"]
    return t["id"]


def test_suena_a_su_hora_con_el_mensaje_firmado(conn):
    tid = tarea(conn, "medir el juez")
    t = timbre.Timbre(ahora=a_las(9, 58))
    assert t.preparar(conn, a_las(9, 59))["pendientes"] == {}          # todavía no
    plan = t.preparar(conn, a_las(10, 0))
    assert list(plan["pendientes"]) == ["alice"]
    texto = t.avisos(plan)["alice"]
    assert texto.startswith("Son las 10:00. Tenías planeado: #%d «medir el juez»." % tid)
    assert "siguiente paso en FarOS" in texto and "prioridad acordada con owner" in texto
    assert texto.endswith("— FarOS (timbre de franjas)")


def test_no_repite_lo_que_ya_sono(conn):
    tarea(conn, "una")
    t = timbre.Timbre(ahora=a_las(9, 58))
    plan = t.preparar(conn, a_las(10, 0))
    t.confirmar(plan, {"alice": "enviado"})
    assert t.preparar(conn, a_las(10, 0, 40))["pendientes"] == {}


def test_una_tarea_movida_suena_a_su_hora_nueva(conn):
    tid = tarea(conn, "movible", hora="10:00")
    t = timbre.Timbre(ahora=a_las(9, 50))
    t.confirmar(t.preparar(conn, a_las(9, 55)), {})
    s.update_ticket(conn, "alice", tid, scheduled_at=f"{HOY.isoformat()}T11:05")   # la mueven a media mañana
    plan = t.preparar(conn, a_las(10, 0))
    assert plan["pendientes"] == {}
    t.confirmar(plan, {})
    assert list(t.preparar(conn, a_las(11, 5))["pendientes"]) == ["alice"]


def test_tras_reiniciar_no_suena_lo_viejo_pero_si_lo_de_ahora(conn):
    tarea(conn, "de las diez", hora="10:00")
    tarea(conn, "de las once y cuarto", hora="11:15")
    t = timbre.Timbre(ahora=a_las(11, 17))          # el daemon arranca a las 11:17
    plan = t.preparar(conn, a_las(11, 17))
    assert [i["title"] for i in plan["pendientes"]["alice"]] == ["de las once y cuarto"]


def test_ni_al_dueno_ni_tareas_cerradas(conn):
    tarea(conn, "del dueño", owner="owner")
    tid = tarea(conn, "ya hecha")
    s.start_ticket(conn, "alice", tid)
    s.complete_ticket(conn, "alice", tid, "command_output", "hecho")
    t = timbre.Timbre(ahora=a_las(9, 58))
    assert t.preparar(conn, a_las(10, 0))["pendientes"] == {}


def test_con_el_relay_caido_reintenta_dentro_de_la_ventana(conn):
    tarea(conn, "importante")
    t = timbre.Timbre(ahora=a_las(9, 58))
    t.confirmar(t.preparar(conn, a_las(10, 0)), None)                      # hub caído
    assert list(t.preparar(conn, a_las(10, 1))["pendientes"]) == ["alice"]  # se reintenta
    t.confirmar(t.preparar(conn, a_las(10, 1)), None)
    assert t.preparar(conn, a_las(10, 6))["pendientes"] == {}               # fuera de la ventana: ya no


def test_sin_sesion_no_se_manda_ni_se_reintenta(conn):
    tarea(conn, "sin nadie")
    t = timbre.Timbre(ahora=a_las(9, 58))
    plan = t.preparar(conn, a_las(10, 0))
    t.confirmar(plan, {"alice": "sin_sesion"})
    assert t.preparar(conn, a_las(10, 1))["pendientes"] == {}


class _WsFalso:
    """Hub de mentira: registra (con un name_taken la primera vez si se pide), lista y acusa recibo."""
    def __init__(self, conectados, nombre_ocupado=False):
        self.conectados, self.ocupado, self.enviado, self.cola = conectados, nombre_ocupado, [], []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send(self, raw):
        m = json.loads(raw)
        self.enviado.append(m)
        if m.get("type") == "register":
            if self.ocupado:
                self.ocupado = False
                self.cola.append({"type": "err", "code": "name_taken"})
            else:
                self.cola.append({"type": "ack", "name": m["name"]})
        elif m.get("type") == "list_peers":
            self.cola += [{"type": "ping", "req_id": "p1"},
                          {"type": "peers", "req_id": m["req_id"], "peers": [{"name": n} for n in self.conectados]}]
        elif m.get("type") == "send":
            self.cola.append({"type": "send_ack", "req_id": m["req_id"], "msg_id": "x", "status": "delivered"})

    def recv(self, timeout=None):
        return json.dumps(self.cola.pop(0))


def test_enviar_solo_a_quien_esta_y_firma_como_faros(monkeypatch):
    monkeypatch.setenv("ECORELAY_WS_TOKEN", "t")
    ws = _WsFalso(conectados=["alice", "otro"], nombre_ocupado=True)
    res = timbre.enviar({"alice": "hola", "bob": "hola"}, conectar=lambda *a, **k: ws)
    assert res == {"alice": "enviado", "bob": "sin_sesion"}
    registros = [m["name"] for m in ws.enviado if m.get("type") == "register"]
    assert registros == ["FarOS", "FarOS-2"]                     # nombre cogido: se sufija
    assert {"type": "pong", "req_id": "p1"} in ws.enviado        # contesta al ping del hub
    envios = [m for m in ws.enviado if m.get("type") == "send"]
    assert [(m["to"], m["text"]) for m in envios] == [("alice", "hola")]


def test_se_apaga_con_la_variable(monkeypatch):
    monkeypatch.setenv("FAROS_TIMBRE", "0")
    assert not timbre.activo()
    monkeypatch.setenv("FAROS_TIMBRE", "1")
    assert timbre.activo()


def test_tras_reiniciar_no_repite_lo_que_ya_sono(conn, tmp_path):
    """Cazado en la prueba real del 13-nov: sin estado guardado, un reinicio a los 4 minutos volvía a
    hacer sonar las dos tareas de la ventana."""
    tarea(conn, "A", hora="10:01")
    tarea(conn, "B", hora="10:04")
    estado = tmp_path / "timbre_ultimo.txt"
    t = timbre.Timbre(ahora=a_las(10, 0), estado=estado)
    for h, m in ((10, 1), (10, 4)):
        plan = t.preparar(conn, a_las(h, m))
        t.confirmar(plan, {"alice": "enviado"})
    nuevo = timbre.Timbre(ahora=a_las(10, 5), estado=estado)        # el daemon se reinicia a las 10:05
    assert nuevo.preparar(conn, a_las(10, 5))["pendientes"] == {}


def test_el_aviso_que_llega_tarde_dice_la_hora_de_verdad(conn):
    tarea(conn, "de las diez")
    t = timbre.Timbre(ahora=a_las(9, 58))
    t.confirmar(t.preparar(conn, a_las(10, 0)), None)              # relay caído a su hora
    plan = t.preparar(conn, a_las(10, 2))
    assert t.avisos(plan)["alice"].startswith("Son las 10:02. Tenías planeado: #")
    assert "«de las diez» (a las 10:00)" in t.avisos(plan)["alice"]


# ---- #1217: lo operable desde la app (el interruptor y qué ha sonado) ----

def test_el_interruptor_de_la_app_lo_apaga_y_al_encenderlo_no_suena_lo_atrasado(conn, monkeypatch):
    from faros import settings
    monkeypatch.setenv("FAROS_TIMBRE", "1")
    tarea(conn, "durante el apagón", hora="10:00")
    tarea(conn, "después", hora="10:10")
    t = timbre.Timbre(ahora=a_las(9, 58))
    settings.patch(conn, "owner", {"timbre_activo": False})
    assert not timbre.activo(conn)
    t.saltar(a_las(10, 5))                       # el scheduler salta mientras está apagado
    settings.patch(conn, "owner", {"timbre_activo": True})
    assert timbre.activo(conn)
    assert t.preparar(conn, a_las(10, 6))["pendientes"] == {}                       # lo de las 10:00 no suena
    assert list(t.preparar(conn, a_las(10, 10))["pendientes"]) == ["alice"]       # lo siguiente sí


def test_el_entorno_manda_sobre_el_interruptor(conn, monkeypatch):
    from faros import settings
    settings.patch(conn, "owner", {"timbre_activo": True})
    monkeypatch.setenv("FAROS_TIMBRE", "0")
    assert not timbre.activo(conn)


def test_queda_apuntado_que_sono_y_a_quien(conn, tmp_path, monkeypatch):
    tid = tarea(conn, "apuntada", hora="10:00")
    reg = tmp_path / "timbre_registro.jsonl"
    t = timbre.Timbre(ahora=a_las(9, 58), registro=reg)
    plan = t.preparar(conn, a_las(10, 0))
    t.confirmar(plan, {"alice": "enviado"})
    linea = json.loads(reg.read_text(encoding="utf-8").splitlines()[0])
    assert linea["agente"] == "alice" and linea["resultado"] == "enviado"
    assert linea["tareas"] == [{"id": tid, "titulo": "apuntada", "inicio": "10:00"}]
    assert linea["hora"].startswith(HOY.isoformat())


def test_la_api_dice_si_esta_encendido_y_que_ha_sonado_hoy(tmp_path, monkeypatch):
    import sqlite3
    from fastapi.testclient import TestClient
    from faros import api
    monkeypatch.setattr(api, "load_token", lambda: "t" * 32)
    monkeypatch.setenv("FAROS_TIMBRE", "1")
    db.connect(tmp_path / "t.db").close()
    c = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    (tmp_path / "timbre_registro.jsonl").write_text(
        json.dumps({"hora": f"{HOY.isoformat()}T10:00:09", "agente": "alice", "resultado": "enviado",
                    "tareas": [{"id": 1, "titulo": "x", "inicio": "10:00"}]}) + "\n"
        + json.dumps({"hora": "2020-01-01T10:00:00", "agente": "bob", "resultado": "enviado", "tareas": []}) + "\n",
        encoding="utf-8")
    (tmp_path / "timbre_ultimo.txt").write_text(f"{HOY.isoformat()}T10:00:09", encoding="utf-8")
    cl, H = TestClient(api.create_app(c)), {"X-FarOS-Token": "t" * 32}
    r = cl.get("/api/timbre", headers=H).json()
    assert r["activo"] is True and r["ultima_vuelta"] == f"{HOY.isoformat()}T10:00:09"
    assert [a["agente"] for a in r["avisos"]] == ["alice"]                 # solo lo de hoy
    assert cl.get("/api/timbre", headers=H, params={"fecha": "2020-01-01"}).json()["avisos"][0]["agente"] == "bob"
    assert cl.get("/api/timbre", headers=H, params={"fecha": "ayer"}).status_code == 400
    cl.patch("/api/settings", headers=H, json={"agent": "owner", "timbre_activo": False})
    assert cl.get("/api/timbre", headers=H).json()["activo"] is False


# ---- tiempo libre: suena como invitación con el texto de cada uno (idea de Pepe por Eco, 13-nov) ----

def _libre(conn, tareas, textos):
    from faros import settings
    settings.patch(conn, "owner", {"timbre_tiempo_libre": {"tareas": tareas, "textos": textos}})


def test_la_franja_libre_suena_con_tu_invitacion_y_sin_el_loop(conn, tmp_path):
    md = tmp_path / "cron_tiempo_libre.md"
    md.write_text("# Cron de tiempo libre\n\n```\n/loop 1h\n\nTe ha dado rato libre.\n-leer\n-escribir\n```\n\nCron: 17 * * * *\n",
                  encoding="utf-8")
    tid = tarea(conn, "Rato a elegir", hora="12:30")
    _libre(conn, [tid], {"alice": str(md)})
    t = timbre.Timbre(ahora=a_las(12, 28))
    texto = t.avisos(t.preparar(conn, a_las(12, 30)))["alice"]
    assert texto.startswith(f"Son las 12:30: empieza tu rato libre (#{tid} «Rato a elegir»).")
    assert "Te ha dado rato libre.\n-leer\n-escribir" in texto
    assert "/loop" not in texto and "Cron:" not in texto and "Cierra lo que estés haciendo" not in texto


def test_sin_texto_propio_remite_a_su_tarea_de_descanso(conn):
    tid = tarea(conn, "Descanso", hora="14:00")
    _libre(conn, [tid], {})
    t = timbre.Timbre(ahora=a_las(13, 58))
    assert "Lo que puedes hacer está en tu tarea de descanso." in t.avisos(t.preparar(conn, a_las(14, 0)))["alice"]


def test_si_coincide_con_trabajo_suena_el_aviso_normal(conn):
    libre = tarea(conn, "Rato a elegir", hora="15:00")
    tarea(conn, "algo mío", hora="15:00")
    _libre(conn, [libre], {})
    t = timbre.Timbre(ahora=a_las(14, 58))
    assert t.avisos(t.preparar(conn, a_las(15, 0)))["alice"].startswith("Son las 15:00. Tenías planeado:")


# ---- silenciar el propio timbre sin pedir permiso (condición de Eco, 13-nov) ----

def test_cada_uno_silencia_el_suyo_y_no_suena_ni_se_guarda(conn, monkeypatch):
    tarea(conn, "silenciada", hora="10:00")
    tarea(conn, "después", hora="11:05")
    r = timbre.silenciar(conn, "alice", "alice", "1h", ahora=a_las(9, 50))
    assert r["silenciado_hasta"] == f"{HOY.isoformat()}T10:50:10"
    t = timbre.Timbre(ahora=a_las(9, 58))
    plan = t.preparar(conn, a_las(10, 0))
    assert plan["pendientes"] == {}
    t.confirmar(plan, {})
    assert t.preparar(conn, a_las(10, 51))["pendientes"] == {}             # al volver, lo atrasado no llega
    assert list(t.preparar(conn, a_las(11, 5))["pendientes"]) == ["alice"]  # lo siguiente sí


def test_solo_el_suyo_salvo_el_dueno(conn):
    from faros.service import TicketError
    with pytest.raises(TicketError):
        timbre.silenciar(conn, "bob", "alice", "1h")
    assert timbre.silenciar(conn, "owner", "alice", "siempre")["silencios"] == {"alice": "siempre"}
    assert timbre.silenciar(conn, "alice", "alice", "")["silencios"] == {}           # y se quita con vacío


def test_mañana_es_hasta_las_doce_de_esta_noche_y_lo_raro_se_rechaza(conn):
    from faros.service import TicketError
    r = timbre.silenciar(conn, "alice", "alice", "mañana", ahora=a_las(17, 0))
    assert r["silenciado_hasta"].endswith("T00:00:00") and r["silenciado_hasta"] > HOY.isoformat()
    with pytest.raises(TicketError):
        timbre.silenciar(conn, "alice", "alice", "un ratito")


def test_api_y_estado_ensenan_los_silencios(tmp_path, monkeypatch):
    import sqlite3
    from fastapi.testclient import TestClient
    from faros import api
    monkeypatch.setattr(api, "load_token", lambda: "t" * 32)
    db.connect(tmp_path / "t.db").close()
    c = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    cl, H = TestClient(api.create_app(c)), {"X-FarOS-Token": "t" * 32}
    r = cl.post("/api/timbre/silencio", headers=H, json={"agent": "alice", "hasta": "siempre"})
    assert r.status_code == 200 and r.json()["silencios"] == {"alice": "siempre"}
    assert cl.get("/api/timbre", headers=H).json()["silencios"] == {"alice": "siempre"}
    assert cl.post("/api/timbre/silencio", headers=H, json={"agent": "bob", "agente": "alice", "hasta": ""}).status_code == 400
    assert cl.post("/api/timbre/silencio", headers=H, json={"agent": "owner", "agente": "alice", "hasta": ""}).json()["silencios"] == {}


# ---- cada uno marca qué tareas suyas son tiempo libre (Pepe: «dividir la tarea en dos», 13-nov) ----

def test_partir_la_hora_libre_en_dos_da_dos_invitaciones(conn):
    a = tarea(conn, "libre primera mitad", hora="15:00")
    b = tarea(conn, "libre segunda mitad", hora="15:30")
    timbre.marcar_libre(conn, "alice", a)
    assert timbre.marcar_libre(conn, "alice", b)["tareas"] == sorted([a, b])
    t = timbre.Timbre(ahora=a_las(14, 58))
    plan = t.preparar(conn, a_las(15, 0)); t.confirmar(plan, {"alice": "enviado"})
    assert "empieza tu rato libre" in t.avisos(plan)["alice"]
    assert "empieza tu rato libre" in t.avisos(t.preparar(conn, a_las(15, 30)))["alice"]


def test_solo_las_suyas_y_se_desmarca(conn):
    from faros.service import TicketError
    a = tarea(conn, "de alice", hora="15:00")
    with pytest.raises(TicketError):
        timbre.marcar_libre(conn, "bob", a)
    timbre.marcar_libre(conn, "alice", a)
    assert timbre.marcar_libre(conn, "alice", a, libre=False)["tareas"] == []
