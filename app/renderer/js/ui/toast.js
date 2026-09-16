import { t as t2 } from '../i18n.js';  // #262
/* toast.js — avisos efímeros. El rechazo del motor SIEMPRE se ve. */

const root = () => document.getElementById('toasts');

export function toast(text, kind = 'info', ms = 5000) {
  const el = document.createElement('div');
  el.className = `toast${kind === 'reject' ? ' toast--reject' : kind === 'ok' ? ' toast--ok' : ''}`;
  /* #291: los toasts NO pasaban por el catalogo. applyI18n recorre el DOM ya
     pintado, y un toast nace despues y vive cinco segundos: nunca lo alcanza.
     Asi que TODOS los avisos de la app salian en espanol con la interfaz en
     ingles — 5 fijos y 34 con interpolacion. toastReject ya traducia su
     etiqueta; toast no traducia nada.
     t2() es seguro aqui: si la cadena no esta en el catalogo devuelve la
     original, asi que los interpolados que no cuadren se quedan como estaban. */
  el.textContent = t2(text);
  root().appendChild(el);
  setTimeout(() => el.remove(), ms);
}

export function toastReject(reason) {
  const el = document.createElement('div');
  el.className = 'toast toast--reject';
  const b = document.createElement('b');
  b.textContent = t2('El motor lo rechaza:') + ' ';  // #262/#264
  el.append(b, document.createTextNode(reason));
  root().appendChild(el);
  setTimeout(() => el.remove(), 7000);
}
