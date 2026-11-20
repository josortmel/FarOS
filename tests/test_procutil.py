"""T1.1 (v3.1): los subprocesos del daemon no crean consolas visibles."""

import os
import subprocess
import sys

import pytest

from faros import procutil


def test_kwargs_windows_hidden():
    kw = procutil.hidden_popen_kwargs()
    if os.name != "nt":
        assert kw == {}
        return
    assert kw["creationflags"] & subprocess.CREATE_NO_WINDOW
    si = kw["startupinfo"]
    assert si.dwFlags & subprocess.STARTF_USESHOWWINDOW
    assert si.wShowWindow == subprocess.SW_HIDE


def test_run_hidden_keeps_pipes():
    r = procutil.run_hidden(
        [sys.executable, "-c", "import sys; print('ok'); sys.exit(3)"], capture_output=True, text=True, timeout=30
    )
    assert r.returncode == 3
    assert r.stdout.strip() == "ok"


@pytest.mark.skipif(os.name != "nt", reason="solo Windows")
def test_no_console_window_created():
    """El hijo no tiene consola: GetConsoleWindow() devuelve 0 dentro de él."""
    code = "import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())"
    r = procutil.run_hidden([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert r.stdout.strip() == "0"


@pytest.mark.skipif(os.name != "nt", reason="solo Windows")
def test_hidden_console_kwargs_child_has_hidden_console():
    """Vivía en test_opencode_sync.py (borrado el 20-nov, #1361); prueba procutil, no opencode."""
    kw = procutil.hidden_console_popen_kwargs()
    assert kw["creationflags"] & subprocess.CREATE_NEW_CONSOLE
    code = (
        "import ctypes; h=ctypes.windll.kernel32.GetConsoleWindow();"
        " print(h != 0, ctypes.windll.user32.IsWindowVisible(h))"
    )
    r = subprocess.run(
        [os.environ.get("PYTHON", "python"), "-c", code], capture_output=True, text=True, timeout=30, **kw
    )
    assert r.stdout.strip() == "True 0"  # tiene consola propia y NO es visible
