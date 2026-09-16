# AgenticOS — tarea VIGILANTE independiente (finding #5, 3-sep 12:55; v3.1 T1.2, 4-sep).
#
# Por qué existe: la repetición y el "reiniciar si falla" de la tarea del daemon solo
# actúan sobre instancias lanzadas por SU trigger (logon). Una instancia arrancada a
# mano (Start-ScheduledTask, la app) queda sin vigilancia: medido en producción — daemon
# matado 12:51, ningún relanzamiento en 5 min. Un trigger de TIEMPO no depende de eso.
#
# v3.1 (el dueño, betatesting 2º): la versión anterior lanzaba powershell.exe cada 5 min y
# la consola PARPADEABA aunque la app estuviera cerrada (-WindowStyle Hidden no evita
# el flash: la consola nace antes de leer el argumento). Ahora el vigilante es:
#   empaquetado (-Exe): faros-daemon.exe --watchdog --port N   (console=False → sin ventana)
#   desarrollo:         pythonw.exe -m agenticos.watchdog --port N (sin consola; log a fichero)
# Nunca powershell.exe, nunca cmd.exe.
#
# Uso: powershell -ExecutionPolicy Bypass -File tools\install_watchdog.ps1 [-Port 8756]
#      [-Exe ruta\faros-daemon.exe] [-DaemonTask AgenticOS-daemon]
#      [-TaskName AgenticOS-watchdog] [-Minutes 5]
param(
    [int]$Port = 8756,
    [string]$Exe = "",
    [string]$DaemonTask = "AgenticOS-daemon",
    [string]$TaskName = "AgenticOS-watchdog",
    [int]$Minutes = 5
)
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$DataDir = Join-Path $env:LOCALAPPDATA "AgenticOS"
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

if ($Exe) {
    if (-not (Test-Path $Exe)) { throw "no existe el daemon empaquetado: $Exe" }
    $Action = New-ScheduledTaskAction -Execute $Exe `
        -Argument "--watchdog --port $Port --task $DaemonTask" `
        -WorkingDirectory (Split-Path -Parent $Exe)
    $modo = "empaquetado ($Exe --watchdog)"
} else {
    $Python = (Get-Command python).Source
    $Pythonw = Join-Path (Split-Path -Parent $Python) "pythonw.exe"
    if (-not (Test-Path $Pythonw)) { throw "no existe pythonw.exe junto a $Python" }
    $Action = New-ScheduledTaskAction -Execute $Pythonw `
        -Argument "-m agenticos.watchdog --port $Port --task $DaemonTask" `
        -WorkingDirectory $RepoRoot
    $modo = "desarrollo (pythonw -m agenticos.watchdog)"
}

$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $Minutes)
$Trigger.Repetition.Duration = ""
$Trigger.Repetition.StopAtDurationEnd = $false
$Settings = New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal `
    -Description "AgenticOS vigilante (sin ventana): cada $Minutes min comprueba /api/health en :$Port y relanza '$DaemonTask' si no responde." | Out-Null
# El script antiguo de PowerShell ya no se usa: fuera, para que nadie lo relance.
Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $DataDir ("watchdog_" + $Port + ".ps1"))
Write-Host "Tarea '$TaskName' registrada — modo $modo (cada $Minutes min, health :$Port -> relanza '$DaemonTask'). Log: $DataDir\watchdog.log"
