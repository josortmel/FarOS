/* #1320 (Lienzo, orden de Pepe del 18-nov): la lupa. Busca en todo FarOS a la vez, con GET /api/search:
   tareas, preguntas (decisiones), workflows y trabajos de agentes, cada grupo con sus 10 más parecidos.
   Escritorio: un cuadro arriba en el centro (Ctrl+K o «/» lo abren). Móvil: pantalla completa, con el
   campo a 16 px para que iOS no haga zoom. Flechas para moverse, Intro abre, Esc cierra.
   Abrir un resultado lo decide app.js (onOpen): cada tipo vive en una superficie distinta. */
import { esc, humanError } from '../util.js';
import { STATUS_LABEL } from '../transitions.js';
import { t as tr } from '../i18n.js';

const LIMITE = 10;          // search.py: LIMIT por grupo
const ESPERA = 180;         // ms entre teclas antes de preguntar al daemon

const GRUPOS = [
  { k: 'tickets',   kind: 'ticket',   titulo: 'Tareas' },
  { k: 'decisions', kind: 'decision', titulo: 'Preguntas' },
  { k: 'workflows', kind: 'workflow', titulo: 'Workflows' },
  { k: 'jobs',      kind: 'job',      titulo: 'Agentes' },
];
const ESTADO_PREGUNTA = { pending: 'sin contestar', decided: 'decidida', deferred: 'aplazada' };
const ESTADO_WF = { active: 'activo', closed: 'cerrado', archived: 'archivado', cancelled: 'cancelado' };
const ESTADO_JOB = { active: 'activo', paused: 'en pausa', archived: 'archivado', draft: 'borrador' };
/* lo que pide algo de alguien va en naranja; lo demás, en gris */
const VIVO = new Set(['in_progress', 'pending', 'active']);

const ICONO = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.2"/><path d="M15.2 15.2 20 20"/></svg>';

let api = () => null;
let onOpen = async () => {};
let raiz, campo, lista, estado, boton;
let filas = [];            // [{ kind, item }] en el orden en que se pintan
let activa = -1;
let turno = 0;             // descarta respuestas viejas si se escribe deprisa
let espera = null;
let ultimaQ = '';

