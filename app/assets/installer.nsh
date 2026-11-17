; FarOS — añadidos al instalador NSIS de electron-builder (#317 A).
; Las tareas programadas viven FUERA de la carpeta del producto: el desinstalador de serie
; no las veía y se quedaban (el vigilante, cada 5 min, apuntando a un .exe borrado).
; Desinstalar de verdad: quita las tareas (nombres nuevos y viejos) y para el daemon.
; Actualizar (el instalador nuevo ejecuta el desinstalador viejo): solo para el daemon, para
; que NSIS pueda reemplazar el exe (finding #19); la app nueva re-registra las tareas al abrirse.
!macro customUnInstall
  ${ifNot} ${isUpdated}
    nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\resources\tools\uninstall_service.ps1" -Stop -InstallDir "$INSTDIR"'
  ${else}
    nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\resources\tools\uninstall_service.ps1" -StopOnly -InstallDir "$INSTDIR"'
  ${endIf}
!macroend
