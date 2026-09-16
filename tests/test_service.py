"""Tests del motor de AgenticOS (SPEC §3-4, §7). Incluye los 18 de casa-tickets v0."""

import pytest

from faros import db, service as s
from faros.service import TicketError


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _propose(conn, **kw):
    args = dict(agent="alice", title="Arreglar el widget", description="el widget está roto")
    args.update(kw)
    return s.propose_ticket(conn, **args)["ticket"]


def _propose_agentic(conn, **kw):
    job = kw.pop("job", None) or {"prompt": "di hola", "model": "haiku"}
    args = dict(agent="alice", title="Resumen de correo", description="resumen matinal",
                kind="agentic", verification_level="auto", job=job)
    args.update(kw)
    return s.propose_ticket(conn, **args)["ticket"]


# ------------------------------------------------------------ ciclo completo (v0)

def test_full_lifecycle_peer(conn):
    t = _propose(conn, owner="alice", verification_level="peer")
    assert t["status"] == "proposed"
    s.accept_ticket(conn, "owner", t["id"])
    s.start_ticket(conn, "alice", t["id"])
    done = s.complete_ticket(conn, "alice", t["id"], "file_path", "C:/x/widget.py")
    assert done["status"] == "done"
    v = s.verify_ticket(conn, "bob", t["id"], "pass", "comprobado en vivo")
    assert v["status"] == "verified" and v["verified_by"] == "bob" and v["closed_at"]
    hist = s.ticket_history(conn, t["id"])["history"]
    assert [h["new_status"] for h in hist] == [
        "proposed", "accepted", "in_progress", "done", "verified"]


def test_self_level_completes_directly_to_verified(conn):
    t = _propose(conn, owner="alice")
    s.accept_ticket(conn, "owner", t["id"])
    s.start_ticket(conn, "alice", t["id"])
    done = s.complete_ticket(conn, "alice", t["id"], "command_output", "pytest 12 passed")
    assert done["status"] == "verified" and done["verified_by"] == "alice"


def test_complete_without_evidence_is_impossible(conn):
    t = _propose(conn, owner="alice")
    s.accept_ticket(conn, "owner", t["id"])
    with pytest.raises(TicketError, match="evidencia"):
        s.complete_ticket(conn, "alice", t["id"], "file_path", "   ")
    with pytest.raises(TicketError, match="evidence_type"):
        s.complete_ticket(conn, "alice", t["id"], "vibes", "trust me")


def test_verifier_cannot_be_owner_on_peer_level(conn):
    t = _propose(conn, owner="alice", verification_level="peer")
    s.accept_ticket(conn, "owner", t["id"])
    s.complete_ticket(conn, "alice", t["id"], "memory_id", "abc-123")
    with pytest.raises(TicketError, match="no puede ser el owner"):
        s.verify_ticket(conn, "alice", t["id"], "pass")


def test_verify_fail_reopens_with_mandatory_note(conn):
    t = _propose(conn, owner="alice", verification_level="peer")
    s.accept_ticket(conn, "owner", t["id"])
    s.complete_ticket(conn, "alice", t["id"], "file_path", "x.py")
    with pytest.raises(TicketError, match="note"):
        s.verify_ticket(conn, "bob", t["id"], "fail")
    r = s.verify_ticket(conn, "bob", t["id"], "fail", "el test no cubre el caso N")
    assert r["status"] == "in_progress"


def test_recurrente_never_closes_and_stamps_chronology(conn):
    t = _propose(conn, owner="alice", source="recurrente", cadence_days=7,
                 title="Backup EcoDB", description="dump + grafo + media")
    s.accept_ticket(conn, "owner", t["id"])
    for i in range(3):
        r = s.complete_ticket(conn, "alice", t["id"], "command_output", f"push OK run {i}")
        assert r["status"] == "accepted" and r["closed_at"] is None
        assert r["last_done_at"] is not None
    hist = s.ticket_history(conn, t["id"])["history"]
    runs = [h for h in hist if h["note"] and "recurrente ejecutada" in h["note"]]
    assert len(runs) == 3


