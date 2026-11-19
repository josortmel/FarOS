"""#465 — nivel de verificación «externa» y escalado del incierto a DeepSeek (decisión 22 de Pepe, 7-oct).

Haiku es el verificador habitual; DeepSeek, el último recurso:
- una tarea marcada «external» al crearla la juzga DeepSeek, por MAYORÍA de 3 corridas (un veredicto suelto
  de DeepSeek no es fiable: el mismo caso dio pasa y falla en corridas seguidas, tanda 6 del #378);
- un «incierto» de Haiku sube a DeepSeek (×3) en vez de pararse en una persona;
- sin mayoría, o sin clave de DeepSeek, decide una persona, como hasta ahora.
Las tres corridas quedan en el historial y la verificación lleva su procedencia (#377): external:deepseek-flash.
"""

import asyncio

import pytest

from faros import db, jobs, service as s, settings, verifier


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


_GET = settings.get


def _ajustes(monkeypatch, **valores):
    """settings.get con estos valores delante; lo demás, como siempre (y sin la clave del entorno)."""
    monkeypatch.setattr(settings, "get",
                        lambda c, k: valores[k] if k in valores else ("" if k == "deepseek_api_key" else _GET(c, k)))


@pytest.fixture
def con_clave(conn, monkeypatch):
    _ajustes(monkeypatch, deepseek_api_key="sk-prueba-465", verifier_enabled=True)
    return conn


def _juez(veredictos, llamadas):
    async def judge(prompt, model=None, env=None, read_dirs=None, read_files=None):
        llamadas.append(("haiku", model))
        return veredictos.pop(0)
    return judge


def _externo(veredictos, llamadas):
    async def judge_externo(conn, prompt, read_dirs=None, read_files=None):
        llamadas.append(("deepseek", None))
        return veredictos.pop(0)
    return judge_externo


def _v(verdict, reason="motivo"):
    return {"verdict": verdict, "reason": reason}


def _hecho(conn, level):
    t = s.propose_ticket(conn, "owner", "Informe", owner="alice", auto_accept=True,
                         verification_level=level, verify_criteria="cifras contra la fuente")["ticket"]
    s.complete_ticket(conn, "alice", t["id"], "command_output", "3 cifras citadas")
    return t["id"]


def _ultima(conn, tid):
    return s.ticket_history(conn, tid)["history"][-1]


def _parchear(monkeypatch, haiku, deepseek):
    llamadas = []
    monkeypatch.setattr(verifier, "_judge", _juez(list(haiku), llamadas))
    monkeypatch.setattr(verifier, "_judge_externo", _externo(list(deepseek), llamadas))
    return llamadas


# ------------------------------------------------------------------ el nivel

def test_external_es_un_nivel_valido(conn):
    assert "external" in s.VERIFICATION_LEVELS
    assert s._get(conn, _hecho(conn, "external"))["status"] == "done"


def test_un_claude_no_sella_una_externa_y_pepe_si(conn):
    tid = _hecho(conn, "external")
    with pytest.raises(s.TicketError) as e:
        s.verify_ticket(conn, "bob", tid, "pass")
    assert e.value.code == "verify.external_only"
    assert s.verify_ticket(conn, s.HOUSE_OWNER, tid, "pass")["status"] == "verified"
    assert _ultima(conn, tid)["provenance"] == "external"


# ------------------------------------------------------------------ nivel external: DeepSeek ×3

def test_externa_mayoria_pasa(con_clave, monkeypatch):
    tid = _hecho(con_clave, "external")
    llamadas = _parchear(monkeypatch, [], [_v("pass", "a"), _v("fail", "b"), _v("pass", "c")])
    res = asyncio.run(verifier.sweep(con_clave))
    assert [v["ticket_id"] for v in res["verified"]] == [tid]
    assert llamadas == [("deepseek", None)] * 3                       # Haiku no la toca
    fila = _ultima(con_clave, tid)
    assert fila["provenance"] == "external:deepseek-flash"
    assert "pass, fail, pass" in fila["note"] and "a" in fila["note"] and "c" in fila["note"]


