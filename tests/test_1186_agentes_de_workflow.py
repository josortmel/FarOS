"""#1186: agentes de workflow declarados en roster.json.

Una casa puede tener un agente que verifica (o hace otra tarea de workflow) sin ser de la
familia: no es dueño de tareas ni crea proyectos, pero su veredicto tiene que constar con
su nombre. Hasta ahora los únicos nombres de workflow eran los cuatro fijos del código
(WORKFLOW_AGENTS) y cualquier otro daba «agent.unknown».

roster.json admite una lista opcional «workflow_agents». Vale solo cuando el roster sale
del fichero (con el roster por env, la suite no depende de lo que haya en la máquina), y
escribir el roster desde la app no la borra.
"""

import json

import pytest

from faros import db, roster, roster_admin as ra, service as s
from faros.service import TicketError


@pytest.fixture
def home(tmp_path, monkeypatch):
    for k in ("FAROS_OWNER", "FAROS_AGENTS", "AGENTICOS_OWNER", "AGENTICOS_AGENTS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FAROS_HOME", str(tmp_path))

    def escribir(**extra):
        data = {"owner": "owner", "agents": ["owner", "alice", "bob"], **extra}
        (tmp_path / "roster.json").write_text(json.dumps(data), encoding="utf-8")
        roster.reload()

    escribir(workflow_agents=["auditor"])
    yield escribir
    monkeypatch.undo()
    roster.reload()


@pytest.fixture
def conn(home):
    c = db.connect(":memory:")
    yield c
    c.close()


def _hecha(conn, owner="alice", nivel="peer"):
    t = s.propose_ticket(conn, owner, "Arreglar el scheduler", owner=owner, auto_accept=True,
                         verification_level=nivel)["ticket"]
    s.start_ticket(conn, owner, t["id"])
    s.complete_ticket(conn, owner, t["id"], "command_output", "pytest: 12 passed")
    return t["id"]


def test_un_agente_de_workflow_del_fichero_es_valido(conn):
    assert s._validate_agent("auditor") == "auditor"


def test_no_es_de_la_familia(conn):
    assert "auditor" not in roster.AGENTS
    with pytest.raises(TicketError) as e:
        s._validate_agent("auditor", family_only=True)
    assert e.value.code == "agent.unknown"


def test_su_veredicto_consta_con_su_nombre(conn):
    tid = _hecha(conn)
    r = s.verify_ticket(conn, "auditor", tid, "pass", note="12 passed, lo he corrido")
    assert r["status"] == "verified" and r["verified_by"] == "auditor"
    h = s.ticket_history(conn, tid)["history"]
    assert h[-1]["changed_by"] == "auditor" and h[-1]["new_status"] == "verified"


def test_tambien_puede_suspender(conn):
    tid = _hecha(conn)
    r = s.verify_ticket(conn, "auditor", tid, "fail", note="el criterio pide 13 y hay 12")
    assert r["status"] != "verified"


def test_vale_en_una_tarea_de_nivel_auto(conn):
    tid = _hecha(conn, nivel="auto")
    assert s.verify_ticket(conn, "auditor", tid, "pass")["verified_by"] == "auditor"


def test_sin_la_lista_sigue_siendo_desconocido(home, conn):
    home()  # roster.json sin workflow_agents
    with pytest.raises(TicketError) as e:
        s._validate_agent("auditor")
    assert e.value.code == "agent.unknown"


def test_con_el_roster_por_env_no_cuenta_el_fichero(home, conn, monkeypatch):
    monkeypatch.setenv("FAROS_OWNER", "owner")
    monkeypatch.setenv("FAROS_AGENTS", "owner,alice")
    roster.reload()
    with pytest.raises(TicketError):
        s._validate_agent("auditor")


def test_lista_mal_formada_no_rompe_el_roster(home, conn):
    home(workflow_agents="auditor")  # una cadena, no una lista
    assert roster.SOURCE == "file" and "alice" in roster.AGENTS
    with pytest.raises(TicketError):
        s._validate_agent("auditor")


def test_un_nombre_de_la_familia_no_se_duplica_como_workflow(home, conn):
    home(workflow_agents=["auditor", "alice"])
    assert "alice" in roster.AGENTS and "alice" not in roster.WORKFLOW_EXTRA


def test_escribir_el_roster_desde_la_app_no_borra_la_lista(home, conn, tmp_path):
    ra.create(conn, "carol")
    data = json.loads((tmp_path / "roster.json").read_text(encoding="utf-8"))
    assert data["workflow_agents"] == ["auditor"]
    assert s._validate_agent("auditor") == "auditor"


def test_no_se_puede_crear_un_miembro_con_el_nombre_de_un_agente_de_workflow(conn):
    with pytest.raises(ra.RosterError):
        ra.create(conn, "Auditor")
