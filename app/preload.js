// Contrato con el renderer (cerrado con Lienzo, 1-sep):
// window.agenticos = { token, baseUrl }. Si token es null, el renderer cae a fixtures.
const { contextBridge, ipcRenderer } = require("electron");

function argValue(prefix) {
  const arg = process.argv.find((a) => a.startsWith(prefix));
  return arg ? arg.slice(prefix.length) : null;
}

contextBridge.exposeInMainWorld("agenticos", {
  baseUrl: argValue("--agenticos-base-url=") || "http://127.0.0.1:8756",
  token: argValue("--agenticos-token=") || null,
  // T0.7: la version la manda main.js por argumento — en el preload sandboxed de Electron
  // require("./package.json") lanza y tumba todo el bridge (pantalla negra, 3-sep).
  appVersion: argValue("--agenticos-app-version=") || "",
  // T3.6: abrir un documento del workflow con la app del sistema. Devuelve "" si ok,
  // o el mensaje de error de shell.openPath.
  openPath: (path) => ipcRenderer.invoke("agenticos:open-path", path),
});