def test_externa_mayoria_falla_devuelve_la_tarea(con_clave, monkeypatch):
    tid = _hecho(con_clave, "external")
    _parchear(monkeypatch, [], [_v("fail", "x"), _v("fail", "y"), _v("pass", "z")])
    res = asyncio.run(verifier.sweep(con_clave))
    assert [b["ticket_id"] for b in res["bounced"]] == [tid]
    fila = _ultima(con_clave, tid)
    assert (fila["new_status"], fila["provenance"]) == ("in_progress", "external:deepseek-flash")


def test_externa_sin_mayoria_la_decide_una_persona(con_clave, monkeypatch):
    tid = _hecho(con_clave, "external")
    _parchear(monkeypatch, [], [_v("pass"), _v("fail"), _v("uncertain")])
    res = asyncio.run(verifier.sweep(con_clave))
    assert [u["ticket_id"] for u in res["uncertain"]] == [tid]
    t = s._get(con_clave, tid)
    assert t["status"] == "done" and t["verification_level"] == "external"
    assert "sin mayoría" in _ultima(con_clave, tid)["note"]
    assert asyncio.run(verifier.sweep(con_clave))["swept"] == 0       # no se rejuzga en bucle


def test_tres_inciertos_dicen_incierto_y_no_sin_mayoria(con_clave, monkeypatch):
    tid = _hecho(con_clave, "external")
    _parchear(monkeypatch, [], [_v("uncertain")] * 3)
    asyncio.run(verifier.sweep(con_clave))
    nota = _ultima(con_clave, tid)["note"]
    assert "uncertain, uncertain, uncertain → incierto" in nota and s._get(con_clave, tid)["status"] == "done"


def test_sin_saldo_es_como_sin_clave(con_clave, monkeypatch):
    monkeypatch.setattr(verifier, "_hay_saldo", lambda key: False)
    ext, auto = _hecho(con_clave, "external"), _hecho(con_clave, "auto")
    llamadas = _parchear(monkeypatch, [_v("uncertain")], [])
    res = asyncio.run(verifier.sweep(con_clave))
    assert [c[0] for c in llamadas] == ["haiku"]                      # DeepSeek no se llama
    assert s._get(con_clave, ext)["status"] == "done" and "saldo" in res["errors"][0]["error"]
    assert s._get(con_clave, auto)["verification_level"] == "peer"    # el incierto, a una persona


def test_el_saldo_se_mira_una_vez_por_barrido(con_clave, monkeypatch):
    vistas = []
    monkeypatch.setattr(verifier, "_hay_saldo", lambda key: vistas.append(key) or True)
    for _ in range(2):
        _hecho(con_clave, "external")
    _parchear(monkeypatch, [], [_v("pass")] * 6)
    asyncio.run(verifier.sweep(con_clave))
    assert vistas == ["sk-prueba-465"]


def test_externa_sin_clave_no_se_juzga_y_lo_dice(conn, monkeypatch):
    _ajustes(monkeypatch)
    tid = _hecho(conn, "external")
    llamadas = _parchear(monkeypatch, [], [])
    res = asyncio.run(verifier.sweep(conn))
    assert llamadas == [] and s._get(conn, tid)["status"] == "done"
    assert res["errors"] and "clave" in res["errors"][0]["error"]


# ------------------------------------------------------------------ nivel auto: Haiku, y el incierto sube

def test_auto_pasa_con_haiku_y_no_llama_a_deepseek(con_clave, monkeypatch):
    tid = _hecho(con_clave, "auto")
    llamadas = _parchear(monkeypatch, [_v("pass")], [])
    asyncio.run(verifier.sweep(con_clave))
    assert [c[0] for c in llamadas] == ["haiku"]
    assert _ultima(con_clave, tid)["provenance"] == "same-family"


