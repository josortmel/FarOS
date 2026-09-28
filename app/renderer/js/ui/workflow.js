/* ============================================================
   workflow.js — superficie v2/v3: la Oficina de un workflow.
   T3.1 (corrección del dueño): columnas POR ESTADO, como el Tablero
   —«esa vista es perfecta»— y sin temporalidad. La fase deja de
   ser columna y pasa a ser filtro: una tira de chips con la barra
   de progreso de cada fase (la fase «se vacía» ahí). Nunca mezcla
   workflows: todo sale de workflow.tickets_by_phase del activo.
   El candado dice POR QUÉ una tarea no está lista (deps sin verificar).
   ============================================================ */

import { STATUS_LABEL, COLUMNS } from '../transitions.js';
import { fmtWhen, esc } from '../util.js';
import { t as tr, applyI18n } from '../i18n.js';
import { buildStateColumn, makeDraggable } from './board.js';
import { renderDecisionsPane, pendingCount } from './decisions.js';

let handlers = null;
// { onSelectWorkflow, onSelectPhase, onSelectTab, onMarkSeen, onOpenTicket, onDropTicket,
//   onOpenDispatch, onDispatchFinding, onDismissFinding, onToggleDrawer, onToggleHandoff,
//   onEditHandoff, onCopyDraft, onMarkSaved }

export function initWorkflow(h) { handlers = h; }

const SEV = {
  critical: { label: 'crítica', cls: 'sev--critical', glyph: '◆◆' },
  high:     { label: 'alta',    cls: 'sev--high',     glyph: '◆' },
  medium:   { label: 'media',   cls: 'sev--medium',   glyph: '◇' },
  low:      { label: 'baja',    cls: 'sev--low',      glyph: '·' },
};

/* ---------------- barra: selector + mesa de bugs ---------------- */

/* ---------------- T4.3 (el dueño): menú lateral — slider Activos/Archivados + buscador + lista ---------------- */

/* #291: los cuatro caian dentro del MISMO nodo de texto que el porcentaje
   («18% · activo»), asi que el catalogo no los alcanzaba nunca: t()
   sustituye la cadena COMPLETA del nodo y esa cadena cambia con la cifra.
   Vocabulario cerrado de cuatro palabras, asi que se traduce en origen.
   Lo encontro el barrido de la app CORRIENDO en ingles, no el codigo. */
const WF_STATUS = { active: 'activo', closed: 'cerrado', cancelled: 'cancelado', archived: 'archivado' };
const wfEstado = (st) => tr(WF_STATUS[st] ?? st);

function closeMenu() { document.querySelectorAll('.ctxmenu').forEach(m => m.remove()); }
/* menú contextual de una fila: clic derecho o el ⋯ de la tarjeta. Las mismas opciones. */
function openMenu(x, y, w, archived) {
  closeMenu();
  const m = document.createElement('div');
  m.className = 'ctxmenu';
  m.setAttribute('role', 'menu');
  const items = archived
    ? [['Reactivar', () => handlers.onUnarchive(w), false, 'vuelve a Activos']]
    : [
        ['Archivar', () => handlers.onArchive(w), false, w.status === 'active' ? 'lo cierra y lo guarda; queda buscable' : 'sale de Activos, queda buscable'],
        ['Cancelar…', () => handlers.onCancel(w), w.status !== 'active', 'con motivo; después se puede archivar'],
      ];
  for (const [label, fn, disabled, hint] of items) {
    const b = document.createElement('button');
    b.type = 'button'; b.setAttribute('role', 'menuitem');
    b.className = 'ctxmenu__item';
    b.disabled = disabled;
    b.innerHTML = `${esc(label)}<small>${esc(hint)}</small>`;
    b.addEventListener('click', () => { closeMenu(); fn(); });
    m.appendChild(b);
  }
  document.body.appendChild(m);
  /* #291, quinto de la clase: este menu se pinta en document.body, o sea FUERA del arbol
     que recorre applyI18n, y no existe hasta que alguien lo abre — asi que ningun barrido
     de vistas lo ve. En ingles salia entero en espaniol, con tildes y todo:
        «Archivar · lo cierra y lo guarda; queda buscable · Cancelar… · con motivo;
         despues se puede archivar»
     Es la misma forma que los toasts, la caja de errores, los modales y el bloque de
     programacion del modal de job: LO QUE SE PINTA DESPUES DEL RECORRIDO HAY QUE
     RECORRERLO OTRA VEZ. Buscados los demas: los unicos sitios que pintan fuera del arbol
     son este appendChild y los contenedores de nivel superior, y esos ya pasan. La clase
     queda cerrada en cinco, no abierta. */
  applyI18n(m);
  const r = m.getBoundingClientRect();
  m.style.left = `${Math.min(x, window.innerWidth - r.width - 8)}px`;
  m.style.top = `${Math.min(y, window.innerHeight - r.height - 8)}px`;
  setTimeout(() => {
    document.addEventListener('click', closeMenu, { once: true });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenu(); }, { once: true });
  }, 0);
}

