/* ============================================================
   agents.js — la ventana que da nombre al proyecto (T1.12).
   Tablero = agentes con nombre. Agentes = tareas automatizadas
   con LLM, en su sitio: Programados (agenda propia, decisión 4),
   En ejecución (vivo, cancelar), Resultados (la lectura de la
   mañana: último run + veredicto por job), Jobs (operar).
   Todo contra los contratos T1.1/T1.11; el motor decide, la UI
   enseña el 409.
   ============================================================ */

import { renderMcpSettings } from './mcp_settings.js';
/* #262: la fecha larga salia con 'es-ES' clavado en la llamada */
import { fechaDiaMes, t as tr } from '../i18n.js';
import { renderCalendar } from './calendar.js';

/* ---------- T5.2 (el dueño, queja 1ª): Agentes en el tiempo ----------
   Las vistas Día/Semana/Mes/Año son las del Tablero; aquí se alimentan de
   /api/agents/calendar y cada elemento es un job (programado) o un run pasado
   (veredicto en color). Solo lectura: clic abre el job; la programación se edita
   en su modal. Carriles = harness. */
export const AG_CAL_TABS = [['day', 'Hoy'], ['week', 'Semana'], ['month', 'Mes'], ['year', 'Año']];

function agStatus(it) {
  if (it.kind === 'run') {
    if (it.status === 'running') return 'running';
    if (it.verdict === 'pass') return 'pasa';
    if (it.verdict === 'fail' || ['error', 'timeout', 'cancelled'].includes(it.status)) return 'falla';
    return 'sinveredicto';
  }
  return it.status === 'paused' ? 'pausado' : 'job';
}
export function agCalState(state) {
  const data = state.agCalData;
  if (!data) return { calView: state.agCalView, calDate: state.agCalDate, calDaySel: state.agCalDaySel, calData: null };
  const items = (data.items ?? []).map(it => ({
    ...it,
    ticket_id: it.job_id,
    owner: it.harness ?? '—',
    title: it.kind === 'run' ? `${it.name} · run ${it.run_id}` : it.name,
    status: agStatus(it),
    ghost: Boolean(it.ghost),
    readonly: true,
    cadence_days: null,
    priority: null,
    due_state: null,
  }));
  const lanes = [...new Set([...(data.lanes ?? []), ...items.map(i => i.owner)])].filter(Boolean);
  return { calView: state.agCalView, calDate: state.agCalDate, calDaySel: state.agCalDaySel, calData: { ...data, lanes, items } };
}
import { fmtWhen, fmtSchedule, fmtRelative, fmtDayShort, fmtUsd, runOutputText, esc } from '../util.js';
import { api } from '../api.js';
import { showPanelShell } from './panel.js';

let handlers = null;
// { onSelectTab, onSetDays, onSetJobFilter, onOpenJob, onNewJob, onEditJob,
//   onJobAction(job, action), onCancelRun(runId), onVerdict(run, verdict), onClose }

export function initAgents(h) { handlers = h; }

export const AG_TABS = [
  ['scheduled', 'Programados'],
  ['running',   'En ejecución'],
  ['results',   'Resultados'],
  ['jobs',      'Jobs'],
  /* #277 (el dueño: «el botón de ajustes lanza el panel de agentes, no abre ningún ajuste»):
     el botón de la barra dice «Ajustes» y aterrizaba en una pestaña llamada «MCP», dentro
     de una superficie llamada «Agentes». El destino tiene que llamarse como el botón —
     si no, el que lo pulsa no sabe que ya ha llegado. MCP es hoy el único ajuste, no el nombre. */
  ['mcp', 'Ajustes'], // T5.9: el registro propio de servidores
];

/* #277: en Ajustes, la barra de Agentes no manda. Ni el conmutador Kanban/Calendario
   (configurar no tiene calendario), ni el pulso de la máquina, ni «Nuevo job» — que es
   el botón más fuerte de la pantalla y no ajusta nada. Configurar no es trabajar. */
export const isSettings = (state) =>
  state.surface === 'agents' && state.agTab === 'mcp' && state.agMode !== 'calendar';

