"""#483 (22-oct, pedido por Prima con el run 51): el juez de un run puede leer del DISCO los ficheros que la
evidencia nombra por su ruta. Caso real: la revista del 22-oct salió FAIL porque el job pegó la nota cortada en su
respuesta, y la nota en disco estaba entera.

Las rutas las escribe el agente, así que se validan antes de abrirlas (lección del #600, 20-oct): solo ficheros que
EXISTEN, dentro de las raíces que la casa pone en Ajustes › verifier_read_roots, sin escaparse con «..». Sin raíces,
no se abre nada (apagado). El juez los lee con la regla de solo esos ficheros, y el output del run va con ellos.
"""

import asyncio

import pytest

from faros import db, settings, verifier


@pytest.fixture
def casa(tmp_path):
    c = db.connect(":memory:")
    raiz = tmp_path / "vault" / "revista"
    raiz.mkdir(parents=True)
    nota = raiz / "2026-10-22.md"
    nota.write_text("10 ítems", encoding="utf-8")
    fuera = tmp_path / "secreto.md"
    fuera.write_text("no", encoding="utf-8")
    salida = tmp_path / "run_51" / "output.md"
    salida.parent.mkdir()
    return c, raiz, nota, fuera, salida


def _run(salida, texto):
    salida.write_text(texto, encoding="utf-8")
    return {"id": 51, "output_path": str(salida)}


def test_sin_raices_no_se_abre_nada(casa):
    c, raiz, nota, fuera, salida = casa
    assert verifier._ficheros_del_run(c, _run(salida, f"nota en {nota.as_posix()}")) == []


def test_un_fichero_nombrado_dentro_de_la_raiz_se_abre(casa):
    c, raiz, nota, fuera, salida = casa
    settings.patch(c, "owner", {"verifier_read_roots": str(raiz)})
    r = verifier._ficheros_del_run(c, _run(salida, f"python x.py {nota.as_posix()} --anterior y\nfin"))
    assert r == [str(nota.resolve())]


def test_fuera_de_la_raiz_no_existe_o_con_puntos_no_se_abre(casa):
    c, raiz, nota, fuera, salida = casa
    settings.patch(c, "owner", {"verifier_read_roots": str(raiz)})
    texto = (
        f"{fuera.as_posix()}\n{(raiz / 'no_existe.md').as_posix()}\n{(raiz / '..' / '..' / 'secreto.md').as_posix()}\n"
    )
    assert verifier._ficheros_del_run(c, _run(salida, texto)) == []


def test_la_ruta_pegada_a_la_puntuacion_se_limpia(casa):
    c, raiz, nota, fuera, salida = casa
    settings.patch(c, "owner", {"verifier_read_roots": str(raiz)})
    r = verifier._ficheros_del_run(c, _run(salida, f"(registrada `{nota.as_posix()}`)."))
    assert r == [str(nota.resolve())]


def test_sweep_runs_juzga_con_la_nota_y_el_output(casa, monkeypatch):
    c, raiz, nota, fuera, salida = casa
    settings.patch(c, "owner", {"verifier_read_roots": str(raiz)})
    run = _run(salida, f"nota: {nota.as_posix()}")
    from faros import jobs

    monkeypatch.setattr(jobs, "runs_pending_verdict", lambda conn: [dict(run, job_id=13)])
    monkeypatch.setattr(
        jobs,
        "get_job",
        lambda conn, jid: {"id": 13, "name": "revista", "cwd": None, "prompt": "p", "verify_criteria": "c"},
    )
    monkeypatch.setattr(jobs, "set_verdict", lambda conn, rid, v, reason, provenance=None: {"verdict": v})
    visto = {}

    async def juez(prompt, model=None, env=None, read_dirs=None, read_files=None):
        visto.update(read_dirs=read_dirs, read_files=read_files)
        return {"verdict": "pass", "reason": "ok"}

    monkeypatch.setattr(verifier, "_judge", juez)
    asyncio.run(verifier.sweep_runs(c))
    assert str(nota.resolve()) in visto["read_files"] and str(salida) in visto["read_files"]
    assert visto["read_dirs"]