export function renderWorkflowSidebar(state) {
  const side = document.getElementById('wf-side');
  side.innerHTML = '';
  const archived = state.wfListMode === 'archived';
  const q = (state.wfQuery ?? '').trim().toLowerCase();

  /* slider Activos ⇄ Archivados */
  const sw = document.createElement('div');
  sw.className = 'wfside__switch';
  sw.setAttribute('role', 'tablist');
  sw.innerHTML = `
    <button type="button" role="tab" data-list="active" class="${archived ? '' : 'is-active'}" aria-selected="${!archived}">Activos</button>
    <button type="button" role="tab" data-list="archived" class="${archived ? 'is-active' : ''}" aria-selected="${archived}">Archivados</button>
    <span class="wfside__knob${archived ? ' is-right' : ''}" aria-hidden="true"></span>`;
  sw.querySelectorAll('[data-list]').forEach(b => b.addEventListener('click', () => handlers.onListMode(b.dataset.list)));
  side.appendChild(sw);

  const search = document.createElement('input');
  search.type = 'search'; search.id = 'wf-q'; search.className = 'wfside__search';
  search.placeholder = 'buscar por nombre…'; search.value = state.wfQuery ?? '';
  search.setAttribute('aria-label', 'Buscar workflows');
  search.addEventListener('input', () => handlers.onSearch(search.value));
  side.appendChild(search);

  const all = (archived ? state.wfArchived : state.workflows) ?? null;
  const list = (all ?? []).filter(w => !q || w.name.toLowerCase().includes(q));
  const ul = document.createElement('div');
  ul.className = 'wfside__list';
  ul.setAttribute('role', 'list');
  if (all == null) ul.innerHTML = '<div class="wfside__empty">cargando…</div>';
  else if (!list.length) ul.innerHTML = `<div class="wfside__empty">${q ? 'Nada coincide.' : archived ? 'Ningún workflow archivado.' : 'Sin workflows. Se importan desde un Plan por MCP o API.'}</div>`;
  for (const w of list) {
    const row = document.createElement('div');
    row.className = `wfrow${state.workflowId === w.id ? ' is-active' : ''}${['closed', 'cancelled', 'archived'].includes(w.status) ? ' wfrow--closed' : ''}`;
    row.setAttribute('role', 'listitem');
    row.tabIndex = 0;
    row.dataset.id = w.id;
    row.innerHTML = `
      <span class="wfrow__name">${esc(w.name)}</span>
      <span class="wfrow__meta">${w.pct_global ?? 0}%${w.open_findings ? ` · <b class="wfrow__bugs">${w.open_findings}</b>` : ''} · ${esc(wfEstado(w.status))}</span>
      <button type="button" class="wfrow__more" aria-label="Opciones" title="Opciones">⋯</button>`;
    row.addEventListener('click', (e) => { if (!e.target.closest('.wfrow__more')) handlers.onSelectWorkflow(w.id); });
    row.addEventListener('keydown', (e) => { if (e.key === 'Enter') handlers.onSelectWorkflow(w.id); });
    row.addEventListener('contextmenu', (e) => { e.preventDefault(); openMenu(e.clientX, e.clientY, w, archived); });
    row.querySelector('.wfrow__more').addEventListener('click', (e) => {
      const r = e.currentTarget.getBoundingClientRect();
      openMenu(r.left, r.bottom + 4, w, archived);
    });
    ul.appendChild(row);
  }
  side.appendChild(ul);
  if (document.activeElement === document.body && state.wfQuery) { search.focus(); search.setSelectionRange(search.value.length, search.value.length); }
}

/* ---------------- barra del workflow abierto: pestañas + meta + mesa de bugs ---------------- */

