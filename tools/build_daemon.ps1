# FarOS - empaqueta el daemon con PyInstaller (T4.2) en build/dist/faros-daemon (onedir).
# Uso: powershell -ExecutionPolicy Bypass -File tools\build_daemon.ps1
# Requiere: pip install pyinstaller. El spec fijo vive en build/faros-daemon.spec
# (datas: verify/ ; hiddenimports: adapters de harness + uvicorn + anyio).
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

# El nativo, fuera del alcance de -ErrorAction Stop.
$ErrorActionPreference = "Continue"
python -m PyInstaller --noconfirm --clean --distpath build/dist --workpath build/work `
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