def test_auto_incierto_sube_a_deepseek(con_clave, monkeypatch):
    tid = _hecho(con_clave, "auto")
    llamadas = _parchear(monkeypatch, [_v("uncertain", "no puedo abrir la fuente")],
                         [_v("pass"), _v("pass"), _v("fail")])
    res = asyncio.run(verifier.sweep(con_clave))
    assert [c[0] for c in llamadas] == ["haiku", "deepseek", "deepseek", "deepseek"]
    assert [v["ticket_id"] for v in res["verified"]] == [tid]
    fila = _ultima(con_clave, tid)
    assert fila["provenance"] == "external:deepseek-flash"
    assert "no puedo abrir la fuente" in fila["note"]                 # el motivo de Haiku se conserva


def test_auto_incierto_sin_mayoria_externa_pasa_a_persona(con_clave, monkeypatch):
    tid = _hecho(con_clave, "auto")
    _parchear(monkeypatch, [_v("uncertain")], [_v("pass"), _v("uncertain"), _v("fail")])
    res = asyncio.run(verifier.sweep(con_clave))
    assert [u["ticket_id"] for u in res["uncertain"]] == [tid]
    assert s._get(con_clave, tid)["verification_level"] == "peer"


def test_auto_incierto_sin_clave_hace_lo_de_antes(conn, monkeypatch):
    _ajustes(monkeypatch)
    tid = _hecho(conn, "auto")
    llamadas = _parchear(monkeypatch, [_v("uncertain")], [])
    asyncio.run(verifier.sweep(conn))
    assert [c[0] for c in llamadas] == ["haiku"]
    assert s._get(conn, tid)["verification_level"] == "peer"


# ------------------------------------------------------------------ veredicto por run

def test_run_incierto_sube_a_deepseek_y_completa_su_tarea(con_clave, monkeypatch):
    t = s.propose_ticket(con_clave, "owner", "Job con tarea", auto_accept=True,
                         verification_level="auto")["ticket"]
    j = jobs.create_job(con_clave, "owner", "j", "haz x", ticket_id=t["id"], verify_level="auto")
    r = jobs.create_run(con_clave, j["id"])
    jobs.finish_run(con_clave, r["id"], "ok", result_summary="hecho x")
    _parchear(monkeypatch, [_v("uncertain")], [_v("pass"), _v("pass"), _v("pass")])
    res = asyncio.run(verifier.sweep_runs(con_clave))
    assert [p["run_id"] for p in res["passed"]] == [r["id"]]
    assert s._get(con_clave, t["id"])["status"] == "verified"
    assert _ultima(con_clave, t["id"])["provenance"] == "external:deepseek-flash"


# ------------------------------------------------------------------ el juez externo de verdad (sin red)

def test_juez_externo_va_a_deepseek_con_entorno_limpio_y_sin_la_clave_en_el_comando(con_clave, monkeypatch):
    visto = {}

    class _P:
        returncode = 0

        async def communicate(self, data):
            return (b'{"type":"result","structured_output":{"verdict":"pass","reason":"ok"}}', b"")

    async def fake_exec(*cmd, **kw):
        visto["cmd"], visto["env"] = list(cmd), kw.get("env")
        return _P()

    monkeypatch.setenv("GITHUB_TOKEN", "no-debe-pasar")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    v = asyncio.run(verifier._judge_externo(con_clave, "prompt"))
    assert v["verdict"] == "pass"
    assert visto["cmd"][visto["cmd"].index("--model") + 1] == "deepseek-flash"
    assert "sk-prueba-465" not in " ".join(visto["cmd"])
    # #1109: el juez recibe el reenvío del daemon y un token de un solo uso, nunca la clave.
    from faros import deepseek_proxy
    assert visto["env"]["ANTHROPIC_AUTH_TOKEN"].startswith(deepseek_proxy.PREFIJO_TOKEN)
    assert not deepseek_proxy.vigente(visto["env"]["ANTHROPIC_AUTH_TOKEN"])   # caducó al acabar
    assert not any("sk-prueba-465" in v for v in visto["env"].values())
    assert visto["env"]["ANTHROPIC_BASE_URL"] == deepseek_proxy.url_local()
    assert "GITHUB_TOKEN" not in visto["env"] and "ANTHROPIC_API_KEY" not in visto["env"]