export function renderWorkflowBar(state) {
  const bar = document.getElementById('wf-bar');
  bar.innerHTML = '';
  const wf = state.workflow;
  if (!wf) {
    bar.innerHTML = `<span class="wf__empty">${(state.workflows ?? []).length ? 'Elige un workflow en el menú de la izquierda.' : 'Sin workflows. Se importan desde un Plan (workflow-diseño) por MCP o API.'}</span>`;
    return;
  }
  /* T3.5: pestañas de la Oficina — Tareas · Handoffs · Actividad */
  const tabs = document.createElement('nav');
  tabs.className = 'views wf__tabs';
  tabs.setAttribute('aria-label', 'Secciones del workflow');
  const fresh = state.wfLastSeen ? (state.wfActivity?.length ?? 0) : 0;
  const defs = [
    ['tasks', 'Tareas', ''],
    ['handoffs', 'Handoffs', state.wfHandoffs?.length ? `<span class="views__n">${state.wfHandoffs.length}</span>` : ''],
    ['activity', 'Actividad', fresh ? `<span class="wf-badge wf-badge--new" title="desde tu última visita">${fresh}</span>` : ''],
    ['documents', 'Documentos', state.wfDocuments?.length ? `<span class="views__n">${state.wfDocuments.length}</span>` : ''],
    ['decisions', 'Bisagras', pendingCount(state.wfDecisions) ? `<span class="wf-badge wf-badge--new" title="pendientes del dueño">${pendingCount(state.wfDecisions)}</span>` : ''],
  ];
  for (const [key, label, extra] of defs) {
    const b = document.createElement('button');
    b.className = `views__tab${state.wfTab === key ? ' is-active' : ''}`;
    /* Mismo hueco que tenian las pestanias de Agentes: sin data- solo se podian
       agarrar por su ETIQUETA, que se traduce. El id no se traduce. */
    b.dataset.wf = key;
    b.innerHTML = `${label}${extra}`;
    b.addEventListener('click', () => handlers.onSelectTab(key));
    tabs.appendChild(b);
  }
  bar.appendChild(tabs);

  const meta = document.createElement('span');
  meta.className = 'wf__meta';
  meta.innerHTML = `<b>${esc(wf.name)}</b> · coordinador <b>${esc(wf.coordinator)}</b> · ${esc(wfEstado(wf.status))}`;
  bar.appendChild(meta);

  const open = state.findings?.filter(f => f.status === 'open').length ?? 0;
  const bugs = document.createElement('button');
  bugs.className = 'btn btn--ghost wf__bugsbtn';
  bugs.innerHTML = `Mesa de bugs${open ? ` <span class="wf-badge">${open}</span>` : ''}`;
  bugs.addEventListener('click', () => handlers.onToggleDrawer());
  bar.appendChild(bugs);
}

/* ---------------- handoff: el «por dónde vamos» entre sesiones ---------------- */

export function renderHandoff(state) {
  const el = document.getElementById('wf-handoff');
  const wf = state.workflow;
  if (!wf) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = '';

  const h = wf.latest_handoff;
  const row = document.createElement('div');
  row.className = 'handoff__row';

  if (h) {
    const firstLine = (h.content ?? '').split('\n').find(l => l.trim()) ?? '';
    row.innerHTML = `
      <span class="handoff__label">handoff</span>
      <span class="handoff__meta">${esc(h.created_by)} · ${fmtWhen(h.created_at)}</span>
      <span class="handoff__lead">${esc(firstLine)}</span>`;
    const toggle = document.createElement('button');
    toggle.className = 'btn btn--ghost btn--sm';
    toggle.textContent = state.handoffOpen ? 'Plegar' : 'Leer';
    toggle.setAttribute('aria-expanded', String(Boolean(state.handoffOpen)));
    toggle.addEventListener('click', () => handlers.onToggleHandoff());
    row.appendChild(toggle);
    const all = document.createElement('button');
    all.className = 'btn btn--ghost btn--sm';
    all.textContent = 'Todos…';
    all.addEventListener('click', () => handlers.onSelectTab('handoffs'));
    row.appendChild(all);
  } else {
    row.innerHTML = `
      <span class="handoff__label">handoff</span>
      <span class="handoff__lead handoff__lead--empty">Sin handoff todavía — la próxima sesión entrará a ciegas.</span>`;
  }

  const edit = document.createElement('button');
  edit.className = 'btn btn--primary btn--sm';
  edit.textContent = h ? 'Editar handoff…' : 'Escribir handoff…';
  edit.addEventListener('click', () => handlers.onEditHandoff());
  row.appendChild(edit);
  el.appendChild(row);

  if (h && state.handoffOpen) {
    const pre = document.createElement('pre');
    pre.className = 'handoff__content';
    pre.textContent = h.content;
    el.appendChild(pre);
  }
}

/* ---------------- banner de hitos ---------------- */

export function renderMilestoneBanner(state) {
  const el = document.getElementById('wf-banner');
  const pending = state.workflow?.milestones_pending_ecodb
    ?? (state.workflow?.milestones ?? []).filter(m => !m.ecodb_saved);
  if (!state.workflow || !pending.length) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = '';
  for (const m of pending) {
    const row = document.createElement('div');
    row.className = 'wf__banner-row';
    const what = m.kind === 'workflow_closed'
      ? `Workflow <b>${esc(state.workflow.name)}</b> cerrado`
      : `Fase <b>«${esc(m.phase)}»</b> completada`;
    row.innerHTML = `
      <span class="wf__banner-text">✦ ${what} — <b>memoria EcoDB pendiente</b></span>`;
    const copy = document.createElement('button');
    copy.className = 'btn btn--ghost btn--sm';
    copy.textContent = 'Copiar borrador';
    copy.addEventListener('click', () => handlers.onCopyDraft(m));
    const mark = document.createElement('button');
    mark.className = 'btn btn--primary btn--sm';
    mark.textContent = 'Marcar guardada…';
    mark.addEventListener('click', () => handlers.onMarkSaved(m));
    row.append(copy, mark);
    el.appendChild(row);
  }
}

