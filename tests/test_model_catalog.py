"""T5.4 (v3.1): catálogo de modelos concretos por familia, caché del refresco, validación en jobs."""
import pytest

from faros import db, jobs as jm, settings
from faros.harness import model_catalog as mc
from faros.service import TicketError


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def test_seed_has_families_and_measured_flags():
    entries = mc.seed_entries(["opus", "sonnet", "haiku", "fable"])
    by = {e.id: e for e in entries}
    assert by["opus"].alias_of == "claude-opus-5" and by["opus"].family == "opus"
    assert by["claude-opus-4-6"].validated is True and by["claude-opus-4-6"].source == "measured"
    assert by["claude-sonnet-4-6"].validated is None and by["claude-sonnet-4-6"].source == "seed"
    g = mc.grouped(entries)
    assert [x["family"] for x in g] == ["fable", "opus", "sonnet", "haiku"]
    assert any(m["id"] == "claude-opus-4-6[1m]" for m in g[1]["models"])


def test_merge_cache_marks_rejected_and_adds_unknown():
    entries = mc.seed_entries(["opus"])
    cache = {"at": "2026-09-04T12:00", "models": {
        "claude-sonnet-4-6": {"validated": False, "validated_at": "2026-09-04T12:00"},
        "claude-opus-6": {"validated": True, "validated_at": "2026-09-04T12:00"}}}
    merged = {e.id: e for e in mc.merge_cache(entries, cache)}
    assert merged["claude-sonnet-4-6"].validated is False
    assert merged["claude-opus-6"].validated is True and merged["claude-opus-6"].family == "opus"


def test_is_acceptable():
    entries = mc.merge_cache(mc.seed_entries(["opus"]),
                             {"at": "x", "models": {"claude-sonnet-4-6": {"validated": False}}})
    assert mc.is_acceptable("opus", entries)[0]
    assert mc.is_acceptable("claude-opus-4-6", entries)[0]
    assert mc.is_acceptable("claude-nuevo-9", entries) == (True, "forma válida, sin sondar")
    ok, why = mc.is_acceptable("claude-sonnet-4-6", entries)
    assert not ok and "rechazó" in why
    ok, why = mc.is_acceptable("gpt-5", entries)
    assert not ok and "desconocido" in why and "claude-opus-4-6" in why


def test_job_model_validated_against_catalog(conn):
    j = jm.create_job(conn, "alice", "escribe", "x", model="claude-opus-4-6")
    assert jm.get_job(conn, j["id"])["model"] == "claude-opus-4-6"
    with pytest.raises(TicketError, match="desconocido"):
        jm.create_job(conn, "alice", "mal", "x", model="gpt-5")
    settings.patch(conn, "alice", {"model_catalog": {"at": "2026-09-04T12:00", "models": {
        "claude-sonnet-4-6": {"validated": False, "validated_at": "2026-09-04T12:00"}}}})
    with pytest.raises(TicketError, match="rechazó"):
        jm.update_job(conn, "alice", j["id"], model="claude-sonnet-4-6")
    # opencode no pasa por el catálogo de claude
    jm.create_job(conn, "alice", "ds", "x", harness_name="opencode", model="deepseek/deepseek-v4-pro")


def test_run_probe_and_refresh_async_serialize():
    calls = []
    res = mc.run_probe("claude", ["a", "b"], lambda exe, m: calls.append(m) or m == "a")
    assert res["models"]["a"]["validated"] is True and res["models"]["b"]["validated"] is False
    assert calls == ["a", "b"]
    saved = {}
    import time
    started = mc.refresh_async("claude", ["a"], lambda exe, m: True, lambda r: saved.update(r))
    assert started
    for _ in range(50):
        if not mc.refresh_state()["running"] and saved:
            break
        time.sleep(0.02)
    assert saved["models"]["a"]["validated"] is True
