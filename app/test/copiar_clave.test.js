// #1217: node --test app/test/copiar_clave.test.js
// El botón «Copiar» de Claves: el valor va al portapapeles desde el proceso principal y NUNCA vuelve al renderer;
// una clave no entregable no toca el portapapeles; la limpieza a los 30 s no borra lo que Pepe haya copiado después.
const test = require('node:test');
const assert = require('node:assert');
const { crearCopiador, PARA_QUE } = require('../copiar_clave');

const SECRETO = 'valor-de-prueba-' + 'x'.repeat(10); // obviously fake, built in pieces so secret scanners do not read it as a key

function montar(respuestas) {
  const llamadas = [];
  const fetch = async (url, op) => {
    llamadas.push({ url, op });
    const ruta = new URL(url).pathname;
    const [status, cuerpo] = respuestas[ruta];
    return { ok: status < 400, status, json: async () => cuerpo };
  };
  const pp = {
    texto: 'lo que había antes',
    writeText(t) {
      this.texto = t;
    },
    readText() {
      return this.texto;
    },
    clear() {
      this.texto = '';
    },
  };
  const temporizadores = [];
  const copiar = crearCopiador({
    baseUrl: 'http://127.0.0.1:8756',
    token: () => 'tok',
    fetch,
    clipboard: pp,
    setTimeout: (fn, ms) => temporizadores.push({ fn, ms }),
  });
  return { copiar, llamadas, pp, temporizadores };
}

const ROSTER = [200, { owner: 'Pepe' }];

test('copia al portapapeles y al renderer no le llega el valor', async () => {
  const m = montar({
    '/api/roster': ROSTER,
    '/api/credentials/deliver': [200, { name: 'Kavita (Pepe)', value: SECRETO }],
  });
  const r = await m.copiar('Kavita (Pepe)');
  assert.deepStrictEqual(r, { ok: true, nombre: 'Kavita (Pepe)', se_borra_en: 300 });
  assert.ok(!JSON.stringify(r).includes(SECRETO));
  assert.strictEqual(m.pp.texto, SECRETO);
  const cuerpo = JSON.parse(m.llamadas[1].op.body);
  assert.deepStrictEqual(cuerpo, { name: 'Kavita (Pepe)', requested_by: 'Pepe', purpose: PARA_QUE });
  assert.strictEqual(m.llamadas[1].op.headers['X-FarOS-Token'], 'tok');
});

test('a los 5 min se borra, pero solo si sigue estando la clave', async () => {
  const m = montar({ '/api/roster': ROSTER, '/api/credentials/deliver': [200, { name: 'K', value: SECRETO }] });
  await m.copiar('K');
  assert.strictEqual(m.temporizadores[0].ms, 300000);
  m.temporizadores[0].fn();
  assert.strictEqual(m.pp.texto, '');

  const n = montar({ '/api/roster': ROSTER, '/api/credentials/deliver': [200, { name: 'K', value: SECRETO }] });
  await n.copiar('K');
  n.pp.writeText('una dirección que Pepe copió después');
  n.temporizadores[0].fn();
  assert.strictEqual(n.pp.texto, 'una dirección que Pepe copió después');
});

test('una clave no entregable no toca el portapapeles y dice por qué', async () => {
  const m = montar({
    '/api/roster': ROSTER,
    '/api/credentials/deliver': [
      403,
      { error: 'forbidden', reason: '«DeepSeek» no está marcada como entregable', code: 'credential.not_deliverable' },
    ],
  });
  const r = await m.copiar('DeepSeek');
  assert.deepStrictEqual(r, { ok: false, motivo: 'no_entregable' });
  assert.strictEqual(m.pp.texto, 'lo que había antes');
  assert.strictEqual(m.temporizadores.length, 0);
});

test('otro fallo trae su motivo y su mensaje, y un daemon caído no rompe', async () => {
  const m = montar({
    '/api/roster': ROSTER,
    '/api/credentials/deliver': [409, { reason: '«X» está vacía', code: 'credential.value_empty' }],
  });
  assert.deepStrictEqual(await m.copiar('X'), {
    ok: false,
    motivo: 'credential.value_empty',
    mensaje: '«X» está vacía',
  });
  const caido = crearCopiador({
    baseUrl: 'http://127.0.0.1:1',
    token: () => 'tok',
    fetch: async () => {
      throw new Error('ECONNREFUSED');
    },
    clipboard: {
      writeText() {
        throw new Error('no');
      },
    },
    setTimeout,
  });
  assert.deepStrictEqual(await caido('X'), {
    ok: false,
    motivo: 'sin_daemon',
    mensaje: 'FarOS no contesta: no se ha copiado nada.',
  });
  assert.strictEqual((await m.copiar('')).motivo, 'nombre_invalido');
});