/* ---------------- fases: tira de filtro con progreso ---------------- */

function stackedBar(p) {
  const total = p.total || 1;
  const seg = (n, cls) => n ? `<span class="progress__seg ${cls}" style="flex-grow:${n}" title="${n}"></span>` : '';
  const rest = total - p.verified - p.done - p.in_progress;
  return `<div class="progress" role="img" aria-label="${p.verified} verificadas de ${p.total}">
    ${seg(p.verified, 'progress__seg--verified')}
    ${seg(p.done, 'progress__seg--done')}
    ${seg(p.in_progress, 'progress__seg--live')}
    ${rest > 0 ? `<span class="progress__seg progress__seg--rest" style="flex-grow:${rest}"></span>` : ''}
  </div>`;
}

const STRAY = 'sin fase';

/* todas las tareas del workflow activo, con su fase — y de NINGÚN otro */
function workflowTickets(wf) {
  const out = [];
  for (const phase of wf.phases) for (const t of wf.tickets_by_phase?.[phase] ?? []) out.push({ t, phase });
  for (const t of wf.stray_tickets ?? []) out.push({ t, phase: STRAY });
  return out;
}

export function renderPhaseStrip(state) {
  const el = document.getElementById('wf-phases');
  const wf = state.workflow;
  if (!wf) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = '';

  const all = workflowTickets(wf);
  const sum = (arr) => ({
    total: arr.length,
    verified: arr.filter(x => x.t.status === 'verified').length,
    done: arr.filter(x => x.t.status === 'done').length,
    in_progress: arr.filter(x => x.t.status === 'in_progress').length,
    ready: arr.filter(x => x.t.ready && x.t.status === 'accepted' && !x.t.dispatched_to).length,
  });

  const chip = (label, key, p, { done = false } = {}) => {
    const b = document.createElement('button');
    b.className = `phchip${state.wfPhase === key ? ' is-active' : ''}${done ? ' phchip--done' : ''}`;
    b.setAttribute('aria-pressed', String(state.wfPhase === key));
    b.innerHTML = `
      <span class="phchip__row">
        <span class="phchip__name">${esc(label)}</span>
        ${p.ready ? `<span class="phchip__ready">${p.ready} lista${p.ready > 1 ? 's' : ''}</span>` : ''}
        <span class="phchip__count">${p.verified}/${p.total}</span>
      </span>
      ${stackedBar(p)}`;
    b.addEventListener('click', () => handlers.onSelectPhase(state.wfPhase === key ? null : key));
    return b;
  };

  el.appendChild(chip('Todas', null, sum(all)));
  for (const phase of wf.phases) {
    const mine = all.filter(x => x.phase === phase);
    const p = (wf.phases_summary ?? []).find(s => s.phase === phase);
    const ps = { ...sum(mine), ...(p ? { total: p.total, verified: p.verified, done: p.done, in_progress: p.in_progress, ready: p.ready } : {}) };
    el.appendChild(chip(phase, phase, ps, { done: ps.total > 0 && ps.verified === ps.total }));
  }
  if (wf.stray_tickets?.length) {
    el.appendChild(chip(STRAY, STRAY, sum(all.filter(x => x.phase === STRAY))));
  }
}

/* ---------------- tarjetas ---------------- */

/* el motor compone el título como «[T1] Schema»; el chip ya lleva la key */
function cleanTitle(t) {
  return t.plan_key ? t.title.replace(`[${t.plan_key}] `, '') : t.title;
}

