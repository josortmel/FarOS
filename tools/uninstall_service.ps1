# FarOS — quita las tareas programadas del daemon y del vigilante.
# Uso: powershell -ExecutionPolicy Bypass -File tools\uninstall_service.ps1 [-Stop] [-StopOnly] [-Port 8756] [-InstallDir <carpeta>]
#   -Stop      además para el daemon que escucha en el puerto.
#   -StopOnly  SOLO para el daemon, sin quitar tareas (lo usa el instalador al ACTUALIZAR:
#              NSIS se cuelga si el exe está en uso — finding #19).
#   -InstallDir  solo toca lo que es DE ESA instalación: las tareas cuyo exe vive dentro de
#              esa carpeta y el daemon del puerto solo si su exe vive dentro. Lo pasa el
#              desinstalador. Sin él, se comporta como siempre (por nombre), para el uso manual.
# #317 (A): quita las DOS tareas, con el nombre nuevo (#288) y con el viejo. Antes quitaba
# solo AgenticOS-daemon y el vigilante se quedaba corriendo cada 5 min apuntando a un .exe
# borrado. Lo llama el desinstalador (assets/installer.nsh).
# 1-oct: sin -InstallDir, desinstalar UNA COPIA (la de prueba del #301 D, en otra carpeta)
# paraba el daemon de la casa y le quitaba las tareas de arranque: los nombres y el puerto
# son de la máquina, no de la instalación. Medido con tareas y puertos desechables.
param([switch]$Stop, [switch]$StopOnly, [int]$Port = 8756, [string]$InstallDir = "",
      [string[]]$TaskNames = @("FarOS-watchdog", "FarOS-daemon", "AgenticOS-watchdog", "AgenticOS-daemon"))
$ErrorActionPreference = "SilentlyContinue"

$dir = if ($InstallDir) { [IO.Path]::GetFullPath($InstallDir.Trim('"')).TrimEnd('\') + '\' } else { "" }
function Mine([string]$exe) {
    if (-not $dir) { return $true }
    if (-not $exe) { return $false }
    return [IO.Path]::GetFullPath($exe.Trim('"')).StartsWith($dir, [StringComparison]::OrdinalIgnoreCase)
}
$tasks = foreach ($t in $TaskNames) {
    $st = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
    if ($st -and (Mine $st.Actions[0].Execute)) { $t }
    elseif ($st) { Write-Host "Tarea '$t' apunta a otra instalacion: no se toca." }
}

if ($Stop -or $StopOnly) {
    foreach ($t in $tasks) { Stop-ScheduledTask -TaskName $t }
    $p = Get-NetTCPConnection -LocalPort $Port -State Listen | Select-Object -First 1 -ExpandProperty OwningProcess
    if ($p -and (Mine (Get-Process -Id $p).Path)) { Stop-Process -Id $p -Force -Confirm:$false; Write-Host "daemon (pid $p) parado" }
    elseif ($p) { Write-Host "El puerto $Port es de otra instalacion (pid $p): no se para." }
}
if ($StopOnly) { exit 0 }
# el vigilante primero: si no, puede relanzar el daemon entre medias
foreach ($t in $tasks) {
    Unregister-ScheduledTask -TaskName $t -Confirm:$false
    Write-Host "Tarea '$t' eliminada."
}
