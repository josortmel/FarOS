/* ============================================================
   FarOS — capa de móvil (15-oct-2026, Lienzo; FarOS #517)
   No toca app.js: pulsa los mismos botones que el escritorio
   (superficies, modos) y añade lo que el móvil necesita:
   barra de abajo, carril de estados del tablero y Preguntas
   como superficie propia. Solo se activa hasta 540 px (la ventana
   de escritorio baja hasta 560) y en tabletas táctiles hasta 1366
   (#773, iPad): la misma consulta que mobile.css.
   ============================================================ */

import { api } from './api.js';
import { renderDecisionsPane, resaltarPregunta } from './ui/decisions.js';
import { toast, toastReject } from './ui/toast.js';
import { getState } from './store.js';
import { t as tr } from './i18n.js';

/* 16-oct: quien decide desde el móvil es el agente de la app (el dueño de la casa que da el
   daemon), como en el escritorio. Estaba escrito a mano con el nombre del dueño, y eso rompe en
   cualquier otra casa. */
const quien = () => getState().agent ?? getState().meta?.owner;

/* #698: desde dónde se decide. La capa de móvil también se enciende en una ventana del escritorio
   estrecha; dentro de la app de escritorio (Electron, que trae window.agenticos) sigue siendo
   «desktop». Fuera de ella, la capa de móvil es el navegador del teléfono por el puente. */
const superficie = () => (window.agenticos ? 'desktop' : 'mobile');

const MQ = matchMedia('(max-width: 540px), (max-height: 500px) and (pointer: coarse), (hover: none) and (pointer: coarse) and (max-width: 1366px)');
/* iPad en horizontal: la misma consulta que el segundo bloque de tableta de mobile.css */
const ANCHO = matchMedia('(hover: none) and (pointer: coarse) and (min-width: 1000px) and (max-width: 1366px) and (min-height: 501px)');
const $ = (s, r = document) => r.querySelector(s);
const svg = d => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;

const ICON = {
  board: svg('<rect x="3.5" y="4" width="5" height="16" rx="1.5"/><rect x="9.5" y="4" width="5" height="11" rx="1.5"/><rect x="15.5" y="4" width="5" height="7" rx="1.5"/>'),
  cal: svg('<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 9.5h16M8.5 3v4M15.5 3v4M8 13h3M8 16.5h6"/>'),
  dec: svg('<path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 1.5H10l-4 4v-4h-.5A1.5 1.5 0 0 1 4 14.5z"/><path d="M10 8.6a2 2 0 1 1 2.6 1.9c-.4.2-.6.5-.6.9v.3M12 13.4v.1"/>'),
  wf: svg('<circle cx="6" cy="6" r="2.2"/><circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="12" r="2.2"/><path d="M6 8.2v7.6M8.2 6.6c4 .6 6.8 2.4 7.8 4.2M8.2 17.4c4-.6 6.8-2.4 7.8-4.2"/>'),
  ag: svg('<rect x="5" y="7" width="14" height="11" rx="3"/><path d="M12 3.5V7M9.5 12v1.5M14.5 12v1.5M2.5 12.5v2M21.5 12.5v2"/>'),
  plus: svg('<path d="M12 5v14M5 12h14"/>'),
  lib: svg('<path d="M12 6.5C10 5 7.5 4.5 4 4.5v14c3.5 0 6 .5 8 2 2-1.5 4.5-2 8-2v-14c-3.5 0-6 .5-8 2zM12 6.5v14"/>'),
  more: svg('<circle cx="5.5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="18.5" cy="12" r="1.3"/>'),
  lupa: svg('<circle cx="10.5" cy="10.5" r="6.2"/><path d="M15.2 15.2 20 20"/>'),   // #1320
};

const TABS = [
  { k: 'board', label: 'Tablero', icon: ICON.board },
  { k: 'cal', label: 'Calendario', icon: ICON.cal },
  { k: 'dec', label: 'Preguntas', icon: ICON.dec },
  { k: 'wf', label: 'Workflows', icon: ICON.wf },
  { k: 'ag', label: 'Agentes', icon: ICON.ag },
  { k: 'lib', label: 'Biblioteca', icon: ICON.lib },
];
const STATE_NAMES = { proposed: 'Propuestas', accepted: 'Aceptadas', in_progress: 'En curso', done: 'Hechas', verified: 'Verificadas' };

let current = 'board';
let decisions = null;

