"""T1.2 (v3.1): vigilante sin PowerShell."""
from pathlib import Path

from faros import watchdog


def test_health_ok_no_relaunch(tmp_path):
    calls = []
    ok = watchdog.check(8756, "T", data_dir=tmp_path,
                        _health=lambda p: True, _start=lambda t: calls.append(t) or (0, ""))
    assert ok and calls == []
    assert not (tmp_path / "watchdog.log").exists()


def test_health_down_relaunches_and_logs(tmp_path):
    calls = []
    ok = watchdog.check(8756, "AgenticOS-daemon", data_dir=tmp_path,
                        _health=lambda p: False, _start=lambda t: calls.append(t) or (0, "SUCCESS"))
    assert ok and calls == ["AgenticOS-daemon"]
    log = (tmp_path / "watchdog.log").read_text(encoding="utf-8")
    assert "health caido en :8756" in log and "AgenticOS-daemon" in log


def test_start_failure_reported(tmp_path):
    ok = watchdog.check(8756, "T", data_dir=tmp_path,
                        _health=lambda p: False, _start=lambda t: (1, "ERROR: no existe"))
    assert not ok
    assert "rc=1" in (tmp_path / "watchdog.log").read_text(encoding="utf-8")


def test_health_probe_real_closed_port():
    # Un puerto sin nadie escuchando: False, sin excepción, rápido.
    assert watchdog.health_ok(1, timeout=1.0) is False