def test_recurrente_due_computed_on_read(conn):
    t = _propose(conn, owner="alice", source="recurrente", cadence_days=7)
    assert t["due"] is True and t["due_state"] == "due"
    s.accept_ticket(conn, "owner", t["id"])
    s.complete_ticket(conn, "alice", t["id"], "command_output", "ok")
    fresh = s.list_tickets(conn, owner="alice")[0]
    assert fresh["due"] is False and fresh["overdue"] is False and fresh["age_days"] == 0
    assert fresh["due_state"] == "ok"


def test_virtual_expiry_on_read(conn):
    """T4.5: reads compute expired status in memory without DB writes.
    The scheduler writes the real expiry; reads show it virtually."""
    t = _propose(conn, expires_at="2020-01-01T00:00:00")
    listed = s.list_tickets(conn, include_closed=True)
    exp = [x for x in listed if x["id"] == t["id"]][0]
    assert exp["status"] == "expired" and exp["closed_at"]
    hist_before = s.ticket_history(conn, t["id"])["history"]
    hist_count = len(hist_before)
    s.list_tickets(conn, include_closed=True)
    hist_after = s.ticket_history(conn, t["id"])["history"]
    assert len(hist_after) == hist_count, "read must not write to ticket_history"
    open_listed = s.list_tickets(conn)
    assert not any(x["id"] == t["id"] for x in open_listed)
    s._expire_lazy(conn)
    hist_final = s.ticket_history(conn, t["id"])["history"]
    assert hist_final[-1]["changed_by"] == "system"


def test_expired_ticket_rejects_operations(conn):
    t = _propose(conn, expires_at="2020-01-01T00:00:00")
    with pytest.raises(TicketError, match="ilegal desde 'expired'"):
        s.accept_ticket(conn, "owner", t["id"])


def test_illegal_transitions(conn):
    t = _propose(conn, owner="alice")
    with pytest.raises(TicketError):
        s.start_ticket(conn, "alice", t["id"])
    with pytest.raises(TicketError):
        s.verify_ticket(conn, "bob", t["id"], "pass")
    s.accept_ticket(conn, "owner", t["id"])
    with pytest.raises(TicketError):
        s.accept_ticket(conn, "owner", t["id"])
    with pytest.raises(TicketError, match="reason"):
        s.reject_ticket(conn, "owner", t["id"], "")
    with pytest.raises(TicketError, match="owner"):
        s.start_ticket(conn, "bob", t["id"])


def test_reject_requires_reason_and_closes(conn):
    t = _propose(conn)
    r = s.reject_ticket(conn, "owner", t["id"], "duplicado de #1")
    assert r["status"] == "rejected" and r["closed_at"]


def test_block_and_resume(conn):
    t = _propose(conn, owner="alice")
    s.accept_ticket(conn, "owner", t["id"])
    b = s.block_ticket(conn, "alice", t["id"], "gate: decisión de owner pendiente")
    assert b["status"] == "blocked" and b["blocked_reason"]
    r = s.start_ticket(conn, "alice", t["id"], "gate resuelto")
    assert r["status"] == "in_progress" and r["blocked_reason"] is None


def test_claim_implicito_ownerless(conn):
    t = _propose(conn, owner=None)
    s.accept_ticket(conn, "owner", t["id"])
    r = s.start_ticket(conn, "bob", t["id"])
    assert r["owner"] == "bob"


def test_unknown_agent_rejected(conn):
    with pytest.raises(TicketError, match="agente desconocido"):
        _propose(conn, agent="Rando")


def test_peer_bypass_ownerless(conn):
    t = _propose(conn, owner=None, verification_level="peer")
    s.accept_ticket(conn, "owner", t["id"])
    done = s.complete_ticket(conn, "bob", t["id"], "file_path", "x.py")
    assert done["owner"] == "bob"
    with pytest.raises(TicketError, match="no puede ser el owner"):
        s.verify_ticket(conn, "bob", t["id"], "pass")
    ok = s.verify_ticket(conn, "alice", t["id"], "pass")
    assert ok["status"] == "verified"


def test_recurrente_peer_combo_rejected(conn):
    with pytest.raises(TicketError, match="recurrentes son verification_level=self"):
        _propose(conn, cadence_days=7, verification_level="peer")


def test_dedup_finds_similar_open_ticket(conn):
    _propose(conn, title="Backup EcoDB semanal", description="dump grafo media github")
    res = s.propose_ticket(conn, "bob", "Hacer backup de EcoDB",
                           description="subir dump a github")
    dups = res["possible_duplicates"]
    assert any("Backup" in d["title"] for d in dups)


