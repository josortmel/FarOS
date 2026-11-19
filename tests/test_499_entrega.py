"""#499 — claves bajo demanda (encargo de Pepe del 14-oct; alcance aprobado el 16-oct).

Una clave guardada en FarOS llega SOLO al entorno del proceso que la necesita (el MCP de X, por ejemplo),
sin pasar por transcripts, .mcp.json, líneas de comandos ni logs. Límite dicho a Pepe tal cual: el token de
la API está en un fichero que cualquier proceso del mismo usuario puede leer, así que esto no la esconde de
quien la busque a propósito. Por eso solo se entregan las que Pepe marca como entregables, y cada petición
queda registrada (sin el valor). El MCP de FarOS NO tiene herramienta para pedir valores.
Diseño: Hilo/Documentacion/diseno_499_claves_bajo_demanda.md.
"""

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from faros import api, credentials as cr, db, mcp_server, service as s

SECRETO = "otra-" + "a1b2c3d4e5f6" * 3
LANZADOR = Path(__file__).resolve().parent.parent / "tools" / "faros_con_claves.py"


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _err(fn, *a, **k):
    with pytest.raises(cr.ClaveError) as e:
        fn(*a, **k)
    return e.value


def _clave(conn, nombre="X_API_KEY", valor=SECRETO, entregable=True):
    c = cr.create(conn, "owner", nombre, "otra", valor)
    if entregable:
        cr.update(conn, s.HOUSE_OWNER, c["id"], {"deliverable": True})
    return c


# ------------------------------------------------------------------ entregable: solo lo marca el dueño

def test_por_defecto_no_es_entregable(conn):
    assert cr.create(conn, "owner", "X", "otra", "v")["deliverable"] is False


def test_solo_el_dueno_de_la_casa_la_marca_entregable(conn):
    c = cr.create(conn, "owner", "X", "otra", "v")
    assert _err(cr.update, conn, "alice", c["id"], {"deliverable": True}).code == "credential.deliverable_owner_only"
    assert cr.update(conn, s.HOUSE_OWNER, c["id"], {"deliverable": True})["deliverable"] is True


# ------------------------------------------------------------------ la entrega

def test_entrega_por_nombre_y_queda_registrada_sin_el_valor(conn):
    _clave(conn)
    res = cr.deliver(conn, "x_api_key", requested_by="mcp:x-twitter", purpose="arrancar el MCP de X")
    assert res == {"name": "X_API_KEY", "value": SECRETO}
    [fila] = cr.deliveries(conn)
    assert (fila["name"], fila["requested_by"], fila["ok"]) == ("X_API_KEY", "mcp:x-twitter", True)
    volcado = json.dumps([dict(r) for r in conn.execute("SELECT * FROM credential_deliveries")])
    assert SECRETO not in volcado


@pytest.mark.parametrize("caso,status,code", [
    ("no_existe", 404, "credential.not_found"),
    ("no_entregable", 403, "credential.not_deliverable"),
    ("vacia", 409, "credential.value_empty"),
])
def test_lo_que_no_se_entrega_se_niega_y_tambien_se_registra(conn, caso, status, code):
    if caso == "no_entregable":
        _clave(conn, entregable=False)
    elif caso == "vacia":
        c = _clave(conn)
        conn.execute("UPDATE credentials SET value = '' WHERE id = ?", (c["id"],)); conn.commit()
    e = _err(cr.deliver, conn, "X_API_KEY", requested_by="mcp:x", purpose="p")
    assert (e.status, e.code) == (status, code)
    [fila] = cr.deliveries(conn)
    assert fila["ok"] is False and fila["reason"]


def test_quien_y_para_que_son_obligatorios_y_cortos(conn):
    _clave(conn)
    assert _err(cr.deliver, conn, "X_API_KEY", requested_by="", purpose="p").field == "requested_by"
    assert _err(cr.deliver, conn, "X_API_KEY", requested_by="a\nb", purpose="p").field == "requested_by"
    assert _err(cr.deliver, conn, "X_API_KEY", requested_by="mcp:x", purpose="x" * 300).field == "purpose"


def test_el_backup_lleva_el_registro_pero_nunca_valores(conn, tmp_path, monkeypatch):
    from datetime import datetime
    from faros import backup
    monkeypatch.setenv("AGENTICOS_BACKUP_DIR", str(tmp_path))
    _clave(conn)
    cr.deliver(conn, "X_API_KEY", requested_by="mcp:x", purpose="p")
    path = backup.create_backup(conn, datetime(2026, 10, 16, 8, 0))
    assert SECRETO.encode() not in Path(path).read_bytes()


def test_el_mcp_de_faros_no_tiene_herramienta_para_pedir_valores():
    nombres = {t.name for t in asyncio_run(mcp_server.mcp.list_tools())}
    assert not any("deliver" in n or "entreg" in n for n in nombres)


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


# ------------------------------------------------------------------ API

TOKEN = "t-499"
H = {"X-FarOS-Token": TOKEN}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "load_token", lambda: TOKEN)
    db.connect(tmp_path / "t.db").close()
    c = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    yield TestClient(api.create_app(c)), c
    c.close()


