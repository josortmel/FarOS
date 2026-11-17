/* ============================================================
   calendar_day.js — vista Día (T2.5). Un día entero, dividido por
   horas, de un solo vistazo. Decisión 5 del dueño: un carril por
   agente. Decisión 8: las franjas de la Casa (settings.casa_slots)
   sombreadas. Arriba, la banda «sin hora»: lo que vence ese día
   sin hora fijada — se arrastra a una hora y el motor lo programa.
   Sin estado optimista: la tarjeta no se mueve hasta que el motor
   dice que sí; si dice que no, toast con el motivo y sigue donde
   estaba.
   ============================================================ */

import { fmtWhen, fmtDate, esc, projectColor } from '../util.js';
import { t as tr } from '../i18n.js';  // #314

/* kind=closed (contrato del 3-sep): el trabajo hecho del día se ve, tachado, y no se toca */
export const isClosed = (it) => it.kind === 'closed';
/* vencida = la ocurrencia real de una vencida; ni cerradas ni proyecciones */
export const isOverdue = (it) => !isClosed(it) && !it.ghost && Boolean(it.overdue_since || it.due_state === 'overdue');
export function closedLine(it) {
  const when = it.closed_at ? fmtWhen(it.closed_at) : '';
  return `${STATUS_LABEL[it.status] ?? it.status}${when ? ' · ' + when : ''}`;
}
import { STATUS_LABEL } from '../transitions.js';

export const HOUR_H = 64;          // px por hora
/* #1197 (Pepe, 13-nov): al soltar, la tarea va libre de 5 en 5 minutos y, cerca de en punto o de
   y media, se queda pegada a esa hora como un imán. El imán abarca MAGNET_MIN a cada lado: con 4,
   la media hora atrapa 8 minutos de recorrido (antes 30, ahora casi el doble que un paso de 5) y
   :25 y :35 se siguen pudiendo elegir. */
export const SNAP_MIN = 5;
export const MAGNET_MIN = 4;
export function snapMinute(raw) {
  const half = Math.round(raw / 30) * 30;
  return Math.abs(raw - half) <= MAGNET_MIN ? half : Math.round(raw / SNAP_MIN) * SNAP_MIN;
}
const STRETCH_SNAP = 15;           // el estirado se pega al cuarto de hora
export const MIN_DUR = 15;
export const EMPTY_LANE_PX = 96;   // un carril sin nada solo dice su nombre
/* #795: el ancho mínimo de cada subcolumna de solape. Por debajo, el título se queda en una
   o dos letras por línea («F…», «E…»): con siete a la vez en el iPad salían de 52 px. */
export const SUBCOL_MIN_PX = 140;
export const DEFAULT_DUR = 60;
const DAY_FROM = 7, DAY_TO = 22;   // horas visibles por defecto; se abren si hay algo fuera

let handlers = null; // { onOpen(ticketId), onSchedule(item, patch) → Promise, onStamp(item), todayISO() }
export let dragItem = null; // { ticket_id, owner, date, cadence, timed, duration } — arrastre en curso (Día y Semana)
export function getDrag() { return dragItem; }

export function initDay(h) { handlers = h; }

/* ---------------- tiempo ---------------- */

export function toMin(hhmm) { return hhmm ? Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5)) : null; }
export function toHHMM(min) { return `${String(Math.floor(min / 60)).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`; }
export function durationOf(it) {
  if (it.duration_min) return Number(it.duration_min);
  const a = toMin(it.start), b = toMin(it.end);
  return a != null && b != null && b > a ? b - a : DEFAULT_DUR;
}

/* ---------------- render ---------------- */

