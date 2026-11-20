// #384: node --test app/test/open_path_guard.test.js
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { rejectReason } = require('../open_path_guard');

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'faros-384-'));
const md = path.join(dir, 'plan.md');
const bat = path.join(dir, 'evil.bat');
const exeAsMd = path.join(dir, 'EVIL.MD.exe');
fs.writeFileSync(md, '# plan\n');
fs.writeFileSync(bat, '@echo off\n');
fs.writeFileSync(exeAsMd, 'MZ');
fs.mkdirSync(path.join(dir, 'carpeta.md'));

test('un .md legítimo que existe se puede abrir', () => {
  assert.strictEqual(rejectReason(md), null);
});

test('extensión en mayúsculas cuenta igual', () => {
  const up = path.join(dir, 'NOTA.TXT');
  fs.writeFileSync(up, 'x');
  assert.strictEqual(rejectReason(up), null);
});

test('.bat existente → rechazado', () => {
  assert.match(rejectReason(bat), /no permitido/);
});

test('.exe con .md en medio del nombre → rechazado', () => {
  assert.match(rejectReason(exeAsMd), /no permitido/);
});

test('ruta UNC → rechazada sin tocar la red', () => {
  assert.match(rejectReason('\\\\host\\share\\x.md'), /locales/);
  assert.match(rejectReason('//host/share/x.md'), /locales/);
  assert.match(rejectReason('\\\\?\\C:\\x.md'), /locales/);
});

test('byte nulo → rechazada', () => {
  assert.match(rejectReason(md + '\0.exe'), /no válida/);
});

test('ruta relativa, vacía o no-string → rechazada', () => {
  assert.match(rejectReason('plan.md'), /absoluta/);
  assert.match(rejectReason(''), /vacía/);
  assert.match(rejectReason(null), /vacía/);
  assert.match(rejectReason({ path: md }), /vacía/);
});

test('fichero que no existe → rechazado', () => {
  assert.match(rejectReason(path.join(dir, 'no-existe.md')), /no existe/);
});

test('una carpeta con nombre .md → rechazada', () => {
  assert.match(rejectReason(path.join(dir, 'carpeta.md')), /no es un fichero/);
});

test('un enlace .md que apunta a un .bat se juzga por su destino', (t) => {
  const link = path.join(dir, 'trampa.md');
  try {
    fs.symlinkSync(bat, link);
  } catch {
    t.skip('sin permiso para crear enlaces en esta máquina');
    return;
  }
  assert.match(rejectReason(link), /no permitido/);
});

// #402: flujo alternativo NTFS. «evil.bat:nota.md» tiene extname .md y
// realpath/stat funcionan sobre el flujo: el guard lo dejaba pasar.
test('flujo ADS («x.bat:y.md») → rechazado', () => {
  fs.writeFileSync(`${bat}:nota.md`, '# nota\n');
  assert.match(rejectReason(`${bat}:nota.md`), /flujo/);
});

test('la letra de unidad sigue valiendo (C:\\…)', () => {
  assert.strictEqual(rejectReason(md), null);
  assert.ok(/^[A-Za-z]:/.test(md));
});
