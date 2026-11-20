"""#1281: un run que corta por timeout guarda lo que el proceso llevaba escrito, marcado como parcial. Antes,
communicate() juntaba toda la salida y el timeout la tiraba: el run quedaba «sin output» y no había forma de
saber dónde se había quedado (la semanal del 13-nov). El camino normal no cambia: el prompt llega por stdin y la
salida sale entera."""

import asyncio
import sys
from pathlib import Path

from faros import db, jobs, runner

COLGADO = (
    "import sys, time\n"
    "print('paso 1: abro la nota', flush=True)\n"
    "print('paso 2: compruebo 60 URL', flush=True)\n"
    "time.sleep(60)\n"
)
ECO = "import sys\nprint('he leido: ' + sys.stdin.read().strip(), flush=True)\n"


def _correr(tmp_path, monkeypatch, script, timeout_s, stdin=b""):
    conn = db.connect(tmp_path / "r.db")
    monkeypatch.setattr(runner, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(
        runner, "build_invocation", lambda adapter, job, run_id: ([sys.executable, "-c", script], stdin)
    )
    job = jobs.create_job(
        conn, "owner", name="revista", harness="claude-cli", prompt="hola", model="haiku", timeout_s=max(timeout_s, 30)
    )
    # create_job no admite menos de 30 s; en la base del test se baja a mano para no esperar medio minuto
    conn.execute("UPDATE agent_jobs SET timeout_s = ? WHERE id = ?", (timeout_s, job["id"]))
    conn.commit()
    run = asyncio.run(runner.execute_job(conn, job["id"]))
    return run, Path(run["output_path"]).read_text(encoding="utf-8")


def test_el_timeout_guarda_lo_escrito_y_lo_marca_como_parcial(tmp_path, monkeypatch):
    run, salida = _correr(tmp_path, monkeypatch, COLGADO, timeout_s=2)
    assert run["status"] == "timeout"
    assert salida.startswith("(PARCIAL: el run cortó por timeout a los 2 s")  # antes: «(sin output)»
    assert "paso 1: abro la nota" in salida and "paso 2: compruebo 60 URL" in salida
    assert run["result_summary"].startswith("(PARCIAL")


def test_sin_timeout_el_prompt_llega_y_la_salida_sale_entera(tmp_path, monkeypatch):
    run, salida = _correr(tmp_path, monkeypatch, ECO, timeout_s=30, stdin=b"el prompt del job\n")
    assert run["status"] == "ok"
    assert salida.strip() == "he leido: el prompt del job"