export function renderDay(body, state) {
  const data = state.calData;
  const date = state.calDate;
  body.innerHTML = '';
  body.classList.add('cal__body--day');

  if (data == null) {
    body.insertAdjacentHTML('beforeend', '<div class="cal__placeholder"><b>Día</b><span>cargando…</span></div>');
    return;
  }

  const lanes = data.lanes ?? [];
  const slots = data.slots ?? [];
  const items = (data.items ?? []).filter(it => it.date === date);
  const timed = items.filter(it => it.start);
  const loose = items.filter(it => !it.start);

  /* rango de horas: el de la Casa, abierto si hay algo fuera */
  let hFrom = DAY_FROM, hTo = DAY_TO;
  for (const it of timed) {
    hFrom = Math.min(hFrom, Math.floor(toMin(it.start) / 60));
    hTo = Math.max(hTo, Math.ceil((toMin(it.start) + durationOf(it)) / 60));
  }
  hTo = Math.min(hTo, 24);
  const hours = [];
  for (let h = hFrom; h < hTo; h++) hours.push(h);
  const gridH = hours.length * HOUR_H;
  const isToday = date === handlers.todayISO();

  const day = document.createElement('div');
  day.className = 'day';
  day.style.setProperty('--lanes', lanes.length);
  /* T2.2-C: el ancho va donde hay contenido. Un carril vacío solo dice «este no tiene nada»
     (y admite que le suelten una tarea de otro, #924) — y eso cabe en 96 px.
     Repartir por igual era gastar el 80 % del ancho en carriles vacíos y obligar a truncar
     los títulos del único que trabajaba ese día. */
  const laneCount = Object.fromEntries(lanes.map(l => [l, items.filter(it => it.owner === l).length]));
  /* #795: lo mismo con los solapes. El carril con más tareas a la vez se ensancha (cada
     subcolumna, al menos SUBCOL_MIN_PX) y, si no cabe, la rejilla se desliza de lado.
     No se esconde ninguna tarea. */
  const layouts = Object.fromEntries(lanes.map(l => [l, layoutOverlaps(
    timed.filter(it => it.owner === l).sort((a, b) => toMin(a.start) - toMin(b.start) || a.ticket_id - b.ticket_id))]));
  const maxCols = Object.fromEntries(lanes.map(l => [l, Math.max(0, ...layouts[l].map(p => p.colCount))]));
  if (lanes.some(l => laneCount[l])) {
    day.style.setProperty('--lane-cols',
      lanes.map(l => laneCount[l]
        ? `minmax(${Math.max(180, maxCols[l] * SUBCOL_MIN_PX)}px, ${Math.max(1, maxCols[l])}fr)`
        : `${EMPTY_LANE_PX}px`).join(' '));
  }
  /* el móvil enseña un carril cada vez (mobile.js): le dice el mínimo del que elija */
  day.dataset.laneMins = lanes.map(l => maxCols[l] > 1 ? maxCols[l] * SUBCOL_MIN_PX : 0).join(',');

  /* ---- cabecera de carriles ---- */
  const head = document.createElement('div');
  head.className = 'day__head';
  head.innerHTML = `<div class="day__corner"></div>` + lanes.map(l => {
    const n = items.filter(it => it.owner === l).length;
    return `<div class="day__lane-head"><span class="day__lane-name">${esc(l)}</span><span class="day__lane-count">${n ? n : '—'}</span></div>`;
  }).join('');
  day.appendChild(head);

  /* ---- banda sin hora ---- */
  const band = document.createElement('div');
  band.className = 'day__band';
  /* T2.3 (el dueño: «el subtablón de sin hora debería ser solo para las que de verdad no
     tengan hora… incluso eso no debería existir»): la banda pasa a ser la EXCEPCIÓN.
     Vacía no ocupa sitio; reaparece durante un arrastre porque es donde se suelta para
     quitar la hora, y una acción sin sitio donde soltarla es una acción perdida. */
  if (!loose.length) band.classList.add('is-empty');
  const bandLabel = document.createElement('div');
  bandLabel.className = 'day__band-label';
  bandLabel.innerHTML = `<span>sin hora</span><small>${loose.length ? 'arrastra abajo para darle hora' : 'quitar la hora'}</small>`;
  band.appendChild(bandLabel);
  for (const l of lanes) {
    const cell = document.createElement('div');
    cell.className = 'day__band-cell';
    cell.dataset.lane = l;
    for (const it of loose.filter(x => x.owner === l)) cell.appendChild(renderChip(it, { pointer: true }));
    /* soltar aquí una tarjeta con hora = quitarle la hora (all_day / sin programar): lo resuelve
       el arrastre por puntero (makeDayDraggable) leyendo data-lane */
    band.appendChild(cell);
  }
  day.appendChild(band);

  /* ---- rejilla horas × carriles ---- */
  const scroll = document.createElement('div');
  scroll.className = 'day__scroll';
  const grid = document.createElement('div');
  grid.className = 'day__grid';
  grid.style.height = `${gridH}px`;

  const gutter = document.createElement('div');
  gutter.className = 'day__gutter';
  for (const h of hours) {
    const row = document.createElement('div');
    row.className = 'day__hour';
    row.style.height = `${HOUR_H}px`;
    row.innerHTML = `<span>${String(h).padStart(2, '0')}:00</span>`;
    gutter.appendChild(row);
  }
  grid.appendChild(gutter);

  for (const l of lanes) {
    const col = document.createElement('div');
    col.className = 'day__lane';
    col.dataset.lane = l;
    /* franjas de la Casa (decisión 8): el tramo en que la casa está abierta, sombreado */
    for (const [a, b] of slots) {
      const top = (toMin(a) - hFrom * 60) / 60 * HOUR_H;
      const h = (toMin(b) - toMin(a)) / 60 * HOUR_H;
      if (h <= 0) continue;
      const s = document.createElement('div');
      s.className = 'day__slot';
      s.style.top = `${top}px`;
      s.style.height = `${h}px`;
      s.title = `franja de la Casa ${a}–${b}`;
      col.appendChild(s);
    }
    /* líneas de hora */
    for (let i = 0; i < hours.length; i++) {
      const ln = document.createElement('div');
      ln.className = 'day__line';
      ln.style.top = `${i * HOUR_H}px`;
      col.appendChild(ln);
      const half = document.createElement('div');
      half.className = 'day__line day__line--half';
      half.style.top = `${i * HOUR_H + HOUR_H / 2}px`;
      col.appendChild(half);
    }
    /* eventos del carril, con solapes repartidos en columnas */
    for (const { it, colIdx, colCount } of layouts[l]) {
      col.appendChild(renderEvent(it, hFrom, colIdx, colCount));
    }
    col.dataset.hfrom = hFrom;   // makeDayDraggable convierte la altura en hora con esto
    grid.appendChild(col);
  }

  /* ahora: solo si el día es hoy y la hora cae en la rejilla */
  if (isToday) {
    const now = new Date();
    const m = now.getHours() * 60 + now.getMinutes();
    if (m >= hFrom * 60 && m <= hTo * 60) {
      const line = document.createElement('div');
      line.className = 'day__now';
      line.style.top = `${(m - hFrom * 60) / 60 * HOUR_H}px`;
      line.innerHTML = `<span class="day__now-time">${toHHMM(m)}</span>`;
      grid.appendChild(line);
    }
  }

  scroll.appendChild(grid);
  /* #795: la rejilla se desliza de lado; la cabecera y la banda van con ella */
  scroll.addEventListener('scroll', () => { head.scrollLeft = band.scrollLeft = scroll.scrollLeft; }, { passive: true });
  day.appendChild(scroll);
  body.appendChild(day);

  /* que la mañana esté a la vista: hoy, la hora actual; otro día, la primera franja */
  const anchorMin = isToday ? (new Date().getHours() * 60 - 60) : (slots[0] ? toMin(slots[0][0]) : hFrom * 60);
  placeScroll(scroll, `day:${date}`, Math.max(0, (anchorMin - hFrom * 60) / 60 * HOUR_H - 8));
}

