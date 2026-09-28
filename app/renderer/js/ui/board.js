/* ============================================================
   board.js — tablero kanban.
   Regla de la casa: la UI nunca fuerza estados. En el drop no hay
   movimiento optimista: se pide la transición al motor y, si la
   rechaza, la tarjeta ni se movió — toast con el motivo y flash.
   ============================================================ */

import { TRANSITIONS, COLUMNS } from '../transitions.js';
import { t as tb } from '../i18n.js';  // #262
import { fmtWhen, fmtDate, fmtWhenScheduled, esc, projectColor } from '../util.js';
import { STATUS_LABEL } from '../transitions.js';

let handlers = null; // { onOpen, onDrop, onOpenJob }
let dragTicket = null; // { id, status }

export function initBoard(h) { handlers = h; }

/* ---------------- tarjeta ---------------- */

function runGlyph(status) {
  switch (status) {
    case 'ok':        return { g: '✓', cls: 'ok',        label: 'run ok' };
    case 'error':     return { g: '✕', cls: 'error',     label: 'run con error' };
    case 'timeout':   return { g: '✕', cls: 'timeout',   label: 'run agotó el tiempo' };
    case 'running':   return { g: '●', cls: 'running',   label: 'run en curso' };
    case 'cancelled': return { g: '⊘', cls: 'cancelled', label: 'run cancelado' };
    default:          return { g: '▸', cls: 'none',      label: 'sin runs' };
  }
}

export function renderCard(t, board, { draggable = true } = {}) {
  const proj = board.projects.find(p => p.id === t.project_id);
  const el = document.createElement('article');
  el.className = 'card';
  el.dataset.id = t.id;
  el.tabIndex = 0;
  el.setAttribute('role', 'button');
  el.setAttribute('aria-label', 'abrir detalle');  // #262: el titulo ya va dentro de la tarjeta

  /* fila superior: proyecto + badges */
  const top = document.createElement('div');
  top.className = 'card__top';
  /* #301: una tarea SIN proyecto pintaba «● –» — un punto de color de un proyecto que no
     existe y un guion suelto. Salia en 8 de cada 10 tarjetas de la primera pantalla, que
     es literalmente lo primero que ve quien abre la app. Un hueco vacio dice menos que un
     hueco ocupado por un simbolo que no significa nada: si no hay proyecto, no se pinta ni
     el punto ni el guion, y el sitio se lo quedan los badges. */
  top.innerHTML = proj
    ? `<span class="card__project">
      <span class="card__project-dot" style="background:${esc(projectColor(proj))}"></span>${esc(proj.name)}
    </span>`
    : '<span class="card__project card__project--none"></span>';

  const badges = [];
  if (t.priority === 'alta') badges.push(`<span class="badge badge--prio" title="${esc(tb('prioridad alta'))}">${esc(tb('! alta'))}</span>`);
  if (t.cadence_days) badges.push(`<span class="badge badge--recur">↻ ${t.cadence_days} d</span>`);
  if (t.due_state === 'overdue') badges.push(`<span class="badge badge--overdue">${esc(tb('▲ vencida'))}${t.due_at ? ' · ' + fmtDate(t.due_at) : ''}</span>`);
  else if (t.due_state === 'due') badges.push(`<span class="badge badge--due">${esc(tb('● hoy'))}</span>`);
  else if (t.due_at) badges.push(`<span class="badge badge--recur">${fmtDate(t.due_at)}</span>`);
  if (t.status === 'blocked') badges.push(`<span class="badge badge--blocked">${esc(tb('⛔ bloqueada'))}</span>`);

  if (badges.length) {
    const wrap = document.createElement('span');
    wrap.style.marginLeft = 'auto';
    wrap.style.display = 'inline-flex';
    wrap.style.gap = '0.4em';
    wrap.innerHTML = badges.join('');
    top.appendChild(wrap);
  }
  el.appendChild(top);

  /* título */
  const title = document.createElement('h3');
  title.className = 'card__title';
  title.textContent = t.title;
  el.appendChild(title);

  /* meta: dueño */
  const who = t.owner ?? t.created_by;
  if (who) {
    const meta = document.createElement('div');
    meta.className = 'card__meta';
    meta.innerHTML = `<span>${esc(who)}</span>`;
    el.appendChild(meta);
  }

  /* motivo de bloqueo — 2 líneas en tarjeta, completo en el panel */
  if (t.status === 'blocked' && t.blocked_reason) {
    const r = document.createElement('div');
    r.className = 'card__reason';
    const span = document.createElement('span');
    span.textContent = t.blocked_reason;
    r.appendChild(span);
    el.appendChild(r);
  }

  /* T1.14 — brazo automático: la manual vinculada a un job enseña su último run
     y su veredicto; el job vive en Agentes, aquí solo se ve el brazo */
  if (t.linked_job) {
    const lj = t.linked_job; const lr = lj.last_run;
    const { g, cls, label } = runGlyph(lr?.status === 'ok' && lr?.verdict ? (lr.verdict === 'pass' ? 'ok' : 'error') : lr?.status);
    const arm = document.createElement('button');
    arm.className = 'card__arm';
    arm.type = 'button';
    arm.title = 'ver el job en Agentes';  // #262
    arm.setAttribute('aria-label', 'brazo automático: ver el job en Agentes');  // #262
    arm.innerHTML = `<span class="card__run-glyph card__run-glyph--${cls}" title="${label}">${g}</span>
      <span class="card__arm-text"><span class="card__arm-tag">brazo automático</span> · ${esc(lj.name)}${lr ? ` · run ${lr.id} ${esc(lr.status)}${lr.verdict ? (lr.verdict === 'pass' ? ' · pasa' : ' · falla') : ''}` : ' · sin runs'}</span>
      <span class="card__arm-go">Agentes →</span>`;
    arm.addEventListener('click', (e) => { e.stopPropagation(); handlers.onOpenJob?.(lj.id); });
    el.appendChild(arm);
  }

  /* T2.10 — el tiempo, en líneas fijas y legibles, no como notita en un tag:
     cuándo está programada · cuándo cambió de estado por última vez */
  const when = fmtWhenScheduled(t);
  const lc = t.last_change;
  if (when || lc) {
    const tm = document.createElement('div');
    tm.className = 'card__time';
    if (when) tm.innerHTML += `<span class="card__when">${t.cadence_days && !t.scheduled_at ? '↻' : '📅'} ${esc(when)}</span>`;
    if (lc) tm.innerHTML += `<span class="card__change">→ ${esc(tb(STATUS_LABEL[lc.to] ?? lc.to))} · ${fmtWhen(lc.at)}${lc.by ? ' · ' + esc(lc.by) : ''}</span>`;
    el.appendChild(tm);
  }

  /* interacción */
  el.addEventListener('click', () => handlers.onOpen(t.id));
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handlers.onOpen(t.id); }
  });

  if (draggable) makeDraggable(el, t);

  return el;
}

