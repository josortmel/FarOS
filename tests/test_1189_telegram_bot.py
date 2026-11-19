"""#1189: el bot de Telegram de FarOS. Le manda al dueño sus timbres y las decisiones pendientes con botones;
el botón (o una respuesta en texto) decide en FarOS y avisa a quien la pidió; solo atiende el chat del dueño
y lo de cualquier otro chat queda apuntado sin respuesta; el token no sale en los errores."""

import asyncio
import json
from datetime import date, datetime, time

import httpx
import pytest

from faros import db, decisions, roster, service as s, settings, telegram_bot as tb, timbre

TOKEN, CHAT = "123:SECRETO", "999"


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def configurar(conn):
    settings.patch(conn, "owner", {"telegram_token": TOKEN, "telegram_chat_id": CHAT})


class Telegram:
    """Telegram de mentira: apunta cada llamada y devuelve lo que toca."""

    def __init__(self):
        self.llamadas, self.updates, self.n = [], [], 100

    def __call__(self, token, metodo, cuerpo=None, timeout=15):
        assert token == TOKEN
        self.llamadas.append((metodo, cuerpo or {}))
        if metodo == "getUpdates":
            ups, self.updates = self.updates, []
            return ups
        if metodo == "sendMessage":
            self.n += 1
            return {"message_id": self.n}
        return True

    def de(self, metodo):
        return [c for m, c in self.llamadas if m == metodo]


@pytest.fixture
def tg(monkeypatch):
    t = Telegram()
    monkeypatch.setattr(tb, "llamar", t)
    return t


def bot(tmp_path, avisos=None):
    avisos = avisos if avisos is not None else []
    return tb.Bot(estado=tmp_path / "estado.json", registro=tmp_path / "registro.jsonl",
                  avisar=lambda agente, texto: avisos.append((agente, texto)))


def pedir(conn):
    return decisions.ask(conn, "alice", "¿Subimos la versión hoy?",
                         [{"label": "Sí, hoy", "text": "Subirla hoy a las 16:00"},
                          {"label": "El lunes", "text": "Esperar al lunes"}], context="Lo pide el juez.")


def vuelta(b, conn):
    return asyncio.run(b.vuelta(conn, espera=0))


def registro(tmp_path):
    return [json.loads(l) for l in (tmp_path / "registro.jsonl").read_text(encoding="utf-8").splitlines()]


def test_sin_token_no_hace_nada(conn, tg, tmp_path):
    assert vuelta(bot(tmp_path), conn) is False
    assert tg.llamadas == []


def test_la_decision_sale_una_vez_con_sus_botones(conn, tg, tmp_path):
    configurar(conn)
    d = pedir(conn)
    b = bot(tmp_path)
    vuelta(b, conn)
    vuelta(b, conn)
    enviados = tg.de("sendMessage")
    assert len(enviados) == 1
    m = enviados[0]
    assert m["chat_id"] == CHAT and "¿Subimos la versión hoy?" in m["text"] and "alice" in m["text"]
    assert "Subirla hoy a las 16:00" in m["text"] and "Lo pide el juez." in m["text"]
    assert m["reply_markup"]["inline_keyboard"] == [[{"text": "Sí, hoy", "callback_data": f"d:{d['id']}:0"}],
                                                    [{"text": "El lunes", "callback_data": f"d:{d['id']}:1"}]]


def test_tras_reiniciar_no_la_vuelve_a_mandar(conn, tg, tmp_path):
    configurar(conn)
    pedir(conn)
    vuelta(bot(tmp_path), conn)
    vuelta(bot(tmp_path), conn)          # otro Bot, mismo fichero de estado
    assert len(tg.de("sendMessage")) == 1


def boton(update_id, data, de=CHAT, mensaje=101):
    return {"update_id": update_id, "callback_query": {"id": f"cb{update_id}", "from": {"id": int(de)},
                                                       "data": data, "message": {"message_id": mensaje}}}