/* Pepe (2-nov): «se recarga todo el tablero y me mueve hacia arriba, lo cual es molesto si estoy
   reajustando varias tareas». Cada repintado (soltar, guardar en el panel, el refresco) colocaba la
   rejilla en su hora de anclaje. Ahora, si se repinta el MISMO día o la misma semana, se queda donde
   la dejó; solo un día o una semana nuevos se colocan en su hora. */
let lastScroll = { key: null, top: 0, left: 0 };
export function placeScroll(scroll, key, anchorTop) {
  const apply = () => {
    if (lastScroll.key === key) {
      scroll.scrollTop = lastScroll.top;
      scroll.scrollLeft = lastScroll.left;   // el evento de scroll arrastra la cabecera y la banda con ella
    } else {
      scroll.scrollTop = anchorTop;
      lastScroll = { key, top: scroll.scrollTop, left: scroll.scrollLeft };
    }
    scroll.addEventListener('scroll', () => {
      lastScroll = { key, top: scroll.scrollTop, left: scroll.scrollLeft };
    }, { passive: true });
  };
  /* la vista se pinta fuera de la página y calendar.js la mete después: en un elemento que no está
     en la página el scroll no se aplica (medido: se quedaba en 0, y por eso la hora de anclaje
     tampoco funcionaba nunca). Se aplica en cuanto entra, antes del primer pintado. */
  if (scroll.isConnected) apply(); else queueMicrotask(apply);
}

/* T2.2-C: el ancho de una tarjeta es el carril partido entre columnas, y el carril
   depende de cuántos dueños tengan algo ese día — no se sabe hasta montarlo. Se mide
   una vez, ya montado, y por debajo de 200 px la tarjeta suelta la hora (la rejilla ya
   la da por posición) y le da esas líneas al título. */
export const NARROW_PX = 200;
export function fitNarrow(root) {
  const evs = root.querySelectorAll('.day__ev');
  if (!evs.length) return;
  const widths = [...evs].map(el => el.getBoundingClientRect().width);   // leer todo…
  if (!widths.some(w => w > 0)) {   // árbol desprendido: medir aquí es medir cero
    console.warn('fitNarrow: el árbol no está en el documento; no se puede medir el ancho');
    return;
  }
  evs.forEach((el, i) => el.classList.toggle('is-narrow', widths[i] < NARROW_PX));
}

/* solapes: columnas dentro del carril (greedy, como un calendario de escritorio) */
export function layoutOverlaps(list) {
  const out = [];
  let cluster = [], clusterEnd = -1;
  const flush = () => {
    if (!cluster.length) return;
    const colsEnd = [];
    const placed = cluster.map(it => {
      const s = toMin(it.start), e = s + Math.max(durationOf(it), MIN_DUR);
      let c = colsEnd.findIndex(end => end <= s);
      if (c === -1) { c = colsEnd.length; colsEnd.push(e); } else colsEnd[c] = e;
      return { it, colIdx: c };
    });
    for (const p of placed) out.push({ ...p, colCount: colsEnd.length });
    cluster = []; clusterEnd = -1;
  };
  for (const it of list) {
    const s = toMin(it.start), e = s + Math.max(durationOf(it), MIN_DUR);
    if (cluster.length && s >= clusterEnd) flush();
    cluster.push(it);
    clusterEnd = Math.max(clusterEnd, e);
  }
  flush();
  return out;
}

