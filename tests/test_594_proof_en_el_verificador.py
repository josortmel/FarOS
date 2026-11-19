"""#594: la evidencia «proof» se valida al completar y el verificador corre sus comprobaciones
ANTES del juez: si el código ya ve rojo, el juez no se llama (fail determinista)."""

import asyncio
import json
import subprocess

import pytest

from faros import db, service as s, settings, verifier
from faros.service import TicketError


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True)


@pytest.fixture
def casa(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "master")
    (r / "ok.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    _git(r, "checkout", "-q", "-b", "rama")
    (r / "nuevo.py").write_text("x = 1\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "sin fusionar")
    _git(r, "checkout", "-q", "master")
    c = db.connect(":memory:")
    settings.patch(c, s.HOUSE_OWNER, {"proof_repos": str(r)})
    yield c, r
    c.close()


def _sha(r, ref):
    return subprocess.run(["git", "-C", str(r), "rev-parse", "--short", ref],
                          capture_output=True, text=True, check=True).stdout.strip()


def _tarea_hecha(c, r, ref, prueba_cmd="python ok.py"):
    t = s.propose_ticket(c, "alice", "con prueba", owner="alice", auto_accept=True,
                         verification_level="auto")["ticket"]
    proof = json.dumps({"repo": str(r), "commit": _sha(r, ref), "prueba": prueba_cmd, "texto": "hecho"})
    s.complete_ticket(c, "alice", t["id"], "proof", proof)
    return t["id"]


def test_una_prueba_mal_declarada_no_se_completa(casa):
    c, r = casa
    t = s.propose_ticket(c, "alice", "x", owner="alice", auto_accept=True, verification_level="auto")["ticket"]
    with pytest.raises(TicketError) as e:
        s.complete_ticket(c, "alice", t["id"], "proof", '{"repo": "C:/no-admitido", "commit": "abc"}')
    assert e.value.code == "complete.proof_invalid"


def test_rojo_del_codigo_es_fail_sin_llamar_al_juez(casa, monkeypatch):
    c, r = casa
    tid = _tarea_hecha(c, r, "rama")                      # el commit NO está en master

    async def juez_prohibido(*a, **k):
        raise AssertionError("el juez no debía llamarse")
    monkeypatch.setattr(verifier, "_judge", juez_prohibido)
    res = asyncio.run(verifier.sweep(c))
    assert [b["ticket_id"] for b in res["bounced"]] == [tid] and not res["errors"]
    h = s.ticket_history(c, tid)["history"][-1]
    assert "NO está en master" in h["note"] and h["provenance"] == "deterministic:faros.prueba"


def test_verde_del_codigo_llega_al_juez_con_las_comprobaciones(casa, monkeypatch):
    c, r = casa
    tid = _tarea_hecha(c, r, "master")
    visto = {}

    async def juez(prompt, model=None, env=None, read_dirs=None, read_files=None):
        visto["prompt"], visto["dirs"] = prompt, read_dirs
        return {"verdict": "pass", "reason": "visto en ok.py:2"}
    monkeypatch.setattr(verifier, "_judge", juez)
    res = asyncio.run(verifier.sweep(c))
    assert [v["ticket_id"] for v in res["verified"]] == [tid]
    assert "Comprobaciones del código" in visto["prompt"] and "OK · prueba en el commit" in visto["prompt"]
    assert visto["dirs"] == [str(r)]


def test_693_declarar_la_propia_rama_no_llega_a_pass(casa, monkeypatch):
    # #693: con «en»: «rama» el código daba OK (el commit está en esa rama) y un juez satisfecho dejaba pasar una
    # tarea sin fusionar (los casos del 16-oct). Ahora el pass baja a incierto y la tarea queda para una persona.
    c, r = casa
    t = s.propose_ticket(c, "alice", "en mi rama", owner="alice", auto_accept=True,
                         verification_level="auto")["ticket"]
    proof = json.dumps({"repo": str(r), "commit": _sha(r, "rama"), "en": "rama", "texto": "hecho"})
    s.complete_ticket(c, "alice", t["id"], "proof", proof)

    async def juez(prompt, model=None, env=None, read_dirs=None, read_files=None):
        return {"verdict": "pass", "reason": "todo bien"}
    monkeypatch.setattr(verifier, "_judge", juez)
    monkeypatch.setattr(verifier, "_external_key", lambda conn: "")
    res = asyncio.run(verifier.sweep(c))
    assert [u["ticket_id"] for u in res["uncertain"]] == [t["id"]] and not res["verified"]
    assert "no fusionado" in res["uncertain"][0]["reason"]