const RUN_GLYPH = { ok: '✓', error: '✕', timeout: '✕', running: '●', cancelled: '⊘' };

function runGlyph(r) {
  if (!r) return { g: '—', cls: 'none' };
  if (r.status === 'running') return { g: '●', cls: 'running' };
  if (r.verdict) return { g: r.verdict === 'pass' ? '✓' : '✕', cls: r.verdict };
  return { g: RUN_GLYPH[r.status] ?? '·', cls: r.status === 'ok' ? 'pending' : r.status };
}

function costTxt(r) {
  if (!r || r.cost_usd == null) return '';
  return r.cost_kind === 'billed' ? `$${fmtUsd(r.cost_usd)}` : `≈$${fmtUsd(r.cost_usd)}`;
}

/* ---------------- barra: pestañas + pulso + nuevo job ---------------- */

export function renderAgentsBar(state) {
  const bar = document.getElementById('ag-bar');
  bar.innerHTML = '';

  /* T5.2: el mismo conmutador que el Tablero — Kanban (las pestañas de siempre) o Calendario */
  const isCal = state.agMode === 'calendar';
  const settings = isSettings(state); // #277
  if (!settings) {
    const mode = document.createElement('nav');
    mode.className = 'views views--mode';
    mode.id = 'ag-mode-tabs';
    mode.setAttribute('aria-label', 'Modo de Agentes');
    for (const [key, label] of [['kanban', 'Kanban'], ['calendar', 'Calendario']]) {
      const b = document.createElement('button');
      b.className = `views__tab${(isCal ? 'calendar' : 'kanban') === key ? ' is-active' : ''}`;
      b.dataset.agMode = key;
      b.textContent = label;
      b.addEventListener('click', () => handlers.onSetMode(key));
      mode.appendChild(b);
    }
    bar.appendChild(mode);
  }

  const tabs = document.createElement('nav');
  tabs.className = 'views';
  tabs.id = isCal ? 'ag-cal-tabs' : 'ag-tabs';
  tabs.setAttribute('aria-label', isCal ? 'Vista del calendario de Agentes' : 'Secciones de Agentes');
  const running = state.agRunning?.length ?? 0;
  if (isCal) {
    for (const [key, label] of AG_CAL_TABS) {
      const b = document.createElement('button');
      b.className = `views__tab${state.agCalView === key ? ' is-active' : ''}`;
      b.dataset.agCal = key;
      b.textContent = label;
      b.addEventListener('click', () => handlers.onSetCalView(key));
      tabs.appendChild(b);
    }
  } else {
    for (const [key, label] of AG_TABS) {
      const b = document.createElement('button');
      b.className = `views__tab${state.agTab === key ? ' is-active' : ''}`;
      /* Las pestanas de modo llevan data-ag-mode y las del calendario data-ag-cal;
         estas no llevaban nada y solo se podian agarrar por su ETIQUETA, que desde
         hoy se traduce. Un hueco de consistencia que solo se ve al intentar USARLA. */
      b.dataset.ag = key;
      b.innerHTML = `${label}${key === 'running' && running ? `<span class="wf-badge wf-badge--new">${running}</span>` : ''}`;
      b.addEventListener('click', () => handlers.onSelectTab(key));
      tabs.appendChild(b);
    }
  }
  bar.appendChild(tabs);

  if (settings) return; // #277: en Ajustes no van ni el pulso de la máquina ni «Nuevo job»

  /* pulso: lo que importa de la máquina, en una línea */
  const jobs = state.agJobs ?? [];
  const active = jobs.filter(j => j.status === 'active').length;
  const next = jobs.filter(j => j.status === 'active' && j.next_fire).sort((a, b) => a.next_fire.localeCompare(b.next_fire))[0];
  const failed = (state.agResults ?? []).filter(r => r.last_run?.verdict === 'fail' || ['error', 'timeout'].includes(r.last_run?.status)).length;
  const bits = [];
  if (running) bits.push(`<span class="boardpulse__live">● ${running} en ejecución</span>`);
  if (failed) bits.push(`<span class="boardpulse__bad">✕ ${failed} con fallo</span>`);
  bits.push(`<span>${active} activo${active === 1 ? '' : 's'}</span>`);
  /* #262: misma razon que en util.js — la etiqueta se traduce aparte del dato */
  if (next) bits.push(`<span>${esc(tr('próxima'))} ${esc(fmtRelative(next.next_fire))} · ${esc(next.name)}</span>`);
  const pulse = document.createElement('div');
  pulse.className = 'ag__pulse';
  pulse.innerHTML = bits.join('<span class="boardpulse__sep">·</span>');
  bar.appendChild(pulse);

  const btn = document.createElement('button');
  btn.className = 'btn btn--primary';
  btn.id = 'btn-new-job';          // como #btn-settings: el texto se traduce, el id no
  btn.textContent = 'Nuevo job';
  btn.addEventListener('click', () => handlers.onNewJob());
  bar.appendChild(btn);
}

