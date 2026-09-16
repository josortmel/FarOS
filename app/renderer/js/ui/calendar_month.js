/* ============================================================
   calendar_month.js — vista Mes (T2.7). el dueño: «calendario abstracto
   con densidad; al clicar un día NO desaparece: se encoge y el día
   se abre al lado con su lista». Cada celda lleva el número y la
   densidad del día en puntos (uno por ocurrencia, el color dice el
   estado); al abrir un día el mes pasa a la izquierda, pequeño, y a
   la derecha va la lista del día con las MISMAS tarjetas de Día —
   con sus acciones (estampar, próxima, hora) y el panel al clicar.
   ============================================================ */

import { esc } from '../util.js';
import { renderChip, isClosed, isOverdue, toMin, makeDraggable, getDrag, clearDropMarks, markPending } from './calendar_day.js';

let handlers = null; // { onOpen, onSchedule, onStamp, todayISO, onSelectDay(dateISO|null), onOpenDay(dateISO) }
export function initMonth(h) { handlers = h; }

/* #262/#264 (Prima): la cabecera del mes llevaba los dias clavados en espanol
   mientras calendar_week ya usaba diaCorto(). Es VISIBLE EN UNA CAPTURA: un README
   en ingles con la rejilla del mes en espanol, cocido en la imagen.
   Es una FUNCION y no una constante porque el idioma cambia en caliente y una
   constante de modulo se evalua una vez. La rejilla es lunes-primero ((getDay()+6)%7)
   e Intl es domingo-primero, de ahi el giro de indice. */
const diasCab = () => [1, 2, 3, 4, 5, 6, 0].map(diaCorto);

/* #262: fecha entera a Intl — ver i18n.js */
import { fechaDiaMes, diaCorto } from '../i18n.js';
const MAX_DOTS = 8;

function iso(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }

/* las semanas del mes, de lunes a domingo, con los días de fuera del mes en gris */
export function monthWeeks(year, month) {
  const first = new Date(year, month, 1);
  const start = new Date(first); start.setDate(first.getDate() - ((first.getDay() + 6) % 7));
  const weeks = [];
  const d = new Date(start);
  do {
    const w = [];
    for (let i = 0; i < 7; i++) { w.push({ iso: iso(d), inMonth: d.getMonth() === month }); d.setDate(d.getDate() + 1); }
    weeks.push(w);
  } while (d.getMonth() === month);
  return weeks;
}

/* el punto de una ocurrencia: qué es y en qué estado */
function dotClass(it) {
  if (isClosed(it)) return 'closed';
  if (it.ghost) return 'ghost';
  if (isOverdue(it)) return 'overdue';
  if (it.status === 'in_progress') return 'live';
  if (it.status === 'blocked') return 'blocked';
  return 'plain';
}

/* T3.3: una celda del mes acepta lo que se arrastra desde la lista del día abierto.
   Recurrentes no van al pasado (409 del motor); al mismo día, nada que hacer. */
function wireCellDrop(cell, date) {
  cell.addEventListener('dragover', (e) => {
    const d = getDrag();
    if (!d) return;
    e.preventDefault();
    const ok = date !== d.date && !(d.cadence && date < handlers.todayISO());
    cell.classList.toggle('is-drop-ok', ok);
    cell.classList.toggle('is-drop-no', !ok);
    e.dataTransfer.dropEffect = ok ? 'move' : 'none';
  });
  cell.addEventListener('dragleave', () => cell.classList.remove('is-drop-ok', 'is-drop-no'));
  cell.addEventListener('drop', (e) => {
    e.preventDefault();
    const d = getDrag();
    clearDropMarks();
    if (!d || date === d.date || (d.cadence && date < handlers.todayISO())) return;
    markPending(d.ticket_id, true);
    handlers.onSchedule(d, { date, start: null }).finally(() => markPending(d.ticket_id, false));
  });
}