function wfCard(t, phase, { showPhase }) {
  const el = document.createElement('article');
  el.dataset.id = t.id;
  el.tabIndex = 0;

  if (t.status === 'verified') {
    // fila fina: lo terminado ocupa lo mínimo para que lo vivo mande
    el.className = 'card card--slim';
    el.innerHTML = `
      <span class="card--slim__check">✓</span>
      <span class="card--slim__key">${esc(t.plan_key ?? '#' + t.id)}</span>
      <span class="card--slim__title">${esc(cleanTitle(t))}</span>
      ${t.verified_by ? `<span class="card--slim__by">${esc(t.verified_by)}</span>` : ''}`;
    el.addEventListener('click', () => handlers.onOpenTicket(t));
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handlers.onOpenTicket(t); }
    });
    return el;
  }

  el.className = 'card card--wf';
  el.setAttribute('role', 'button');
  el.setAttribute('aria-label', `${t.title} — abrir detalle`);

  let lock = '';
  if (t.ready === false) {
    const deps = (t.unmet_deps ?? [])
      .map(d => `${d.plan_key ?? '#' + d.id} (${STATUS_LABEL[d.status] ?? d.status})`).join(' · ');
    lock = `<span class="wflock" tabindex="0" aria-label="${tr('Bloqueada por dependencias:')} ${esc(deps)}">🔒<span class="wflock__tip">${tr('espera a')} ${esc(deps)}</span></span>`;
  }

  const badges = [];
  if (t.priority === 'alta') badges.push('<span class="badge badge--prio">! alta</span>');
  if (t.status === 'blocked') badges.push('<span class="badge badge--blocked">⛔ bloqueada</span>');

  el.innerHTML = `
    <div class="card__top">
      <span class="card--wf__key">${esc(t.plan_key ?? '#' + t.id)}</span>
      ${showPhase ? `<span class="wfphase" title="fase">${esc(phase)}</span>` : ''}
      <span style="margin-left:auto;display:inline-flex;gap:0.4em;align-items:center;">${badges.join('')}${lock}</span>
    </div>
    <h3 class="card__title">${esc(cleanTitle(t))}</h3>`;

  /* meta: dueño / despacho */
  const meta = document.createElement('div');
  meta.className = 'card__meta';
  if (t.dispatched_to) {
    meta.innerHTML = `<span class="wfdispatch" title="despachada ${t.dispatched_at ? fmtWhen(t.dispatched_at) : ''}">→ ${esc(t.dispatched_to)}</span>`;
  } else if (t.owner) {
    meta.innerHTML = `<span>${esc(t.owner)}</span>`;
  }
  /* lista para despachar y sin dueño: el coordinador la manda desde aquí */
  if (t.ready && !t.dispatched_to && t.status === 'accepted') {
    const btn = document.createElement('button');
    btn.className = 'wfdispatch wfdispatch--btn';
    btn.textContent = t.suggested_agent
      ? `${tr('Despachar')} → ${t.suggested_agent}…`   /* #262: la etiqueta, aparte del nombre */
      : 'Despachar…';
    btn.addEventListener('click', (e) => { e.stopPropagation(); handlers.onOpenDispatch(t); });
    meta.appendChild(btn);
  }
  if (meta.childNodes.length) el.appendChild(meta);

  /* motivo de bloqueo — 2 líneas en tarjeta, completo en el panel */
  if (t.status === 'blocked' && t.blocked_reason) {
    const r = document.createElement('div');
    r.className = 'card__reason';
    const span = document.createElement('span');
    span.textContent = t.blocked_reason;
    r.appendChild(span);
    el.appendChild(r);
  }

  el.addEventListener('click', () => handlers.onOpenTicket(t));
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handlers.onOpenTicket(t); }
  });
  makeDraggable(el, t);
  return el;
}

/* ---------------- kanban por estado del workflow ---------------- */

/* orden dentro de una columna: por fase (el pipeline) y luego por key del Plan */
function planOrder(a, b, phases) {
  const pa = phases.indexOf(a.phase), pb = phases.indexOf(b.phase);
  if (pa !== pb) return (pa < 0 ? 99 : pa) - (pb < 0 ? 99 : pb);
  const ka = a.t.plan_key ?? '', kb = b.t.plan_key ?? '';
  return ka.localeCompare(kb, 'es', { numeric: true }) || a.t.id - b.t.id;
}

/* #301: el degradado del borde derecho solo tiene sentido si queda algo por ver. Marca
   data-mas="si" cuando el contenido no cabe, y lo quita cuando llegas al final: asi la
   ultima columna no se desvanece cuando ya la estas viendo entera. Se recalcula al pintar,
   al desplazar y al cambiar el tamano de la ventana. */
function marcarScrollWf(root) {
  if (!root) return;
  const queda = root.scrollWidth - root.clientWidth - root.scrollLeft;
  if (queda > 4) root.dataset.mas = 'si'; else delete root.dataset.mas;
}