/* punto vivo en la pestaña de superficie */
export function renderAgentsSurfaceBadge(state) {
  const b = document.getElementById('ag-live-badge');
  b.hidden = !(state.agRunning?.length);
}

/* ---------------- cuerpo por pestaña ---------------- */

export function renderAgentsBody(state) {
  const body = document.getElementById('ag-body');
  if (state.agMode === 'calendar') {                       // T5.2
    body.innerHTML = '';
    body.classList.add('ag__body--cal');
    const root = document.createElement('div');
    root.className = 'cal ag-cal';
    root.id = 'ag-cal-root';
    body.appendChild(root);
    renderCalendar(agCalState(state), root);
    return;
  }
  body.classList.remove('ag__body--cal');
  body.innerHTML = '';
  switch (state.agTab) {
    case 'scheduled': return renderScheduled(body, state);
    case 'running':   return renderRunning(body, state);
    case 'results':   return renderResults(body, state);
    case 'jobs':      return renderJobs(body, state);
    case 'mcp':       return renderMcpSettings(body, state); // T5.9
  }
}

/* Programados — agenda propia de los agentes, agrupada por día */
function renderScheduled(body, state) {
  const head = document.createElement('div');
  head.className = 'pane__head';
  head.innerHTML = `<span class="pane__title">Próximas ejecuciones</span>
    <span class="pane__hint">Calendario propio de los agentes — no se mezcla con el Tablero (decisión 4).</span>`;
  const seg = document.createElement('div');
  seg.className = 'seg';
  seg.style.margin = '0';
  for (const d of [7, 14, 30]) {
    const b = document.createElement('button');
    b.className = state.agScheduleDays === d ? 'is-active' : '';
    b.textContent = `${d} días`;
    b.addEventListener('click', () => handlers.onSetDays(d));
    seg.appendChild(b);
  }
  head.appendChild(seg);
  body.appendChild(head);

  const sch = state.agSchedule;
  if (!sch) { body.insertAdjacentHTML('beforeend', '<div class="pane__empty">cargando…</div>'); return; }
  const items = sch.items ?? [];
  if (!items.length) {
    body.insertAdjacentHTML('beforeend', `<div class="pane__empty">Nada programado hasta ${esc(sch.to)}. Los jobs manuales solo corren con el botón.</div>`);
    return;
  }
  const byDay = new Map();
  for (const it of items) {
    const day = it.fire_at.slice(0, 10);
    if (!byDay.has(day)) byDay.set(day, []);
    byDay.get(day).push(it);
  }
  const agenda = document.createElement('div');
  agenda.className = 'agenda';
  const today = new Date(); const todayISO = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
  for (const [day, arr] of byDay) {
    const block = document.createElement('section');
    block.className = 'agenda__day';
    const d = new Date(day + 'T00:00:00');
    block.innerHTML = `<div class="agenda__date${day === todayISO ? ' agenda__date--today' : ''}">${esc(fmtDayShort(day))}<small>${esc(fechaDiaMes(d))}</small></div>`;
    const list = document.createElement('div');
    list.className = 'agenda__list';
    for (const it of arr) {
      const row = document.createElement('div');
      row.className = 'agenda__item';
      row.setAttribute('role', 'button'); row.tabIndex = 0;
      row.innerHTML = `
        <span class="agenda__time">${esc(it.fire_at.slice(11, 16))}</span>
        <span class="agenda__name">${esc(it.name)}</span>
        <span class="agenda__meta">${esc(it.harness)} · ${esc(it.model)} · ${esc(fmtSchedule(it.schedule))}</span>`;
      row.addEventListener('click', () => handlers.onOpenJob(it.job_id));
      list.appendChild(row);
    }
    block.appendChild(list);
    agenda.appendChild(block);
  }
  body.appendChild(agenda);
}

