"""#1361: cancelar un run EN CURSO lo deja «cancelled», no «error». Lo cazó el lint (ruff F841): execute_job
calculaba `cancelled` y no lo usaba, y cancel_run solo mataba el árbol, así que el run acababa como «error» y el
aviso de Telegram decía «terminó error». El aviso por un run cancelado ya existía y se queda como está."""

import asyncio
import sys

from faros import db, jobs, notify, runner

COLGADO = "import time\nprint('empiezo', flush=True)\ntime.sleep(60)\n"


def test_cancelar_un_run_en_curso_lo_deja_cancelado(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "r.db")
    monkeypatch.setattr(runner, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(runner, "build_invocation", lambda adapter, job, run_id: ([sys.executable, "-c", COLGADO], b""))
    avisos = []
    monkeypatch.setattr(notify, "telegram", lambda conn, texto: avisos.append(texto) or True)
    job = jobs.create_job(conn, "owner", name="largo", harness="claude-cli", prompt="hola", model="haiku")

    async def lanzar_y_cancelar():
        tarea = asyncio.create_task(runner.execute_job(conn, job["id"]))
        for _ in range(200):  # hasta 20 s a que el proceso esté vivo
            if runner.LIVE_RUNS:
                break
            await asyncio.sleep(0.1)
        (run_id,) = runner.LIVE_RUNS.keys()
        await runner.cancel_run(conn, run_id)
        return await asyncio.wait_for(tarea, timeout=30)

    run = asyncio.run(lanzar_y_cancelar())
    assert run["status"] == "cancelled"
    assert len(avisos) == 1 and "terminó cancelled" in avisos[0]