def test_my_board_sections(conn):
    t1 = _propose(conn, owner="alice", title="Tarea abierta uno")
    s.accept_ticket(conn, "owner", t1["id"])
    t2 = _propose(conn, owner="alice", source="recurrente", cadence_days=7,
                  title="Backup EcoDB")
    t3 = _propose(conn, agent="bob", owner="bob", verification_level="peer",
                  title="Spec de retrieval")
    s.accept_ticket(conn, "owner", t3["id"])
    s.complete_ticket(conn, "bob", t3["id"], "file_path", "spec.md")
    board = s.my_board(conn, "alice")
    assert any(t["id"] == t1["id"] for t in board["mine_open"])
    assert any(t["id"] == t2["id"] for t in board["due_recurrentes"])
    assert any(t["id"] == t3["id"] for t in board["needs_my_verification"])
    prima_board = s.my_board(conn, "bob")
    assert not prima_board["needs_my_verification"]


# ============================================================ nuevos: proyectos

def test_project_crud_and_assignment(conn):
    p = s.create_project(conn, "owner", "La Casa", color="#e8590c")
    assert p["name"] == "La Casa" and p["position"] == 1
    with pytest.raises(TicketError, match="ya existe"):
        s.create_project(conn, "owner", "La Casa")
    t = _propose(conn, project_id=p["id"])
    assert t["project_id"] == p["id"]
    with pytest.raises(TicketError, match="no existe"):
        _propose(conn, project_id=999)
    s.update_project(conn, "owner", p["id"], archived=True)
    assert s.list_projects(conn) == []
    assert len(s.list_projects(conn, include_archived=True)) == 1


# ============================================================ nuevos: agentic

def test_agentic_requires_job_prompt(conn):
    with pytest.raises(TicketError, match="requiere job"):
        _propose(conn, kind="agentic", verification_level="auto", job=None)
    with pytest.raises(TicketError, match="harness inválido"):
        _propose_agentic(conn, job={"prompt": "x", "harness": "gpt-cli"})
    with pytest.raises(TicketError, match="schedule.type"):
        _propose_agentic(conn, job={"prompt": "x", "schedule": {"type": "hourly"}})
    with pytest.raises(TicketError, match="run_at"):
        _propose_agentic(conn, job={"prompt": "x", "schedule": {"type": "once"}})
    with pytest.raises(TicketError, match="every_days"):
        _propose_agentic(conn, job={"prompt": "x", "schedule": {"type": "recurring", "at": "08:00"}})


def test_agentic_ticket_embeds_job_and_last_run(conn):
    t = _propose_agentic(conn)
    assert t["kind"] == "agentic"
    assert t["job"]["prompt"] == "di hola" and t["job"]["schedule"] == {"type": "manual"}
    assert t["last_run"] is None


def test_update_job_reschedule_and_disable(conn):
    t = _propose_agentic(conn)
    jid = t["job"]["id"]
    r = s.update_job(conn, "owner", jid,
                     schedule={"type": "recurring", "every_days": 1, "at": "08:00"},
                     model="sonnet", enabled=False)
    assert r["job"]["schedule"]["at"] == ["08:00"]  # v3: lista de horas
    assert r["job"]["model"] == "sonnet" and r["job"]["enabled"] == 0
    with pytest.raises(TicketError, match="no editables"):
        s.update_job(conn, "owner", jid, ticket_id=99)


# ============================================================ nuevos: runs

def test_run_lifecycle_ok_completes_to_done_queue(conn):
    t = _propose_agentic(conn)  # auto level
    run = s.create_run(conn, t["job"]["id"])
    assert run["status"] == "running"
    fresh = s.list_tickets(conn, kind="agentic")[0]
    assert fresh["status"] == "in_progress" and fresh["owner"] == "runner-bot"
    done = s.finish_run(conn, run["id"], "ok", exit_code=0, cost_usd=0.01,
                        output_path="artifacts/run_1/output.md", result_summary="hola")
    assert done["status"] == "ok"
    fresh = s.list_tickets(conn, kind="agentic")[0]
    assert fresh["status"] == "done"  # cola de verificación, NO verified
    assert fresh["evidence_type"] == "run_output" and fresh["evidence"] == str(run["id"])
    assert fresh["last_run"]["result_summary"] == "hola"