/* ---------------- piezas ---------------- */

function statusLine(it) {
  const lc = it.last_change;
  return lc ? `→ ${esc(STATUS_LABEL[lc.to] ?? lc.to)} · ${fmtWhen(lc.at)}${lc.by ? ' · ' + esc(lc.by) : ''}` : '';
}
export function badges(it) {
  const b = [];
  /* vencida solo la ocurrencia real: una proyección (ghost) de una vencida no está vencida */
  if (it.overdue_since) b.push(`<span class="badge badge--overdue">▲ ${tr('vencida desde')} ${esc(fmtDate(it.overdue_since))}</span>`);
  else if (it.due_state === 'overdue' && !it.ghost) b.push(`<span class="badge badge--overdue">${tr('▲ vencida')}</span>`);   // #497: la semana pinta despues del recorrido
  if (it.cadence_days) b.push(`<span class="badge badge--recur">↻ ${it.cadence_days} d${it.ghost ? ' · prevista' : ''}</span>`);
  if (it.all_day) b.push(`<span class="badge badge--recur">todo el día</span>`);
  return b.join('');
}
export function projectStripe(it) {
  return projectColor({ id: it.project_id });
}

/* chip de la banda sin hora */
export function renderChip(it, { pointer = false } = {}) {
  const el = document.createElement('article');
  el.className = `day__chip${it.ghost ? ' is-ghost' : ''}${isClosed(it) ? ' is-closed' : ''}${isOverdue(it) ? ' is-overdue' : ''}`;
  el.dataset.id = it.ticket_id;
  el.tabIndex = 0;
  el.setAttribute('role', 'button');
  el.setAttribute('aria-label', `${it.title} — ${tr('sin hora. Abrir detalle')}`);
  el.style.setProperty('--stripe', projectStripe(it));
  el.innerHTML = `
    <div class="day__chip-title">${esc(it.title)}</div>
    <div class="day__chip-meta">${isClosed(it) ? '' : badges(it)}<span class="day__status">${isClosed(it) ? '✓ ' + esc(closedLine(it)) : esc(STATUS_LABEL[it.status] ?? it.status)}</span></div>
    ${!isClosed(it) && statusLine(it) ? `<div class="day__change">${statusLine(it)}</div>` : ''}`;
  wireOpen(el, it);
  if (!it.ghost && !isClosed(it) && !it.readonly) {
    (pointer ? makeDayDraggable : makeDraggable)(el, it);   // el Día arrastra por puntero; el Mes, todavía nativo
    el.appendChild(recurrenceActions(it));
  }
  return el;
}

/* tarjeta con hora, absoluta en su carril */
function renderEvent(it, hFrom, colIdx, colCount) {
  const s = toMin(it.start), dur = Math.max(durationOf(it), MIN_DUR);
  const el = document.createElement('article');
  /* T2.2-A: lo que cabe lo decide la ALTURA en px, no la duración. El presupuesto real
     de la caja es padding 12,8 + hora 16 + título 18,2/línea + badges 20 + cambio 20:
     una tarea de 60 min son 64 px y el contenido lleno pide 107. Escalones medidos. */
  const px = dur / 60 * HOUR_H;
  const fit = px < 28 ? ' is-fit-micro' : px < 50 ? ' is-fit-xs' : px < 70 ? ' is-fit-sm' : px < 112 ? ' is-fit-md' : '';
  el.className = `day__ev day__ev--${it.status}${it.ghost ? ' is-ghost' : ''}${isClosed(it) ? ' is-closed' : ''}${isOverdue(it) ? ' is-overdue' : ''}${fit}`;
  el.dataset.id = it.ticket_id;
  el.tabIndex = 0;
  el.setAttribute('role', 'button');
  el.setAttribute('aria-label', `${it.title}, ${it.start}${it.end ? '–' + it.end : ''}. ${tr('Abrir detalle')}`);
  el.style.setProperty('--stripe', projectStripe(it));
  el.style.top = `${(s - hFrom * 60) / 60 * HOUR_H}px`;
  el.style.height = `${dur / 60 * HOUR_H}px`;
  const w = 100 / colCount;
  el.style.left = `calc(${colIdx * w}% + 2px)`;
  el.style.width = `calc(${w}% - 4px)`;
  const end = it.end ?? toHHMM(Math.min(s + dur, 24 * 60 - 1));
  /* T2.2-C: al partir el ancho entre columnas el título multiplica líneas; con 3 o más
     el rango completo se come la fila, así que se queda solo la hora de inicio. */
  const timeLabel = colCount >= 3 ? esc(it.start) : `${esc(it.start)}–${esc(end)}`;
  el.dataset.cols = Math.min(colCount, 4);   // el CSS mide el carril y deduce el ancho real
  el.title = it.title;
  el.innerHTML = `
    <div class="day__ev-time">${timeLabel} <span class="day__status">${isClosed(it) ? '✓ ' + esc(closedLine(it)) : esc(STATUS_LABEL[it.status] ?? it.status)}</span></div>
    <div class="day__ev-title">${esc(it.title)}</div>
    <div class="day__ev-meta">${isClosed(it) ? '' : badges(it)}</div>
    ${!isClosed(it) && statusLine(it) ? `<div class="day__change">${statusLine(it)}</div>` : ''}
    ${it.ghost || isClosed(it) ? '' : '<div class="day__ev-handle" title="estirar para cambiar la duración" aria-hidden="true"></div>'}`;
  wireOpen(el, it);
  if (!it.ghost && !isClosed(it) && !it.readonly) {
    makeDayDraggable(el, it);
    wireStretch(el, it, dur);
    /* T2.2-B: las acciones ya no viven dentro de la caja (antes: absolute+bottom, opacas,
       envolviendo a 3 filas y tapando el título de su propia tarjeta). Ahora son un popover
       bajo la tarjeta, visible solo con hover o foco — por eso ya no dependen de la altura. */
    el.insertBefore(recurrenceActions(it), el.querySelector('.day__ev-handle'));
  }
  return el;
}

