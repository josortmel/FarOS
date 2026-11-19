"""#460 — Ajustes › Claves: una lista abierta de claves, probadas antes de guardarse.

Lo que pidió el dueño (7-oct-2026): varias claves de cada tipo («imagínate que creamos
varios bots de Telegram»), FarOS usa UNA de cada tipo, se prueban antes de guardarse y
solo se guardan si valen, y el tipo «otra» se admite sin prueba, con su origen.
La prueba de red está sustituida en conftest (_claves_sin_red): «mala…» = rechazada.
"""

import json
import os
import sqlite3
import tempfile
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AGENTICOS_BACKUP_DIR", tempfile.mkdtemp(prefix="faros_460_bk_"))

from faros import api, backup, credentials as cr, db, settings  # noqa: E402


@pytest.fixture
def conn(monkeypatch):
    for v in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "AGENTICOS_TG_BOT_TOKEN", "FAROS_TG_BOT_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    c = db.connect(":memory:")
    yield c
    c.close()


def _err(fn, *a, **k):
    with pytest.raises(cr.ClaveError) as e:
        fn(*a, **k)
    return e.value


# ------------------------------------------------------------------ alta y prueba

def test_otra_se_guarda_sin_probar_y_no_sale_a_la_red(conn, _claves_sin_red):
    c = cr.create(conn, "owner", "API del ayuntamiento", "otra", "abc123", origin="portal de datos abiertos")
    assert c["kind"] == "otra" and c["test_ok"] is None and c["tested_at"] is None
    assert c["in_use"] is False and c["origin"] == "portal de datos abiertos"
    assert c["value"] == cr.MASK and "abc123" not in json.dumps(c)
    assert _claves_sin_red == []                      # «otra» no se prueba


def test_la_primera_de_su_tipo_queda_en_uso_y_alimenta_su_ajuste(conn, _claves_sin_red):
    c = cr.create(conn, "owner", "DeepSeek principal", "deepseek", "  sk-bueno  ")
    assert c["in_use"] is True and c["test_ok"] is True and c["test_detail"] == "prueba falsa"
    assert _claves_sin_red == [("deepseek", "sk-bueno")]          # se prueba ya sin espacios
    assert settings.get(conn, "deepseek_api_key") == "sk-bueno"   # quien lee el ajuste, la ve
    assert settings.get_all(conn)["deepseek_api_key"] == settings.MASK


def test_una_clave_que_no_vale_no_se_guarda(conn):
    e = _err(cr.create, conn, "owner", "DeepSeek", "deepseek", "mala-clave")
    assert e.status == 422 and e.code == "credential.test_failed" and e.field == "value"
    assert e.extra["detail"] == "el servicio la rechaza (HTTP 401)"
    assert cr.listing(conn) == [] and settings.get(conn, "deepseek_api_key") == ""


def test_varios_bots_y_farOS_usa_el_marcado(conn):
    a = cr.create(conn, "owner", "Bot de Eco", "telegram", "111:aaa")
    b = cr.create(conn, "owner", "Bot de Prima", "telegram", "222:bbb")
    assert a["in_use"] and not b["in_use"]                         # la segunda no quita la primera
    cr.update(conn, "owner", b["id"], {"in_use": True})
    filas = {r["name"]: r for r in cr.listing(conn)}
    assert filas["Bot de Prima"]["in_use"] and not filas["Bot de Eco"]["in_use"]
    assert settings.get(conn, "telegram_token") == "222:bbb"
    cr.update(conn, "owner", b["id"], {"in_use": False})            # ninguna en uso: el ajuste se vacía
    assert settings.get(conn, "telegram_token") == ""


def test_cambiar_el_valor_lo_prueba_y_si_no_vale_no_toca_nada(conn):
    c = cr.create(conn, "owner", "Anthropic", "anthropic", "sk-ant-1")
    e = _err(cr.update, conn, "owner", c["id"], {"value": "mala-2"})
    assert e.code == "credential.test_failed"
    assert settings.get(conn, "anthropic_api_key") == "sk-ant-1"
    cr.update(conn, "owner", c["id"], {"value": "sk-ant-2"})
    assert settings.get(conn, "anthropic_api_key") == "sk-ant-2"
    cr.update(conn, "owner", c["id"], {"value": cr.MASK, "name": "Anthropic casa"})   # «••••» = no tocar
    assert settings.get(conn, "anthropic_api_key") == "sk-ant-2"


def test_reglas_de_entrada(conn):
    cr.create(conn, "owner", "DeepSeek", "deepseek", "sk-1")
    assert _err(cr.create, conn, "owner", "deepseek", "deepseek", "sk-2").status == 409   # nombre, sin mayúsculas
    assert _err(cr.create, conn, "owner", "X", "openai", "sk").code == "credential.kind_invalid"
    assert _err(cr.create, conn, "owner", "", "otra", "v").code == "credential.name_required"
    assert _err(cr.create, conn, "owner", "Y", "otra", "  ").code == "credential.value_required"
    assert _err(cr.create, conn, "owner", "Z", "otra", "v", in_use=True).code == "credential.not_usable"
    o = cr.create(conn, "owner", "Otra", "otra", "v")
    assert _err(cr.update, conn, "owner", o["id"], {"in_use": True}).code == "credential.not_usable"
    assert _err(cr.update, conn, "owner", o["id"], {"kind": "deepseek"}).code == "credential.field_not_editable"
    assert _err(cr.update, conn, "owner", 999, {"name": "n"}).status == 404