/* En ejecución — lo vivo, con cancelar y la cola del output */
function renderRunning(body, state) {
  const head = document.createElement('div');
  head.className = 'pane__head';
  head.innerHTML = `<span class="pane__title">En ejecución</span><span class="pane__hint">Runs vivos ahora mismo. Cancelar mata el proceso; el run queda «cancelado».</span>`;
  body.appendChild(head);
  const runs = state.agRunning;
  if (!runs) { body.insertAdjacentHTML('beforeend', '<div class="pane__empty">cargando…</div>'); return; }
  if (!runs.length) { body.insertAdjacentHTML('beforeend', '<div class="pane__empty">Máquina parada. Nada corre ahora.</div>'); return; }
  const wrap = document.createElement('div');
  wrap.className = 'running';
  for (const r of runs) {
    const job = r.job ?? (state.agJobs ?? []).find(j => j.id === r.job_id) ?? { name: `job #${r.job_id}` };
    const card = document.createElement('article');
    card.className = 'runcard';
    card.innerHTML = `
      <div class="runcard__top">
        <span class="runcard__name">${esc(job.name)}</span>
        <span class="runcard__meta"><b>●</b> run ${r.id} · ${esc(tr('desde'))} ${fmtWhen(r.started_at)} (${esc(fmtRelative(r.started_at))}) · ${esc(job.harness ?? '')} · ${esc(job.model ?? '')}</span>
        <span class="runcard__actions"></span>
      </div>`;
    const acts = card.querySelector('.runcard__actions');
    const open = document.createElement('button');
    open.className = 'btn btn--ghost btn--sm';
    open.style.cssText = 'color:var(--color-surface-text);border-color:var(--color-surface-rule)';
    open.textContent = 'Ficha';
    open.addEventListener('click', () => handlers.onOpenJob(r.job_id));
    const cancel = document.createElement('button');
    cancel.className = 'btn btn--danger btn--sm';
    cancel.textContent = 'Cancelar run';
    cancel.addEventListener('click', () => handlers.onCancelRun(r.id));
    acts.append(open, cancel);
    const out = document.createElement('pre');
    out.className = 'machine__output';
    out.textContent = 'output…';
    card.appendChild(out);
    api().runOutput(r.id)
      .then(res => { out.textContent = runOutputText(res); })
      .catch(() => { out.textContent = '(sin output todavía)'; });
    wrap.appendChild(card);
  }
  body.appendChild(wrap);
}

