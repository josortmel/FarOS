# AgenticOS — daemon como tarea programada de Windows al inicio de sesión (T4.1 + T4.3).
#
# Por qué tarea y no servicio: el daemon lanza `claude -p` con la sesión OAuth del
# usuario (perfil, ~/.claude.json, MCP de user scope). Un servicio de sistema corre
# como otra cuenta y no ve nada de eso. La tarea ONLOGON corre como el usuario,
# sin ventana, y sobrevive al cierre de la app (L52: nunca dentro de una sesión).
#
# Dos modos:
#   desarrollo (por defecto): cmd oculto → python -m agenticos.daemon con stdout/err a logs.
#   empaquetado (-Exe ruta):  el onedir de PyInstaller directamente (sin consola: el exe
#                             redirige su stdio a los logs él solo; finding #5).
# Vigilante (finding #5): el trigger de logon se repite cada 5 min con
# MultipleInstances=IgnoreNew — si el daemon sigue vivo la repetición no hace nada;
# si murió, lo relanza. Sin daemon aparte, sin código.
#
# Uso:  powershell -ExecutionPolicy Bypass -File tools\install_service.ps1 [-Port 8756] [-Exe ruta] [-Start]
# Idempotente: si la tarea existe, se reemplaza.

param(
    [int]$Port = 8756,
    [string]$Exe = "",
    [string]$TaskName = "AgenticOS-daemon",
    [string]$DataHome = "",
    [switch]$Start
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$DataDir = Join-Path $env:LOCALAPPDATA "AgenticOS"
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

if ($Exe) {
    if (-not (Test-Path $Exe)) { throw "no existe el daemon empaquetado: $Exe" }
    $args_ = "--port $Port"
    if ($DataHome) { $args_ += " --home `"$DataHome`"" }   # solo pruebas: casa aparte
    $Action = New-ScheduledTaskAction -Execute $Exe -Argument $args_ `
        -WorkingDirectory (Split-Path -Parent $Exe)
    $modo = "empaquetado ($Exe)"
} else {
    $Python = (Get-Command python).Source
    # Envoltorio: cmd oculto que redirige stdout/stderr a ficheros (uvicorn escribe a
    # stdout; sin redirección + pythonw el daemon muere en silencio — medido 1-sep).
    $Launcher = Join-Path $DataDir "daemon_launcher.cmd"
    @"
@echo off
cd /d "$RepoRoot"
"$Python" -m agenticos.daemon --port $Port >> "$DataDir\daemon_stdout.log" 2>> "$DataDir\daemon_stderr.log"
"@ | Set-Content -Path $Launcher -Encoding ASCII
    $Action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\cmd.exe" `
        -Argument "/c `"$Launcher`"" -WorkingDirectory $RepoRoot
    $modo = "desarrollo (python, launcher $Launcher)"
}

$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# Vigilante: repetir cada 5 min indefinidamente; IgnoreNew hace que solo actúe si murió.
$rep = (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)).Repetition
$rep.Duration = ""          # sin duracion = indefinido (TimeSpan::MaxValue no es XML valido)
$rep.StopAtDurationEnd = $false
$Trigger.Repetition = $rep
$Settings = New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Principal $Principal `
    -Description "AgenticOS daemon (API 127.0.0.1:$Port + scheduler). Arranca al iniciar sesión; vigilante cada 5 min." | Out-Null

Write-Host "Tarea '$TaskName' registrada — modo $modo (ONLOGON + vigilante 5 min, oculta, sin límite de tiempo)."
if ($Start) {
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 4
    try {
        $h = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" -UseBasicParsing -TimeoutSec 5
        Write-Host "health: $($h.Content)"
    } catch {
        Write-Host "health: sin respuesta todavía ($_)"
    }
}