def test_run_anti_overlap(conn):
    t = _propose_agentic(conn)
    s.create_run(conn, t["job"]["id"])
    with pytest.raises(TicketError, match="ya tiene un run en curso"):
        s.create_run(conn, t["job"]["id"])


def test_run_error_blocks_ticket(conn):
    t = _propose_agentic(conn)
    run = s.create_run(conn, t["job"]["id"])
    s.finish_run(conn, run["id"], "error", exit_code=1)
    fresh = s.list_tickets(conn, kind="agentic")[0]
    assert fresh["status"] == "blocked" and "run #" in fresh["blocked_reason"]


def test_run_on_disabled_or_closed_rejected(conn):
    t = _propose_agentic(conn)
    s.update_job(conn, "owner", t["job"]["id"], enabled=False)
    with pytest.raises(TicketError, match="deshabilitado"):
        s.create_run(conn, t["job"]["id"])
    s.update_job(conn, "owner", t["job"]["id"], enabled=True)
    s.reject_ticket(conn, "owner", t["id"], "no procede")
    with pytest.raises(TicketError, match="cerrado"):
        s.create_run(conn, t["job"]["id"])


def test_finish_run_twice_rejected(conn):
    t = _propose_agentic(conn)
    run = s.create_run(conn, t["job"]["id"])
    s.finish_run(conn, run["id"], "ok", result_summary="x")
    with pytest.raises(TicketError, match="ya está cerrado"):
        s.finish_run(conn, run["id"], "error")


# ============================================================ nuevos: verify auto

def test_auto_level_verified_by_bot(conn):
    t = _propose_agentic(conn)
    run = s.create_run(conn, t["job"]["id"])
    s.finish_run(conn, run["id"], "ok", result_summary="resumen del correo")
    v = s.verify_ticket(conn, "verifier-bot", t["id"], "pass", "output coherente con el prompt")
    assert v["status"] == "verified" and v["verified_by"] == "verifier-bot"


def test_auto_level_bot_fail_bounces(conn):
    t = _propose_agentic(conn)
    run = s.create_run(conn, t["job"]["id"])
    s.finish_run(conn, run["id"], "ok", result_summary="vacío")
    v = s.verify_ticket(conn, "verifier-bot", t["id"], "fail", "output vacío, no cumple el criterio")
    assert v["status"] == "in_progress"


def test_peer_level_rejects_bot_verifier(conn):
    t = _propose(conn, owner="alice", verification_level="peer")
    s.accept_ticket(conn, "owner", t["id"])
    s.complete_ticket(conn, "alice", t["id"], "file_path", "x.py")
    with pytest.raises(TicketError, match="no un bot"):
        s.verify_ticket(conn, "verifier-bot", t["id"], "pass")


def test_runner_bot_cannot_verify(conn):
    t = _propose_agentic(conn)
    run = s.create_run(conn, t["job"]["id"])
    s.finish_run(conn, run["id"], "ok", result_summary="x")
    with pytest.raises(TicketError, match="no verifica"):
        s.verify_ticket(conn, "runner-bot", t["id"], "pass")


def test_auto_accept_and_verify_criteria(conn):
    # Review carol: lo que crea owner desde la UI nace aceptado; criterio de verificación con campo propio
    r = s.propose_ticket(conn, "owner", "Comprar dominio", auto_accept=True,
                         verify_criteria="el dominio resuelve y está en el registrar")
    t = r["ticket"]
    assert t["status"] == "accepted"
    assert t["verify_criteria"].startswith("el dominio")
    hist = s.ticket_history(conn, t["id"])["history"]
    assert [h["new_status"] for h in hist] == ["proposed", "accepted"]