/* Resultados — la lectura de la mañana */
function renderResults(body, state) {
  const head = document.createElement('div');
  head.className = 'pane__head';
  head.innerHTML = `<span class="pane__title">Resultados</span><span class="pane__hint">Último run de cada job con su veredicto. Lo que hizo la máquina mientras no mirabas.</span>`;
  const pending = (state.agResults ?? []).filter(x => x.last_run?.status === 'ok' && !x.last_run.verdict && x.job.verify_level !== 'none').length;
  const verify = document.createElement('button');
  verify.className = 'btn btn--ghost btn--sm';
  verify.id = 'ag-verify-now';
  verify.textContent = pending ? `Verificar ahora (${pending})` : 'Verificar ahora';
  verify.title = 'Pasa el juez por los runs ok sin veredicto, sin esperar al barrido de 5 min';
  verify.addEventListener('click', () => handlers.onVerifyRuns(verify));
  head.appendChild(verify);
  body.appendChild(head);
  const res = state.agResults;
  if (!res) { body.insertAdjacentHTML('beforeend', '<div class="pane__empty">cargando…</div>'); return; }
  if (!res.length) { body.insertAdjacentHTML('beforeend', '<div class="pane__empty">Sin jobs todavía. Crea el primero con «Nuevo job».</div>'); return; }

  /* orden de lectura: fallos primero, luego lo pendiente de veredicto, luego el resto por fecha */
  const rank = (x) => {
    const r = x.last_run;
    if (!r) return 5;
    if (r.status === 'running') return 1;
    if (r.verdict === 'fail' || ['error', 'timeout'].includes(r.status)) return 0;
    if (r.status === 'ok' && !r.verdict) return 2;
    return 3;
  };
  const rows = [...res].sort((a, b) => rank(a) - rank(b) || (b.last_run?.started_at ?? '').localeCompare(a.last_run?.started_at ?? ''));

  const list = document.createElement('div');
  list.className = 'res';
  for (const { job, last_run: r } of rows) {
    const { g, cls } = runGlyph(r);
    const row = document.createElement('article');
    row.className = 'res__row';
    row.setAttribute('role', 'button'); row.tabIndex = 0;
    /* #302: cuando un run FALLA, su salida y la razon del veredicto son la misma frase, y
       la fila la pintaba DOS VECES seguidas con distinta etiqueta:
           exit 1: web search denied without an allowlist.
           FAIL  exit 1: web search denied without an allowlist.
       Sale asi en verification-queue.png, que esta en la portada del repo. Repetir al
       usuario lo mismo dos veces no anade informacion: anade ruido y hace dudar de si son
       dos cosas distintas. Si la razon ya esta dicha arriba, queda solo el sello. */
    const mismo = (a, b) => {
      const n = x => (x ?? '').replace(/\s+/g, ' ').trim().toLowerCase();
      return n(a) && n(a) === n(b);
    };
    let verdict = '';
    if (r?.verdict) {
      const razon = mismo(r.verdict_reason, r.result_summary) ? '' : (r.verdict_reason ?? '');
      verdict = `<div class="res__verdict res__verdict--${r.verdict}"><b>${esc(tr(r.verdict === 'pass' ? 'pasa' : 'falla'))}</b><span>${esc(razon)}</span></div>`;
    }
    else if (r?.status === 'ok') verdict = `<div class="res__verdict res__verdict--pending">sin veredicto todavía${job.verify_level === 'none' ? ' (este job no se verifica)' : ''}</div>`;
    row.innerHTML = `
      <span class="res__glyph res__glyph--${cls}" aria-hidden="true">${g}</span>
      <div>
        <div class="res__name">${esc(job.name)}</div>
        <div class="res__meta">${r ? `run ${r.id} · ${esc(r.status)} · ${fmtWhen(r.finished_at ?? r.started_at)}${costTxt(r) ? ' · ' + costTxt(r) : ''}` : 'sin runs todavía'} · ${esc(job.harness)} · ${esc(job.model)}</div>
        ${r?.result_summary ? `<div class="res__summary">${esc(r.result_summary)}</div>` : ''}
        ${verdict}
      </div>
      <div class="res__actions"></div>`;
    const acts = row.querySelector('.res__actions');
    if (r && r.status !== 'running') {
      if (!r.verdict || r.verdict === 'fail') {
        const ok = document.createElement('button');
        ok.className = 'btn btn--ghost btn--sm'; ok.textContent = 'Pasa';
        ok.title = tr('Veredicto manual: pasa');
        ok.addEventListener('click', (e) => { e.stopPropagation(); handlers.onVerdict(r, 'pass'); });
        acts.appendChild(ok);
      }
      if (!r.verdict || r.verdict === 'pass') {
        const ko = document.createElement('button');
        ko.className = 'btn btn--danger btn--sm'; ko.textContent = 'Falla…';
        ko.title = 'Veredicto manual: falla, con motivo';
        ko.addEventListener('click', (e) => { e.stopPropagation(); handlers.onVerdict(r, 'fail'); });
        acts.appendChild(ko);
      }
    }
    row.addEventListener('click', () => handlers.onOpenJob(job.id));
    row.addEventListener('keydown', (e) => { if (e.key === 'Enter') handlers.onOpenJob(job.id); });
    list.appendChild(row);
  }
  body.appendChild(list);
}

