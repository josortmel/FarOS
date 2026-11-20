"""#451 — el verificador puede decir «incierto» (decisión 22 de Pepe, 7-oct-2026).

El juez corre sin herramientas y su esquema solo admitía pass/fail: cuando un punto del
criterio no se podía comprobar con la evidencia (abrir una URL, mirar un fichero que no
viene), le obligábamos a elegir, y elegía pass (run 28 de la revista, 6-oct). Ahora hay un
tercer veredicto, «uncertain»: no completa nada, no se vuelve a juzgar y queda para una
persona (o, desde el #465, para DeepSeek). Un juez falso en todos los tests de aquí.
"""

import asyncio
import json
import sqlite3

import pytest

from faros import db, jobs, runner, verifier
from faros import service as s


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _fake_judge(verdicts):
    async def judge(prompt, model=None, read_dirs=None, read_files=None):
        return verdicts.pop(0)

    return judge


def _run_ok(conn, job_id, summary="salida"):
    r = jobs.create_run(conn, job_id)
    jobs.finish_run(conn, r["id"], "ok", result_summary=summary)
    return r["id"]


def _history_notes(conn, ticket_id):
    return [
        r["note"] or ""
        for r in conn.execute("SELECT note FROM ticket_history WHERE ticket_id=? ORDER BY id", (ticket_id,))
    ]


# ------------------------------------------------------------------ el juez


def test_el_esquema_del_juez_admite_incierto():
    assert json.loads(verifier.VERDICT_SCHEMA)["properties"]["verdict"]["enum"] == ["pass", "fail", "uncertain"]


def test_judge_acepta_un_incierto_del_cli(monkeypatch):
    out = json.dumps(
        {
            "type": "result",
            "is_error": False,
            "structured_output": {"verdict": "uncertain", "reason": "las URL no se pueden abrir sin herramientas"},
        }
    )

    class Proc:
        async def communicate(self, data):
            return out.encode("utf-8"), b""

    async def fake_exec(*cmd, **kw):
        return Proc()

    monkeypatch.setattr(runner, "get_adapter", lambda name: type("A", (), {"exe": "claude"})())
    monkeypatch.setattr(verifier.asyncio, "create_subprocess_exec", fake_exec)
    v = asyncio.run(verifier._judge("prompt"))
    assert v["verdict"] == "uncertain"


def test_el_prompt_ya_no_manda_pass_cuando_no_se_puede_comprobar():
    texto = verifier._load_template()
    assert '"uncertain"' in texto
    assert 'di "pass" si\nla forma es correcta' not in texto  # la frase que causó el run 28


# ------------------------------------------------------------------ runs (brazo automático)


def test_run_incierto_no_completa_el_ticket_ni_se_vuelve_a_juzgar(conn, monkeypatch):
    t = s.propose_ticket(conn, "owner", "Revista diaria", auto_accept=True, verification_level="peer")["ticket"]
    j = jobs.create_job(
        conn,
        "owner",
        "Revista",
        "escribe la revista",
        ticket_id=t["id"],
        verify_criteria="cada URL abre el artículo correcto, no la portada",
    )
    rid = _run_ok(conn, j["id"], "1. Titular — https://example.com/a")
    monkeypatch.setattr(verifier, "_judge", _fake_judge([{"verdict": "uncertain", "reason": "no puedo abrir las URL"}]))
    res = asyncio.run(verifier.sweep_runs(conn))
    assert [u["run_id"] for u in res["uncertain"]] == [rid]
    assert res["passed"] == [] and res["failed"] == []
    run = conn.execute("SELECT verdict, verdict_reason FROM runs WHERE id=?", (rid,)).fetchone()
    assert run["verdict"] == "uncertain" and "no puedo abrir" in run["verdict_reason"]
    fresh = s._as_dict(conn, s._get(conn, t["id"]))
    assert fresh["status"] in {"accepted", "in_progress"} and fresh["evidence"] is None
    assert any("incierto" in n and "no puedo abrir" in n for n in _history_notes(conn, t["id"]))
    assert asyncio.run(verifier.sweep_runs(conn))["swept"] == 0  # no se re-juzga solo


