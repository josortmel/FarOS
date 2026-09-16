/* ============================================================
   calendar_year.js — vista Año (T2.8). Abstracción máxima: doce
   mini-meses con la densidad de cada día (más oscuro = más
   ocurrencias; el rojo dice vencidas reales, el naranja «en curso»).
   Clic en un día o en el nombre del mes → vista Mes de ese mes.
   Se alimenta del mismo GET /api/calendar del año entero.
   ============================================================ */

import { esc } from '../util.js';
import { monthWeeks } from './calendar_month.js';
import { isClosed, isOverdue } from './calendar_day.js';

let handlers = null; // { todayISO(), onOpenMonth(dateISO) }
export function initYear(h) { handlers = h; }

/* #262 */
import { mesLargo as MES } from '../i18n.js';
const DIAS = ['l', 'm', 'x', 'j', 'v', 's', 'd'];

/* nivel de densidad 0–4 por número de ocurrencias del día (escala corta, legible de lejos) */
function level(n) { return n === 0 ? 0 : n <= 2 ? 1 : n <= 5 ? 2 : n <= 10 ? 3 : 4; }

export function renderYear(body, state) {
  const data = state.calData;
  body.innerHTML = '';
  body.classList.add('cal__body--day');
  if (data == null) {
    body.insertAdjacentHTML('beforeend', '<div class="cal__placeholder"><b>Año</b><span>cargando…</span></div>');
    return;
  }
  const anchor = new Date(state.calDate + 'T00:00:00'); // T3.3: ventana rodante de 12 meses desde este mes
  const today = handlers.todayISO();
  const byDay = new Map();
  for (const it of data.items ?? []) {
    const d = byDay.get(it.date) ?? { n: 0, overdue: 0, live: 0, closed: 0 };
    d.n++;
    if (isOverdue(it)) d.overdue++;
    else if (isClosed(it)) d.closed++;
    else if (it.status === 'in_progress') d.live++;
    byDay.set(it.date, d);
  }
  const total = (data.items ?? []).length;

  const root = document.createElement('div');
  root.className = 'year';
  const scroll = document.createElement('div');
  scroll.className = 'year__scroll';
  const grid = document.createElement('div');
  grid.className = 'year__grid';

  for (let k = 0; k < 12; k++) {
    const dm = new Date(anchor.getFullYear(), anchor.getMonth() + k, 1);
    const year = dm.getFullYear(), m = dm.getMonth();
    const first = `${year}-${String(m + 1).padStart(2, '0')}-01`;
    const weeks = monthWeeks(year, m);
    const monthN = [...byDay.entries()].filter(([d]) => d.startsWith(first.slice(0, 7))).reduce((a, [, v]) => a + v.n, 0);
    const box = document.createElement('section');
    box.className = `year__month${today.startsWith(first.slice(0, 7)) ? ' is-current' : ''}${monthN ? '' : ' is-empty'}`;
    box.dataset.month = first.slice(0, 7);
    box.innerHTML = `
      <button type="button" class="year__name" title="abrir en la vista Mes">
        <span>${esc(MES(m))}${anchor.getMonth() === 0 ? '' : ` <small>${String(year).slice(2)}</small>`}</span><b>${monthN ? monthN : '—'}</b>
      </button>
      <div class="year__cab">${DIAS.map(d => `<i>${d}</i>`).join('')}</div>
      <div class="year__days"></div>`;
    box.querySelector('.year__name').addEventListener('click', () => handlers.onOpenMonth(first));
    const days = box.querySelector('.year__days');
    for (const w of weeks) {
      for (const { iso, inMonth } of w) {
        const cell = document.createElement('button');
        cell.type = 'button';
        const v = inMonth ? byDay.get(iso) : null; // los días de fuera del mes no pintan nada: ya salen en su mes
        const lv = v ? level(v.n) : 0;
        cell.className = `year__day lv-${lv}${inMonth ? '' : ' is-out'}${inMonth && iso === today ? ' is-today' : ''}${v?.overdue ? ' has-overdue' : ''}${v?.live ? ' has-live' : ''}`;
        cell.dataset.date = iso;
        cell.textContent = inMonth ? String(Number(iso.slice(8, 10))) : '';
        cell.title = inMonth ? `${iso}: ${v ? `${v.n} tarea${v.n === 1 ? '' : 's'}${v.overdue ? ` · ${v.overdue} vencidas` : ''}${v.live ? ` · ${v.live} en curso` : ''}${v.closed ? ` · ${v.closed} hechas` : ''}` : 'nada'}` : '';
        if (!inMonth) cell.disabled = true;
        cell.addEventListener('click', () => handlers.onOpenMonth(iso));
        days.appendChild(cell);
      }
    }
    grid.appendChild(box);
  }
  scroll.appendChild(grid);
  root.appendChild(scroll);

  const legend = document.createElement('div');
  legend.className = 'year__legend';
  legend.innerHTML = `
    <span class="year__legend-total">${total} ocurrencia${total === 1 ? '' : 's'} en ${anchor.getMonth() === 0 ? anchor.getFullYear() : 'estos 12 meses'}</span>
    <span class="year__legend-scale"><i class="lv-0"></i><i class="lv-1"></i><i class="lv-2"></i><i class="lv-3"></i><i class="lv-4"></i> menos → más</span>
    <span><i class="year__legend-dot year__legend-dot--overdue"></i> vencidas</span>
    <span><i class="year__legend-dot year__legend-dot--live"></i> en curso</span>
    <span class="hint">‹ › desplaza la ventana un mes · clic en un día o en el mes → vista Mes</span>`;
  root.appendChild(legend);
  body.appendChild(root);
  /* si el año no cabe, que el mes en curso esté a la vista */
  root.querySelector('.year__month.is-current')?.scrollIntoView({ block: 'nearest' });
}