/* arrastre: la misma mecánica para el Tablero y para el kanban del workflow
   (T3.1). Solo affordance — el motor decide en el drop. */
export function makeDraggable(el, t) {
  el.draggable = true;
  el.addEventListener('dragstart', (e) => {
    dragTicket = { id: t.id, status: t.status };
    el.classList.add('is-dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', String(t.id));
  });
  el.addEventListener('dragend', () => {
    el.classList.remove('is-dragging');
    dragTicket = null;
    document.querySelectorAll('.col').forEach(c => c.classList.remove('is-drop-ok', 'is-drop-no'));
  });
}

/* columna por estado con su zona de drop. `cards` ya vienen renderizadas;
   `onDrop(id, status)` lo pone quien monta el tablero (Tablero o workflow). */
export function buildStateColumn(def, cards, { onDrop, live = '', extraClass = '' } = {}) {
  const col = document.createElement('section');
  col.className = `col${extraClass ? ' ' + extraClass : ''}`;
  col.dataset.status = def.status;
  col.innerHTML = `
    <header class="col__head">
      <span class="col__title">${def.title}</span>${live}
      ${def.note ? `<span class="col__note">${def.note}</span>` : ''}
      <span class="col__count">${cards.length}</span>
    </header>`;

  const body = document.createElement('div');
  body.className = 'col__body';
  if (cards.length === 0) {
    const e = document.createElement('div');
    e.className = 'col__empty';
    e.textContent = def.empty;
    body.appendChild(e);
  } else {
    for (const c of cards) body.appendChild(c);
  }
  col.appendChild(body);

  /* drop target — affordance según el espejo de transiciones */
  col.addEventListener('dragover', (e) => {
    if (!dragTicket) return;
    e.preventDefault();
    const legal = TRANSITIONS[dragTicket.status]?.[def.status];
    const same = dragTicket.status === def.status;
    col.classList.toggle('is-drop-ok', Boolean(legal) && !same);
    col.classList.toggle('is-drop-no', !legal && !same);
    e.dataTransfer.dropEffect = legal && !same ? 'move' : 'none';
  });
  col.addEventListener('dragleave', () => col.classList.remove('is-drop-ok', 'is-drop-no'));
  col.addEventListener('drop', (e) => {
    e.preventDefault();
    col.classList.remove('is-drop-ok', 'is-drop-no');
    const id = Number(e.dataTransfer.getData('text/plain'));
    if (!id || def.status === dragTicket?.status) return;
    onDrop(id, def.status);
  });
  return col;
}

/* ---------------- columnas ---------------- */

const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
let firstPaint = true;

/* FLIP: las tarjetas se DESLIZAN a su nueva posición cuando el tablero
   cambia (SSE, transición, run que acaba). El movimiento es la narración:
   se ve QUÉ se movió y A DÓNDE sin leer nada. */
const BOARD_CARDS = '#board .card, #laterals .card';

