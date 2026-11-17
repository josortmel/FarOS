// #402: node --test app/test/window_policy.test.js
const test = require("node:test");
const assert = require("node:assert");
const { puedeNavegar, abrirFuera, peticionLocalPermitida } = require("../window_policy");

const INDEX = "file:///C:/Program%20Files/FarOS/resources/app.asar/renderer/index.html";
const BASE = "http://127.0.0.1:8756";

test("navegar a la propia index.html (con query o hash) → sí", () => {
  assert.ok(puedeNavegar(INDEX, INDEX));
  assert.ok(puedeNavegar(`${INDEX}#board`, INDEX));
  assert.ok(puedeNavegar(`${INDEX}?x=1`, INDEX));
});

test("navegar fuera de la app → no", () => {
  assert.ok(!puedeNavegar("https://evil.example/", INDEX));
  assert.ok(!puedeNavegar("file:///D:/x/evil.html", INDEX));
  assert.ok(!puedeNavegar("http://127.0.0.1:8756/", INDEX));
  assert.ok(!puedeNavegar(`${INDEX}.evil.html`, INDEX));
});

test("window.open: solo https sale, y al navegador del sistema", () => {
  assert.strictEqual(abrirFuera("https://github.com/josortmel/FarOS"), true);
  assert.strictEqual(abrirFuera("http://example.com/"), false);
  assert.strictEqual(abrirFuera("file:///C:/Windows/System32/calc.exe"), false);
  assert.strictEqual(abrirFuera("javascript:alert(1)"), false);
  assert.strictEqual(abrirFuera("no es una url"), false);
});

test("peticiones locales: solo al puerto del daemon", () => {
  assert.ok(peticionLocalPermitida(`${BASE}/api/health`, BASE));
  assert.ok(peticionLocalPermitida(`${BASE}/api/events?x=1`, BASE));
  assert.ok(!peticionLocalPermitida("http://127.0.0.1:19736/", BASE));   // relay ws
  assert.ok(!peticionLocalPermitida("http://localhost:8000/memories", BASE)); // EcoDB
  assert.ok(!peticionLocalPermitida("http://localhost:8756/api/health", BASE)); // otro origen
});

test("lo que no es local no lo juzga esta regla (lo corta la CSP)", () => {
  assert.ok(peticionLocalPermitida(INDEX, BASE));
  assert.ok(peticionLocalPermitida("devtools://devtools/x", BASE));
  assert.ok(peticionLocalPermitida("data:image/png;base64,xx", BASE));
});
