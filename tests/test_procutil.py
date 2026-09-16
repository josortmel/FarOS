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
    r = procutil.run_hidden([sys.executable, "-c", "import sys; print('ok'); sys.exit(3)"],
                            capture_output=True, text=True, timeout=30)
    assert r.returncode == 3
    assert r.stdout.strip() == "ok"


@pytest.mark.skipif(os.name != "nt", reason="solo Windows")
def test_no_console_window_created():
    """El hijo no tiene consola: GetConsoleWindow() devuelve 0 dentro de él."""
    code = "import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())"
    r = procutil.run_hidden([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert r.stdout.strip() == "0"