export function renderMonth(body, state, range) {
  const data = state.calData;
  body.innerHTML = '';
  body.classList.add('cal__body--day');
  if (data == null) {
    body.insertAdjacentHTML('beforeend', '<div class="cal__placeholder"><b>Mes</b><span>cargando…</span></div>');
    return;
  }
  const anchor = new Date(state.calDate + 'T00:00:00');
  const weeks = monthWeeks(anchor.getFullYear(), anchor.getMonth());
  const items = data.items ?? [];
  const byDay = new Map();
  for (const it of items) { if (!byDay.has(it.date)) byDay.set(it.date, []); byDay.get(it.date).push(it); }
  const today = handlers.todayISO();
  const sel = state.calDaySel && state.calDaySel >= range.from && state.calDaySel <= range.to ? state.calDaySel : null;

  const root = document.createElement('div');
  root.className = `month${sel ? ' is-open' : ''}`;

  /* ---- rejilla ---- */
  const grid = document.createElement('div');
  grid.className = 'month__grid';
  grid.setAttribute('role', 'grid');
  grid.innerHTML = `<div class="month__cab">${diasCab().map(d => `<span>${d}</span>`).join('')}</div>`;
  for (const w of weeks) {
    const row = document.createElement('div');
    row.className = 'month__row';
    for (const { iso: d, inMonth } of w) {
      const list = byDay.get(d) ?? [];
      const cell = document.createElement('button');
      cell.type = 'button';
      cell.className = `month__cell${inMonth ? '' : ' is-out'}${d === today ? ' is-today' : ''}${d === sel ? ' is-sel' : ''}${d < today ? ' is-past' : ''}`;
      cell.dataset.date = d;
      cell.setAttribute('aria-label', `${d}: ${list.length ? list.length + ' tareas' : 'sin tareas'}`);
      const dots = list.slice(0, MAX_DOTS).map(it => `<i class="month__dot month__dot--${dotClass(it)}"></i>`).join('');
      const more = list.length > MAX_DOTS ? `<em class="month__more">+${list.length - MAX_DOTS}</em>` : '';
      const overdue = list.filter(isOverdue).length;
      cell.innerHTML = `
        <span class="month__num">${new Date(d + 'T00:00:00').getDate()}</span>
        ${list.length ? `<span class="month__count">${list.length}${overdue ? `<b title="${overdue} vencidas">▲${overdue}</b>` : ''}</span>` : ''}
        <span class="month__dots">${dots}${more}</span>`;
      cell.addEventListener('click', () => handlers.onSelectDay(d === sel ? null : d));
      cell.addEventListener('dblclick', () => handlers.onOpenDay(d));
      wireCellDrop(cell, d);
      row.appendChild(cell);
    }
    grid.appendChild(row);
  }
  root.appendChild(grid);

  /* ---- lista del día abierto ---- */
  if (sel) {
    const side = document.createElement('aside');
    side.className = 'month__side';
    const d = new Date(sel + 'T00:00:00');
    const list = (byDay.get(sel) ?? []).slice().sort((a, b) =>
      Number(isClosed(a)) - Number(isClosed(b)) || Number(a.ghost) - Number(b.ghost)
      || (toMin(a.start) ?? -1) - (toMin(b.start) ?? -1) || a.ticket_id - b.ticket_id);
    side.innerHTML = `
      <div class="month__side-head">
        <h3 class="month__side-title">${esc(fechaDiaMes(d))}${sel === today ? ' <span class="month__side-today">hoy</span>' : ''}</h3>
        <span class="month__side-count">${list.length ? `${list.length} tarea${list.length === 1 ? '' : 's'}` : 'nada ese día'}</span>
        <button type="button" class="btn btn--ghost btn--sm month__side-day" title="abrir en la vista Día">Día →</button>
        <button type="button" class="month__side-close" aria-label="Cerrar la lista del día">✕</button>
      </div>
      <div class="month__side-list"></div>`;
    const ul = side.querySelector('.month__side-list');
    for (const it of list) {
      const chip = renderChip(it);
      if (it.start) chip.querySelector('.day__chip-meta').insertAdjacentHTML('afterbegin', `<b class="day__chip-hour">${esc(it.start)}${it.end ? '–' + esc(it.end) : ''}</b>`);
      chip.querySelector('.day__chip-meta').insertAdjacentHTML('beforeend', `<span class="day__chip-owner">${esc(it.owner ?? '')}</span>`);
      /* T3.3 (el dueño §2): desde la lista del día se arrastra a otro día de la rejilla —
         mismo contrato que Semana (recurrente: solo la próxima; programada: todo el día) */
      if (!it.ghost && !isClosed(it)) makeDraggable(chip, it);
      ul.appendChild(chip);
    }
    if (!list.length) ul.innerHTML = '<div class="pane__empty">Nada programado ni vencido este día.</div>';
    side.querySelector('.month__side-close').addEventListener('click', () => handlers.onSelectDay(null));
    side.querySelector('.month__side-day').addEventListener('click', () => handlers.onOpenDay(sel));
    root.appendChild(side);
  }

  body.appendChild(root);
}
