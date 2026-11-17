// #281 parte 1 / #153 (F-SEG-07): node --test app/test/token_ipc.test.js
// El token de la API llegaba al renderer por additionalArguments, o sea en la LÍNEA DE
// COMANDOS del proceso renderer (Win32_Process la enseña a quien la pida). Ahora el preload
// lo pide por IPC síncrono y el contrato con el renderer no cambia: window.agenticos.token.
// Este test carga el preload.js REAL con un electron simulado. Lo que NO prueba (y es lo que
// rompió la L57): la app INSTALADA reiniciada con el tablero cargando — eso se verifica a mano.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const path = require("path");
const Module = require("module");

function cargarPreload({ argv = [], tokenIpc = "" } = {}) {
  const expuesto = {};
  const llamadas = [];
  const electron = {
    contextBridge: { exposeInMainWorld: (nombre, api) => { expuesto[nombre] = api; } },
    ipcRenderer: {
      sendSync: (canal) => { llamadas.push(canal); return canal === "agenticos:get-token" ? tokenIpc : undefined; },
      invoke: () => Promise.resolve(""),
      send: () => {},
    },
  };
  const original = Module._load;
  const argvOriginal = process.argv;
  Module._load = function (req, ...rest) { return req === "electron" ? electron : original.call(this, req, ...rest); };
  process.argv = [...argvOriginal, ...argv];
  const ruta = require.resolve("../preload.js");
  delete require.cache[ruta];
  try { require(ruta); } finally { Module._load = original; process.argv = argvOriginal; }
  return { api: expuesto.agenticos, llamadas };
}

test("el token sale del IPC, no de los argumentos", () => {
  const { api, llamadas } = cargarPreload({ argv: ["--agenticos-token=DEL-ARGV"], tokenIpc: "DEL-IPC" });
  assert.strictEqual(api.token, "DEL-IPC");
  assert.deepStrictEqual(llamadas, ["agenticos:get-token"]);
});

test("sin token (daemon caído) el renderer recibe null y cae a fixtures, como antes", () => {
  const { api } = cargarPreload({ tokenIpc: "" });
  assert.strictEqual(api.token, null);
});

test("lo demás del contrato sigue igual", () => {
  const { api } = cargarPreload({ argv: ["--agenticos-base-url=http://127.0.0.1:9999", "--agenticos-app-version=3.3.2"], tokenIpc: "t" });
  assert.strictEqual(api.baseUrl, "http://127.0.0.1:9999");
  assert.strictEqual(api.appVersion, "3.3.2");
  assert.strictEqual(typeof api.openPath, "function");
  assert.strictEqual(typeof api.setLang, "function");
});

test("main.js ya no pone el token en la línea de comandos y responde solo a su ventana", () => {
  const main = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf-8");
  assert.ok(!main.includes("--agenticos-token="), "main.js sigue pasando --agenticos-token= por argumento");
  assert.match(main, /ipcMain\.on\("agenticos:get-token"/);
  // #476: la comprobación vive en ipc_guard.js (sender === win.webContents y su marco principal),
  // probada en test/ipc_guard.test.js; aquí se mira que get-token la use.
  assert.match(main, /agenticos:get-token[\s\S]{0,120}senderEsLaVentana\(event,\s*win\)/);
});

test("#1217: copiarClave va por su canal y main.js solo lo atiende desde su ventana", () => {
  const { api } = cargarPreload({ tokenIpc: "t" });
  assert.strictEqual(typeof api.copiarClave, "function");
  const preload = fs.readFileSync(path.join(__dirname, "..", "preload.js"), "utf-8");
  assert.match(preload, /copiarClave:\s*\(nombre\)\s*=>\s*ipcRenderer\.invoke\("agenticos:copiar-clave",\s*nombre\)/);
  const main = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf-8");
  assert.match(main, /ipcMain\.handle\("agenticos:copiar-clave"[\s\S]{0,120}senderEsLaVentana\(event,\s*win\)/);
  assert.ok(!main.includes("readText"), "main.js no lee el portapapeles: eso solo lo hace copiar_clave.js para limpiar");
});
