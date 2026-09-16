"""Subprocesos sin ventana (T1.1, v3.1).

el dueño (betatesting 3-sep, 2º y 5º): al lanzar un job desde la app aparecía una
ventana de consola de `claude`, y el vigilante abría un PowerShell cada pocos
minutos. Causa: el daemon empaquetado (console=False) NO tiene consola; cuando
crea un hijo de consola (claude.cmd → cmd.exe → node, powershell.exe, opencode)
Windows le asigna una consola NUEVA y visible.

Regla de la casa: TODO subprocess/asyncio.create_subprocess_* del daemon pasa
por `hidden_popen_kwargs()`. En Windows añade CREATE_NO_WINDOW (el hijo no
recibe consola) y un STARTUPINFO con SW_HIDE (por si el hijo crea su propia
ventana). En otros SO devuelve {} — no hay consolas visibles que esconder.

Medido 4-sep-2026: CREATE_NO_WINDOW basta para cmd/node/powershell; el
STARTUPINFO cubre el caso de un hijo GUI. Los dos juntos no estorban a los
pipes (stdin/stdout/stderr siguen funcionando) ni al JobObject del runner.
"""

from __future__ import annotations

import os
import subprocess


def hidden_popen_kwargs() -> dict:
    """kwargs extra para Popen / create_subprocess_exec: sin ventana en Windows."""
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": si,
    }


def hidden_console_popen_kwargs() -> dict:
    """Para hijos que LANZAN OTROS PROCESOS DE CONSOLA (opencode/claude con servidores
    MCP stdio): con CREATE_NO_WINDOW el hijo no tiene consola y cada nieto se crea la
    SUYA, visible (4-sep: veinte terminales en el escritorio del dueño). Con
    CREATE_NEW_CONSOLE + SW_HIDE el hijo recibe una consola propia OCULTA y los nietos
    la heredan. Medido 4-sep con tools/window_monitor.py."""
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NEW_CONSOLE,
        "startupinfo": si,
    }


def run_hidden(cmd, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run sin ventana. Mismos parámetros que subprocess.run."""
    merged = dict(hidden_popen_kwargs())
    merged.update(kwargs)
    return subprocess.run(cmd, **merged)