def test_api_entrega_niega_y_lista_el_registro(client):
    cli, c = client
    _clave(c)
    _clave(c, "NO_ENTREGABLE", "otro-valor", entregable=False)
    ok = cli.post("/api/credentials/deliver", headers=H,
                  json={"name": "X_API_KEY", "requested_by": "mcp:x", "purpose": "arrancar"})
    assert ok.status_code == 200 and ok.json()["value"] == SECRETO
    no = cli.post("/api/credentials/deliver", headers=H,
                  json={"name": "NO_ENTREGABLE", "requested_by": "mcp:x", "purpose": "arrancar"})
    assert no.status_code == 403 and "otro-valor" not in no.text
    reg = cli.get("/api/credentials/deliveries", headers=H)
    assert reg.status_code == 200 and [r["ok"] for r in reg.json()] == [False, True]
    assert SECRETO not in reg.text
    lista = cli.get("/api/credentials", headers=H)
    assert SECRETO not in lista.text and {r["name"]: r["deliverable"] for r in lista.json()} == {
        "X_API_KEY": True, "NO_ENTREGABLE": False}


def test_api_sin_token_no_entrega(client):
    cli, c = client
    _clave(c)
    r = cli.post("/api/credentials/deliver", json={"name": "X_API_KEY", "requested_by": "x", "purpose": "p"})
    assert r.status_code == 401 and SECRETO not in r.text


# ------------------------------------------------------------------ el lanzador, contra un daemon de mentira

HIJO = ("import hashlib, os, sys; v = os.environ.get('X_TOKEN', ''); "
        "print('sha=' + hashlib.sha256(v.encode()).hexdigest()); print('argv=' + repr(sys.argv)); "
        "sys.exit(7)")


class _Daemon:
    """Un servidor HTTP mínimo que responde como /api/credentials/deliver (sin FarOS detrás)."""

    def __init__(self, tmp_path, entregar=True):
        import http.server
        self.peticiones = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.peticiones.append((self.headers.get("X-FarOS-Token"), body))
                if entregar and body["name"] == "X_API_KEY":
                    data, code = {"name": "X_API_KEY", "value": SECRETO}, 200
                else:
                    data, code = {"error": "forbidden", "code": "credential.not_deliverable",
                                  "reason": "no es entregable"}, 403
                raw = json.dumps(data).encode()
                self.send_response(code); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

            def log_message(self, *a):
                pass

        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.home = tmp_path / "home"
        self.home.mkdir()
        (self.home / "token").write_text("tok-lanzador\n", encoding="utf-8")

    def env(self):
        e = {k: v for k, v in os.environ.items() if k.upper() not in ("X_TOKEN",)}
        e.update(FAROS_HOME=str(self.home), FAROS_PORT=str(self.port))
        return e


def _lanzar(d, *claves):
    args = [sys.executable, str(LANZADOR)]
    for c in claves:
        args += ["--clave", c]
    args += ["--para", "test 499", "--", sys.executable, "-c", HIJO]
    return subprocess.run(args, capture_output=True, text=True, env=d.env(), timeout=60)


def test_lanzador_mete_la_clave_solo_en_el_entorno_del_hijo(tmp_path):
    d = _Daemon(tmp_path)
    r = _lanzar(d, "X_API_KEY=X_TOKEN")
    assert r.returncode == 7                                           # el código del hijo, tal cual
    assert f"sha={hashlib.sha256(SECRETO.encode()).hexdigest()}" in r.stdout
    assert SECRETO not in r.stdout and SECRETO not in r.stderr        # ni el hijo ni el lanzador lo enseñan
    assert SECRETO not in r.stdout.split("argv=")[1]                   # ni va en la línea de comandos del hijo
    tok, body = d.peticiones[0]
    assert tok == "tok-lanzador" and body["name"] == "X_API_KEY" and body["purpose"] == "test 499"
    d.srv.shutdown()


def test_lanzador_si_una_clave_se_niega_no_arranca_el_hijo(tmp_path):
    d = _Daemon(tmp_path, entregar=False)
    r = _lanzar(d, "X_API_KEY=X_TOKEN")
    assert r.returncode == 2 and "sha=" not in r.stdout
    assert "X_API_KEY" in r.stderr and "no es entregable" in r.stderr
    d.srv.shutdown()


def test_lanzador_sin_daemon_lo_dice_y_no_arranca(tmp_path):
    d = _Daemon(tmp_path)
    d.srv.shutdown(); d.srv.server_close()
    r = _lanzar(d, "X_API_KEY=X_TOKEN")
    assert r.returncode == 2 and "sha=" not in r.stdout and "FarOS" in r.stderr


# ------------------------------------------------------------------ #577: el stderr del hijo sale enmascarado

