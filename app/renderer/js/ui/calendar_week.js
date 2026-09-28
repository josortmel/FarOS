/* ============================================================
   calendar_week.js — vista Semana (T2.6). El mismo concepto que
   Día, condensado: 7 columnas × horas, todos los agentes juntos,
   tarjetas mínimas (punto de estado + título + hora + quién).
   Arriba, la banda «sin hora» de cada día: ahí vive casi toda la
   Casa (las recurrentes no tienen hora). Arrastrar entre días →
   el motor mueve la fecha (/schedule; una recurrente mueve SOLO su
   próxima ocurrencia). Las ocurrencias proyectadas (ghost) van en
   gris y solo se miran. Sin estado optimista.
   ============================================================ */

import { esc } from '../util.js';
import { STATUS_LABEL } from '../transitions.js';
import { toMin, toHHMM, durationOf, layoutOverlaps, badges, projectStripe, makeDraggable, clearDropMarks, getDrag, markPending, SNAP_MIN, MIN_DUR, isClosed, isOverdue, closedLine } from './calendar_day.js';

export const WEEK_HOUR_H = 48;     // px por hora — más denso que Día
const DAY_FROM = 7, DAY_TO = 22;

let handlers = null; // { onOpen(ticketId), onSchedule(item, patch) → Promise, todayISO() }
export function initWeek(h) { handlers = h; }


/* #262 */
import { mesCorto as MES, diaCorto as DIAS_F, t as tr } from '../i18n.js';