def test_agentic_recurring_inherits_cadence_and_self(conn):
    # Review bob F10: recurring+auto se cerraría tras el primer run
    with pytest.raises(TicketError, match="agentic recurring exige"):
        _propose_agentic(conn, job={"prompt": "x",
                                    "schedule": {"type": "recurring", "every_days": 1, "at": "08:00"}})
    t = _propose_agentic(conn, verification_level="self",
                         job={"prompt": "resume el correo",
                              "schedule": {"type": "recurring", "every_days": 1, "at": "08:00"}})
    assert t["cadence_days"] == 1
    run = s.create_run(conn, t["job"]["id"])
    s.finish_run(conn, run["id"], "ok", result_summary="resumen del día")
    fresh = s.list_tickets(conn, kind="agentic")[0]
    assert fresh["status"] == "accepted" and fresh["closed_at"] is None  # viva
    assert fresh["last_done_at"] is not None
    run2 = s.create_run(conn, t["job"]["id"])  # relanzable mañana
    assert run2["status"] == "running"


def test_kanban_backward_transitions(conn):
    # Retrocesos del tablero: reopen (done→in_progress) y backlog (in_progress→accepted)
    t = _propose(conn, owner="alice", verification_level="peer")
    s.accept_ticket(conn, "owner", t["id"])
    s.start_ticket(conn, "alice", t["id"])
    b = s.backlog_ticket(conn, "alice", t["id"])
    assert b["status"] == "accepted"
    s.start_ticket(conn, "alice", t["id"])
    s.complete_ticket(conn, "alice", t["id"], "file_path", "x.py")
    r = s.reopen_ticket(conn, "owner", t["id"], "faltaba el caso N")
    assert r["status"] == "in_progress"
    with pytest.raises(TicketError):  # reopen ilegal desde accepted
        s.reopen_ticket(conn, "owner", t["id"])


def test_job_mcp_config_persisted(conn):
    t = _propose_agentic(conn, job={"prompt": "lee el correo",
                                    "mcp_config": '{"mcpServers":{"gmail":{}}}'})
    assert t["job"]["mcp_config"] == '{"mcpServers":{"gmail":{}}}'


# ============================================================ nuevos: board

def test_board_columns_and_views(conn):
    p = s.create_project(conn, "owner", "Correo")
    t1 = _propose(conn, owner="alice", project_id=p["id"], due_at="2020-01-01")
    s.accept_ticket(conn, "owner", t1["id"])
    t2 = _propose_agentic(conn, project_id=p["id"])
    run = s.create_run(conn, t2["job"]["id"])
    s.finish_run(conn, run["id"], "ok", result_summary="ok")
    b = s.board(conn, project_id=p["id"])
    assert b["counts"]["accepted"] == 1 and b["counts"]["done"] == 1
    card = b["columns"]["done"][0]
    assert card["job"]["model"] == "haiku" and card["last_run"]["status"] == "ok"
    accepted_card = b["columns"]["accepted"][0]
    assert accepted_card["due_state"] == "overdue"
    b_today = s.board(conn, project_id=p["id"], view="today")
    assert any(x["id"] == t1["id"] for x in b_today["columns"]["accepted"])  # overdue entra en hoy


def test_board_views_are_distinct(conn):
    from datetime import date, timedelta
    p = s.create_project(conn, "owner", "Vistas")
    today = date.today().isoformat()
    next_month = (date.today() + timedelta(days=20)).isoformat()
    # v3.1: ya no existe 'sin fecha' (ancla automática al próximo laborable 14:00, que cae en
    # la semana). El backlog lejano se programa a 20 días: solo en 'future' y 'all'.
    backlog = _propose(conn, owner="alice", project_id=p["id"], title="backlog lejano",
                       scheduled_at=f"{next_month}T14:00")
    s.accept_ticket(conn, "owner", backlog["id"])
    auto = _propose(conn, owner="alice", project_id=p["id"], title="nace sin hora")
    assert auto["scheduled_at"] and auto["scheduled_at"][11:16] == "14:00"
    # con due hoy: en today y week
    hoy = _propose(conn, owner="alice", project_id=p["id"], title="vence hoy", due_at=today)
    s.accept_ticket(conn, "owner", hoy["id"])
    # con due en 20 días: solo future (y week no)
    fut = _propose(conn, owner="alice", project_id=p["id"], title="vence en 20d", due_at=next_month)
    s.accept_ticket(conn, "owner", fut["id"])

    ids = lambda b: {t["id"] for col in b["columns"].values() for t in col}
    today_ids = ids(s.board(conn, project_id=p["id"], view="today"))
    week_ids = ids(s.board(conn, project_id=p["id"], view="week"))
    future_ids = ids(s.board(conn, project_id=p["id"], view="future"))
    all_ids = ids(s.board(conn, project_id=p["id"], view="all"))

    assert backlog["id"] in all_ids and backlog["id"] not in today_ids and backlog["id"] not in week_ids
    assert hoy["id"] in today_ids and hoy["id"] in week_ids
    assert fut["id"] in future_ids and fut["id"] not in today_ids and fut["id"] not in week_ids
    assert week_ids != all_ids  # B1: semana ya no es todas