export function renderWorkflowBoard(state) {
  const root = document.getElementById('wf-board');
  const lat = document.getElementById('wf-laterals');
  root.innerHTML = '';
  lat.innerHTML = '';
  lat.hidden = true;
  if (state.wfTab !== 'tasks') return; // otra pestaña: el pane manda (renderWorkflowPane)
  const wf = state.workflow;
  if (!wf) {
    root.innerHTML = `<div class="board-empty"><b>Elige un workflow.</b>Su tablero por estados aparece aquí.</div>`;
    return;
  }

  const showPhase = state.wfPhase == null;
  const items = workflowTickets(wf)
    .filter(x => state.wfPhase == null || x.phase === state.wfPhase)
    .sort((a, b) => planOrder(a, b, wf.phases));

  if (!items.length) {
    root.innerHTML = `<div class="board-empty"><b>Nada en esta fase.</b>Quita el filtro de fase para ver todo el workflow.</div>`;
    return;
  }

  for (const def of COLUMNS) {
    const cards = items.filter(x => x.t.status === def.status).map(x => wfCard(x.t, x.phase, { showPhase }));
    root.appendChild(buildStateColumn(def, cards, { onDrop: handlers.onDropTicket, extraClass: 'col--wf' }));
  }

  /* laterales: bloqueadas / rechazadas / expiradas del workflow */
  const groups = [
    ['blocked', 'Bloqueadas'],
    ['rejected', 'Rechazadas'],
    ['expired', 'Expiradas'],
  ].map(([k, label]) => [label, items.filter(x => x.t.status === k)]).filter(([, arr]) => arr.length);
  if (!groups.length) return;
  lat.hidden = false;
  for (const [label, arr] of groups) {
    const head = document.createElement('div');
    head.className = 'laterals__head';
    head.innerHTML = `<span class="laterals__title">${label} · ${arr.length}</span>`;
    lat.appendChild(head);
    const row = document.createElement('div');
    row.className = 'laterals__row';
    for (const x of arr) row.appendChild(wfCard(x.t, x.phase, { showPhase }));
    lat.appendChild(row);
  }
}

/* ---------------- T3.5: pestañas Handoffs / Actividad ---------------- */

const A_STATUS = { open: 'abierto', dispatched: 'despachado', fixed: 'corregido', dismissed: 'descartado' };

export function renderWorkflowPane(state) {
  const pane = document.getElementById('wf-pane');
  const onTasks = state.wfTab === 'tasks' || !state.workflow;
  document.getElementById('wf-phases').hidden = onTasks ? !state.workflow : true;
  document.getElementById('wf-board').hidden = !onTasks;
  if (!onTasks) document.getElementById('wf-laterals').hidden = true;
  pane.hidden = onTasks;
  if (onTasks) return;
  pane.innerHTML = '';

  if (state.wfTab === 'handoffs') {
    const head = document.createElement('div');
    head.className = 'pane__head';
    head.innerHTML = `<span class="pane__title">Handoffs · ${esc(state.workflow.name)}</span>
      <span class="pane__hint">El «por dónde vamos» de cada sesión, todos, con autor y hora. Más reciente arriba.</span>`;
    const write = document.createElement('button');
    write.className = 'btn btn--primary btn--sm';
    write.textContent = 'Escribir handoff…';
    write.addEventListener('click', () => handlers.onEditHandoff());
    head.appendChild(write);
    pane.appendChild(head);

    const list = state.wfHandoffs;
    if (list == null) { pane.insertAdjacentHTML('beforeend', `<div class="pane__empty">cargando…</div>`); return; }
    if (!list.length) { pane.insertAdjacentHTML('beforeend', `<div class="pane__empty">Sin handoffs todavía — la próxima sesión entrará a ciegas. Escribe el primero.</div>`); return; }
    for (const h of list) {
      const item = document.createElement('article');
      item.className = 'hitem';
      const firstLine = (h.content ?? '').split('\n').find(l => l.trim()) ?? '';
      item.innerHTML = `
        <div class="hitem__top">
          <span class="hitem__by">${esc(h.created_by)}</span>
          <span class="hitem__when">${fmtWhen(h.created_at)}</span>
          <span class="hitem__lead">${esc(firstLine)}</span>
        </div>`;
      const pre = document.createElement('pre');
      pre.className = 'handoff__content';
      pre.textContent = h.content;
      item.appendChild(pre);
      pane.appendChild(item);
    }
    return;
  }

  if (state.wfTab === 'documents') { renderDocuments(pane, state); return; }
  if (state.wfTab === 'decisions') { renderDecisionsPane(pane, state, handlers); return; }

  if (state.wfTab === 'activity') {
    const head = document.createElement('div');
    head.className = 'pane__head';
    const sinceTxt = state.wfLastSeen
      ? `desde tu última visita (${fmtWhen(state.wfLastSeen)})`
      : 'reciente — primera visita a este workflow, sin sello previo';
    head.innerHTML = `<span class="pane__title">Actividad · ${esc(state.workflow.name)}</span>
      <span class="pane__hint">${esc(sinceTxt)}</span>`;
    const seen = document.createElement('button');
    seen.className = 'btn btn--ghost btn--sm';
    seen.textContent = 'Marcar como visto';
    seen.title = 'Sella ahora: la próxima vez solo verás lo que pase a partir de este momento';
    seen.addEventListener('click', () => handlers.onMarkSeen());
    head.appendChild(seen);
    pane.appendChild(head);

    const list = state.wfActivity;
    if (list == null) { pane.insertAdjacentHTML('beforeend', `<div class="pane__empty">cargando…</div>`); return; }
    if (!list.length) { pane.insertAdjacentHTML('beforeend', `<div class="pane__empty">${state.wfLastSeen ? `${tr('Nada nuevo desde')} ${fmtWhen(state.wfLastSeen)}.` : tr('Nada nuevo.')}</div>`); return; }
    const ul = document.createElement('ul');
    ul.className = 'act';
    for (const a of list) {
      const li = document.createElement('li');
      li.className = `act__row act__row--${a.kind}`;
      if (a.kind === 'ticket') {
        const move = a.new_status
          ? `${a.old_status ? esc(STATUS_LABEL[a.old_status] ?? a.old_status) + ' → ' : ''}<b>${esc(STATUS_LABEL[a.new_status] ?? a.new_status)}</b>`
          : '';
        li.innerHTML = `
          <time>${fmtWhen(a.at)}</time>
          <span class="act__who">${esc(a.who ?? '')}</span>
          <span class="act__what">
            <button class="act__key" data-ticket="${a.ticket_id}">${esc(a.plan_key ?? '#' + a.ticket_id)}</button>
            <span class="act__title">${esc(cleanTitle(a))}</span>
            ${move ? `<span class="act__move">${move}</span>` : ''}
            ${a.note ? `<span class="act__note">${esc(a.note)}</span>` : ''}
          </span>`;
        li.querySelector('.act__key').addEventListener('click', () => {
          const t = findInWorkflow(state.workflow, a.ticket_id);
          if (t) handlers.onOpenTicket(t);
        });
      } else {
        const sev = SEV[a.severity] ?? SEV.low;
        li.innerHTML = `
          <time>${fmtWhen(a.at)}</time>
          <span class="act__who">${esc(a.who ?? '')}</span>
          <span class="act__what">
            <span class="sev ${sev.cls}">${sev.glyph} ${tr(sev.label)}</span>
            <span class="act__title">hallazgo: ${esc(a.title ?? '')}</span>
            ${a.status ? `<span class="act__move">${esc(A_STATUS[a.status] ?? a.status)}</span>` : ''}
          </span>`;
      }
      ul.appendChild(li);
    }
    pane.appendChild(ul);
  }
}