def test_el_boton_del_dueno_decide_y_avisa_a_quien_la_pidio(conn, tg, tmp_path):
    configurar(conn)
    d = pedir(conn)
    avisos = []
    b = bot(tmp_path, avisos)
    vuelta(b, conn)                                   # sale la decisión (message_id 101)
    tg.updates = [boton(7, f"d:{d['id']}:1")]
    vuelta(b, conn)
    hecha = decisions.get_decision(conn, d["id"])
    assert hecha["status"] == "decided" and hecha["decision"] == "El lunes"
    assert hecha["decided_by"] == roster.HOUSE_OWNER
    assert hecha["decidido_por"] == {"tipo": "humano", "quien": roster.HOUSE_OWNER}
    assert hecha["history"][-1]["surface"] == "telegram"
    assert avisos and avisos[0][0] == "alice" and "El lunes" in avisos[0][1]
    editado = tg.de("editMessageText")[0]
    assert editado["message_id"] == 101 and "✅ Decidido: El lunes" in editado["text"]
    assert "reply_markup" not in editado               # sin botones: ya no se puede pulsar otra vez
    assert tg.de("answerCallbackQuery")[0]["callback_query_id"] == "cb7"
    assert b.offset == 8 and json.loads((tmp_path / "estado.json").read_text())["offset"] == 8


def test_el_boton_de_otro_no_decide_ni_contesta_y_queda_apuntado(conn, tg, tmp_path):
    configurar(conn)
    d = pedir(conn)
    b = bot(tmp_path)
    vuelta(b, conn)
    tg.llamadas.clear()
    tg.updates = [boton(3, f"d:{d['id']}:0", de="555")]
    vuelta(b, conn)
    assert decisions.get_decision(conn, d["id"])["status"] == "pending"
    assert [m for m, _ in tg.llamadas] == ["getUpdates"]
    assert registro(tmp_path)[-1]["tipo"] == "ajeno" and registro(tmp_path)[-1]["de"] == 555


def test_un_mensaje_de_otro_chat_no_recibe_respuesta_y_queda_apuntado(conn, tg, tmp_path):
    configurar(conn)
    b = bot(tmp_path)
    tg.updates = [{"update_id": 1, "message": {"message_id": 5, "chat": {"id": 555}, "from": {"id": 555,
                                                "username": "curioso"}, "text": "hola bot"}}]
    vuelta(b, conn)
    assert tg.de("sendMessage") == []
    r = registro(tmp_path)[-1]
    assert r["tipo"] == "ajeno" and r["usuario"] == "curioso" and r["texto"] == "hola bot"


def test_contestar_en_texto_decide_con_ese_texto(conn, tg, tmp_path):
    configurar(conn)
    d = pedir(conn)
    b = bot(tmp_path)
    vuelta(b, conn)                                   # message_id 101
    tg.updates = [{"update_id": 2, "message": {"message_id": 6, "chat": {"id": int(CHAT)}, "from": {"id": int(CHAT)},
                                                "text": "Mañana a primera hora, que hoy no estoy",
                                                "reply_to_message": {"message_id": 101}}}]
    vuelta(b, conn)
    hecha = decisions.get_decision(conn, d["id"])
    assert hecha["decision"] == "Mañana a primera hora, que hoy no estoy"
    assert "✅ Decidido" in tg.de("sendMessage")[-1]["text"]


def test_un_mensaje_suelto_del_dueno_recibe_la_ayuda(conn, tg, tmp_path):
    configurar(conn)
    tg.updates = [{"update_id": 1, "message": {"message_id": 5, "chat": {"id": int(CHAT)},
                                                "from": {"id": int(CHAT)}, "text": "/start"}}]
    vuelta(bot(tmp_path), conn)
    assert tg.de("sendMessage")[0]["text"] == tb.AYUDA