def test_recurrente_buckets_by_next_due(conn):
    from datetime import date, timedelta
    p = s.create_project(conn, "owner", "Recurr")
    # recurrente cadencia 7, hecha hace 6 días → next_due mañana → week, no today
    t = _propose(conn, owner="alice", project_id=p["id"], source="recurrente",
                 cadence_days=7, title="casi vence")
    s.accept_ticket(conn, "owner", t["id"])
    six_ago = (date.today() - timedelta(days=6)).isoformat()
    conn.execute("UPDATE tickets SET last_done_at=? WHERE id=?", (six_ago, t["id"]))
    conn.commit()
    ids = lambda b: {x["id"] for col in b["columns"].values() for x in col}
    assert t["id"] not in ids(s.board(conn, project_id=p["id"], view="today"))
    assert t["id"] in ids(s.board(conn, project_id=p["id"], view="week"))
    assert t["id"] not in ids(s.board(conn, project_id=p["id"], view="future"))


# ------------------------------------------------------------ v3: T0.2 last_change / T0.3 temporales

def test_last_change_after_three_transitions(conn):
    t = _propose(conn, owner="alice", verification_level="peer")
    assert s._as_dict(conn, s._get(conn, t["id"]))["last_change"] is None  # solo creación
    s.accept_ticket(conn, "owner", t["id"])
    s.start_ticket(conn, "alice", t["id"])
    done = s.complete_ticket(conn, "alice", t["id"], "file_path", "C:/x.py")
    lc = done["last_change"]
    assert lc["to"] == "done" and lc["from"] == "in_progress" and lc["by"] == "alice" and lc["at"]
    # un 'editado' (mismo estado) NO cuenta como cambio de estado
    edited = s.update_ticket(conn, "alice", t["id"], priority="alta")
    assert edited["last_change"]["to"] == "done"


def test_temporal_fields_validate_and_persist(conn):
    t = _propose(conn, owner="alice", scheduled_at="2026-09-03T09:00", duration_min=60)
    assert t["scheduled_at"] == "2026-09-03T09:00" and t["duration_min"] == 60 and t["all_day"] == 0
    with pytest.raises(TicketError, match="scheduled_at"):
        s.update_ticket(conn, "alice", t["id"], scheduled_at="mañana")
    with pytest.raises(TicketError, match="hora"):
        s.update_ticket(conn, "alice", t["id"], scheduled_at="2026-09-03")
    with pytest.raises(TicketError, match="duration_min"):
        s.update_ticket(conn, "alice", t["id"], duration_min=0)
    with pytest.raises(TicketError, match="preferred_time"):
        s.update_ticket(conn, "alice", t["id"], preferred_time="9am")
    with pytest.raises(TicketError, match="recurrentes"):
        s.update_ticket(conn, "alice", t["id"], next_due_override="2026-09-10")
    u = s.update_ticket(conn, "alice", t["id"], scheduled_at="2026-09-04T14:30", all_day=1,
                        duration_min="")
    assert u["scheduled_at"] == "2026-09-04T14:30" and u["all_day"] == 1 and u["duration_min"] is None
    hist = s.ticket_history(conn, t["id"])["history"]
    assert any("editado: all_day, duration_min, scheduled_at" in (h["note"] or "") for h in hist)