/* ---------------- T2.9: operar la recurrente desde su fecha ----------------
   Solo en la ocurrencia REAL (ghost=false, la próxima). Estampar = completar con
   evidencia (el motor la devuelve a su ciclo). Mover la próxima = POST /shift_next
   (la cadencia no cambia). Hora preferida = PATCH preferred_time. Los ghosts se
   miran, no se operan. */
export function recurrenceActions(it) {
  const wrap = document.createElement('div');
  if (!it.cadence_days || it.ghost) return wrap;
  wrap.className = 'day__acts';
  wrap.innerHTML = `
    <button type="button" class="day__act day__act--stamp" title="completar con evidencia: vuelve a su ciclo">✓ Estampar…</button>
    <button type="button" class="day__act" data-act="next" title="mover solo la próxima ocurrencia; la cadencia no cambia">→ Próxima…</button>
    <button type="button" class="day__act" data-act="time" title="hora preferida de esta recurrente">⏰ Hora…</button>
    <form class="day__act-form" data-form="next" hidden>
      <input type="date" min="${handlers.todayISO()}" value="${esc(it.date)}" aria-label="Mover la próxima a">
      <button type="submit" class="btn btn--primary btn--sm">Mover</button>
      <span class="hint">solo la próxima</span>
    </form>
    <form class="day__act-form" data-form="time" hidden>
      <input type="time" value="${esc(it.preferred_time ?? it.start ?? '')}" aria-label="Hora preferida">
      <button type="submit" class="btn btn--primary btn--sm">Guardar</button>
      <button type="button" class="btn btn--ghost btn--sm" data-clear>Sin hora</button>
    </form>`;
  const stop = (e) => e.stopPropagation();
  wrap.addEventListener('click', stop);
  wrap.addEventListener('keydown', stop);
  wrap.addEventListener('dragstart', (e) => e.preventDefault());
  const forms = { next: wrap.querySelector('[data-form="next"]'), time: wrap.querySelector('[data-form="time"]') };
  wrap.querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', () => {
    const f = forms[b.dataset.act];
    const open = f.hidden;
    Object.values(forms).forEach(x => { x.hidden = true; });
    f.hidden = !open;
    if (open) f.querySelector('input').focus();
  }));
  wrap.querySelector('.day__act--stamp').addEventListener('click', () => handlers.onStamp(it));
  forms.next.addEventListener('submit', (e) => {
    e.preventDefault();
    const date = forms.next.querySelector('input').value;
    if (!date || date === it.date) { forms.next.hidden = true; return; }
    markPending(it.ticket_id, true);
    handlers.onSchedule({ ticket_id: it.ticket_id, owner: it.owner, date: it.date, cadence: true, timed: Boolean(it.start), duration: durationOf(it) }, { date }) // solo la fecha: la hora preferida no se toca
      .finally(() => markPending(it.ticket_id, false));
  });
  const saveTime = (value) => {
    markPending(it.ticket_id, true);
    handlers.onSchedule({ ticket_id: it.ticket_id, owner: it.owner, date: it.date, cadence: true, timed: Boolean(it.start), duration: durationOf(it) }, { date: it.date, start: value || null, preferredOnly: true })
      .finally(() => markPending(it.ticket_id, false));
  };
  forms.time.addEventListener('submit', (e) => { e.preventDefault(); saveTime(forms.time.querySelector('input').value); });
  forms.time.querySelector('[data-clear]').addEventListener('click', () => saveTime(''));
  return wrap;
}