function build() {
  /* dos botones en la cabecera: nueva tarea y «más» (ajustes, tema, idioma, verificar) */
  const plus = document.createElement('button');
  plus.className = 'mob-iconbtn is-primary mob-only'; plus.type = 'button';
  plus.setAttribute('aria-label', 'Nueva tarea'); plus.innerHTML = ICON.plus;
  plus.addEventListener('click', () => $('#btn-create').click());
  const more = document.createElement('button');
  more.className = 'mob-iconbtn mob-only'; more.type = 'button';
  more.setAttribute('aria-label', 'Más'); more.setAttribute('aria-expanded', 'false'); more.innerHTML = ICON.more;
  more.addEventListener('click', e => {
    e.stopPropagation();
    const open = $('.topbar__actions').classList.toggle('is-open');
    more.setAttribute('aria-expanded', String(open));
  });
  document.addEventListener('click', e => {
    if (!e.target.closest('.topbar__actions')) { $('.topbar__actions').classList.remove('is-open'); more.setAttribute('aria-expanded', 'false'); }
  });
  /* #1320: la lupa, a la izquierda de «+»: abre la misma búsqueda que el escritorio, a pantalla completa */
  const lupa = document.createElement('button');
  lupa.className = 'mob-iconbtn mob-lupa mob-only'; lupa.type = 'button';
  lupa.setAttribute('aria-label', tr('Buscar en FarOS')); lupa.setAttribute('aria-haspopup', 'dialog'); lupa.innerHTML = ICON.lupa;
  lupa.addEventListener('click', e => { e.stopPropagation(); $('#btn-lupa').click(); });
  $('.topbar').append(lupa, plus, more);
  /* #851: en el teléfono la Biblioteca vive en «Más»; en la tableta tiene su pestaña abajo */
  const lib = document.createElement('button');
  lib.className = 'btn btn--ghost mob-only'; lib.id = 'mob-lib'; lib.type = 'button'; lib.textContent = 'Biblioteca';
  lib.addEventListener('click', () => { $('.topbar__actions').classList.remove('is-open'); go('lib'); });
  $('.topbar__actions').prepend(lib);

  /* carril de estados del tablero */
  const states = document.createElement('nav');
  states.className = 'mob-states mob-only'; states.id = 'mob-states'; states.setAttribute('aria-label', 'Estado');
  $('#board').before(states);
  states.addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    const col = $('#board').children[Number(b.dataset.i)];
    col?.scrollIntoView({ behavior: 'smooth', inline: 'start', block: 'nearest' });
  });
  $('#board').addEventListener('scroll', markState, { passive: true });

  /* carril de personas del calendario */
  const lanes = document.createElement('nav');
  lanes.className = 'mob-lanes mob-only'; lanes.id = 'mob-lanes'; lanes.hidden = true; lanes.setAttribute('aria-label', 'Persona');
  $('#cal-root').before(lanes);
  lanes.addEventListener('click', e => { const b = e.target.closest('button'); if (b) { lane = b.dataset.n; syncLanes(); } });
  new MutationObserver(() => { if (current === 'cal') syncLanes(); }).observe($('#cal-root'), { childList: true, subtree: true });

  /* Preguntas: superficie propia, con las de toda la casa */
  const dec = document.createElement('main');
  dec.className = 'mob-dec'; dec.id = 'mob-dec'; dec.hidden = true; dec.setAttribute('aria-label', 'Preguntas');
  $('#ag-root').after(dec);

  /* barra de abajo */
  const nav = document.createElement('nav');
  nav.className = 'mob-tabs mob-only'; nav.setAttribute('aria-label', 'Secciones');
  nav.innerHTML = TABS.map(t => `<button type="button" data-k="${t.k}">${t.icon}${t.label}${t.k === 'dec' ? '<span class="mob-badge" id="mob-badge"></span>' : ''}</button>`).join('');
  nav.addEventListener('click', e => { const b = e.target.closest('button'); if (b) go(b.dataset.k); });
  document.body.append(nav);

  /* el tablero se repinta solo (SSE, filtros): el carril lo sigue */
  new MutationObserver(syncStates).observe($('#board'), { childList: true });
  panelMobile();
}