def test_una_decision_ya_decidida_avisa_y_no_cambia(conn, tg, tmp_path):
    configurar(conn)
    d = pedir(conn)
    b = bot(tmp_path)
    vuelta(b, conn)
    decisions.decide(conn, roster.HOUSE_OWNER, d["id"], option="Sí, hoy", rationale="desde la app")
    tg.updates = [boton(4, f"d:{d['id']}:1")]
    vuelta(b, conn)
    assert decisions.get_decision(conn, d["id"])["decision"] == "Sí, hoy"
    alerta = tg.de("answerCallbackQuery")[0]
    assert alerta["show_alert"] is True and "ya está decidida" in alerta["text"]


def test_decidida_en_la_app_su_mensaje_pierde_los_botones_una_sola_vez(conn, tg, tmp_path):
    configurar(conn)
    d = pedir(conn)
    b = bot(tmp_path)
    vuelta(b, conn)                                   # message_id 101, con botones
    decisions.decide(conn, roster.HOUSE_OWNER, d["id"], option="El lunes", rationale="desde la app", surface="desktop")
    vuelta(b, conn)
    vuelta(b, conn)
    editados = tg.de("editMessageText")
    assert len(editados) == 1 and editados[0]["message_id"] == 101 and "reply_markup" not in editados[0]
    assert f"✅ Decidido en FarOS por {roster.HOUSE_OWNER}: El lunes" in editados[0]["text"]
    assert "Pulsa una opción" not in editados[0]["text"]
    vuelta(bot(tmp_path), conn)                       # tras reiniciar tampoco se repite
    assert len(tg.de("editMessageText")) == 1


def test_decidida_con_el_boton_no_se_vuelve_a_editar(conn, tg, tmp_path):
    configurar(conn)
    d = pedir(conn)
    b = bot(tmp_path)
    vuelta(b, conn)
    tg.updates = [boton(7, f"d:{d['id']}:0")]
    vuelta(b, conn)
    vuelta(b, conn)
    assert len(tg.de("editMessageText")) == 1


def test_el_token_no_sale_en_el_error(monkeypatch):
    def revienta(url, **kw):
        raise httpx.ConnectError(f"no llego a {url}")
    monkeypatch.setattr(tb.httpx, "post", revienta)
    with pytest.raises(tb.BotError) as e:
        tb.llamar(TOKEN, "getMe")
    assert TOKEN not in str(e.value) and "ConnectError" in str(e.value)


# ---------------------------------------------------------------- el endpoint para los scripts de la casa

@pytest.fixture
def cliente(tmp_path, monkeypatch):
    import sqlite3
    from fastapi.testclient import TestClient
    from faros import api
    monkeypatch.setattr(api, "load_token", lambda: "t" * 32)
    db.connect(tmp_path / "t.db").close()
    c = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    yield TestClient(api.create_app(c)), c
    c.close()


H = {"X-FarOS-Token": "t" * 32}


def test_un_script_manda_al_dueno_sin_ver_el_token(cliente, tg):
    cli, c = cliente
    assert cli.post("/api/telegram/enviar", json={"agent": "alice", "text": "hola"}, headers=H).status_code == 409
    configurar(c)
    r = cli.post("/api/telegram/enviar", json={"agent": "alice", "text": "La revista de hoy"}, headers=H)
    assert r.status_code == 200 and r.json() == {"ok": True, "message_id": 101}
    assert TOKEN not in r.text
    assert tg.de("sendMessage") == [{"chat_id": CHAT, "text": "La revista de hoy"}]
    assert cli.post("/api/telegram/enviar", json={"agent": "alice", "text": "x"}).status_code == 401
    assert cli.post("/api/telegram/enviar", json={"agent": "nadie", "text": "x"}, headers=H).status_code == 400