def test_recurrente_override_moves_only_next_due(conn):
    from datetime import date, timedelta
    today = date.today()
    t = _propose(conn, owner="alice", cadence_days=7, preferred_time="09:00")
    assert t["preferred_time"] == "09:00" and t["next_due"] == today.isoformat() and t["due"]
    s.accept_ticket(conn, "owner", t["id"])
    s.start_ticket(conn, "alice", t["id"])
    done = s.complete_ticket(conn, "alice", t["id"], "command_output", "hecho")
    assert done["next_due"] == (today + timedelta(days=7)).isoformat() and done["due_state"] == "ok"
    # #224: un override en sáb/dom salta al lunes → se usan fechas que tras el snap
    # sigan siendo pasado (hoy-3 nunca aterriza en hoy) y el +30 se compara ya saltado.
    def _snap(d):
        return d + timedelta(days=7 - d.weekday()) if d.weekday() >= 5 else d
    ahead = s.update_ticket(conn, "alice", t["id"], next_due_override=(today - timedelta(days=3)).isoformat())
    assert ahead["due_state"] == "overdue" and ahead["cadence_days"] == 7
    later = s.update_ticket(conn, "alice", t["id"], next_due_override=(today + timedelta(days=30)).isoformat())
    assert later["due_state"] == "ok" and later["next_due"] == _snap(today + timedelta(days=30)).isoformat()
    # el board 'week' respeta el override: a 30 días no entra en la semana
    week = s.board(conn, view="week")
    assert t["id"] not in [x["id"] for col in week["columns"].values() for x in col]
    cleared = s.update_ticket(conn, "alice", t["id"], next_due_override="")
    assert cleared["next_due"] == (today + timedelta(days=7)).isoformat()


# ------------------------------------------------------------ v3: T1.14 linked_job / T2.11 vista Hoy

def test_manual_ticket_exposes_linked_job(conn):
    from faros import jobs
    t = _propose(conn, owner="owner", verification_level="peer")
    s.accept_ticket(conn, "owner", t["id"])
    j = jobs.create_job(conn, "owner", "Brazo", "haz la tarea", ticket_id=t["id"])
    one = s._as_dict(conn, s._get(conn, t["id"]))
    assert one["linked_job"]["id"] == j["id"] and one["linked_job"]["last_run"] is None and "job" not in one
    run = jobs.create_run(conn, j["id"]); jobs.finish_run(conn, run["id"], "ok", result_summary="x")
    jobs.set_verdict(conn, run["id"], "fail", "no vale")
    lj = [x for x in s.list_tickets(conn) if x["id"] == t["id"]][0]["linked_job"]
    assert lj["last_run"]["verdict"] == "fail" and lj["last_run"]["verdict_reason"] == "no vale"


def test_board_today_uses_scheduled_date_and_window(conn):
    from datetime import date, timedelta
    from faros import settings
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    a = _propose(conn, title="Programada hoy", owner="alice", scheduled_at=f"{today}T10:00", due_at=tomorrow)
    b = _propose(conn, title="Vence manana", owner="alice", due_at=tomorrow)
    c = _propose(conn, title="Vencida", owner="alice", due_at="2020-01-01")
    for x in (a, b, c):
        s.accept_ticket(conn, "owner", x["id"])
    ids = lambda v: {x["id"] for col in s.board(conn, view=v)["columns"].values() for x in col}
    assert a["id"] in ids("today") and b["id"] not in ids("today") and c["id"] in ids("today")
    assert b["id"] in ids("week")
    settings.patch(conn, "owner", {"today_window": {"overdue": False}})
    assert c["id"] not in ids("today") and a["id"] in ids("today")


def test_owner_can_complete_on_behalf_of_agent(conn):
    """Regla 3-sep: owner (dueño de la Casa) puede completar/estampar tickets de agentes
    desde la app; otro agente que no sea el owner sigue rechazado."""
    from faros import service as s
    t = s.propose_ticket(conn, "owner", "Briefing I+D", owner="bob", cadence_days=7,
                         auto_accept=True)["ticket"]
    import pytest
    with pytest.raises(s.TicketError):
        s.complete_ticket(conn, "dave", t["id"], "file_path", "no soy el owner")
    r = s.complete_ticket(conn, "owner", t["id"], "file_path", "lo vi hecho")
    assert r["last_done_at"] is not None and r["status"] == "accepted"
    assert "owner" in str(s.ticket_history(conn, t["id"]))  # la transicion queda firmada por owner


# ------------------------------------------------------------ Lote 1 (14-sep): #224, 3a, bug6, evidence