def test_solo_la_familia(conn):
    from faros.service import TicketError
    with pytest.raises(TicketError):
        cr.create(conn, "scheduler", "X", "otra", "v")


def test_probar_de_nuevo_apunta_el_resultado_sin_borrar(conn, monkeypatch):
    c = cr.create(conn, "owner", "DeepSeek", "deepseek", "sk-1")
    monkeypatch.setattr(cr, "PROBAR", lambda k, v: (False, "el servicio la rechaza (HTTP 401)"))
    r = cr.retest(conn, "owner", c["id"])
    assert r["test_ok"] is False and r["test_detail"].startswith("el servicio la rechaza")
    assert r["in_use"] and settings.get(conn, "deepseek_api_key") == "sk-1"   # no se quita sola
    o = cr.create(conn, "owner", "Otra", "otra", "v")
    assert _err(cr.retest, conn, "owner", o["id"]).code == "credential.untestable"


def test_borrar_la_que_estaba_en_uso_vacia_su_ajuste(conn):
    c = cr.create(conn, "owner", "DeepSeek", "deepseek", "sk-1")
    assert cr.delete(conn, "owner", c["id"]) == {"id": c["id"], "deleted": True, "was_in_use": True}
    assert settings.get(conn, "deepseek_api_key") == "" and cr.listing(conn) == []


# ------------------------------------------------------------------ PATCH /api/settings (el script de Prima, patch_settings)

def test_el_ajuste_escribe_en_la_marcada_y_si_no_hay_la_crea(conn):
    settings.patch(conn, "owner", {"deepseek_api_key": "sk-script"})
    [c] = cr.listing(conn)
    assert c["name"] == "DeepSeek (ajustes)" and c["in_use"] and c["test_ok"] is True
    settings.patch(conn, "owner", {"deepseek_api_key": "sk-script-2"})
    [c] = cr.listing(conn)                                         # la misma, con valor nuevo
    assert settings.get(conn, "deepseek_api_key") == "sk-script-2"
    settings.patch(conn, "owner", {"deepseek_api_key": ""})        # '' = ninguna en uso; la lista no pierde nada
    [c] = cr.listing(conn)
    assert not c["in_use"] and settings.get(conn, "deepseek_api_key") == ""


def test_un_patch_con_una_clave_que_no_vale_no_deja_nada_a_medias(conn):
    e = _err(settings.patch, conn, "owner", {"verifier_model": "sonnet", "deepseek_api_key": "mala"})
    assert e.code == "credential.test_failed"
    assert settings.get(conn, "verifier_model") == "haiku" and cr.listing(conn) == []


# ------------------------------------------------------------------ esquema 6, sobre COPIA

def test_v6_las_claves_que_ya_habia_entran_en_la_lista(tmp_path, monkeypatch):
    for v in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    p = tmp_path / "copia.db"
    c = db.connect(p)
    now = datetime.now().isoformat(timespec="seconds")
    # una base v5: el ajuste con la clave, sin lista de Claves y sin la fila de la versión 6
    c.execute("INSERT INTO settings (key, value, secret, updated_at) VALUES ('deepseek_api_key', ?, 1, ?)",
              (json.dumps("sk-de-antes"), now))
    c.execute("DELETE FROM schema_version WHERE version >= 6")   # una v5 no tiene ni la 6 ni la 7
    c.commit(); c.close()
    c = db.connect(p)
    [cl] = cr.listing(c)
    assert cl["name"] == "DeepSeek" and cl["in_use"] and cl["test_ok"] is None
    assert settings.get(c, "deepseek_api_key") == "sk-de-antes" and db.schema_version(c) == db.SCHEMA_VERSION
    c.close()
    c = db.connect(p)                                              # idempotente
    assert len(cr.listing(c)) == 1
    c.close()


# ------------------------------------------------------------------ backup

def test_el_backup_sale_sin_los_valores(conn):
    cr.create(conn, "owner", "Bot de Eco", "telegram", "111:secreto")
    path = backup.create_backup(conn, datetime(2026, 10, 7, 8, 0))
    bk = sqlite3.connect(str(path))
    try:
        assert bk.execute("SELECT value FROM credentials").fetchall() == [("",)]
        assert "111:secreto" not in json.dumps(bk.execute("SELECT * FROM settings").fetchall())
    finally:
        bk.close()
    res = backup.verify_backup(path, conn)
    assert "Claves › Bot de Eco" in res["secrets_blanked"]


def test_restaurada_vacia_lo_dice_y_no_se_prueba(conn):
    c = cr.create(conn, "owner", "DeepSeek", "deepseek", "sk-1")
    conn.execute("UPDATE credentials SET value = '' WHERE id = ?", (c["id"],)); conn.commit()
    [r] = cr.listing(conn)
    assert r["empty"] is True and r["value"] == ""
    assert _err(cr.retest, conn, "owner", c["id"]).code == "credential.value_empty"