/* ---------- Ficha: acciones fijas abajo y descripción plegada ---------- */
function panelMobile() {
  const panel = document.getElementById('panel');
  let bar = document.getElementById('mob-panelbar');
  if (!bar) { bar = document.createElement('div'); bar.id = 'mob-panelbar'; bar.className = 'mob-panelbar mob-only'; bar.hidden = true; document.body.append(bar); }
  const sync = () => {
    const open = !panel.hidden && panel.classList.contains('is-open');
    const acts = panel.querySelector('.panel__actions');
    if (acts) { bar.replaceChildren(acts); }            /* se mueve el nodo: sus botones siguen con sus manejadores */
    bar.hidden = !open || !bar.querySelector('.panel__actions');
    const desc = panel.querySelector('.panel__desc');
    if (desc && !desc.dataset.mob && desc.scrollHeight > 160) {
      desc.dataset.mob = '1'; desc.classList.add('mob-cut');
      const more = document.createElement('button'); more.type = 'button'; more.className = 'mob-more'; more.textContent = tr('Leer todo');
      more.addEventListener('click', () => { const cut = desc.classList.toggle('mob-cut'); more.textContent = tr(cut ? 'Leer todo' : 'Recoger'); });
      desc.after(more);
    }
  };
  new MutationObserver(sync).observe(panel, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'hidden'] });
}

function syncStates() {
  const cols = [...$('#board').children].filter(c => c.classList.contains('col'));
  $('#mob-states').innerHTML = cols.map((c, i) => {
    const st = c.dataset.status ?? c.dataset.state ?? '';
    const name = (STATE_NAMES[st] && tr(STATE_NAMES[st])) ?? c.querySelector('.col__title')?.textContent.trim() ?? `Columna ${i + 1}`;
    const n = c.querySelector('.col__count')?.textContent.trim() ?? c.querySelectorAll('.card').length;
    return `<button type="button" data-i="${i}">${name} <b>${n}</b></button>`;
  }).join('');
  markState();
}

function markState() {
  /* #773: en el iPad caben dos o tres columnas; se marcan las que se ven, no solo la primera */
  const board = $('#board'), cols = [...board.children].filter(c => c.classList.contains('col'));
  const paso = cols.length > 1 ? cols[1].offsetLeft - cols[0].offsetLeft : (board.clientWidth || 1);
  const i = Math.round(board.scrollLeft / (paso || 1));
  const n = Math.max(1, Math.round((board.clientWidth || 1) / (paso || 1)));
  $('#mob-states').querySelectorAll('button').forEach((b, j) => b.setAttribute('aria-current', String(j >= i && j < i + n)));
  $('#mob-states').querySelector('[aria-current="true"]')?.scrollIntoView({ inline: 'nearest', block: 'nearest' });
}

async function loadDecisions() {
  try { decisions = await api().decisions(null); } catch { decisions = []; }
  const open = decisions.filter(d => d.status !== 'decided').length;
  $('#mob-badge').textContent = open || '';
  if (current === 'dec') renderDec();
}

let foco = null;   // #1320: la pregunta que se abrió desde la lupa
function renderDec() {
  const pane = $('#mob-dec'); pane.innerHTML = '';
  const open = (decisions ?? []).filter(d => d.status !== 'decided');
  pane.insertAdjacentHTML('beforeend', `<div class="mob-dec__head"><h1>${tr('Preguntas')}</h1><span>${tr(`${open.length} sin contestar`)}</span></div>`);
  /* las decididas, solo las 5 últimas: en el móvil importa lo que falta */
  const done = (decisions ?? []).filter(d => d.status === 'decided').sort((a, b) => (b.decided_at ?? '').localeCompare(a.decided_at ?? '')).slice(0, 5);
  const visto = (decisions ?? []).find(d => d.id === foco);
  const lista = [...open, ...done];
  if (visto && !lista.includes(visto)) lista.push(visto);
  renderDecisionsPane(pane, { wfDecisions: lista, workflow: { name: tr('la casa') } }, {
    buscarVigentes: (tema, superadas) => api().decisionsVigentes(tema, superadas),   // #919
    onDecide: async (d, { option, decision, rationale, ...extra }) => {   // extra: #919
      try {
        await api().decide(d.id, { agent: quien(), option, decision, rationale, surface: superficie(), ...extra });
        toast(tr(`Decidida: «${option ?? decision}». Registrada con su porqué.`), 'ok');
      } catch (err) { toastReject(err?.body?.reason ?? String(err)); }
      await loadDecisions(); renderDec();
    },
    onDefer: async (d, until) => {
      try {
        await api().deferDecision(d.id, { agent: quien(), until, note: 'desde el móvil' });
        toast(until ? `${tr('Aplazada hasta')} ${until}.` : tr('Aplazada.'), 'ok');
      } catch (err) { toastReject(err?.body?.reason ?? String(err)); }
      await loadDecisions(); renderDec();
    },
  });
}