function wireOpen(el, it) {
  el.addEventListener('click', (e) => {
    if (el.classList.contains('is-stretching') || e.target.classList.contains('day__ev-handle')) return;
    if (el._justDragged) return;   // el clic que cierra un arrastre no abre el panel
    handlers.onOpen(it.ticket_id);
  });
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handlers.onOpen(it.ticket_id); }
  });
}

/* ---------------- arrastre (misma mecánica que el kanban: solo affordance) ---------------- */

export function makeDraggable(el, it) {
  if (it.readonly) return; // T5.2: los jobs/runs del calendario de Agentes no se arrastran (se editan en el modal)
  el.draggable = true;
  el.addEventListener('dragstart', (e) => {
    if (el.classList.contains('is-stretching')) { e.preventDefault(); return; }
    dragItem = { ticket_id: it.ticket_id, owner: it.owner, date: it.date, cadence: Boolean(it.cadence_days), timed: Boolean(it.start), duration: durationOf(it) };
    el.classList.add('is-dragging');
    /* T2.3: la banda «sin hora» vacía no se pinta (el dueño: «eso no debería existir»), pero
       es el sitio donde se suelta para QUITAR la hora. Se enseña mientras dura el
       arrastre: existe exactamente cuando sirve. */
    document.body.classList.add('is-dragging-cal');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', String(it.ticket_id));
  });
  el.addEventListener('dragend', () => {
    el.classList.remove('is-dragging');
    document.body.classList.remove('is-dragging-cal');
    dragItem = null;
    clearDropMarks();
  });
}

export function clearDropMarks() {
  document.querySelectorAll('.day__lane, .day__band-cell, .week__col, .week__band-cell, .month__cell').forEach(c => c.classList.remove('is-drop-ok', 'is-drop-no'));
  document.querySelectorAll('.day__ghost').forEach(g => g.remove());
}

/* #924 (Pepe, 2-nov): en el Día se arrastra con eventos de PUNTERO, no con el arrastre nativo
   de HTML5. El nativo no existe con el dedo (iPad, móvil) y puede dejar a Chromium dentro de su
   bucle de arrastre (las e2e lo esquivaban). Con el puntero vale igual ratón, dedo y lápiz:
   - ratón y lápiz: empieza al moverse 6 px (un clic sigue abriendo el panel);
   - dedo: empieza al mantenerlo quieto LONG_PRESS_MS; si se mueve antes, es un desplazamiento
     y se deja al navegador (las tarjetas no llevan touch-action: none, o no se podría desplazar
     el calendario por encima de ellas).
   Se suelta en cualquier carril: si es de otro, la tarea pasa a ese agente (antes estaba
   prohibido; lo pidió el dueño). Soltar en la banda «sin hora» le quita la hora. */
const LONG_PRESS_MS = 350;
const MOVE_SLOP_PX = 6;
const EDGE_PX = 40;          // cerca del borde del scroll, la rejilla se desliza sola
const EDGE_SPEED = 10;       // px por fotograma, como mucho (pegado al filo)
const EDGE_ARM_PX = 40;      // y solo cuando el puntero se ha alejado esto de donde cogió la tarjeta

/* Lo que cambia entre vistas: dónde se puede soltar, qué altura tiene una hora y qué cambio
   pide cada destino. Por defecto, el Día (carriles por agente); la Semana pasa los suyos. */
const DAY_DRAG = {
  targets: '.day__lane, .day__band-cell',
  grid: 'day__lane',
  hourH: HOUR_H,
  allowed: () => true,
  label: (t, it) => (t.dataset.lane !== it.owner ? ` · ${t.dataset.lane}` : ''),
  /* m = minuto en la rejilla, o null si es la banda «sin hora». null = nada que hacer */
  toPatch: (t, m, it, duration) => {
    const owner = t.dataset.lane !== it.owner ? t.dataset.lane : undefined;
    if (m != null) {
      if (!owner && it.start && toMin(it.start) === m) return null;   // soltada donde estaba
      return { date: it.date, start: toHHMM(m), duration_min: duration, owner };
    }
    if (!owner && !it.start) return null;                              // sin hora, a su misma banda
    return { date: it.date, start: null, all_day: true, owner };
  },
};

