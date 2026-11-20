// #402: qué puede hacer la ventana fuera de su index.html. Módulo aparte (sin
// require("electron")) para poder probarlo con `node --test`; main.js lo cablea.
// La ventana lleva el preload (token + openPath): cualquier página a la que
// navegue lo heredaría. Hoy nada del renderer abre enlaces; esto es para que
// un texto que algún día se pinte como enlace no se lleve la ventana.

// Solo la propia index.html (con query o hash).
function puedeNavegar(url, indexUrl) {
  const sinResto = String(url).split(/[?#]/)[0];
  return sinResto === indexUrl.split(/[?#]/)[0];
}

// window.open / target=_blank: nunca una ventana nueva de la app; un https va
// al navegador del sistema (shell.openExternal) y lo demás se descarta.
function abrirFuera(url) {
  try {
    return new URL(url).protocol === 'https:';
  } catch {
    return false;
  }
}

// connect-src de la CSP no puede fijar el puerto (FAROS_PORT lo cambia y los
// daemons de prueba usan otros), así que aquí: a 127.0.0.1/localhost solo se
// habla con el origen del daemon. EcoDB, el relay y el resto quedan fuera.
function peticionLocalPermitida(url, baseUrl) {
  let u;
  try {
    u = new URL(url);
  } catch {
    return true;
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:' && u.protocol !== 'ws:' && u.protocol !== 'wss:') return true;
  if (u.hostname !== '127.0.0.1' && u.hostname !== 'localhost') return true;
  return u.origin === new URL(baseUrl).origin;
}

module.exports = { puedeNavegar, abrirFuera, peticionLocalPermitida };