/* Jobs — la lista operable */
function renderJobs(body, state) {
  const head = document.createElement('div');
  head.className = 'pane__head';
  head.innerHTML = `<span class="pane__title">Jobs</span><span class="pane__hint">Cada job es una entidad propia: su ciclo (activo · pausado · archivado), su programación, su coste.</span>`;
  body.appendChild(head);

  const filters = document.createElement('div');
  filters.className = 'jobs__filters';
  const jobs = state.agJobs ?? [];
  for (const [key, label] of [['active', 'Activos'], ['paused', 'Pausados'], ['archived', 'Archivados'], ['all', 'Todos']]) {
    const n = key === 'all' ? jobs.length : jobs.filter(j => j.status === key).length;
    const b = document.createElement('button');
    b.className = `pchip${state.agJobFilter === key ? ' is-active' : ''}`;
    b.textContent = `${label} · ${n}`;
    b.addEventListener('click', () => handlers.onSetJobFilter(key));
    filters.appendChild(b);
  }
  body.appendChild(filters);

  if (!state.agJobs) { body.insertAdjacentHTML('beforeend', '<div class="pane__empty">cargando…</div>'); return; }
  const shown = jobs.filter(j => state.agJobFilter === 'all' || j.status === state.agJobFilter);
  if (!shown.length) { body.insertAdjacentHTML('beforeend', '<div class="pane__empty">Ningún job en este filtro.</div>'); return; }

  const list = document.createElement('div');
  list.className = 'jobs';
  for (const j of shown) {
    const r = j.last_run;
    const { g, cls } = runGlyph(r);
    const row = document.createElement('article');
    row.className = `jobrow jobrow--${j.status}`;
    row.dataset.id = j.id;
    const cost = j.billing_mode === 'api'
      ? `$${(j.total_cost_usd ?? 0).toFixed(2)} facturado`
      : j.billing_mode === 'local' ? 'local · gratis' : `≈$${(j.total_equivalent_usd ?? 0).toFixed(2)} equiv. · suscripción`;
    row.innerHTML = `
      <div>
        <div class="jobrow__name">${esc(j.name)} <span class="jobstate jobstate--${j.status}">${j.status === 'active' ? 'activo' : j.status === 'paused' ? 'pausado' : 'archivado'}</span></div>
        <div class="jobrow__sub">${esc(j.harness)} · ${esc(j.model)}${j.ticket_id ? ` · ${tr('brazo de')} #${j.ticket_id}` : ''}</div>
      </div>
      <div class="jobrow__cell">${esc(fmtSchedule(j.schedule))}<small>${j.next_fire ? esc(tr('próxima')) + ' ' + esc(fmtRelative(j.next_fire)) + ' · ' + fmtWhen(j.next_fire) : j.status === 'active' ? esc(tr('sin próxima (manual)')) : '—'}</small></div>
      <div class="jobrow__cell"><span class="res__glyph res__glyph--${cls}" style="font-size:1em">${g}</span> ${r ? `run ${r.id} · ${esc(r.status)}${r.verdict ? ' · ' + esc(tr(r.verdict === 'pass' ? 'pasa' : 'falla')) : ''}` : esc(tr('sin runs'))}<small>${r ? fmtWhen(r.finished_at ?? r.started_at) : ''}</small></div>
      <div class="jobrow__cell"><small>${esc(cost)}</small></div>
      <div class="jobrow__actions"></div>`;
    row.querySelector('.jobrow__name').addEventListener('click', () => handlers.onOpenJob(j.id));
    const acts = row.querySelector('.jobrow__actions');
    const mk = (label, cls, action, title) => {
      const b = document.createElement('button');
      b.className = `btn ${cls} btn--sm`; b.textContent = label; if (title) b.title = title;
      b.addEventListener('click', () => handlers.onJobAction(j, action));
      return b;
    };
    if (j.status === 'active') {
      acts.appendChild(mk(r?.status === 'running' ? 'Ejecutándose…' : '▶ Lanzar', 'btn--primary', 'launch', `Lanzar ahora (${j.model})`));
      acts.appendChild(mk('Pausar', 'btn--ghost', 'pause'));
    }
    if (j.status === 'paused') acts.appendChild(mk('Reanudar', 'btn--primary', 'resume'));
    if (j.status !== 'archived') {
      const ed = document.createElement('button');
      ed.className = 'btn btn--ghost btn--sm'; ed.textContent = 'Editar…';
      ed.addEventListener('click', () => handlers.onEditJob(j));
      acts.appendChild(ed);
      acts.appendChild(mk('Duplicar', 'btn--ghost', 'duplicate'));
      acts.appendChild(mk('Archivar', 'btn--danger', 'archive'));
    }
    list.appendChild(row);
  }
  body.appendChild(list);
}

/* ---------------- ficha de job: reutiliza el panel lateral ---------------- */

export async function openJobPanel(job) {
  const panel = showPanelShell();

  const head = document.createElement('header');
  head.className = 'panel__head';
  head.innerHTML = `
    <div class="panel__topline">
      <span class="jobstate jobstate--${job.status}">${job.status === 'active' ? 'activo' : job.status === 'paused' ? 'pausado' : 'archivado'}</span>
      ${job.ticket_id ? `<span class="badge badge--recur">brazo automático de #${job.ticket_id}</span>` : ''}
      <button class="panel__close" aria-label="Cerrar ficha">✕</button>
    </div>
    <h2 class="panel__title">${esc(job.name)}</h2>
    <div class="panel__facts">
      <span>harness <b>${esc(job.harness)}</b></span>
      <span>modelo <b>${esc(job.model)}</b></span>
      <span>programación <b>${esc(fmtSchedule(job.schedule))}</b></span>
      ${job.next_fire ? `<span>${esc(tr('próxima'))} <b>${fmtWhen(job.next_fire)}</b> (${esc(fmtRelative(job.next_fire))})</span>` : ''}
      <span>facturación <b>${esc(job.billing_mode ?? '—')}</b>${job.billing_mode === 'api' && job.max_budget_usd != null ? ` · tope $${job.max_budget_usd}` : ''}</span>
      <span>coste <b>${job.billing_mode === 'api' ? '$' + (job.total_cost_usd ?? 0).toFixed(2) : '≈$' + (job.total_equivalent_usd ?? 0).toFixed(2) + ' equiv.'}</b></span>
      <span>verificación <b>${esc(job.verify_level ?? 'auto')}</b></span>
    </div>`;
  head.querySelector('.panel__close').addEventListener('click', () => handlers.onClose());
  panel.appendChild(head);

  const body = document.createElement('div');
  body.className = 'panel__body';
  panel.appendChild(body);

  /* acciones */
  const acts = document.createElement('section');
  acts.className = 'panel__section';
  acts.innerHTML = `<div class="panel__section-title">Operar</div>`;
  const row = document.createElement('div');
  row.className = 'panel__actions';
  const mk = (label, cls, fn) => { const b = document.createElement('button'); b.className = `btn ${cls}`; b.textContent = label; b.addEventListener('click', fn); return b; };
  const running = job.last_run?.status === 'running';
  if (job.status === 'active') {
    const l = mk(running ? 'Ejecutándose…' : 'Lanzar ahora', 'btn--primary', () => handlers.onJobAction(job, 'launch'));
    l.disabled = running; row.appendChild(l);
    if (running) row.appendChild(mk('Cancelar run', 'btn--danger', () => handlers.onCancelRun(job.last_run.id)));
    row.appendChild(mk('Pausar', 'btn--ghost', () => handlers.onJobAction(job, 'pause')));
  }
  if (job.status === 'paused') row.appendChild(mk('Reanudar', 'btn--primary', () => handlers.onJobAction(job, 'resume')));
  if (job.status !== 'archived') {
    row.appendChild(mk('Editar…', 'btn--ghost', () => handlers.onEditJob(job)));
    row.appendChild(mk('Duplicar', 'btn--ghost', () => handlers.onJobAction(job, 'duplicate')));
    row.appendChild(mk('Archivar', 'btn--danger', () => handlers.onJobAction(job, 'archive')));
  }
  acts.appendChild(row);
  body.appendChild(acts);

  if (job.verify_criteria) {
    const s = document.createElement('section');
    s.className = 'panel__section';
    s.innerHTML = `<div class="panel__section-title">Criterio del verificador</div><div class="panel__criteria">${esc(job.verify_criteria)}</div>`;
    body.appendChild(s);
  }

  /* la máquina: configuración + prompt + runs */
  const m = document.createElement('section');
  m.className = 'machine';
  const tools = job.allowed_tools ?? '';
  const danger = /\b(Read|Bash|Write|Edit)\b/.test(tools);
  m.innerHTML = `
    <div class="machine__title">Job agéntico · #${job.id}</div>
    <dl class="machine__grid">
      <dt>permisos</dt><dd>${esc(job.permission_mode ?? '—')}</dd>
      <dt>tools</dt><dd>${tools ? esc(tools) : 'solo lectura en cwd (sandbox)'}${danger ? ' <span style="color:oklch(68% 0.19 25)">⚠ abre el disco</span>' : ''}</dd>
      <dt>MCP heredados</dt><dd>${(job.inherit_mcp ?? []).length ? esc(job.inherit_mcp.join(', ')) : 'ninguno'}${job.strict_mcp ? ' · strict' : ''}</dd>
      ${(job.add_dirs ?? []).length ? `<dt>carpetas</dt><dd>${esc(job.add_dirs.join('\n'))}</dd>` : ''}
      ${job.cwd ? `<dt>cwd</dt><dd>${esc(job.cwd)}</dd>` : ''}
      ${job.effort ? `<dt>effort</dt><dd>${esc(job.effort)}</dd>` : ''}
      ${job.fallback_model ? `<dt>fallback</dt><dd>${esc(job.fallback_model)}</dd>` : ''}
      ${job.json_schema ? `<dt>salida</dt><dd>JSON con esquema</dd>` : ''}
      <dt>timeout</dt><dd>${job.timeout_s ?? '—'} s</dd>
    </dl>
    <div class="machine__prompt">${esc(job.prompt ?? '')}</div>
    <div class="machine__title" style="margin-top:var(--sp-4)">Runs</div>
    <ul class="runs"><li style="cursor:default">cargando…</li></ul>
    <pre class="machine__output" id="job-run-output" hidden></pre>`;
  body.appendChild(m);

  const list = m.querySelector('.runs');
  const out = m.querySelector('#job-run-output');
  api().jobRuns(job.id)
    .then(runs => {
      list.innerHTML = '';
      if (!runs.length) { list.innerHTML = '<li style="cursor:default">sin runs todavía</li>'; return; }
      for (const r of runs) {
        const li = document.createElement('li');
        const { g } = runGlyph(r);
        li.innerHTML = `
          <span>${g}</span>
          <span>${fmtWhen(r.started_at)}</span>
          <span>run ${r.id} · ${esc(r.status)}${r.verdict ? ` · <span class="runs__verdict--${r.verdict}">${r.verdict === 'pass' ? 'pasa' : 'falla'}</span>${r.verdict_reason ? ' — ' + esc(r.verdict_reason) : ''}` : ''}</span>
          <span class="runs__cost">${costTxt(r)}</span>`;
        li.addEventListener('click', () => {
          list.querySelectorAll('li').forEach(x => x.classList.remove('is-open'));
          li.classList.add('is-open');
          out.hidden = false; out.textContent = 'cargando output…';
          api().runOutput(r.id)
            .then(res => { out.textContent = runOutputText(res); })
            .catch(err => { out.textContent = `(no se pudo cargar: ${err.reason ?? err.message})`; });
        });
        list.appendChild(li);
      }
      // el último run se abre solo: es lo que se viene a leer
      list.querySelector('li')?.click();
    })
    .catch(() => { list.innerHTML = '<li style="cursor:default">(runs no disponibles)</li>'; });
}