def test_una_persona_resuelve_el_incierto_y_entonces_si_completa(conn, monkeypatch):
    t = s.propose_ticket(conn, "owner", "Revista diaria", auto_accept=True, verification_level="peer")["ticket"]
    j = jobs.create_job(conn, "owner", "Revista", "escribe", ticket_id=t["id"])
    rid = _run_ok(conn, j["id"])
    monkeypatch.setattr(verifier, "_judge", _fake_judge([{"verdict": "uncertain", "reason": "x"}]))
    asyncio.run(verifier.sweep_runs(conn))
    assert conn.execute("SELECT verdict FROM runs WHERE id=?", (rid,)).fetchone()[0] == "uncertain"
    r = jobs.set_verdict(conn, rid, "pass", "abiertas a mano las 8 URL: correctas", by="alice")
    assert r["verdict"] == "pass"
    assert s._as_dict(conn, s._get(conn, t["id"]))["status"] == "done"
    with pytest.raises(s.TicketError):  # un pass ya no se cambia
        jobs.set_verdict(conn, rid, "fail", "otra opinión", by="bob")


def test_un_incierto_no_avisa_por_telegram(conn, monkeypatch):
    from faros import notify

    llamadas = []
    monkeypatch.setattr(notify, "on_verdict_fail", lambda *a, **k: llamadas.append(a))
    j = jobs.create_job(conn, "owner", "Job", "x")
    rid = _run_ok(conn, j["id"])
    jobs.set_verdict(conn, rid, "uncertain", "no comprobable")
    assert llamadas == []  # decisión de Pepe (8-oct): Telegram solo con fallo


# ------------------------------------------------------------------ tickets de nivel auto


def test_ticket_auto_incierto_sigue_hecho_y_pasa_a_verificacion_de_par(conn, monkeypatch):
    t = s.propose_ticket(conn, "owner", "Resumen", auto_accept=True, verification_level="auto")["ticket"]
    s.complete_ticket(conn, "owner", t["id"], "command_output", "resumen con 3 correos")
    monkeypatch.setattr(
        verifier, "_judge", _fake_judge([{"verdict": "uncertain", "reason": "fidelidad al correo no comprobable"}])
    )
    res = asyncio.run(verifier.sweep(conn))
    assert [u["ticket_id"] for u in res["uncertain"]] == [t["id"]]
    fresh = s._as_dict(conn, s._get(conn, t["id"]))
    assert fresh["status"] == "done" and fresh["verification_level"] == "peer"
    assert any("incierto" in n and "fidelidad" in n for n in _history_notes(conn, t["id"]))
    assert asyncio.run(verifier.sweep(conn))["swept"] == 0  # sale de la cola del bot
    with pytest.raises(s.TicketError):  # y un bot ya no lo sella
        s.verify_ticket(conn, "verifier-bot", t["id"], "pass")
    assert s.verify_ticket(conn, "alice", t["id"], "pass")["status"] == "verified"


# ------------------------------------------------------------------ esquema 7, sobre COPIA


def test_v7_reconstruye_runs_con_el_check_viejo_sin_perder_filas(tmp_path):
    p = tmp_path / "copia.db"
    db.connect(p).close()
    viejo = db.RUNS_V3.replace("'pass','fail','uncertain'", "'pass','fail'")
    assert viejo != db.RUNS_V3
    raw = sqlite3.connect(str(p))
    raw.executescript("DROP TABLE runs;" + viejo)
    raw.execute("INSERT INTO agent_jobs (id, name, prompt, created_at) VALUES (1, 'j', 'p', '2026-10-08')")
    raw.execute(
        "INSERT INTO runs (id, job_id, started_at, status, verdict, verdict_reason)"
        " VALUES (5, 1, '2026-10-08T10:00', 'ok', 'pass', 'bien')"
    )
    raw.execute("DELETE FROM schema_version WHERE version >= 7")
    raw.commit()
    with pytest.raises(sqlite3.IntegrityError):  # la base vieja no lo admite
        raw.execute("UPDATE runs SET verdict='uncertain' WHERE id=5")
    raw.close()
    c = db.connect(p)
    assert db.schema_version(c) == db.SCHEMA_VERSION  # #377: v8 encima
    row = c.execute("SELECT verdict, verdict_reason FROM runs WHERE id=5").fetchone()
    assert (row["verdict"], row["verdict_reason"]) == ("pass", "bien")
    c.execute("UPDATE runs SET verdict='uncertain' WHERE id=5")  # ahora sí
    assert list((tmp_path / "backups").glob("faros_pre_migration_*.db"))  # copia antes
    c.close()
    c = db.connect(p)  # idempotente
    assert c.execute("SELECT COUNT(*) FROM schema_version WHERE version = ?", (db.SCHEMA_VERSION,)).fetchone()[0] == 1
    c.close()
