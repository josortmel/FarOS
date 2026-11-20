// #476 (decisión #43 de Pepe, 15-oct: «Hacerlo»): puntos 17 y 5 de la lista de seguridad de Electron.
// Módulo aparte (sin require("electron")) para probarlo con `node --test`, como open_path_guard.js.

// Un mensaje IPC solo se atiende si lo manda NUESTRA ventana, y desde su marco principal (no un iframe).
// Antes solo lo comprobaba get-token; open-path y set-lang atendían a cualquiera.
function senderEsLaVentana(event, win) {
  if (!win || !event || (typeof win.isDestroyed === 'function' && win.isDestroyed())) return false;
  const wc = win.webContents;
  if (!wc || event.sender !== wc) return false;
  // senderFrame es null si el marco ya navegó o se destruyó: entonces no se atiende.
  if ('senderFrame' in event && event.senderFrame !== wc.mainFrame) return false;
  return true;
}

// Permisos del navegador: por defecto Electron los CONCEDE todos. La app no carga contenido remoto y solo
// usa uno: copiar al portapapeles (navigator.clipboard.writeText, renderer/js/app.js). Todo lo demás, no.
const PERMISOS = new Set(['clipboard-sanitized-write']);

function permisoPermitido(permiso) {
  return PERMISOS.has(permiso);
}

module.exports = { senderEsLaVentana, permisoPermitido, PERMISOS };