/* T3.6: documentos del workflow, abribles desde Electron (preload.openPath) */
function fmtSize(n) {
  if (n == null) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}
const DOC_KIND = { plan: 'plan', spec: 'spec', review: 'review', handoff: 'handoff', doc: 'doc', screenshot: 'captura', report: 'informe' };

function renderDocuments(pane, state) {
  const head = document.createElement('div');
  head.className = 'pane__head';
  const canOpen = typeof window.agenticos?.openPath === 'function';
  head.innerHTML = `<span class="pane__title">Documentos · ${esc(state.workflow.name)}</span>
    <span class="pane__hint">Plan, spec, reviews y demás ficheros del workflow. ${canOpen ? 'Clic abre el fichero con la app del sistema.' : 'Abrir solo funciona dentro de la app de escritorio (aquí se enseña la ruta).'}</span>`;
  pane.appendChild(head);
  const docs = state.wfDocuments;
  if (docs == null) { pane.insertAdjacentHTML('beforeend', `<div class="pane__empty">cargando…</div>`); return; }
  if (!docs.length) { pane.insertAdjacentHTML('beforeend', `<div class="pane__empty">Sin documentos registrados para este workflow.</div>`); return; }
  const ul = document.createElement('ul');
  ul.className = 'docs';
  for (const d of docs) {
    const li = document.createElement('li');
    li.className = 'docs__row';
    li.setAttribute('role', 'button'); li.tabIndex = 0;
    li.title = d.path;
    li.innerHTML = `
      <span class="docs__kind docs__kind--${esc(d.kind ?? 'doc')}">${esc(DOC_KIND[d.kind] ?? d.kind ?? 'doc')}</span>
      <span class="docs__name">${esc(d.name)}</span>
      <span class="docs__meta">${esc(fmtSize(d.size))}${d.modified ? ' · ' + fmtWhen(d.modified) : ''}</span>
      <span class="docs__path">${esc(d.path)}</span>`;
    const open = () => handlers.onOpenDocument(d);
    li.addEventListener('click', open);
    li.addEventListener('keydown', (e) => { if (e.key === 'Enter') open(); });
    ul.appendChild(li);
  }
  pane.appendChild(ul);
}

function findInWorkflow(wf, id) {
  for (const arr of Object.values(wf.tickets_by_phase ?? {})) {
    const t = arr.find(x => x.id === id);
    if (t) return t;
  }
  return (wf.stray_tickets ?? []).find(x => x.id === id) ?? null;
}

/* ---------------- mesa de bugs ---------------- */

const F_ORDER = { open: 0, dispatched: 1, fixed: 2, dismissed: 3 };
const F_LABEL = { open: 'Abiertos', dispatched: 'Despachados', fixed: 'Corregidos', dismissed: 'Descartados' };

