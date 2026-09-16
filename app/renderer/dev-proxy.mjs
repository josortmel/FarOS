/* dev-proxy.mjs — desarrollo SIN tocar la postura CORS del daemon.
   Sirve el renderer estático Y reenvía /api/* al daemon (127.0.0.1:8756)
   desde el MISMO origin → el navegador no necesita CORS.
   Uso:  node dev-proxy.mjs  →  http://127.0.0.1:8902/index.html?base=&token=…  */

import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = fileURLToPath(new URL('.', import.meta.url));
const DAEMON = { host: '127.0.0.1', port: 8756 };
const MIME = {
  '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript',
  '.json': 'application/json', '.svg': 'image/svg+xml', '.woff2': 'font/woff2',
  '.png': 'image/png',
};

http.createServer(async (req, res) => {
  const [path] = req.url.split('?');

  if (path.startsWith('/api/')) {
    // proxy transparente al daemon (streaming — el SSE pasa entero)
    const up = http.request(
      { ...DAEMON, path: req.url, method: req.method, headers: { ...req.headers, host: `${DAEMON.host}:${DAEMON.port}` } },
      (upRes) => { res.writeHead(upRes.statusCode, upRes.headers); upRes.pipe(res); }
    );
    up.on('error', () => { res.writeHead(502); res.end('daemon no responde'); });
    req.pipe(up);
    return;
  }

  try {
    const file = normalize(join(ROOT, path === '/' ? 'index.html' : path));
    if (!file.startsWith(ROOT)) throw new Error('fuera de raíz');
    const body = await readFile(file);
    res.writeHead(200, {
      'Content-Type': MIME[extname(file)] ?? 'application/octet-stream',
      'Cache-Control': 'no-store', // dev: nunca módulos rancios
    });
    res.end(body);
  } catch {
    res.writeHead(404); res.end('not found');
  }
}).listen(8902, '127.0.0.1', () => console.log('dev-proxy en http://127.0.0.1:8902'));