export function makeDayDraggable(el, it, opts = {}) {
  if (it.readonly) return;
  const cfg = { ...DAY_DRAG, ...opts };
  el.draggable = false;
  el.addEventListener('dragstart', (e) => e.preventDefault());   // ni una imagen nativa de arrastre
  el.addEventListener('pointerdown', (down) => {
    if (down.button !== 0 || !down.isPrimary) return;
    if (down.target.closest('.day__ev-handle, .day__acts, button, input, form')) return;
    if (el.classList.contains('is-stretching')) return;
    const touch = down.pointerType === 'touch';
    const x0 = down.clientX, y0 = down.clientY;
    const r0 = el.getBoundingClientRect();
    const grabDy = it.start ? y0 - r0.top : 0;   // la tarjeta se coge por donde se toca
    const duration = durationOf(it);
    const scroll = el.closest('.day__scroll') ?? el.closest('.day, .week')?.querySelector('.day__scroll');
    let active = false, x = x0, y = y0, timer = null, raf = 0, ghost = null, target = null;

    const findTarget = () => {
      for (const n of document.elementsFromPoint(x, y)) {
        const t = n.closest?.(cfg.targets);
        if (t) return t;
      }
      return null;
    };
    const minuteIn = (lane) => {
      const hFrom = Number(lane.dataset.hfrom);
      const top = y - grabDy - lane.getBoundingClientRect().top;
      const m = hFrom * 60 + snapMinute(top / cfg.hourH * 60);
      return Math.max(hFrom * 60, Math.min(m, 23 * 60 + 30));
    };
    const paint = () => {
      const t = findTarget();
      if (t !== target) {
        target?.classList.remove('is-drop-ok', 'is-drop-no');
        target = t;
        target?.classList.add(cfg.allowed(target, it) ? 'is-drop-ok' : 'is-drop-no');
      }
      if (!target || !target.classList.contains(cfg.grid) || !cfg.allowed(target, it)) { ghost?.remove(); ghost = null; return; }
      const m = minuteIn(target);
      if (!ghost || ghost.parentNode !== target) {
        ghost?.remove();
        ghost = document.createElement('div');
        ghost.className = 'day__ghost';
        target.appendChild(ghost);
      }
      const hFrom = Number(target.dataset.hfrom);
      ghost.style.top = `${(m - hFrom * 60) / 60 * cfg.hourH}px`;
      ghost.style.height = `${Math.max(duration, MIN_DUR) / 60 * cfg.hourH}px`;
      ghost.textContent = `${toHHMM(m)}–${toHHMM(Math.min(m + duration, 24 * 60 - 1))}${cfg.label(target, it)}`;
    };
    /* el borde del scroll empuja la rejilla: sin esto, con el dedo no se llega a la tarde.
       Solo después de alejarse EDGE_ARM_PX de donde se cogió: una tarjeta de primera hora está
       pegada al borde de arriba, y sin esto la rejilla se escapaba nada más cogerla (medido). */
    const edgeLoop = () => {
      if (!active) return;
      if (scroll && Math.hypot(x - x0, y - y0) > EDGE_ARM_PX) {
        const r = scroll.getBoundingClientRect();
        /* la velocidad crece con lo que se mete en el borde: cero al rozarlo, EDGE_SPEED en el
           filo. A velocidad fija (14 px/fotograma ≈ 840 px/s) en una rejilla baja (iPad con
           propuestas arriba) se pasaba de largo antes de poder soltar (medido en la e2e) */
        const speed = (into) => Math.round(EDGE_SPEED * Math.min(1, Math.max(0, into) / EDGE_PX));
        const dy = -speed(r.top + EDGE_PX - y) + speed(y - (r.bottom - EDGE_PX));
        const dx = -speed(r.left + EDGE_PX - x) + speed(x - (r.right - EDGE_PX));
        if (dy || dx) { scroll.scrollTop += dy; scroll.scrollLeft += dx; paint(); }
      }
      raf = requestAnimationFrame(edgeLoop);
    };
    const begin = () => {
      active = true;
      el.classList.add('is-dragging');
      /* T2.3: la banda «sin hora» vacía reaparece para poder soltar en ella, y empuja la rejilla
         hacia abajo lo que mide. Se compensa con el scroll para que nada salte bajo el puntero
         (medido: sin esto, todo bajaba 64 px al cogerla y la tarea caía una hora antes). */
      const top0 = scroll?.getBoundingClientRect().top;
      document.body.classList.add('is-dragging-cal');
      if (scroll) scroll.scrollTop += scroll.getBoundingClientRect().top - top0;
      try { el.setPointerCapture(down.pointerId); } catch { /* ya soltado */ }
      paint();
      raf = requestAnimationFrame(edgeLoop);
    };
    /* con el dedo, una vez activo, el navegador no debe desplazar: el primer touchmove se cancela */
    const noScroll = (e) => { if (active) e.preventDefault(); };
    /* #1110: si fue pulsación larga lo dice la HORA DEL GESTO (timeStamp, la del hardware en un
       móvil), no el temporizador: cuando los eventos llegan tarde al navegador, el temporizador
       vence antes de procesar un movimiento que ocurrió a los 30 ms, y un deslizamiento se tomaba
       por arrastre (la tarea saltaba al soltar). Medido en la e2e D4: 5 de 5 antes del arreglo. */
    let held = !touch;
    const early = (e) => e.timeStamp - down.timeStamp < LONG_PRESS_MS;
    const move = (e) => {
      x = e.clientX; y = e.clientY;
      const far = Math.hypot(x - x0, y - y0) > MOVE_SLOP_PX;
      if (!active) {
        if (touch) { if (far) end(false); }   // se movió antes de tiempo: era un desplazamiento
        else if (far) begin();
        return;
      }
      if (!held) {
        if (early(e)) { if (far) { end(false); return; } }   // el temporizador se adelantó al dedo
        else held = true;
      }
      paint();
    };
    const key = (e) => { if (e.key === 'Escape') end(false); };
    const finish = (e) => end(held || !early(e));
    const cancel = () => end(false);
    function end(drop) {
      clearTimeout(timer);
      cancelAnimationFrame(raf);
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', finish);
      window.removeEventListener('pointercancel', cancel);
      window.removeEventListener('keydown', key);
      el.removeEventListener('touchmove', noScroll);
      if (!active) return;
      active = false;
      const t = target;
      const m = t?.classList.contains(cfg.grid) ? minuteIn(t) : null;
      ghost?.remove(); ghost = null;
      t?.classList.remove('is-drop-ok', 'is-drop-no');
      el.classList.remove('is-dragging');
      const top1 = scroll?.getBoundingClientRect().top;
      document.body.classList.remove('is-dragging-cal');   // la banda se va: la misma compensación al revés
      if (scroll) scroll.scrollTop += scroll.getBoundingClientRect().top - top1;
      el._justDragged = true;
      setTimeout(() => { el._justDragged = false; }, 0);
      if (!drop || !t || !cfg.allowed(t, it)) return;
      const d = { ticket_id: it.ticket_id, owner: it.owner, date: it.date, cadence: Boolean(it.cadence_days), timed: Boolean(it.start), duration };
      const patch = cfg.toPatch(t, m, it, duration);
      if (!patch) return;
      markPending(it.ticket_id, true);
      handlers.onSchedule(d, patch).finally(() => markPending(it.ticket_id, false));
    }
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', finish);
    window.addEventListener('pointercancel', cancel);
    window.addEventListener('keydown', key);
    el.addEventListener('touchmove', noScroll, { passive: false });
    if (touch) timer = setTimeout(begin, LONG_PRESS_MS);
  });
}

