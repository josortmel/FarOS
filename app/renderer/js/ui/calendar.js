/* ============================================================
   calendar.js — el Tablero visto en el tiempo (T2.4).
   Este fichero es el ARMAZÓN: conmutación de vista, rango,
   navegación temporal (‹ hoy ›, selector de fecha, flechas del
   teclado). Las vistas Día/Semana/Mes/Año se cuelgan aquí en
   T2.5–T2.8 sobre GET /api/calendar (T2.1). Hasta entonces el
   cuerpo lo dice: no dibuja una rejilla vacía que se lea como
   «no hay tareas».
   ============================================================ */

import { esc } from '../util.js';
import { initDay, renderDay, flashDayItem, fitNarrow } from './calendar_day.js';
import { initWeek, renderWeek } from './calendar_week.js';
import { initMonth, renderMonth } from './calendar_month.js';
import { initYear, renderYear } from './calendar_year.js';

export { flashDayItem };

let handlers = null; // { onNavigate(dateISO), onOpen(ticketId), onSchedule(item, patch) }

export const CAL_VIEWS = [
  { view: 'day',   title: 'Día' },
  { view: 'week',  title: 'Semana' },
  { view: 'month', title: 'Mes' },
  { view: 'year',  title: 'Año' },
];



/* #262: de Intl, no de un array en espanol — ver i18n.js */
import { mesCorto as MESC, fechaLarga } from '../i18n.js';

/* ---------------- fechas locales (sin UTC: se usa la zona horaria local) ---------------- */

export function toISO(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
export function fromISO(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d);
}
export function todayISO() { return toISO(new Date()); }

function addDays(d, n) { const x = new Date(d); x.setDate(x.getDate() + n); return x; }
function addMonths(d, n) { return new Date(d.getFullYear(), d.getMonth() + n, 1); }

/* la semana empieza en lunes */
function mondayOf(d) {
  const x = new Date(d);
  const dow = (x.getDay() + 6) % 7; // lun=0 … dom=6
  x.setDate(x.getDate() - dow);
  return x;
}

/* rango [from, to] inclusivo en ISO para la vista y la fecha ancla */
export function rangeFor(view, iso) {
  const d = fromISO(iso);
  switch (view) {
    case 'day':   return { from: iso, to: iso };
    case 'week':  { const m = mondayOf(d); return { from: toISO(m), to: toISO(addDays(m, 6)) }; }
    case 'month': return { from: toISO(new Date(d.getFullYear(), d.getMonth(), 1)), to: toISO(new Date(d.getFullYear(), d.getMonth() + 1, 0)) };
    /* T3.3: doce meses desde el mes ancla (≤ 366 días: el API admite hasta 400) */
    case 'year':  return { from: toISO(new Date(d.getFullYear(), d.getMonth(), 1)), to: toISO(new Date(d.getFullYear(), d.getMonth() + 12, 0)) };
    default:      return { from: iso, to: iso };
  }
}

/* anterior / siguiente: un paso del tamaño de la vista */
export function shiftDate(view, iso, dir) {
  const d = fromISO(iso);
  switch (view) {
    case 'day':   return toISO(addDays(d, dir));
    case 'week':  return toISO(addDays(d, 7 * dir));
    case 'month': return toISO(addMonths(d, dir));
    case 'year':  return toISO(addMonths(d, dir)); // T3.3: la ventana de 12 meses se desplaza mes a mes
    default:      return iso;
  }
}

/* etiqueta del rango, en el idioma de la Casa */
export function fmtRange(view, iso) {
  const d = fromISO(iso);
  const { from, to } = rangeFor(view, iso);
  if (view === 'day') return fechaLarga(d);  /* #262: entera a Intl, el «de» no existe en ingles */
  if (view === 'week') {
    const a = fromISO(from), b = fromISO(to);
    if (a.getMonth() === b.getMonth()) return `${a.getDate()} – ${b.getDate()} ${MESC(a.getMonth())} ${b.getFullYear()}`;
    if (a.getFullYear() === b.getFullYear()) return `${a.getDate()} ${MESC(a.getMonth())} – ${b.getDate()} ${MESC(b.getMonth())} ${b.getFullYear()}`;
    return `${a.getDate()} ${MESC(a.getMonth())} ${a.getFullYear()} – ${b.getDate()} ${MESC(b.getMonth())} ${b.getFullYear()}`;
  }
  if (view === 'month') return `${MESC(d.getMonth())} ${d.getFullYear()}`;
  if (d.getMonth() === 0) return String(d.getFullYear());
  const b = fromISO(to);
  return `${MESC(d.getMonth())} ${d.getFullYear()} – ${MESC(b.getMonth())} ${b.getFullYear()}`;
}

