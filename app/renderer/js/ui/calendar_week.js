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
import { toMin, toHHMM, durationOf, layoutOverlaps, badges, projectStripe, makeDayDraggable, markPending, MIN_DUR, isClosed, isOverdue, closedLine, placeScroll } from './calendar_day.js';

export const WEEK_HOUR_H = 48;     // px por hora — más denso que Día
const DAY_FROM = 7, DAY_TO = 22;

let handlers = null; // { onOpen(ticketId), onSchedule(item, patch) → Promise, todayISO() }
let weekRO = null;   // #308: el observador de la semana pintada; se cambia en cada render
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
    col.dataset.hfrom = hFrom;   // el arrastre por puntero convierte la altura en hora con esto
    grid.appendChild(col);
  }
  scroll.appendChild(grid);
  week.appendChild(scroll);
  body.appendChild(week);

  /* #308: cuántos carriles caben depende del ancho REAL de la columna, y aquí todavía no
     lo hay: renderWeek pinta sobre un body que calendar.js mete en el documento después,
     y un nodo fuera del documento mide 0 (la trampa del fitNarrow). Así que los eventos
     se colocan cuando la rejilla tiene tamaño —el ResizeObserver avisa al entrar— y se
     recolocan solo si al cambiar la ventana cambia el número de carriles. */
  const byDay = new Map(days.map(d => [d, timed.filter(it => it.date === d)
    .sort((a, b) => toMin(a.start) - toMin(b.start) || a.ticket_id - b.ticket_id)]));
  const layout = () => {
    for (const col of grid.querySelectorAll('.week__col')) {
      if (!col.clientWidth) continue;
      const lanes = Math.max(1, Math.floor(col.clientWidth / MIN_LANE_W));
      if (col.dataset.lanes === String(lanes)) continue;
      col.dataset.lanes = lanes;
      col.querySelectorAll('.week__ev, .week__more').forEach(el => el.remove());
      placeEvents(col, byDay.get(col.dataset.date), hFrom, lanes);
    }
    ajustarTitulos(grid);   // #497: cada vez, porque el ancho cambia sin que cambien los carriles
  };
  weekRO?.disconnect();
  weekRO = new ResizeObserver(layout);
  weekRO.observe(grid);

  const anchorMin = slots[0] ? toMin(slots[0][0]) : hFrom * 60;
  placeScroll(scroll, `week:${days[0]}`, Math.max(0, (anchorMin - hFrom * 60) / 60 * WEEK_HOUR_H - 6));   // repintar la misma semana no la sube
}

/* #497: TITULOS PARTIDOS A MEDIA LETRA, otra vez, y otra vez por una constante. El clamp
   salia de (alto - 24) / 17, con 24 px «medidos» para la fila de la hora. Pero esa fila
   lleva flex-wrap: en un carril estrecho (dos eventos en la misma columna, 91 px) la
   etiqueta «▲ vencida» baja a una segunda fila, la fila mide 38 y no 17, y el titulo se
   quedaba con 0 px y su linea partida por el overflow del padre. Medido: 5 de 11 eventos.
   Y el max(1, …) forzaba una linea donde no cabia ninguna.
   Es lo que la especificacion resuelve con max-lines: auto (CSS Overflow 4, §5.2: cortar
   en la ultima linea que CABE en el alto de la caja), que no se puede usar aun. Asi que se
   hace a mano lo mismo: se mide el sitio que le queda al titulo y se divide por su linea.
   Si no cabe ni una, primero sueltan sitio las etiquetas (lo vencido ya lo dice el filo
   rojo de la tarjeta y lo hecho el verde; las dos cosas siguen en el title); y si aun
   asi no cabe, la caja solo tiene UNA fila y hace lo mismo que .is-short: el titulo en
   una linea con puntos, sin la fila de hora (la hora ya la dice la posicion en la
   rejilla). Antes .is-short lo decidia la duracion y una caja de 45 min en un carril
   estrecho se quedaba con «11:00 alice» y sin titulo mientras la de 30 min al lado
   enseñaba el suyo: decide la altura, no la duracion. */
function ajustarTitulos(grid) {
  const evs = [...grid.querySelectorAll('.week__ev')];
  evs.forEach(ev => ev.classList.remove('is-apretado', 'is-una-fila'));
  const caben = (ev) => {
    const t = ev.querySelector('.week__mini-title');
    return Math.floor(t.clientHeight / parseFloat(getComputedStyle(t).lineHeight));
  };
  evs.filter(ev => caben(ev) < 1).forEach(ev => ev.classList.add('is-apretado'));
  const n = evs.map(caben);   // segunda lectura, ya sin las etiquetas
  evs.forEach((ev, i) => {
    ev.style.setProperty('--titulo-lineas', String(Math.max(1, n[i])));
    ev.classList.toggle('is-una-fila', n[i] < 1);
  });
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
  el.addEventListener('click', () => { if (!el._justDragged) handlers.onOpen(it.ticket_id); });   // el clic que cierra un arrastre no abre
  el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handlers.onOpen(it.ticket_id); } });
  if (!it.ghost && !isClosed(it)) makeDayDraggable(el, it, WEEK_DRAG);
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
     caben y se ponen puntos donde solo cabe una. Una sola regla para el que mira.
     #497: el calculo ya no se hace aqui con constantes, sino midiendo: ajustarTitulos(). */
  placeLane(el, colIdx, colCount);
  return el;
}