def test_decidida_en_la_app_avisa_por_relay_a_quien_la_pidio(cliente, monkeypatch):
    # Pepe, 17-nov: «me molesta un poco que el propio FarOS, cuando respondo en sus pestañas, no les envíe un mensaje
    # con la pregunta y la decisión» (por Telegram ya llegaba).
    from faros import api
    cli, c = cliente
    avisos = []
    monkeypatch.setattr(api, "AVISAR_RELAY", lambda a: avisos.append(a) or {})
    d = pedir(c)
    r = cli.post(f"/api/decisions/{d['id']}/decide", headers=H,
                 json={"agent": roster.HOUSE_OWNER, "option": "El lunes", "rationale": "mejor con calma", "surface": "desktop"})
    assert r.status_code == 200
    assert avisos == [{"alice": f"{roster.HOUSE_OWNER} ha decidido «¿Subimos la versión hoy?»: El lunes — mejor con calma — FarOS"}]
    propia = decisions.ask(c, "alice", "¿Mía?", ["Sí, esta", "No, esta no"])
    cli.post(f"/api/decisions/{propia['id']}/decide", headers=H, json={"agent": "alice", "option": "Sí, esta", "rationale": "x"})
    assert len(avisos) == 1                                   # quien decide la suya no se avisa a sí mismo


# ---------------------------------------------------------------- timbre del dueño

HOY = date.today()


def a_las(h, m=0, seg=10):
    return datetime.combine(HOY, time(h, m, seg))


def tarea_del_dueno(conn, titulo, hora="10:00"):
    return s.propose_ticket(conn, "owner", titulo, owner=roster.HOUSE_OWNER,
                            scheduled_at=f"{HOY.isoformat()}T{hora}", auto_accept=True)["ticket"]["id"]


def test_el_timbre_del_dueno_va_aparte_y_no_se_repite(conn):
    tarea_del_dueno(conn, "Con Eco: sesión del libro")
    t = timbre.Timbre(ahora=a_las(9, 58))
    assert t.preparar(conn, a_las(10, 0))["pendientes"] == {}        # por el relay, nada
    suyos = t.del_dueno(conn, a_las(10, 0))
    assert [i["title"] for i in suyos] == ["Con Eco: sesión del libro"]
    assert tb.texto_timbre(suyos, a_las(10, 0)) == "⏰ Son las 10:00.\n• Con Eco: sesión del libro"
    t.marcar_dueno(suyos, a_las(10, 0), "enviado")
    assert t.del_dueno(conn, a_las(10, 0, 40)) == []


def test_si_telegram_falla_se_reintenta_mientras_siga_en_la_ventana(conn):
    tarea_del_dueno(conn, "entreno")
    t = timbre.Timbre(ahora=a_las(9, 58))
    t.marcar_dueno(t.del_dueno(conn, a_las(10, 0)), a_las(10, 0), "error: getUpdates: ConnectError")
    assert len(t.del_dueno(conn, a_las(10, 1))) == 1
    assert t.del_dueno(conn, a_las(10, 6)) == []                     # fuera de la ventana de 5 min


def test_tras_reiniciar_no_repite_lo_del_dueno(conn, tmp_path):
    tarea_del_dueno(conn, "entreno")
    estado = tmp_path / "timbre_ultimo.txt"
    t = timbre.Timbre(ahora=a_las(9, 58), estado=estado)
    t.confirmar(t.preparar(conn, a_las(10, 0, 30)), {})               # la vuelta guarda 10:00:30
    t.marcar_dueno(t.del_dueno(conn, a_las(10, 0, 30)), a_las(10, 0, 30), "enviado")
    otro = timbre.Timbre(ahora=a_las(10, 1), estado=estado)           # el daemon se reinicia
    assert otro.del_dueno(conn, a_las(10, 1)) == []


def test_el_dueno_silenciado_no_recibe_timbre(conn):
    tarea_del_dueno(conn, "entreno")
    timbre.silenciar(conn, roster.HOUSE_OWNER, roster.HOUSE_OWNER, "3h", ahora=a_las(9, 0))
    t = timbre.Timbre(ahora=a_las(9, 58))
    assert t.del_dueno(conn, a_las(10, 0)) == []