export function rangeHasToday(view, iso) {
  const { from, to } = rangeFor(view, iso);
  const t = todayISO();
  return from <= t && t <= to;
}

/* ---------------- init: teclado ---------------- */

export function initCalendar(h) {
  handlers = h;
  initDay({ onOpen: h.onOpen, onSchedule: h.onSchedule, onStamp: h.onStamp, todayISO });
  initWeek({ onOpen: h.onOpen, onSchedule: h.onSchedule, todayISO, onOpenDay: h.onOpenDay });
  initMonth({ onOpen: h.onOpen, onSchedule: h.onSchedule, onStamp: h.onStamp, todayISO, onOpenDay: h.onOpenDay, onSelectDay: h.onSelectDay });
  initYear({ todayISO, onOpenMonth: h.onOpenMonth });
  document.addEventListener('keydown', (e) => {
    const root = document.getElementById('cal-root');
    if (!root || root.hidden) return;
    const tag = document.activeElement?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (!document.getElementById('panel').hidden || !document.getElementById('modal-root').hidden) return;
    if (e.key === 'ArrowLeft')  { e.preventDefault(); handlers.onNavigate(shiftDate(root.dataset.view, root.dataset.date, -1)); }
    if (e.key === 'ArrowRight') { e.preventDefault(); handlers.onNavigate(shiftDate(root.dataset.view, root.dataset.date, +1)); }
    if (e.key === 't' || e.key === 'T') { e.preventDefault(); handlers.onNavigate(todayISO()); }
  });
}

/* ---------------- render ---------------- */

export function renderCalendar(state, root = document.getElementById('cal-root')) {
  const view = state.calView;
  const date = state.calDate;
  root.dataset.view = view;
  root.dataset.date = date;
  root.innerHTML = '';

  /* barra: ‹ hoy › · rango · fecha */
  const bar = document.createElement('div');
  bar.className = 'cal__bar';

  const nav = document.createElement('div');
  nav.className = 'cal__nav';
  const prev = document.createElement('button');
  prev.className = 'cal__navbtn';
  prev.setAttribute('aria-label', 'Anterior');
  prev.textContent = '‹';
  prev.addEventListener('click', () => handlers.onNavigate(shiftDate(view, date, -1)));
  const today = document.createElement('button');
  today.className = 'btn btn--ghost btn--sm cal__today';
  today.textContent = 'Hoy';
  /* en el Año, «Hoy» = volver a enero–diciembre del año en curso (la ventana rodante puede
     contener hoy y aun así no ser la del año) */
  today.disabled = view === 'year' ? date === `${todayISO().slice(0, 4)}-01-01` : rangeHasToday(view, date);
  today.addEventListener('click', () => handlers.onNavigate(view === 'year' ? `${todayISO().slice(0, 4)}-01-01` : todayISO()));
  const next = document.createElement('button');
  next.className = 'cal__navbtn';
  next.setAttribute('aria-label', 'Siguiente');
  next.textContent = '›';
  next.addEventListener('click', () => handlers.onNavigate(shiftDate(view, date, +1)));
  nav.append(prev, today, next);
  bar.appendChild(nav);

  const range = document.createElement('h2');
  range.className = 'cal__range';
  range.textContent = fmtRange(view, date);
  bar.appendChild(range);

  const pick = document.createElement('input');
  pick.type = 'date';
  pick.className = 'cal__date';
  pick.value = date;
  pick.setAttribute('aria-label', 'Ir a fecha');
  pick.addEventListener('change', () => { if (pick.value) handlers.onNavigate(pick.value); });
  bar.appendChild(pick);

  root.appendChild(bar);

  /* cuerpo: las vistas se cuelgan aquí (T2.5–T2.8) */
  const body = document.createElement('div');
  body.className = 'cal__body';
  const { from, to } = rangeFor(view, date);
  if (view === 'day') {           // T2.5
    renderDay(body, state);
    root.appendChild(body);
    /* T2.2-C: el ancho de una tarjeta solo existe cuando el árbol está EN el documento;
       dentro de renderDay todo mide cero porque `body` aún cuelga del aire. Se ajusta
       aquí, ya insertado y antes de pintar, así que no parpadea. */
    fitNarrow(body);
    return;
  }
  if (view === 'week') {          // T2.6
    renderWeek(body, state, { from, to });
    root.appendChild(body);
    return;
  }
  if (view === 'month') {         // T2.7
    renderMonth(body, state, { from, to });
    root.appendChild(body);
    return;
  }
  renderYear(body, state);        // T2.8
  root.appendChild(body);
}