/* la pestaña de abajo y Preguntas, sin tocar las superficies (la lupa ya las ha cambiado, #1320) */
function marcar(k) {
  current = k;
  document.querySelectorAll('.mob-tabs button').forEach(b => b.dataset.k === k ? b.setAttribute('aria-current', 'page') : b.removeAttribute('aria-current'));
  const isDec = k === 'dec';
  $('#mob-dec').hidden = !isDec;
  document.body.classList.toggle('mob-dec-on', isDec);   /* las superficies del escritorio se apartan por CSS */
  $('#mob-states').hidden = k !== 'board';
  $('#mob-lanes').hidden = k !== 'cal';
}

function go(k) {
  marcar(k);
  if (k === 'dec') { renderDec(); return; }
  const surface = { board: 'board', cal: 'board', wf: 'workflow', ag: 'agents', lib: 'library' }[k];
  document.querySelector(`#surface-tabs [data-surface="${surface}"]`)?.click();
  if (k === 'board' || k === 'cal') document.querySelector(`#mode-tabs [data-mode="${k === 'cal' ? 'calendar' : 'kanban'}"]`)?.click();
  if (k === 'board') { syncStates(); firstUseful(); }
  if (k === 'cal') syncLanes();
}

/* #1320: lo que abre la lupa en el móvil */
window.addEventListener('faros:superficie', e => { if (document.querySelector('.mob-tabs')) marcar(e.detail.k); });
window.addEventListener('faros:pregunta', async e => {
  if (!document.querySelector('.mob-tabs')) return;
  foco = e.detail.id;
  if (!decisions?.some(d => d.id === foco)) await loadDecisions();
  go('dec');
  const id = foco; foco = null;
  requestAnimationFrame(() => resaltarPregunta($('#mob-dec'), id));
});

/* al entrar al tablero, la primera columna con algo: En curso si tiene, si no la primera llena */
function firstUseful() {
  const cols = [...$('#board').children].filter(c => c.classList.contains('col'));
  const pick = cols.find(c => c.dataset.status === 'in_progress' && c.querySelector('.card')) ?? cols.find(c => c.querySelector('.card'));
  if (pick) pick.scrollIntoView({ inline: 'start', block: 'nearest' });
  markState();
}

/* ---------- Calendario: una persona cada vez ---------- */
let lane = null;
function syncLanes() {
  const day = $('#cal-root .day');
  const bar = $('#mob-lanes');
  if (!day) { bar.innerHTML = ''; return; }
  /* #773: en el iPad en horizontal caben todas las personas: el día se ve entero, como en el escritorio */
  if (ANCHO.matches) {
    bar.innerHTML = ''; delete day.dataset.mobLane; day.style.removeProperty('--mob-lane-min');
    day.querySelectorAll('.is-mob-off').forEach(el => el.classList.remove('is-mob-off'));
    return;
  }
  const heads = [...day.querySelectorAll('.day__lane-head')];
  const names = heads.map(h => h.querySelector('.day__lane-name')?.textContent.trim() ?? '');
  const counts = heads.map(h => h.querySelector('.day__lane-count')?.textContent.trim() ?? '');
  if (lane == null || !names.includes(lane)) lane = names.find((n, i) => Number(counts[i]) > 0) ?? names[0];
  const i = names.indexOf(lane);
  bar.innerHTML = names.map((n, j) => `<button type="button" data-n="${n}" aria-current="${j === i}">${n} <b>${counts[j] || 0}</b></button>`).join('');
  day.dataset.mobLane = i;
  /* #795: el carril elegido no baja del mínimo de sus solapes; si no cabe, se desliza */
  day.style.setProperty('--mob-lane-min', `${Number((day.dataset.laneMins ?? '').split(',')[i]) || 0}px`);
  const off = (list) => list.forEach((el, j) => el.classList.toggle('is-mob-off', j !== i));
  off(heads);
  off([...day.querySelectorAll('.day__band-cell')]);
  off([...day.querySelectorAll('.day__grid > .day__lane')]);
}

function start() {
  if (!MQ.matches || document.querySelector('.mob-tabs')) return;
  build();
  /* la app pinta después de conectar; se espera a que haya columnas */
  const wait = setInterval(() => {
    /* la app ha pintado algo (tablero o calendario, según el modo que recuerde) */
    if ($('#board .col, #cal-root .cal__bar')) {
      clearInterval(wait);
      /* #851: un enlace a la Biblioteca abre en frío también aquí */
      go(location.hash.startsWith('#/biblioteca') ? 'lib' : new URLSearchParams(location.search).get('m') || 'board');
      loadDecisions();   /* después de conectar: antes, api() aún no existe */
    }
  }, 100);
}
start();
MQ.addEventListener('change', start);
ANCHO.addEventListener('change', () => { if (current === 'cal') syncLanes(); });   /* girar el iPad */
