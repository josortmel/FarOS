# FarOS - empaqueta el daemon con PyInstaller (T4.2) en build/dist/faros-daemon (onedir).
# Uso: powershell -ExecutionPolicy Bypass -File tools\build_daemon.ps1
# Requiere: un python en el PATH (solo para crear el venv). El spec fijo vive en
# build/faros-daemon.spec (datas: verify/ ; hiddenimports: adapters de harness + uvicorn + anyio).
#
# VENV LIMPIO (#387, 5-oct): PyInstaller empaqueta lo que encuentra en el Python
# con el que se ejecuta. Desde el Python global (compartido) el exe arrastraba
# trio, werkzeug, rich, websockets, yaml, bcrypt, dns, cryptography... 66 paquetes
# de terceros, 52,6 MB. Ahora cada build crea build/venv-daemon desde cero con
# SOLO `pip install . pyinstaller`: 24 paquetes, todos del cierre de faros, 29,7 MB.
# En el venv no se instala NADA mas (ni pip-audit): lo que haya, entra.
#
# ARREGLADO 16-sep: este script ABORTABA SIEMPRE. PyInstaller escribe sus
# lineas INFO por stderr, y con $ErrorActionPreference = "Stop" PowerShell
# convierte cualquier cosa en stderr de un comando nativo en NativeCommandError
# y mata el script - con el build a medias y el exe de AYER todavia en disco.
# O sea que el modo de fallo no era "falla y te enteras": era "falla, y si
# miras la carpeta encuentras un binario que parece bueno". Es la leccion L64.
# Ahora: Stop solo para los cmdlets, el nativo se evalua por SU EXIT CODE, y
# el artefacto se comprueba por FECHA ademas de por existencia.
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $RepoRoot

$exe = Join-Path $RepoRoot "build/dist/faros-daemon/faros-daemon.exe"
$before = if (Test-Path $exe) { (Get-Item $exe).LastWriteTime } else { [datetime]::MinValue }

$venv = Join-Path $RepoRoot "build/venv-daemon"
if (Test-Path $venv) { Remove-Item -Recurse -Force $venv }

# Los nativos, fuera del alcance de -ErrorAction Stop; cada uno por SU exit code.
$ErrorActionPreference = "Continue"
python -m venv $venv
if ($LASTEXITCODE -ne 0) { throw "python -m venv salio con codigo $LASTEXITCODE" }
$py = Join-Path $venv "Scripts/python.exe"
& $py -m pip install --quiet --disable-pip-version-check $RepoRoot pyinstaller *>&1 |
    Tee-Object -FilePath build/pip_venv.log | Select-Object -Last 3
if ($LASTEXITCODE -ne 0) { throw "pip install en el venv salio con codigo $LASTEXITCODE - mira build/pip_venv.log" }
& $py -m PyInstaller --noconfirm --clean --distpath build/dist --workpath build/work `
    build/faros-daemon.spec *>&1 | Tee-Object -FilePath build/pyinstaller.log | Select-Object -Last 3
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($code -ne 0) { throw "PyInstaller salio con codigo $code - mira build/pyinstaller.log" }
if (-not (Test-Path $exe)) { throw "no se genero $exe" }

# Un exe que existe no es un exe nuevo. Si no se ha reescrito, el build no
# hizo nada y lo que hay es el de la vez anterior.
$after = (Get-Item $exe).LastWriteTime
if ($after -le $before) {
    throw "$exe NO se ha reescrito (fecha $after, anterior $before): el build no produjo binario nuevo"
}

$size = [math]::Round((Get-ChildItem "build/dist/faros-daemon" -Recurse | Measure-Object Length -Sum).Sum / 1MB)
Write-Host "onedir: $exe ($size MB, $after)"
Write-Host "Medir en limpio: tools\measure_daemon_clean.ps1 (PATH sin Python, AGENTICOS_HOME aparte, puerto 8757)."