# #658: el hijo escribe su stderr en UTF-8 explícito y el test lo lee en UTF-8. Con `sys.stderr.write` y
# `text=True` el resultado dependía del entorno: con PYTHONIOENCODING=utf-8 el hijo escribía UTF-8 y el test
# lo descodificaba con el locale (cp1252), y «línea» llegaba como «lÃ­nea» sin que el lanzador tocara un byte.
HIJO_CHIVATO = ("import os, sys; v = os.environ['X_TOKEN']; "
                "print('stdout:' + v, flush=True); "
                "sys.stderr.buffer.write(('error de auth con clave ' + v + '\\nsegunda línea sin clave\\n').encode('utf-8')); "
                "sys.stderr.buffer.write(('final sin salto ' + v).encode('utf-8')); sys.exit(3)")


def test_lanzador_enmascara_la_clave_en_el_stderr_del_hijo_y_no_toca_stdout(tmp_path):
    d = _Daemon(tmp_path)
    args = [sys.executable, str(LANZADOR), "--clave", "X_API_KEY=X_TOKEN", "--para", "test 577", "--",
            sys.executable, "-c", HIJO_CHIVATO]
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", env=d.env(), timeout=60)
    assert r.returncode == 3
    assert SECRETO not in r.stderr
    assert "error de auth con clave ****" in r.stderr and "segunda línea sin clave" in r.stderr
    assert "final sin salto ****" in r.stderr
    assert "stdout:" + SECRETO in r.stdout          # stdout es el canal del protocolo MCP: pasa tal cual
    d.srv.shutdown()


@pytest.mark.parametrize("codificacion", ["utf-8", "cp1252"])
def test_lanzador_pasa_el_stderr_del_hijo_byte_a_byte(tmp_path, codificacion):
    # #658: el lanzador no sabe en qué codifica el hijo, así que no descodifica nada: los bytes salen como entran.
    d = _Daemon(tmp_path)
    linea = "línea con tildes y eñes: añoranza, camión, pingüino\n".encode(codificacion)
    hijo = f"import sys; sys.stderr.buffer.write({linea!r}); sys.stderr.buffer.flush()"
    args = [sys.executable, str(LANZADOR), "--clave", "X_API_KEY=X_TOKEN", "--para", "test 658", "--",
            sys.executable, "-c", hijo]
    r = subprocess.run(args, capture_output=True, env=d.env(), timeout=60)
    assert r.returncode == 0
    assert r.stderr == linea
    d.srv.shutdown()


# ------------------------------------------------------------------ revisión de Prima (16-oct): detección y valores en las etiquetas

@pytest.fixture
def avisos(monkeypatch):
    from faros import notify
    enviados = []
    monkeypatch.setattr(notify, "telegram", lambda conn, text: enviados.append(text) or True)
    return enviados


def test_la_primera_vez_que_alguien_pide_una_clave_para_algo_se_avisa_y_lo_repetido_no(conn, avisos):
    _clave(conn)
    cr.deliver(conn, "X_API_KEY", requested_by="mcp:x", purpose="arrancar el MCP de X")
    cr.deliver(conn, "X_API_KEY", requested_by="mcp:x", purpose="arrancar el MCP de X")
    assert len(avisos) == 1 and "mcp:x" in avisos[0] and "entregada" in avisos[0]
    # #1321, decisión 134 de Pepe (19-nov): una vez al día por persona y clave. Otro purpose el mismo día ya no avisa.
    cr.deliver(conn, "X_API_KEY", requested_by="mcp:x", purpose="otra cosa")
    cr.deliver(conn, "X_API_KEY", requested_by="otro", purpose="arrancar el MCP de X")
    assert len(avisos) == 2
    assert all(SECRETO not in a for a in avisos)


def test_una_peticion_negada_nueva_tambien_avisa(conn, avisos):
    _clave(conn, entregable=False)
    with pytest.raises(cr.ClaveError):
        cr.deliver(conn, "X_API_KEY", requested_by="curioso", purpose="ver qué hay")
    assert len(avisos) == 1 and "NEGADA" in avisos[0] and "credential.not_deliverable" in avisos[0]


def test_un_aviso_que_falla_no_tumba_la_entrega(conn, monkeypatch):
    from faros import notify
    def peta(conn, text):
        raise RuntimeError("telegram caído")
    monkeypatch.setattr(notify, "telegram", peta)
    _clave(conn)
    assert cr.deliver(conn, "X_API_KEY", requested_by="mcp:x", purpose="p")["value"] == SECRETO


def test_una_clave_pegada_en_purpose_o_requested_by_se_rechaza_sin_registrarla(conn, avisos):
    _clave(conn)
    for campos in ({"requested_by": "mcp:x", "purpose": "usar " + SECRETO},
                   {"requested_by": "mcp " + SECRETO, "purpose": "p"}):
        e = _err(cr.deliver, conn, "X_API_KEY", **campos)
        assert e.code == "credential.value_in_label" and SECRETO not in str(e)
    assert cr.deliveries(conn) == [] and avisos == []


def test_lanzador_escribe_sus_mensajes_en_utf8():
    # En Git Bash (stderr por tubería) salían en cp1252 y los «» y las tildes se veían rotos.
    r = subprocess.run([sys.executable, str(LANZADOR), "--para", "x"], capture_output=True, timeout=60)
    assert r.returncode == 2
    assert "falta «--»" in r.stderr.decode("utf-8")