/* --------- resaltar las palabras buscadas, sin tildes ni mayúsculas (FTS5 unicode61 tampoco las mira) --------- */
const plano = (c) => {
  const n = c.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();
  return n.length === 1 ? n : c.toLowerCase();
};
const palabras = (q) => q.split(/\s+/).map(w => w.replace(/"/g, '')).filter(w => w.length > 1).slice(0, 8);

export function marcar(texto, q) {
  const s = String(texto ?? '');
  const ws = palabras(q).map(w => [...w].map(plano).join(''));
  if (!ws.length) return esc(s);
  const chars = [...s];
  const norm = chars.map(plano).join('');
  const on = new Array(chars.length).fill(false);
  for (const w of ws) {
    let i = norm.indexOf(w);
    while (i !== -1) { for (let j = i; j < i + w.length; j++) on[j] = true; i = norm.indexOf(w, i + w.length); }
  }
  let out = '', abierto = false;
  chars.forEach((c, i) => {
    if (on[i] && !abierto) { out += '<mark>'; abierto = true; }
    if (!on[i] && abierto) { out += '</mark>'; abierto = false; }
    out += esc(c);
  });
  return abierto ? out + '</mark>' : out;
}

/* --------- una fila por resultado --------- */
function fila(kind, it, q, n) {
  let titulo, meta, st, stTxt;
  if (kind === 'ticket') {
    titulo = it.title; st = it.status; stTxt = STATUS_LABEL[it.status] ?? it.status;
    meta = [it.owner, it.workflow_id ? tr('en un workflow') : null].filter(Boolean).join(' · ');
  } else if (kind === 'decision') {
    titulo = it.title; st = it.status; stTxt = tr(ESTADO_PREGUNTA[it.status] ?? it.status);
    meta = it.decided_by ? `${tr('decidió')} ${it.decided_by}` : '';
  } else if (kind === 'workflow') {
    titulo = it.name; st = it.status; stTxt = tr(ESTADO_WF[it.status] ?? it.status);
    meta = it.coordinator ? `${tr('coordina')} ${it.coordinator}` : '';
  } else {
    titulo = it.name; st = it.status; stTxt = tr(ESTADO_JOB[it.status] ?? it.status);
    meta = [it.harness, it.model].filter(Boolean).join(' · ');
  }
  const id = kind === 'workflow' ? '' : `#${it.id}`;
  return `<button type="button" class="lupa__row" role="option" tabindex="-1" id="lupa-opt-${n}" data-n="${n}" aria-selected="false">
      <span class="lupa__id">${esc(id)}</span>
      <span class="lupa__main"><span class="lupa__title">${marcar(titulo, q)}</span>
        <span class="lupa__foot"><span class="lupa__meta">${esc(meta)}</span><span class="lupa__st${VIVO.has(st) ? ' is-live' : ''}">${esc(stTxt ?? '')}</span></span></span>
    </button>`;
}

function pintar(res, q) {
  filas = [];
  let html = '';
  for (const g of GRUPOS) {
    const items = res?.[g.k] ?? [];
    if (!items.length) continue;
    const tope = items.length >= LIMITE ? `<span class="lupa__more">${tr('las 10 más parecidas')}</span>` : '';
    html += `<section class="lupa__group" data-k="${g.k}" aria-label="${esc(tr(g.titulo))}"><h3 class="lupa__gh">${esc(tr(g.titulo))} <span class="lupa__n">${items.length}</span>${tope}</h3>`;
    for (const it of items) { html += fila(g.kind, it, q, filas.length); filas.push({ kind: g.kind, item: it }); }
    html += '</section>';
  }
  lista.innerHTML = html;
  if (!filas.length) {
    decir(tr(`Nada con «${q}». Prueba con otra palabra o con menos palabras.`));
    activa = -1;
  } else {
    decir('');
    mover(0);
  }
  campo.setAttribute('aria-expanded', String(filas.length > 0));
}

function decir(txt, tipo = '') {
  estado.textContent = txt;
  estado.dataset.tipo = tipo;
  estado.hidden = !txt;
}

function vacio() {
  filas = []; activa = -1; lista.innerHTML = '';
  campo.removeAttribute('aria-activedescendant');
  campo.setAttribute('aria-expanded', 'false');
  decir(tr('Escribe una palabra: busca a la vez en tareas, preguntas, workflows y agentes.'), 'pista');
}

function mover(n) {
  if (!filas.length) return;
  activa = (n + filas.length) % filas.length;
  lista.querySelectorAll('.lupa__row').forEach(b => b.setAttribute('aria-selected', String(Number(b.dataset.n) === activa)));
  const b = lista.querySelector(`#lupa-opt-${activa}`);
  campo.setAttribute('aria-activedescendant', b.id);
  b.scrollIntoView({ block: 'nearest' });
}

async function buscar(q) {
  const mio = ++turno;
  raiz.classList.add('is-busy');
  try {
    const res = await api().search(q);
    if (mio !== turno) return;
    pintar(res, q);
  } catch (err) {
    if (mio !== turno) return;
    lista.innerHTML = ''; filas = []; activa = -1;
    decir(`${tr('No se pudo buscar:')} ${humanError(err)}`, 'error');
  } finally {
    if (mio === turno) raiz.classList.remove('is-busy');
  }
}

function alEscribir() {
  clearTimeout(espera);
  const q = campo.value.trim();
  if (q === ultimaQ) return;
  ultimaQ = q;
  if (!palabras(q).length) { turno++; raiz.classList.remove('is-busy'); vacio(); return; }
  espera = setTimeout(() => buscar(q), ESPERA);
}

async function abrirFila(n) {
  const f = filas[n];
  if (!f) return;
  cerrarLupa();
  await onOpen(f.kind, f.item);
}

/* --------- abrir y cerrar --------- */
export function abrirLupa() {
  if (!raiz.hidden) { campo.focus(); campo.select(); return; }
  raiz.hidden = false;
  document.body.classList.add('lupa-on');
  boton?.setAttribute('aria-expanded', 'true');
  campo.focus();
  campo.select();
  if (!campo.value.trim()) vacio();
}

export function cerrarLupa({ devolverFoco = false } = {}) {
  if (!raiz || raiz.hidden) return;
  raiz.hidden = true;
  document.body.classList.remove('lupa-on');
  boton?.setAttribute('aria-expanded', 'false');
  if (devolverFoco) (document.querySelector('.mob-lupa:not([hidden])') ?? boton)?.focus();
}

const escribiendo = (el) => el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));

