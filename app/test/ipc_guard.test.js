// #476: node --test app/test/ipc_guard.test.js
const test = require("node:test");
const assert = require("node:assert");
const { senderEsLaVentana, permisoPermitido } = require("../ipc_guard");

function ventana() {
  const mainFrame = { url: "file:///index.html" };
  const webContents = { mainFrame };
  return { webContents, isDestroyed: () => false };
}

test("la propia ventana, desde su marco principal → sí", () => {
  const win = ventana();
  assert.ok(senderEsLaVentana({ sender: win.webContents, senderFrame: win.webContents.mainFrame }, win));
});

test("otro webContents → no", () => {
  const win = ventana();
  const otro = ventana();
  assert.ok(!senderEsLaVentana({ sender: otro.webContents, senderFrame: otro.webContents.mainFrame }, win));
});

test("un iframe de la ventana → no", () => {
  const win = ventana();
  assert.ok(!senderEsLaVentana({ sender: win.webContents, senderFrame: { url: "file:///otro.html" } }, win));
});

test("marco ya destruido (senderFrame null) → no", () => {
  const win = ventana();
  assert.ok(!senderEsLaVentana({ sender: win.webContents, senderFrame: null }, win));
});

test("sin ventana o ventana destruida → no", () => {
  const win = ventana();
  const ev = { sender: win.webContents, senderFrame: win.webContents.mainFrame };
  assert.ok(!senderEsLaVentana(ev, null));
  assert.ok(!senderEsLaVentana(ev, { ...win, isDestroyed: () => true }));
});

test("permisos: solo copiar al portapapeles", () => {
  assert.ok(permisoPermitido("clipboard-sanitized-write"));
  for (const p of ["media", "geolocation", "notifications", "clipboard-read", "openExternal", "fullscreen", "midi"]) {
    assert.ok(!permisoPermitido(p), p);
  }
});