function flipFrom(prevRects, selector = BOARD_CARDS) {
  if (reducedMotion.matches) return;
  requestAnimationFrame(() => {
    let newIdx = 0;
    document.querySelectorAll(selector).forEach(c => {
      const prev = prevRects.get(c.dataset.id);
      if (!prev) {
        // tarjeta nueva: entrada breve (escalonada solo en el primer pintado)
        const delay = firstPaint ? Math.min(newIdx++ * 22, 260) : 0;
        c.animate(
          [{ opacity: 0, transform: 'translateY(7px)' }, { opacity: 1, transform: 'none' }],
          { duration: 230, delay, easing: 'cubic-bezier(0.16,1,0.3,1)', fill: 'backwards' }
        );
        return;
      }
      const now = c.getBoundingClientRect();
      const dx = prev.left - now.left, dy = prev.top - now.top;
      if (dx || dy) {
        c.animate(
          [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }],
          { duration: 300, easing: 'cubic-bezier(0.16,1,0.3,1)' }
        );
      }
    });
    firstPaint = false;
  });
}

export function captureRects(selector = BOARD_CARDS) {
  const m = new Map();
  document.querySelectorAll(selector)
    .forEach(c => m.set(c.dataset.id, c.getBoundingClientRect()));
  return m;
}

export { flipFrom };

export function renderBoard(state) {
  const root = document.getElementById('board');
  root.innerHTML = '';
  const board = state.board;
  if (!board) return;

  /* T1.14: el Tablero es de los agentes con nombre; las agénticas legacy
     (kind=agentic) viven en Agentes y aquí no se pintan */
  const visible = (arr) => (arr ?? []).filter(t => t.kind !== 'agentic');
  const total = COLUMNS.reduce((n, c) => n + visible(board.columns[c.status]).length, 0);
  /* #285: vacío NO es pantalla en blanco. Las cinco columnas SON el producto —el ciclo
     propuesta → verificada—, así que se pintan igual, cada una con su propio vacío, y
     encima una línea con la siguiente acción. Antes había un return aquí y en el primer
     arranque con la base limpia no se veía nada de lo que FarOS es. */
  root.classList.toggle('board--empty', total === 0);
  if (total === 0) {
    const empty = document.createElement('div');
    empty.className = 'board-empty';
    empty.innerHTML = `<b>Nada en esta vista.</b> <span>Cambia de vista o crea una tarea con «Nueva tarea».</span>`;
    root.appendChild(empty);
  }

  // ¿hay brazo automático trabajando? — la columna En curso lo dice con un punto vivo
  const allTickets = [
    ...COLUMNS.flatMap(c => visible(board.columns[c.status])),
    ...Object.values(board.laterals ?? {}).flatMap(visible),
  ];
  const running = allTickets.filter(t => t.linked_job?.last_run?.status === 'running').length;

  for (const def of COLUMNS) {
    const tickets = visible(board.columns[def.status]);
    const live = def.status === 'in_progress' && running
      ? `<span class="col__live" title="${running} run${running > 1 ? 's' : ''} en ejecución">●</span>` : '';
    const cards = tickets.map(t => renderCard(t, board));
    root.appendChild(buildStateColumn(def, cards, { onDrop: handlers.onDrop, live }));
  }
}

/* ---------------- laterales ---------------- */

let lateralsCollapsed = false; // con la Casa real hay 10 bloqueadas: plegable

export function renderLaterals(state) {
  const root = document.getElementById('laterals');
  const board = state.board;
  if (!board) { root.hidden = true; return; }
  const groups = [
    ['blocked', 'Bloqueadas'],
    ['rejected', 'Rechazadas'],
    ['expired', 'Expiradas'],
  ].map(([k, label]) => [k, label, (board.laterals[k] ?? []).filter(t => t.kind !== 'agentic')]).filter(([, , arr]) => arr.length);

  if (!groups.length) { root.hidden = true; return; }
  root.hidden = false;
  root.innerHTML = '';

  for (const [key, label, arr] of groups) {
    const head = document.createElement('div');
    head.className = 'laterals__head';
    head.innerHTML = `<span class="laterals__title">${esc(tb(label))} · ${arr.length}</span>`;  // #262

    const toggle = document.createElement('button');
    toggle.className = 'laterals__toggle';
    toggle.textContent = lateralsCollapsed ? tb('mostrar ▸') : tb('ocultar ▾');
    toggle.setAttribute('aria-expanded', String(!lateralsCollapsed));
    toggle.addEventListener('click', () => { lateralsCollapsed = !lateralsCollapsed; renderLaterals(state); });
    head.appendChild(toggle);
    root.appendChild(head);

    if (lateralsCollapsed) continue;
    const row = document.createElement('div');
    row.className = 'laterals__row';
    for (const t of arr) row.appendChild(renderCard(t, board, { draggable: true }));
    root.appendChild(row);
  }
}

/* flash de rechazo: la tarjeta no se movió, que se vea cuál era */
export function flashCard(id) {
  const el = document.querySelector(`.card[data-id="${id}"]`);
  if (el) {
    el.classList.remove('is-flash');
    void el.offsetWidth; // reinicia la animación
    el.classList.add('is-flash');
  }
}
