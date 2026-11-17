// #384: filtro de lo que la UI puede pedir abrir con shell.openPath.
// shell.openPath EJECUTA lo que le des en Windows (.exe .bat .cmd .lnk .hta .ps1…)
// y acepta rutas UNC (\\host\share\x.exe abre un binario remoto). El renderer
// solo necesita abrir documentos del workflow y la guía de Gmail, así que se
// abre SOLO un fichero local existente con extensión de documento.
// Módulo aparte (sin require("electron")) para poder probarlo con `node --test`.

const fs = require("fs");
const path = require("path");

const ALLOWED_EXT = new Set([".md", ".json", ".txt", ".html", ".pdf"]);

// Devuelve null si la ruta se puede abrir, o el motivo del rechazo (string).
function rejectReason(target) {
  if (typeof target !== "string" || !target) return "ruta vacía";
  if (target.includes("\0")) return "ruta no válida";
  // UNC y dispositivos: \\host\share, //host/share, \\?\, \\.\
  if (/^[\\/]{2}/.test(target)) return "solo ficheros locales";
  if (!path.isAbsolute(target)) return "la ruta tiene que ser absoluta";
  // #402: «x.bat:y.md» es un flujo alternativo NTFS (extname .md, stat funciona).
  // Fuera de la letra de unidad, un «:» nunca es parte de un nombre válido.
  if (target.replace(/^[A-Za-z]:/, "").includes(":")) return "flujo alternativo no permitido";
  let real;
  try {
    // realpath: un .md que en realidad es un enlace a un .exe se juzga por su destino
    real = fs.realpathSync.native(target);
  } catch {
    return "el fichero no existe";
  }
  if (/^[\\/]{2}/.test(real)) return "solo ficheros locales";
  for (const p of [target, real]) {
    if (!ALLOWED_EXT.has(path.extname(p).toLowerCase())) {
      return `tipo de fichero no permitido (${path.extname(p) || "sin extensión"})`;
    }
  }
  let st;
  try { st = fs.statSync(real); } catch { return "el fichero no existe"; }
  if (!st.isFile()) return "no es un fichero";
  return null;
}

module.exports = { rejectReason, ALLOWED_EXT };
