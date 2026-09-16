# AgenticOS — quita la tarea programada del daemon (rollback de T4.1).
# Uso: powershell -ExecutionPolicy Bypass -File tools\uninstall_service.ps1 [-Stop]
param([switch]$Stop)
$ErrorActionPreference = "SilentlyContinue"
$TaskName = "AgenticOS-daemon"
if ($Stop) {
    Stop-ScheduledTask -TaskName $TaskName
    $p = Get-NetTCPConnection -LocalPort 8756 -State Listen | Select-Object -First 1 -ExpandProperty OwningProcess
    if ($p) { Stop-Process -Id $p -Force -Confirm:$false; Write-Host "daemon (pid $p) parado" }
}
if (Get-ScheduledTask -TaskName $TaskName) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Tarea '$TaskName' eliminada."
} else {
    Write-Host "No existía la tarea '$TaskName'."
}