# ------------------------------------------------------------------ API

TOKEN = "t-460"
H = {"X-FarOS-Token": TOKEN}


@pytest.fixture
def client(tmp_path, monkeypatch):
    for v in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(api, "load_token", lambda: TOKEN)
    db.connect(tmp_path / "t.db").close()
    c = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    yield TestClient(api.create_app(c))
    c.close()


def test_api_el_valor_no_sale_nunca(client):
    r = client.post("/api/credentials", headers=H,
                    json={"agent": "owner", "name": "DeepSeek", "kind": "deepseek", "value": "sk-api-secreto"})
    assert r.status_code == 201 and r.json()["in_use"] is True
    cid = r.json()["id"]
    for resp in (client.get("/api/credentials", headers=H),
                 client.patch(f"/api/credentials/{cid}", headers=H, json={"agent": "owner", "origin": "consola"}),
                 client.post(f"/api/credentials/{cid}/test", headers=H, json={"agent": "owner"}),
                 client.get("/api/settings", headers=H)):
        assert resp.status_code == 200 and "sk-api-secreto" not in resp.text


def test_api_la_que_no_vale_da_422_con_su_codigo(client):
    r = client.post("/api/credentials", headers=H,
                    json={"agent": "owner", "name": "DeepSeek", "kind": "deepseek", "value": "mala"})
    assert r.status_code == 422
    assert r.json()["code"] == "credential.test_failed" and r.json()["field"] == "value"
    assert client.get("/api/credentials", headers=H).json() == []


def test_api_ajustes_tambien_prueba(client):
    r = client.patch("/api/settings", headers=H, json={"agent": "owner", "deepseek_api_key": "mala"})
    assert r.status_code == 422 and r.json()["code"] == "credential.test_failed"
    r = client.patch("/api/settings", headers=H, json={"agent": "owner", "deepseek_api_key": "sk-bien"})
    assert r.status_code == 200 and r.json()["deepseek_api_key"] == settings.MASK
    assert [c["name"] for c in client.get("/api/credentials", headers=H).json()] == ["DeepSeek (ajustes)"]


def test_api_borrar_pide_agent(client):
    r = client.post("/api/credentials", headers=H, json={"agent": "owner", "name": "O", "kind": "otra", "value": "v"})
    cid = r.json()["id"]
    assert client.delete(f"/api/credentials/{cid}", headers=H).json()["code"] == "request.agent_required"
    assert client.delete(f"/api/credentials/{cid}?agent=owner", headers=H).json()["deleted"] is True
    assert client.patch("/api/credentials/999", headers=H, json={"agent": "owner", "name": "x"}).status_code == 404


# ------------------------------------------------------------------ la prueba de red de verdad (httpx simulado)

class _R:
    def __init__(self, status, data):
        self.status_code, self._d = status, data

    def json(self):
        return self._d


def _red(monkeypatch, respuestas):
    """httpx.get simulado: la URL elige la respuesta (o la excepción)."""
    vistas = []

    def get(url, headers=None, timeout=None):
        vistas.append(url)
        for trozo, r in respuestas.items():
            if trozo in url:
                if isinstance(r, Exception):
                    raise r
                return r
        raise AssertionError(f"URL no esperada: {url}")
    monkeypatch.setattr(cr.httpx, "get", get)
    return vistas


def test_deepseek_sin_saldo_vale_pero_lo_dice(monkeypatch):
    # Prima, 7-oct: /models da 200 sin saldo y cada llamada da 402
    _red(monkeypatch, {"/models": _R(200, {"data": [{}, {}]}),
                       "/user/balance": _R(200, {"is_available": False, "balance_infos": []})})
    assert cr._probar_red("deepseek", "sk-x") == (True, "2 modelos · sin saldo")


def test_deepseek_con_saldo_o_sin_saber(monkeypatch):
    _red(monkeypatch, {"/models": _R(200, {"data": [{}, {}]}), "/user/balance": _R(200, {"is_available": True})})
    assert cr._probar_red("deepseek", "sk-x") == (True, "2 modelos")
    _red(monkeypatch, {"/models": _R(200, {"data": [{}]}), "/user/balance": cr.httpx.ConnectError("caido")})
    assert cr._probar_red("deepseek", "sk-x") == (True, "1 modelos")      # el saldo no rompe la prueba


def test_rechazada_y_el_error_nunca_lleva_la_clave(monkeypatch):
    _red(monkeypatch, {"/models": _R(401, {})})
    assert cr._probar_red("deepseek", "sk-x") == (False, "el servicio la rechaza (HTTP 401)")
    token = "123456:SECRETO-EN-LA-URL"
    _red(monkeypatch, {"getMe": cr.httpx.ConnectError(f"fallo en https://api.telegram.org/bot{token}/getMe")})
    ok, detalle = cr._probar_red("telegram", token)
    assert ok is False and token not in detalle and detalle == "no se pudo comprobar (ConnectError)"