function placeLane(el, idx, count) {
  const w = 100 / count;
  el.style.left = `calc(${idx * w}% + 2px)`;
  el.style.width = `calc(${w}% - 4px)`;
}

/* #308: con densidad real (12 solapados a las 10:00) el ancho se partía sin tope y cada
   evento quedaba en una tira de 17-23 px con letras sueltas. Ahora una columna admite
   tantos carriles como quepan a MIN_LANE_W; si un grupo de solapes pide más, se pintan
   los primeros carriles y el último se lo queda un «+N más» por tramo de horas, que abre
   ese día en la vista Día (allí cada agente tiene su carril y caben). Un día tranquilo
   no llega al tope y se pinta exactamente igual que antes. */
const MIN_LANE_W = 80;   // px de columna por carril: por debajo el título no se lee
const MORE_MIN = 30;     // minutos de alto mínimo de un «+N» (24 px a WEEK_HOUR_H): cabe la cifra con borde y relleno

function placeEvents(col, list, hFrom, lanes) {
  const hidden = [];
  for (const { it, colIdx, colCount } of layoutOverlaps(list)) {
    if (colCount <= lanes) col.appendChild(renderEvent(it, hFrom, colIdx, colCount));
    else if (colIdx < lanes - 1) col.appendChild(renderEvent(it, hFrom, colIdx, lanes));
    else hidden.push(it);
  }
  /* los ocultos se juntan por tramos que se tocan: un «+N» por tramo, no uno que cruce el día.
     Se juntan por lo que OCUPAN en pantalla (MORE_MIN), no por su duración: un tramo de
     15 min mide 12 px y su cifra no cabe; estirado, pisaría al siguiente si no se uniera. */
  const tramos = [];
  for (const it of hidden.sort((a, b) => toMin(a.start) - toMin(b.start))) {
    const s = toMin(it.start), e = s + Math.max(durationOf(it), MIN_DUR, MORE_MIN);
    const t = tramos[tramos.length - 1];
    if (t && s < t.e) { t.e = Math.max(t.e, e); t.n++; } else tramos.push({ s, e, n: 1 });
  }
  for (const t of tramos) col.appendChild(renderMore(col.dataset.date, t, hFrom, lanes));
}

function renderMore(date, { s, e, n }, hFrom, lanes) {
  const el = document.createElement('button');
  el.type = 'button';
  el.className = 'week__more';
  /* la cifra y la palabra van aparte: en un carril estrecho la palabra baja de línea y se
     esconde, y la cifra se sigue leyendo */
  el.innerHTML = `<b>+${n}</b> <span>${esc(tr('más'))}</span>`;
  el.title = tr('abrir este día');
  el.setAttribute('aria-label', `${tr(`+${n} más`)}, ${toHHMM(s)}–${toHHMM(e)}. ${tr('abrir este día')}`);
  el.style.top = `${(s - hFrom * 60) / 60 * WEEK_HOUR_H}px`;
  el.style.height = `${(e - s) / 60 * WEEK_HOUR_H}px`;
  placeLane(el, lanes - 1, lanes);
  el.addEventListener('click', () => handlers.onOpenDay?.(date));
  return el;
}

/* ---------------- arrastre: a un día (banda) o a un día + hora (rejilla) ----------------
   #924: el mismo arrastre por puntero que el Día (ratón, dedo, lápiz), con destinos por día.
   Aquí no hay carriles por agente: el dueño no cambia. Una recurrente no va al pasado (regla
   del motor): el destino se pinta gris y no se suelta. */
const WEEK_DRAG = {
  targets: '.week__col, .week__band-cell',
  grid: 'week__col',
  hourH: WEEK_HOUR_H,
  allowed: (t, it) => !(it.cadence_days && t.dataset.date < handlers.todayISO()),
  label: () => '',
  toPatch: (t, m, it, duration) => {
    const date = t.dataset.date;
    if (m != null) {
      if (date === it.date && it.start && toMin(it.start) === m) return null;   // soltada donde estaba
      return { date, start: toHHMM(m), duration_min: duration };
    }
    if (date === it.date && !it.start) return null;                            // sin hora, a su mismo día
    return { date, start: null };
  },
};
