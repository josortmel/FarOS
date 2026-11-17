/* ============================================================
   library.js — Biblioteca (#851, Spec v2 §7). Lo producido en la
   casa, por clases, con búsqueda y un visor.

   SE PINTA SOLA, fuera de render(): cada aviso del daemon repinta la
   superficie activa, y aquí eso recargaría el documento a media
   lectura. El estado vive en este módulo; el visor no se toca hasta
   que cambia lo que se lee.

   EL VISOR (estudio del 4-nov, medido en Chromium, Firefox y WebKit):
   · un iframe no manda el token y el frame-src de la app no deja
     cargar el daemon, así que el contenido entra por fetch + srcdoc;
   · en srcdoc la CSP de la respuesta NO llega: se repite en una meta,
     la primera del head;
   · un ancla «#x» se resuelve contra la URL de la app y metería la
     app dentro del visor; el <base> de MDN lo bloquea el base-uri
     'none' heredado, así que se reescribe a about:srcdoc#x.
   ============================================================ */

import { api } from '../api.js';
import { applyI18n, getLang, locale, t as tr } from '../i18n.js';
import { getState } from '../store.js';
import { esc, fmtDate } from '../util.js';
import { toast } from './toast.js';

const CLASES = [
  ['revista', 'Revista'], ['lectura', 'Lecturas'], ['faro', 'Faro'],
  ['resultado', 'Resultados'], ['documentacion', 'Documentación'],
];
const nombreClase = c => tr(CLASES.find(([k]) => k === c)?.[1] ?? c);
const LIMITE = 500;
const SANDBOX = 'allow-popups allow-popups-to-escape-sandbox';
const CSP = "default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'";
const RUTA = /^#\/biblioteca(?:\/(?:revista\/([^/]+)|(\d+)))?$/;
const ANCHO = matchMedia('(min-width: 900px)');
const FUERA = '<svg class="lib__out" viewBox="0 0 24 24" aria-hidden="true"><path d="M14 5h5v5M19 5l-8 8M10 6H6a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1v-4"/></svg>';