def test_next_due_override_weekend_snaps_to_monday(conn):
    """#224 (owner 12-sep: snap, no rechazar). Sáb/dom no hay trabajo (13-ago):
    un override en fin de semana cae al lunes siguiente; un laborable se respeta."""
    t = _propose(conn, owner="carol", cadence_days=7)
    s.accept_ticket(conn, "owner", t["id"])
    sat = s.update_ticket(conn, "carol", t["id"], next_due_override="2026-09-12")  # sábado
    assert sat["next_due_override"] == "2026-09-14" and sat["next_due"] == "2026-09-14"
    sun = s.update_ticket(conn, "carol", t["id"], next_due_override="2026-09-13")  # domingo
    assert sun["next_due_override"] == "2026-09-14"
    fri = s.update_ticket(conn, "carol", t["id"], next_due_override="2026-09-11")  # viernes
    assert fri["next_due_override"] == "2026-09-11"


def test_update_ticket_clears_temporal_with_none(conn):
    """3a: null explícito borra scheduled_at / next_due_override (lo que manda el
    MCP con clear=[...]); el motor no lo rechaza."""
    t = _propose(conn, owner="alice", cadence_days=7, scheduled_at="2026-09-07T14:00")
    s.accept_ticket(conn, "owner", t["id"])
    s.update_ticket(conn, "alice", t["id"], next_due_override="2026-09-30")
    u = s.update_ticket(conn, "alice", t["id"], scheduled_at=None, next_due_override=None)
    assert u["scheduled_at"] is None and u["next_due_override"] is None
    assert u["next_due"] != "2026-09-30"


def test_mcp_update_ticket_clear_sends_nulls(monkeypatch):
    """3a, capa MCP: clear=[...] viaja como null en el PATCH; un campo no borrable
    no llega al daemon."""
    from faros import mcp_server as m
    sent = {}
    monkeypatch.setattr(m, "_call", lambda method, path, **kw: sent.update(method=method, path=path, **kw) or {"ok": 1})
    m.update_ticket("carol", 7, clear=["next_due_override", "scheduled_at"])
    assert sent["method"] == "PATCH" and sent["path"] == "/api/tickets/7"
    assert sent["json"] == {"agent": "carol", "next_due_override": None, "scheduled_at": None}
    sent.clear()
    r = m.update_ticket("carol", 7, clear=["title"])
    assert r["error"] and not sent


def test_evidence_is_editable_after_stamp(conn):
    """14-sep (dave): la evidencia de un ticket estampado se puede reescribir;
    la original queda en history. Sin estampar no hay nada que reescribir."""
    t = _propose(conn, owner="dave", cadence_days=7)
    s.accept_ticket(conn, "owner", t["id"])
    with pytest.raises(TicketError):
        s.update_ticket(conn, "dave", t["id"], evidence="aún no hay estampa")
    s.complete_ticket(conn, "dave", t["id"], "file_path", "C:/x/v1.md — tres límites abiertos")
    u = s.update_ticket(conn, "dave", t["id"], evidence="C:/x/v1.md — dos límites cerrados")
    assert u["evidence"] == "C:/x/v1.md — dos límites cerrados" and u["evidence_type"] == "file_path"
    hist = str(s.ticket_history(conn, t["id"])["history"])
    assert "tres límites abiertos" in hist and "editado: evidence" in hist
    with pytest.raises(TicketError):
        s.update_ticket(conn, "dave", t["id"], evidence="   ")


def test_board_recurrente_with_scheduled_at_uses_next_due(conn):
    """bug6 (owner 12-sep: el calendario es la referencia). Una recurrente que
    además tiene scheduled_at se coloca en Hoy por su next_due, como hace
    agenda.occurrences, no por la fecha programada."""
    from datetime import date, timedelta
    from faros import agenda as cal
    today = date.today()
    far = (today + timedelta(days=20)).isoformat()
    t = _propose(conn, owner="carol", cadence_days=7, scheduled_at=f"{far}T14:00")
    s.accept_ticket(conn, "owner", t["id"])  # sin last_done → next_due = hoy
    ids = lambda v: {x["id"] for col in s.board(conn, view=v)["columns"].values() for x in col}
    assert t["id"] in ids("today") and t["id"] in ids("week")
    cal_today = {i["ticket_id"] for i in cal.occurrences(conn, today.isoformat(), today.isoformat())["items"]}
    assert t["id"] in cal_today