function construir() {
  raiz = document.createElement('div');
  raiz.className = 'lupa';
  raiz.id = 'lupa';
  raiz.hidden = true;
  raiz.innerHTML = `
    <div class="lupa__scrim" data-cerrar></div>
    <div class="lupa__box" role="dialog" aria-modal="true" aria-label="${esc(tr('Buscar en FarOS'))}">
      <div class="lupa__field">
        ${ICONO}
        <input type="search" class="lupa__input" id="lupa-q" autocomplete="off" autocapitalize="off" spellcheck="false"
          enterkeyhint="search" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="lupa-list"
          placeholder="${esc(tr('Buscar en tareas, preguntas, workflows…'))}" aria-label="${esc(tr('Buscar en FarOS'))}">
        <span class="lupa__spin" aria-hidden="true"></span>
        <button type="button" class="lupa__close" data-cerrar>${esc(tr('Cerrar'))}</button>
      </div>
      <p class="lupa__state" role="status" aria-live="polite"></p>
      <div class="lupa__list" id="lupa-list" role="listbox" aria-label="${esc(tr('Resultados'))}"></div>
      <p class="lupa__keys" aria-hidden="true"><kbd>↑</kbd><kbd>↓</kbd> ${esc(tr('moverse'))} <kbd>Intro</kbd> ${esc(tr('abrir'))} <kbd>Esc</kbd> ${esc(tr('cerrar'))}</p>
    </div>`;
  document.body.appendChild(raiz);
  campo = raiz.querySelector('.lupa__input');
  lista = raiz.querySelector('.lupa__list');
  estado = raiz.querySelector('.lupa__state');
}

export function initLupa(opts) {
  api = opts.api;
  onOpen = opts.onOpen;
  boton = document.getElementById('btn-lupa');
  construir();
  boton?.addEventListener('click', (e) => { e.stopPropagation(); abrirLupa(); });

  campo.addEventListener('input', alEscribir);
  campo.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); mover(activa + 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); mover(activa - 1); }
    else if (e.key === 'Enter') {
      e.preventDefault();
      /* si aún no ha contestado el daemon, se busca ya y se espera */
      if (espera && campo.value.trim() !== '' && !filas.length) { clearTimeout(espera); espera = null; buscar(campo.value.trim()); return; }
      if (activa >= 0) abrirFila(activa);
    }
  });
  lista.addEventListener('click', (e) => {
    const b = e.target.closest('.lupa__row');
    if (b) abrirFila(Number(b.dataset.n));
  });
  lista.addEventListener('pointermove', (e) => {
    const b = e.target.closest('.lupa__row');
    if (b && Number(b.dataset.n) !== activa) mover(Number(b.dataset.n));
  });
  raiz.addEventListener('click', (e) => { if (e.target.closest('[data-cerrar]')) cerrarLupa({ devolverFoco: true }); });
  /* el foco no se escapa del cuadro: Tab va del campo a «Cerrar» y vuelve */
  raiz.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab') return;
    const cerrar = raiz.querySelector('.lupa__close');
    const visibles = [campo, cerrar].filter(el => el.offsetParent !== null);
    if (visibles.length < 2) { e.preventDefault(); campo.focus(); return; }
    const i = visibles.indexOf(document.activeElement);
    e.preventDefault();
    visibles[(i + (e.shiftKey ? -1 : 1) + visibles.length) % visibles.length].focus();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !raiz.hidden) { e.preventDefault(); e.stopPropagation(); cerrarLupa({ devolverFoco: true }); return; }
    const atajo = (e.key === 'k' || e.key === 'K') && (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey;
    const barra = e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey && !escribiendo(document.activeElement)
      && document.getElementById('modal-root')?.hidden !== false;
    if (atajo || (barra && raiz.hidden)) { e.preventDefault(); abrirLupa(); }
  }, true);
}

export const LUPA_ICONO = ICONO;
