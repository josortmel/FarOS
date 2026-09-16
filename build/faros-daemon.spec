# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_submodules

# Rutas RELATIVAS al propio spec (#266, 16-sep). Estaban absolutas y con el
# nombre de usuario de la maquina donde se escribio: un clon que ejecutara
# este spec fallaba porque la ruta no existe, y de paso publicaba el usuario.
# SPECPATH lo inyecta PyInstaller; se usa en vez del cwd para que el build
# funcione se invoque desde donde se invoque, no solo desde la raiz.
REPO_ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))  # noqa: F821

hiddenimports = ['faros.harness.claude_cli', 'faros.harness.opencode']
hiddenimports += collect_submodules('uvicorn')
hiddenimports += collect_submodules('anyio')


a = Analysis(
    ['daemon_entry.py'],
    pathex=[REPO_ROOT],
    binaries=[],
    datas=[(os.path.join(REPO_ROOT, 'verify'), 'verify')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'PIL', 'tkinter', '_tkinter', 'IPython', 'scipy', 'pandas',
              'contourpy', 'kiwisolver', 'openpyxl', 'notebook', 'jupyter', 'pytest'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='faros-daemon',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # sin consola: no hay ventana que cerrar ni CTRL_CLOSE que lo mate (finding #5)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='faros-daemon',
)
