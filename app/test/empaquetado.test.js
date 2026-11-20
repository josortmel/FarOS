// #1217: node --test app/test/empaquetado.test.js
// electron-builder.yml empaqueta una lista CERRADA de ficheros. Un require("./x") nuevo en main.js o preload.js
// que no esté en esa lista pasa todos los tests y revienta al abrir la app instalada (casi pasa con copiar_clave.js).
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

test('cada require local de main.js y preload.js va en los files de electron-builder.yml', () => {
  const dir = path.join(__dirname, '..');
  const yml = fs.readFileSync(path.join(dir, 'electron-builder.yml'), 'utf-8');
  const empaquetados = new Set([...yml.matchAll(/^\s*-\s*([\w./-]+\.\w+)\s*$/gm)].map((m) => m[1]));
  for (const f of ['main.js', 'preload.js']) {
    const src = fs.readFileSync(path.join(dir, f), 'utf-8').replace(/^\s*\/\/.*$/gm, ''); // fuera los comentarios
    for (const [, req] of src.matchAll(/require\("\.\/([\w./-]+)"\)/g)) {
      const fichero = /\.\w+$/.test(req) ? req : `${req}.js`;
      assert.ok(
        empaquetados.has(fichero),
        `${f} hace require("./${req}") y ${fichero} no está en files de electron-builder.yml`,
      );
    }
  }
});