export function renderFindingsDrawer(state) {
  const drawer = document.getElementById('findings-drawer');
  if (!state.drawerOpen || !state.workflow) {
    drawer.classList.remove('is-open');
    setTimeout(() => { if (!state.drawerOpen) drawer.hidden = true; }, 240);
    return;
  }
  drawer.hidden = false;
  requestAnimationFrame(() => drawer.classList.add('is-open'));

  const SEV_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };
  const findings = [...(state.findings ?? [])]
    .sort((a, b) => (F_ORDER[a.status] - F_ORDER[b.status])
      || (SEV_ORDER[a.severity] - SEV_ORDER[b.severity])
      || (a.created_at < b.created_at ? 1 : -1));

  drawer.innerHTML = `
    <header class="drawer__head">
      <span class="machine__title" style="margin:0;">${tr('Mesa de bugs')} — ${esc(state.workflow.name)}</span>
      <button class="panel__close drawer__close" aria-label="Cerrar mesa de bugs">✕</button>
    </header>
    <div class="drawer__body"></div>`;
  drawer.querySelector('.drawer__close').addEventListener('click', () => handlers.onToggleDrawer());

  const body = drawer.querySelector('.drawer__body');
  if (!findings.length) {
    body.innerHTML = `<div class="col__empty" style="color:var(--color-surface-text-soft)">Sin hallazgos. Los adversariales aún no han mordido.</div>`;
    return;
  }

  let lastStatus = null;
  for (const f of findings) {
    if (f.status !== lastStatus) {
      lastStatus = f.status;
      const h = document.createElement('div');
      h.className = 'drawer__group';
      h.textContent = F_LABEL[f.status] ?? f.status;
      body.appendChild(h);
    }
    const sev = SEV[f.severity] ?? SEV.low;
    const item = document.createElement('article');
    item.className = `finding finding--${f.status}`;
    item.innerHTML = `
      <div class="finding__top">
        <span class="sev ${sev.cls}">${sev.glyph} ${tr(sev.label)}</span>
        ${f.category ? `<span class="finding__cat">${esc(f.category)}</span>` : ''}
        <span class="finding__by">${esc(f.found_by)} · ${fmtWhen(f.created_at)}</span>
      </div>
      <div class="finding__title">${esc(f.title)}</div>
      ${f.detail ? `<div class="finding__detail">${esc(f.detail)}</div>` : ''}
      ${f.suggestion ? `<div class="finding__suggestion">↪ ${esc(f.suggestion)}</div>` : ''}
      <div class="finding__refs">
        ${f.ticket_id ? `<span class="finding__ref">${tr('en')} #${f.ticket_id}</span>` : ''}
        ${f.correction_ticket ? `<button class="finding__fix" data-fix="${f.correction_ticket.id}">FIX #${f.correction_ticket.id} · ${esc(STATUS_LABEL[f.correction_ticket.status] ?? f.correction_ticket.status)}</button>` : ''}
        ${f.resolved_note ? `<span class="finding__ref">— ${esc(f.resolved_note)}</span>` : ''}
      </div>`;

    if (f.status === 'open') {
      const row = document.createElement('div');
      row.className = 'finding__actions';
      const d = document.createElement('button');
      d.className = 'btn btn--primary btn--sm';
      d.textContent = 'Despachar como FIX';
      d.addEventListener('click', () => handlers.onDispatchFinding(f));
      const x = document.createElement('button');
      x.className = 'btn btn--danger btn--sm';
      x.textContent = 'Descartar…';
      x.addEventListener('click', () => handlers.onDismissFinding(f));
      row.append(d, x);
      item.appendChild(row);
    }
    body.appendChild(item);
  }
}

/* badges en la pestaña Workflows: rojo = findings open; naranja = bisagras que esperan al dueño */
export function renderSurfaceBadge(state) {
  const badge = document.getElementById('wf-findings-badge');
  const open = (state.workflows ?? []).reduce((n, w) => n + (w.open_findings ?? 0), 0);
  badge.hidden = !open;
  badge.textContent = open;
  const dec = document.getElementById('wf-decisions-badge');
  const pending = (state.workflows ?? []).reduce((n, w) => n + (w.decisions_pending_count ?? 0), 0)
    || (state.workflow?.decisions_pending_count ?? pendingCount(state.wfDecisions));
  dec.hidden = !pending;
  dec.textContent = pending;
  dec.title = `${pending} bisagra${pending === 1 ? '' : 's'} esperando al dueño`;
}

/* se engancha una vez: el render lo llama al terminar, y el scroll/resize lo refrescan */
{
  const root = document.getElementById('wf-board');
  if (root) {
    root.addEventListener('scroll', () => marcarScrollWf(root), { passive: true });
    window.addEventListener('resize', () => marcarScrollWf(root));
    new MutationObserver(() => marcarScrollWf(root)).observe(root, { childList: true });
    marcarScrollWf(root);
  }
}
