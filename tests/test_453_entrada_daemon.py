"""#453 — faros-daemon.exe ignoraba los argumentos que no conocía, y «--help» arrancaba un daemon contra la casa
de producción (6-oct, memoria 45b0c150; solo cayó porque el 8756 estaba ocupado).

Ahora la entrada (build/daemon_entry.py, la misma que empaqueta PyInstaller) valida sus argumentos ANTES de
importar faros y antes de tocar la casa: --help imprime la ayuda y sale con 0, un argumento desconocido sale con
2, y en ninguno de los dos casos se crea nada en la casa.
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ENTRADA = Path(__file__).resolve().parent.parent / "build" / "daemon_entry.py"


def _correr(tmp_path, *args, timeout=30):
    casa = tmp_path / "casa"
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("FAROS_", "AGENTICOS_"))}
    env.update(FAROS_HOME=str(casa), PYTHONPATH=str(ENTRADA.parent.parent))
    r = subprocess.run(
        [sys.executable, str(ENTRADA), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        cwd=str(tmp_path),
    )
    return r, casa


def test_help_imprime_la_ayuda_y_no_toca_la_casa(tmp_path):
    r, casa = _correr(tmp_path, "--help")
    assert r.returncode == 0
    assert "--port" in r.stdout and "--home" in r.stdout
    assert not casa.exists()


def test_un_argumento_desconocido_sale_con_2_sin_tocar_la_casa(tmp_path):
    r, casa = _correr(tmp_path, "--xyz")
    assert r.returncode == 2 and "--xyz" in r.stderr
    assert not casa.exists()


def test_un_puerto_que_no_es_numero_tambien_se_rechaza(tmp_path):
    r, casa = _correr(tmp_path, "--port", "ocho")
    assert r.returncode == 2 and not casa.exists()


def test_sin_argumentos_raros_arranca_como_siempre(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        puerto = s.getsockname()[1]
    casa = tmp_path / "casa"
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("FAROS_", "AGENTICOS_"))}
    env.update(FAROS_HOME=str(casa), PYTHONPATH=str(ENTRADA.parent.parent))
    p = subprocess.Popen(
        [sys.executable, str(ENTRADA), "--port", str(puerto)],
        env=env,
        cwd=str(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        import urllib.request

        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/api/health", timeout=2) as r:
                    assert r.status == 200
                    break
            except OSError:
                time.sleep(0.5)
        else:
            raise AssertionError("el daemon no arrancó")
        assert (casa / "token").exists()
    finally:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        p.wait(timeout=10)
