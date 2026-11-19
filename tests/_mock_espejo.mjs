// #722: runs transition scenarios against the fixtures mock of app/renderer/js/api.js
// and prints, for each one, what the last transition answered: {status, error, field, code}.
// Used by tests/test_722_mock_espejo.py, which runs the same scenarios against the daemon.
import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'app', 'renderer');
// a dead daemon: connect() falls back to the mock; the fixtures come from disk
globalThis.location = { search: '?base=http://127.0.0.1:1' };
globalThis.fetch = async (url) => {
  if (String(url).startsWith('fixtures/')) {
    const body = readFileSync(path.join(root, url), 'utf8');
    return { ok: true, json: async () => JSON.parse(body) };
  }
  throw new Error('no network in the mirror test');
};

const { connect, api } = await import(pathToFileURL(path.join(root, 'js', 'api.js')).href);
await connect();
const scenarios = JSON.parse(readFileSync(0, 'utf8'));
const out = [];
for (const sc of scenarios) {
  const t = await api().createTicket({ agent: 'owner', title: sc.name, owner: 'alice', ...(sc.ticket ?? {}) });
  let last = null;
  for (const body of sc.steps) {
    try {
      await api().transition(t.id, body);
      last = { status: 200 };
    } catch (e) {
      last = { status: e.status, error: e.kind ?? null, field: e.field ?? null, code: e.code ?? null };
    }
  }
  out.push(last);
}
process.stdout.write(JSON.stringify(out));