const $ = s => document.querySelector(s);
const attr = s => esc(s).replace(/"/g, '&quot;');
const enlaceFuera = u => (/^https:\/\//.test(u ?? '') ? u : null);

let montada = false;
let clase = 'revista';
let q = '';
let abierto = null;       // la entrada que se está leyendo
let pedidoLista = 0;      // la última respuesta manda: dos pulsaciones seguidas no se pisan
let pedidoVisor = 0;
let espera = null;
let idioma = getLang();
let cargados = [];        // lo que hay en la lista, página a página
let hayMas = false;

export function rutaBiblioteca() {
  const m = RUTA.exec(location.hash);
  if (!m) return null;
  return { dia: m[1] ? decodeURIComponent(m[1]) : null, id: m[2] ? Number(m[2]) : null };
}

/* El estilo de lectura va en los .md (el daemon los envuelve con un estilo mínimo, sin color: en
   oscuro salían en una plancha blanca) y en lo que llega sin estilo. Lo maquetado se deja como está.
   Con los valores ya resueltos: dentro del visor no existen los tokens y font-src no deja cargar Geist. */
function estiloLectura(d) {
  const cs = getComputedStyle(document.documentElement);
  const v = n => cs.getPropertyValue(n).trim();
  const s = d.createElement('style');
  s.textContent = `html{color-scheme:${document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'};background:${v('--color-bg')}}
body{max-width:68ch;margin:0 auto;padding:32px 24px 64px;color:${v('--color-text')};font:18px/1.65 ui-sans-serif,system-ui,sans-serif;overflow-wrap:anywhere}
h1,h2,h3{line-height:1.25;margin:1.6em 0 .5em}h1{font-size:1.6em;margin-top:0}h2{font-size:1.3em}h3{font-size:1.1em}
a{color:${v('--color-orange-deep')};text-underline-offset:.15em}
ul,ol{padding-left:1.4em}li{margin:.3em 0}img{max-width:100%;height:auto}
pre{overflow-x:auto;padding:12px 14px;background:${v('--color-bg-well')};border-radius:8px;font-size:.85em}
code{font-size:.9em}table{border-collapse:collapse;display:block;overflow-x:auto}th,td{padding:6px 10px;border:1px solid ${v('--color-rule')}}`;
  return s;
}

const esTexto = it => /\.md$/i.test(it?.ruta ?? '') && !it?.html_publicado;

export function prepararVisor(html, it) {
  const d = new DOMParser().parseFromString(html, 'text/html');
  if (esTexto(it) || !d.querySelector('style, link[rel="stylesheet"]')) d.head.append(estiloLectura(d));
  const m = d.createElement('meta');
  m.httpEquiv = 'Content-Security-Policy';
  m.content = CSP;
  d.head.prepend(m);
  for (const a of d.querySelectorAll('a[href^="#"]')) a.setAttribute('href', `about:srcdoc${a.getAttribute('href')}`);
  /* un enlace externo sin target navega dentro del visor y muere contra el frame-src de la app */
  for (const a of d.querySelectorAll('a[href^="http:" i], a[href^="https:" i]')) { a.target = '_blank'; a.rel = 'noopener'; }
  return `<!doctype html>${d.documentElement.outerHTML}`;
}

function montar() {
  if (montada) return;
  montada = true;
  const root = $('#lib-root');
  root.innerHTML = `
    <div class="lib__bar">
      <nav class="views lib__tabs" aria-label="Clases de la Biblioteca">
        ${CLASES.map(([k, n]) => `<button type="button" class="views__tab" data-clase="${k}">${n}</button>`).join('')}
      </nav>
      <input class="lib__search" id="lib-q" type="search" placeholder="Buscar en toda la Biblioteca" aria-label="Buscar en toda la Biblioteca" autocomplete="off">
    </div>
    <div class="lib__body">
      <div class="lib__list" id="lib-list"></div>
      <section class="lib__read" id="lib-read" aria-label="Documento"></section>
    </div>`;
  root.querySelector('.lib__tabs').addEventListener('click', e => {
    const b = e.target.closest('[data-clase]');
    if (!b) return;
    clase = b.dataset.clase;
    q = '';
    $('#lib-q').value = '';
    cargarLista();
  });
  $('#lib-q').addEventListener('input', e => {
    clearTimeout(espera);
    espera = setTimeout(() => { q = e.target.value.trim(); cargarLista(); }, 250);
  });
  $('#lib-list').addEventListener('click', e => {
    if (e.target.closest('[data-reintentar]')) cargarLista();
    if (e.target.closest('[data-mas]')) cargarMas();
  });
  applyI18n(root);
}

function marcarPestana() {
  document.querySelectorAll('.lib__tabs [data-clase]').forEach(b => {
    b.classList.toggle('is-active', !q && b.dataset.clase === clase);
  });
  document.querySelector('.lib__tabs .is-active')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

function fila(i) {
  const fuera = i.interactivo ? enlaceFuera(i.abrir) : null;
  const abre = fuera ? `href="${attr(fuera)}" target="_blank" rel="noopener"` : `href="#/biblioteca/${i.id}"`;
  const meta = q ? nombreClase(i.clase) : i.proyecto;
  return `<a class="lib__row${abierto?.id === i.id ? ' is-open' : ''}" data-id="${i.id}" ${abre}${abierto?.id === i.id ? ' aria-current="true"' : ''}>
    <time>${esc(fmtDate(i.fecha))}</time>
    <span class="lib__row-main"><span class="lib__row-title">${esc(i.titulo)}</span>${meta ? `<span class="lib__row-meta">${esc(meta)}</span>` : ''}</span>
    ${fuera ? `${FUERA}<span class="lib__sr">${tr('Se abre en otra pestaña')}</span>` : ''}
  </a>`;
}

function porMeses(items) {
  const mes = new Intl.DateTimeFormat(locale(), { month: 'long', year: 'numeric' });
  let actual = null;
  return items.map(i => {
    const m = i.fecha ? mes.format(new Date(`${i.fecha.slice(0, 10)}T00:00:00`)) : tr('Sin fecha');
    const titulo = m !== actual ? `<h2 class="lib__month">${esc(m)}</h2>` : '';
    actual = m;
    return titulo + fila(i);
  }).join('');
}

const filtro = (offset = 0) => ({ ...(q ? { q } : { clase }), limite: LIMITE, offset });

function pintarLista() {
  const mas = hayMas ? `<button type="button" class="btn btn--ghost lib__more" data-mas>${tr('Ver más antiguos')}</button>` : '';
  $('#lib-list').innerHTML = (q ? cargados.map(fila).join('') : porMeses(cargados)) + mas;
}

async function cargarMas() {
  const n = pedidoLista;
  let items;
  try {
    items = await api().libraryItems(filtro(cargados.length));
  } catch {
    toast(tr('No se ha podido leer la Biblioteca.'), 'reject');
    return;
  }
  if (n !== pedidoLista) return;
  cargados = cargados.concat(items);
  hayMas = items.length === LIMITE;
  pintarLista();
}

async function cargarLista() {
  const n = ++pedidoLista;
  const lista = $('#lib-list');
  marcarPestana();
  lista.innerHTML = `<p class="lib__note">Cargando…</p>`;
  applyI18n(lista);
  let items;
  try {
    items = await api().libraryItems(filtro());
  } catch {
    if (n !== pedidoLista) return;
    lista.innerHTML = `<div class="lib__note"><p>No se ha podido leer la Biblioteca.</p><button type="button" class="btn btn--ghost" data-reintentar>Reintentar</button></div>`;
    applyI18n(lista);
    return;
  }
  if (n !== pedidoLista) return;
  cargados = items;
  hayMas = items.length === LIMITE;
  if (!items.length) {
    lista.innerHTML = `<p class="lib__note">${q ? 'Nada con ese texto.' : 'Todavía no hay nada aquí.'}</p>`;
    applyI18n(lista);
    return;
  }
  pintarLista();
  /* en ancho no se deja el visor vacío: se abre el más reciente, sin escribirlo en la ruta */
  if (!abierto && !q && ANCHO.matches && !rutaBiblioteca()?.id && !rutaBiblioteca()?.dia) leer(items[0]);
}

function cabecera(it) {
  const bits = [it.fecha && fmtDate(it.fecha), it.clase && nombreClase(it.clase), it.proyecto].filter(Boolean).map(esc).join(' · ');
  return `<header class="lib__read-head">
    <a class="lib__back" href="#/biblioteca" aria-label="${tr('Volver')}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg></a>
    <div class="lib__read-title"><h1>${esc(it.titulo)}</h1><p>${bits}</p></div>
  </header>`;
}

function marcarFila() {
  document.querySelectorAll('.lib__row').forEach(r => {
    const open = Number(r.dataset.id) === abierto?.id;
    r.classList.toggle('is-open', open);
    if (open) r.setAttribute('aria-current', 'true'); else r.removeAttribute('aria-current');
  });
}

/* Los avisos se pintan en español y los traduce applyI18n: así guarda el original y vuelven
   al cambiar de idioma. Con tr() se quedaban en el idioma en que nacieron. */
function aviso(texto, extra = '') {
  return `<div class="lib__note lib__note--read"><p>${texto}</p>${extra}</div>`;
}

async function leer(it) {
  const n = ++pedidoVisor;
  abierto = it;
  marcarFila();
  $('#lib-root').dataset.leyendo = '';
  const read = $('#lib-read');
  read.innerHTML = cabecera(it) + aviso('Abriendo…');
  applyI18n(read);
  let r;
  try {
    r = await api().libraryContent(it.id);
  } catch {
    if (n === pedidoVisor) {
      read.innerHTML = cabecera(it) + aviso('Este documento no se puede abrir: ha cambiado de sitio o ya no está.');
      applyI18n(read);
    }
    return;
  }
  if (n !== pedidoVisor) return;
  if (typeof r !== 'string') {
    const fuera = enlaceFuera(r.abrir);
    read.innerHTML = cabecera(it) + (fuera
      ? aviso('Este informe tiene partes interactivas y se abre aparte.', `<a class="btn btn--primary" href="${attr(fuera)}" target="_blank" rel="noopener">Abrir en otra pestaña</a>`)
      : aviso('Este informe no está publicado y no se puede abrir desde aquí. Sigue en su carpeta.', `<code class="lib__path">${esc(it.ruta)}</code>`));
    applyI18n(read);
    return;
  }
  const f = document.createElement('iframe');
  f.setAttribute('sandbox', SANDBOX);
  f.className = 'lib__doc';
  f.title = it.titulo;
  f.srcdoc = prepararVisor(r, it);
  read.innerHTML = cabecera(it);
  read.append(f);
}

function cerrar() {
  pedidoVisor++;
  abierto = null;
  marcarFila();
  delete $('#lib-root').dataset.leyendo;
  $('#lib-read').innerHTML = '';
}

async function aplicarRuta() {
  const r = rutaBiblioteca();
  if (!r) return;
  if (!r.id && !r.dia) {
    if (!ANCHO.matches) cerrar();
    return;
  }
  if (r.id && abierto?.id === r.id) return;
  let it;
  try {
    it = r.dia ? await api().libraryRevista(r.dia) : await api().libraryItem(r.id);
  } catch {
    abierto = null;
    $('#lib-root').dataset.leyendo = '';
    $('#lib-read').innerHTML = cabecera({ titulo: 'Biblioteca' }) + aviso('Este documento no se puede abrir: ha cambiado de sitio o ya no está.');
    applyI18n($('#lib-read'));
    return;
  }
  /* con una búsqueda abierta la lista no es de una clase: abrir un resultado no la toca */
  if (!q && it.clase !== clase) { clase = it.clase; cargarLista(); }
  await leer(it);
}

export function abrirBiblioteca() {
  const fresca = !montada;
  montar();
  /* al volver de otra superficie, lo que se leía sigue en su sitio y sin recargar */
  if (!rutaBiblioteca()) history.replaceState(null, '', abierto ? `#/biblioteca/${abierto.id}` : '#/biblioteca');
  if (fresca) cargarLista();
  aplicarRuta();
}

/* Meses, fechas y clases se pintan ya traducidos: applyI18n no los alcanza. El visor no se toca. */
export function refrescarIdioma() {
  if (!montada || idioma === getLang()) return;
  idioma = getLang();
  if (cargados.length) pintarLista();
  const cab = $('.lib__read-head');
  if (abierto && cab) cab.outerHTML = cabecera(abierto);
}

export function salirDeBiblioteca() {
  if (rutaBiblioteca()) history.replaceState(null, '', location.pathname + location.search);
}

function irASuperficie(k, surface) {
  ($(`.mob-tabs [data-k="${k}"]`) ?? $(`#surface-tabs [data-surface="${surface}"]`))?.click();
}

/* Las rutas mandan: enlace profundo en frío, Atrás del navegador y filas (son enlaces de verdad) */
export function seguirRutas() {
  addEventListener('hashchange', () => {
    if (rutaBiblioteca()) {
      if (getState().surface !== 'library') irASuperficie('lib', 'library');
      else aplicarRuta();
    } else if (getState().surface === 'library') irASuperficie('board', 'board');
  });
  if (rutaBiblioteca() && getState().surface !== 'library') irASuperficie('lib', 'library');
}