function daysBetween(from, to) {
  const out = [];
  const d = new Date(from + 'T00:00:00'), end = new Date(to + 'T00:00:00');
  while (d <= end) {
    out.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`);
    d.setDate(d.getDate() + 1);
  }
  return out;
}
function dayLabel(iso, showMonth) {
  const d = new Date(iso + 'T00:00:00');
  return `${DIAS_F(d.getDay())} ${d.getDate()}${showMonth ? ' ' + MES(d.getMonth()) : ''}`;
}

export function renderWeek(body, state, range) {
  const data = state.calData;
  body.innerHTML = '';
  body.classList.add('cal__body--day');
  if (data == null) {
    body.insertAdjacentHTML('beforeend', '<div class="cal__placeholder"><b>Semana</b><span>cargando…</span></div>');
    return;
  }
  const days = daysBetween(range.from, range.to);
  const slots = data.slots ?? [];
  const items = (data.items ?? []).filter(it => it.date >= range.from && it.date <= range.to);
  const timed = items.filter(it => it.start);
  const today = handlers.todayISO();

  let hFrom = DAY_FROM, hTo = DAY_TO;
  for (const it of timed) {
    hFrom = Math.min(hFrom, Math.floor(toMin(it.start) / 60));
    hTo = Math.max(hTo, Math.ceil((toMin(it.start) + durationOf(it)) / 60));
  }
  hTo = Math.min(hTo, 24);
  const hours = [];
  for (let h = hFrom; h < hTo; h++) hours.push(h);

  const week = document.createElement('div');
  week.className = 'week';
  week.style.setProperty('--days', days.length);

  /* ---- cabecera: un día por columna, hoy marcado ---- */
  const head = document.createElement('div');
  head.className = 'week__head';
  const firstMonth = new Date(days[0] + 'T00:00:00').getMonth();
  head.innerHTML = `<div class="week__corner"></div>` + days.map((d, i) => {
    const n = items.filter(it => it.date === d).length;
    const m = new Date(d + 'T00:00:00').getMonth();
    return `<div class="week__day-head${d === today ? ' is-today' : ''}${d < today ? ' is-past' : ''}" data-date="${d}">
      <span class="week__day-name">${esc(dayLabel(d, i === 0 || m !== firstMonth))}</span>
      <span class="week__day-count">${n ? n : '—'}</span></div>`;
  }).join('');
  day_heads_click(head);
  week.appendChild(head);

  /* ---- banda sin hora, por día ---- */
  const band = document.createElement('div');
  band.className = 'week__band';
  const loose = items.filter(it => !it.start);
  /* T2.3: igual que en Día — la banda vacía no ocupa sitio y solo reaparece mientras
     se arrastra, que es cuando hace falta soltar algo para quitarle la hora. */
  if (!loose.length) band.classList.add('is-empty');
  const label = document.createElement('div');
  label.className = 'day__band-label';
  label.innerHTML = `<span>sin hora</span><small>${loose.length ? `${loose.length} esta semana` : 'quitar la hora'}</small>`;
  band.appendChild(label);
  for (const d of days) {
    const cell = document.createElement('div');
    cell.className = `week__band-cell${d === today ? ' is-today' : ''}`;
    cell.dataset.date = d;
    const mine = loose.filter(it => it.date === d)
      .sort((a, b) => Number(a.ghost) - Number(b.ghost) || (a.overdue_since ? -1 : 0) - (b.overdue_since ? -1 : 0) || a.ticket_id - b.ticket_id);
    for (const it of mine) cell.appendChild(renderMini(it, false));
    wireDrop(cell, d, null, 0);
    band.appendChild(cell);
  }
  week.appendChild(band);

  /* ---- rejilla horas × días ---- */
  const scroll = document.createElement('div');
  scroll.className = 'day__scroll';
  const grid = document.createElement('div');
  grid.className = 'week__grid';
  grid.style.height = `${hours.length * WEEK_HOUR_H}px`;
  const gutter = document.createElement('div');
  gutter.className = 'day__gutter';
  for (const h of hours) {
    const row = document.createElement('div');
    row.className = 'day__hour';
    row.style.height = `${WEEK_HOUR_H}px`;
    row.innerHTML = `<span>${String(h).padStart(2, '0')}:00</span>`;
    gutter.appendChild(row);
  }
  grid.appendChild(gutter);

  for (const d of days) {
    const col = document.createElement('div');
    col.className = `week__col${d === today ? ' is-today' : ''}`;
    col.dataset.date = d;
    for (const [a, b] of slots) {
      const top = (toMin(a) - hFrom * 60) / 60 * WEEK_HOUR_H;
      const h = (toMin(b) - toMin(a)) / 60 * WEEK_HOUR_H;
      if (h <= 0) continue;
      const s = document.createElement('div');
      s.className = 'day__slot';
      s.style.top = `${top}px`; s.style.height = `${h}px`;
      s.title = `franja de la Casa ${a}–${b}`;
      col.appendChild(s);
    }
    for (let i = 0; i < hours.length; i++) {
      const ln = document.createElement('div');
      ln.className = 'day__line'; ln.style.top = `${i * WEEK_HOUR_H}px`;
      col.appendChild(ln);
    }
    const mine = timed.filter(it => it.date === d).sort((a, b) => toMin(a.start) - toMin(b.start) || a.ticket_id - b.ticket_id);
    for (const { it, colIdx, colCount } of layoutOverlaps(mine)) col.appendChild(renderEvent(it, hFrom, colIdx, colCount));
    if (d === today) {
      const m = new Date().getHours() * 60 + new Date().getMinutes();
      if (m >= hFrom * 60 && m <= hTo * 60) {
        const line = document.createElement('div');
        line.className = 'week__now';
        line.style.top = `${(m - hFrom * 60) / 60 * WEEK_HOUR_H}px`;
        line.title = toHHMM(m);
        col.appendChild(line);
      }
    }
    wireDrop(col, d, hFrom, WEEK_HOUR_H);
    grid.appendChild(col);
  }
  scroll.appendChild(grid);
  week.appendChild(scroll);
  body.appendChild(week);

  const anchorMin = slots[0] ? toMin(slots[0][0]) : hFrom * 60;
  scroll.scrollTop = Math.max(0, (anchorMin - hFrom * 60) / 60 * WEEK_HOUR_H - 6);
}

/* clic en la cabecera de un día → ese día en la vista Día */
function day_heads_click(head) {
  head.querySelectorAll('.week__day-head').forEach(h => {
    h.tabIndex = 0;
    h.setAttribute('role', 'button');
    h.title = 'abrir este día';
    h.addEventListener('click', () => handlers.onOpenDay?.(h.dataset.date));
    h.addEventListener('keydown', (e) => { if (e.key === 'Enter') handlers.onOpenDay?.(h.dataset.date); });
  });
}

/* ---------------- tarjeta mínima ---------------- */

function statusDot(it) {
  return `<span class="week__dot week__dot--${esc(it.status)}" title="${esc(STATUS_LABEL[it.status] ?? it.status)}"></span>`;
}

function renderMini(it, timed) {
  const el = document.createElement('article');
  el.className = `week__mini${it.ghost ? ' is-ghost' : ''}${isClosed(it) ? ' is-closed' : ''}${isOverdue(it) ? ' is-overdue' : ''}`;
  el.dataset.id = it.ticket_id;
  el.tabIndex = 0;
  el.setAttribute('role', 'button');
  el.setAttribute('aria-label', `${it.title}${it.start ? ', ' + it.start : ''} — ${it.owner ?? ''}. ${tr('Abrir detalle')}`);
  el.style.setProperty('--stripe', projectStripe(it));
  el.title = it.ghost ? 'ocurrencia prevista: se mueve arrastrando la próxima real' : isClosed(it) ? `${it.title} · ${closedLine(it)}` : `${it.title} · ${STATUS_LABEL[it.status] ?? it.status}`;
  el.innerHTML = `
    <div class="week__mini-title">${statusDot(it)}<span>${esc(it.title)}</span></div>
    <div class="week__mini-meta">${it.start ? `<b>${esc(it.start)}</b>` : ''}<span>${esc(it.owner ?? '')}</span>${isClosed(it) ? `<span class="week__closed">✓ ${esc(closedLine(it))}</span>` : badges(it)}</div>`;
  el.addEventListener('click', () => handlers.onOpen(it.ticket_id));
  el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handlers.onOpen(it.ticket_id); } });
  if (!it.ghost && !isClosed(it)) makeDraggable(el, it);
  return el;
}

function renderEvent(it, hFrom, colIdx, colCount) {
  const s = toMin(it.start), dur = Math.max(durationOf(it), MIN_DUR);
  const el = renderMini(it, true);
  el.classList.add('week__ev', `week__ev--${it.status}`);
  if (dur < 45) el.classList.add('is-short');
  el.style.top = `${(s - hFrom * 60) / 60 * WEEK_HOUR_H}px`;
  const alto = dur / 60 * WEEK_HOUR_H;
  el.style.height = `${alto}px`;
  /* #302 (Hilo): en la misma rejilla convivian DOS reglas de corte. «Migrate the…» con
     puntos suspensivos y «Rotate» sin ellos — y «Rotate» se lee como si la tarea se
     llamara asi. Un titulo que se corta sin avisar miente; con puntos, no.
     La causa: el clamp del titulo estaba fijo en 2 lineas mientras la caja daba para 1
     (20 px de caja, 17 de linea). El clamp no llegaba a actuar nunca y quien recortaba era
     el overflow del padre, que no pone puntos. Otra vez una constante decidiendo sobre un
     espacio que varia.
     Se ata el clamp al sitio que hay de verdad: alto de la caja menos lo que ocupan la
     hora y los rellenos, dividido por la linea. Asi se aprovechan las dos lineas donde
     caben y se ponen puntos donde solo cabe una. Una sola regla para el que mira. */
  const LINEA = 17, RESERVA = 24;   // medidos: alto de linea del titulo y la fila de hora
  el.style.setProperty('--titulo-lineas', String(Math.max(1, Math.floor((alto - RESERVA) / LINEA))));
  const w = 100 / colCount;
  el.style.left = `calc(${colIdx * w}% + 2px)`;
  el.style.width = `calc(${w}% - 4px)`;
  return el;
}

/* ---------------- drop: a un día (banda) o a un día + hora (rejilla) ---------------- */

function wireDrop(el, date, hFrom, hourH) {
  const isGrid = hourH > 0;
  let ghost = null;
  const snapAt = (e) => {
    const y = e.clientY - el.getBoundingClientRect().top;
    const min = hFrom * 60 + Math.round(y / hourH * 60 / SNAP_MIN) * SNAP_MIN;
    return Math.max(hFrom * 60, Math.min(min, 23 * 60 + 30));
  };
  el.addEventListener('dragover', (e) => {
    const d = getDrag();
    if (!d) return;
    e.preventDefault();
    /* una recurrente no va al pasado (regla del motor): el destino se pinta gris */
    const ok = !(d.cadence && date < handlers.todayISO());
    el.classList.toggle('is-drop-ok', ok);
    el.classList.toggle('is-drop-no', !ok);
    e.dataTransfer.dropEffect = ok ? 'move' : 'none';
    if (!ok || !isGrid) { ghost?.remove(); ghost = null; return; }
    const m = snapAt(e);
    if (!ghost) { ghost = document.createElement('div'); ghost.className = 'day__ghost'; el.appendChild(ghost); }
    ghost.style.top = `${(m - hFrom * 60) / 60 * hourH}px`;
    ghost.style.height = `${Math.max(d.duration, MIN_DUR) / 60 * hourH}px`;
    ghost.textContent = toHHMM(m);
  });
  el.addEventListener('dragleave', (e) => {
    if (el.contains(e.relatedTarget)) return;
    el.classList.remove('is-drop-ok', 'is-drop-no');
    ghost?.remove(); ghost = null;
  });
  el.addEventListener('drop', (e) => {
    e.preventDefault();
    const d = getDrag();
    clearDropMarks(); ghost = null;
    if (!d || (d.cadence && date < handlers.todayISO())) return;
    const patch = isGrid ? { date, start: toHHMM(snapAt(e)), duration_min: d.duration } : { date, start: null };
    if (!isGrid && d.cadence && !d.timed && date === d.date) return; // mismo sitio, nada que hacer
    markPending(d.ticket_id, true);
    handlers.onSchedule(d, patch).finally(() => markPending(d.ticket_id, false));
  });
}