export function markPending(id, on) {
  document.querySelectorAll(`.day__ev[data-id="${id}"], .day__chip[data-id="${id}"], .week__mini[data-id="${id}"]`).forEach(el => el.classList.toggle('is-pending', on));
}

/* ---------------- estirar: la duración con el asa de abajo ---------------- */

function wireStretch(el, it, dur0) {
  const handle = el.querySelector('.day__ev-handle');
  if (!handle) return;
  handle.addEventListener('pointerdown', (e) => {
    e.preventDefault(); e.stopPropagation();
    const y0 = e.clientY, h0 = el.offsetHeight;
    let dur = dur0;
    el.classList.add('is-stretching');
    el.draggable = false;
    handle.setPointerCapture(e.pointerId);
    const preview = document.createElement('div');
    preview.className = 'day__ev-preview';
    el.appendChild(preview);
    const move = (ev) => {
      const px = Math.max(MIN_DUR / 60 * HOUR_H, h0 + (ev.clientY - y0));
      dur = Math.max(MIN_DUR, Math.round(px / HOUR_H * 60 / STRETCH_SNAP) * STRETCH_SNAP);
      el.style.height = `${dur / 60 * HOUR_H}px`;
      preview.textContent = tr(`${dur} min · hasta ${toHHMM(Math.min(toMin(it.start) + dur, 24 * 60 - 1))}`);
    };
    const up = () => {
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', up);
      handle.removeEventListener('pointercancel', up);
      preview.remove();
      el.draggable = true;
      /* la altura vuelve a la real: el motor decide; si acepta, el refetch la pinta */
      el.style.height = `${dur0 / 60 * HOUR_H}px`;
      setTimeout(() => el.classList.remove('is-stretching'), 0);
      if (dur === dur0) return;
      markPending(it.ticket_id, true);
      handlers.onSchedule({ ticket_id: it.ticket_id, owner: it.owner, cadence: Boolean(it.cadence_days) }, { duration_min: dur })
        .finally(() => markPending(it.ticket_id, false));
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up);
    handle.addEventListener('pointercancel', up);
  });
}

/* la tarjeta que el motor rechazó parpadea para que se vea cuál era */
export function flashDayItem(id) {
  document.querySelectorAll(`.day__ev[data-id="${id}"], .day__chip[data-id="${id}"], .week__mini[data-id="${id}"]`).forEach(el => {
    el.classList.remove('is-flash');
    void el.offsetWidth;
    el.classList.add('is-flash');
  });
}
