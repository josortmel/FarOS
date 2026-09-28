/* ============================================================
   app.js — arranque y cableado.
   Flujo de verdad único: acción → API → (SSE) board.changed →
   refetch → render. Sin estado optimista: si el motor dice no,
   la tarjeta nunca se movió y el motivo se enseña.
   ============================================================ */

import { connect, api, onBoardChanged, ApiError } from './api.js';
import { getState, setState, subscribe, findTicket, findWorkflowTicket, persist, readLastSeen, stampLastSeen } from './store.js';
import { initCalendar, renderCalendar, rangeFor, flashDayItem, todayISO } from './ui/calendar.js';
import { initAgents, renderAgentsBar, renderAgentsBody, renderAgentsSurfaceBadge, openJobPanel, isSettings } from './ui/agents.js';
import { initMcpSettings } from './ui/mcp_settings.js';
import { initI18n, applyI18n, setLang, getLang, onRepaint, locale, t as tr } from './i18n.js';
import { jobModal } from './ui/job_modal.js';
import { STATUS_LABEL, TRANSITIONS } from './transitions.js';
import { initBoard, renderBoard, renderLaterals, flashCard, captureRects, flipFrom } from './ui/board.js';
import { initPanel, openPanel, closePanel } from './ui/panel.js';
import { createTaskModal, evidenceDialog, reasonDialog, projectDialog, sweepResultToastText, dispatchDialog, handoffComposer } from './ui/modal.js';
import { initWorkflow, renderWorkflowSidebar, renderWorkflowBar, renderMilestoneBanner, renderPhaseStrip, renderWorkflowBoard, renderWorkflowPane, renderFindingsDrawer, renderSurfaceBadge, renderHandoff } from './ui/workflow.js';
import { toast, toastReject } from './ui/toast.js';
import { esc, projectColor, humanError, fmtDayShort } from './util.js';

/* ---------------- refetch ---------------- */

let refetchTimer = null;

async function refetchBoard() {
  const { view, project } = getState();
  try {
    const board = await api().board(view, project);
    setState({ board });
  } catch (err) {
    toast(`${tr('No se pudo cargar el tablero:')} ${humanError(err)}`, 'reject');
  }
}

/* T2.5: el rango en pantalla del calendario — se pide entero, la vista filtra */
async function refetchCalendar() {
  const { calView, calDate } = getState();
  const range = rangeFor(calView, calDate);
  try {
    const calData = await api().calendar(range.from, range.to);
    setState({ calData, calRange: range });
  } catch (err) {
    toast(`${tr('No se pudo cargar el calendario:')} ${humanError(err)}`, 'reject');
  }
}
/* T5.2: el calendario de Agentes tiene su propio rango y su propia fuente */
async function refetchAgCalendar() {
  const { agCalView, agCalDate } = getState();
  const range = rangeFor(agCalView, agCalDate);
  try { setState({ agCalData: await api().agentsCalendar(range.from, range.to), agCalRange: range }); }
  catch (err) { toast(`${tr('No se pudo cargar el calendario de Agentes:')} ${humanError(err)}`, 'reject'); }
}
function ensureAgCalendar() {
  const { agCalView, agCalDate, agCalRange, agCalData } = getState();
  const r = rangeFor(agCalView, agCalDate);
  if (agCalData && agCalRange && agCalRange.from <= r.from && r.to <= agCalRange.to) { setState({}); return; }
  setState({ agCalData: null });
  refetchAgCalendar();
}
function goAgCalendar(agCalView, agCalDate = null, extra = {}) {
  const patch = { agCalView, ...extra };
  if (agCalView === 'year') agCalDate = `${(agCalDate ?? getState().agCalDate).slice(0, 4)}-01-01`;
  if (agCalDate) patch.agCalDate = agCalDate;
  setState(patch);
  ensureAgCalendar();
}
const inAgents = () => getState().surface === 'agents';

/* al cambiar de vista o de fecha: si el rango cargado ya cubre, solo se repinta */
function ensureCalendar() {
  const { calView, calDate, calRange, calData } = getState();
  const r = rangeFor(calView, calDate);
  if (calData && calRange && calRange.from <= r.from && r.to <= calRange.to) { setState({}); return; }
  setState({ calData: null });
  refetchCalendar();
}

async function refetchWorkflows() {
  try {
    /* FIX #192 (el dueño): un archivado solo se ve en Archivados — el listado por defecto del motor trae todos */
    const workflows = (await api().workflows()).filter(w => w.status !== 'archived');
    const patch = { workflows };
    const { workflowId, wfLastSeen } = getState();
    if (workflowId != null) {
      const [workflow, findings, wfHandoffs, wfActivity, wfDocuments, wfDecisions] = await Promise.all([
        api().workflow(workflowId),
        api().findings(workflowId),
        api().handoffs(workflowId).catch(() => null),          // daemon sin T3.5: pestaña vacía, sin ruido
        api().activity(workflowId, wfLastSeen).catch(() => null),
        api().documents(workflowId).catch(() => null),         // T3.6
        api().decisions(workflowId).catch(() => null),         // T3.4
      ]);
      Object.assign(patch, { workflow, findings, wfHandoffs, wfActivity, wfDocuments, wfDecisions });
    }
    setState(patch);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      // daemon v1 sin API de workflows todavía: superficie vacía, sin ruido
      setState({ workflows: [] });
      return;
    }
    toast(`${tr('No se pudieron cargar los workflows:')} ${humanError(err)}`, 'reject');
  }
}

/* v3 Agentes: cuatro lecturas en paralelo; si el daemon aún no tiene la API
   (404), la superficie lo dice sin ruido */
async function refetchAgents() {
  const { agScheduleDays } = getState();
  try {
    const [agJobs, agSchedule, agResults, agRunning, mcpServers] = await Promise.all([
      api().jobs(),
      api().agentsSchedule(agScheduleDays),
      api().agentsResults(),
      api().agentsRunning(),
      api().mcpServers().catch(() => []), // T5.9: daemon sin registro → lista vacía, sin ruido
    ]);
    setState({ agJobs, agSchedule, agResults, agRunning, mcpServers });
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) { setState({ agJobs: [], agSchedule: { items: [] }, agResults: [], agRunning: [] }); return; }
    toast(`${tr('No se pudo cargar Agentes:')} ${humanError(err)}`, 'reject');
  }
}

function scheduleRefetch() {
  clearTimeout(refetchTimer);
  refetchTimer = setTimeout(() => {
    refetchBoard();
    if (getState().boardMode === 'calendar') refetchCalendar();
    if (getState().surface === 'workflow' || getState().workflows) refetchWorkflows();
    if (getState().surface === 'agents' || getState().agJobs) refetchAgents();
    if (getState().surface === 'agents' && getState().agMode === 'calendar') refetchAgCalendar(); // T5.2
  }, 120); // agrupa ráfagas de SSE
}

/* ---------------- transiciones ---------------- */

async function requestTransition(t, target, action) {
  const { agent } = getState();
  const body = { action, agent, to: target };

  // completar exige evidencia — manual: se pide a la mano
  if (target === 'done') {
    const ev = await evidenceDialog(getState().meta, t);
    if (!ev) return; // canceló: la tarjeta no se movió
    Object.assign(body, ev);
  }

  // bloquear y rechazar exigen motivo
  if (target === 'blocked' || target === 'rejected') {
    const verb = target === 'blocked' ? 'Bloquear' : 'Rechazar';
    const rs = await reasonDialog(`${verb} «${t.title}»`, verb);
    if (!rs) return;
    body.note = rs.note;
  }

  // T2.3 (el dueño §1.3): devolver una hecha/verificada exige motivo (≥ 10) — el historial lo guarda como [rechazo]
  if (action === 'rework') {
    const rs = await reasonDialog(`${tr(`Devolver «${t.title}» a`)} ${STATUS_LABEL[target] ?? target}`, 'Devolver', {
      min: 10, hint: 'qué falta o qué está mal — se guarda en el historial con la fecha',
    });
    if (!rs) return;
    body.reason = rs.note;
  }

  try {
    const res = await api().transition(t.id, body);
    /* el motor devuelve el ticket: una recurrente estampada sigue «accepted» con last_done_at de hoy */
    if (res?.recurring_stamped || (t.cadence_days && target === 'done' && res?.status === 'accepted')) {
      toast(`Recurrente estampada: «${t.title}» vuelve a su ciclo.`, 'ok');
    } else if (target === 'done' && res?.status === 'verified') {
      /* T2.2 (el dueño §1.2): una tarea de verificación propia no pasa por Hechas — se dice, no desaparece */
      toast(`«${t.title}» hecha y verificada (verificación propia): pasa a Verificadas.`, 'ok');
    } else if (action === 'rework') {
      /* T2.4 (el dueño §3): «al devolverla, la tarea desaparece». No desaparecía: se quedaba
         sin fecha y las vistas Hoy/Semana no enseñan lo aceptado sin fecha. Con T1.3 el
         motor la ancla a hoy (`anchored`), y aquí se DICE adónde ha ido — que algo se
         mueva en silencio a un sitio que no estás mirando es indistinguible de perderlo. */
      toast(res?.anchored
        ? `${tr(`«${t.title}» devuelta a`)} ${STATUS_LABEL[target] ?? target} — ${tr('vence hoy, la tienes en la vista de Hoy.')}`
        : `${tr(`«${t.title}» devuelta a`)} ${STATUS_LABEL[target] ?? target}.`, 'ok');
    }
    closePanel();
    setState({ selected: null });
    await refetchBoard();
    /* que se vea CUÁL volvió: sin esto vuelve a ser un cambio en silencio */
    if (action === 'rework') { flashCard(t.id); flashDayItem(t.id); }
    if (getState().boardMode === 'calendar') await refetchCalendar(); // T2.9: estampar desde el calendario
    if (getState().surface === 'workflow') await refetchWorkflows(); // el kanban de la Oficina también se mueve
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      toastReject(humanError(err));
      flashCard(t.id); flashDayItem(t.id); // la tarjeta no se movió: que se vea cuál era
    } else {
      toast(`${tr('Error de red:')} ${humanError(err)}`, 'reject');
    }
  }
}

/* ---------------- panel ---------------- */

function reopenSelected() {
  const { selected, selectedJob, board } = getState();
  if (selectedJob != null) {
    const j = (getState().agJobs ?? []).find(x => x.id === selectedJob);
    if (j) openJobPanel(j); else { closePanel(); setState({ selectedJob: null }); }
    return;
  }
  if (selected == null) return;
  const t = findTicket(selected) ?? findWorkflowTicket(selected);
  if (t) openPanel(t, board ?? { projects: [] });
  else if (getState().boardMode === 'calendar') openTicketById(selected); // T2.5: abierto desde el calendario, fuera de la foto del kanban
  else { closePanel(); setState({ selected: null }); }
}

/* T2.5: el calendario enseña ocurrencias, no tickets — el panel pide el ticket entero */
async function openTicketById(id) {
  try {
    const t = findTicket(id) ?? findWorkflowTicket(id) ?? await api().ticket(id);
    if (!t) { closePanel(); setState({ selected: null }); return; }
    setState({ selected: id });
    openPanel(t, getState().board ?? { projects: [] });
  } catch (err) {
    toast(`${tr('No se pudo abrir la tarea:')} ${humanError(err)}`, 'reject');
  }
}

/* ---------------- barra de proyectos ---------------- */

function renderProjectBar(state) {
  const bar = document.getElementById('project-bar');
  bar.innerHTML = '';
  if (!state.board) return;

  const all = document.createElement('button');
  all.className = `pchip${state.project == null ? ' is-active' : ''}`;
  all.textContent = 'Todos los proyectos';
  all.addEventListener('click', () => { setState({ project: null }); refetchBoard(); });
  bar.appendChild(all);

  for (const p of state.board.projects.filter(p => !p.archived)) {
    const chip = document.createElement('button');
    chip.className = `pchip${state.project === p.id ? ' is-active' : ''}`;
    chip.innerHTML = `<span class="pchip__dot" style="background:${esc(projectColor(p))}"></span>${esc(p.name)}`;
    chip.addEventListener('click', () => {
      setState({ project: state.project === p.id ? null : p.id });
      refetchBoard();
    });
    bar.appendChild(chip);

    /* T4.8: el proyecto activo se edita desde su chip (nombre, color, archivar) */
    if (state.project === p.id) {
      const edit = document.createElement('button');
      edit.className = 'pchip pchip--edit';
      edit.title = `Editar «${p.name}»`;
      edit.setAttribute('aria-label', `Editar proyecto ${p.name}`);
      edit.textContent = '✎';
      edit.addEventListener('click', async () => {
        const out = await projectDialog(p);
        if (!out) return;
        try {
          await api().patchProject(p.id, { agent: state.agent, ...out });
          if (out.archived) {
            toast(`Proyecto «${p.name}» archivado — sus tareas siguen, solo desaparece del filtro.`, 'ok');
            setState({ project: null });
          } else {
            toast(`Proyecto «${out.name}» guardado.`, 'ok');
          }
          await refetchBoard();
        } catch (err) {
          if (err instanceof ApiError && (err.status === 409 || err.status === 400)) toastReject(humanError(err));
          else toast(`${tr('No se pudo guardar:')} ${humanError(err)}`, 'reject');
        }
      });
      bar.appendChild(edit);
    }
  }

  const add = document.createElement('button');
  add.className = 'pchip pchip--new';
  add.textContent = '+ proyecto';
  add.addEventListener('click', async () => {
    const p = await projectDialog();
    if (!p) return;
    try {
      await api().createProject(p);
      toast(`Proyecto «${p.name}» creado.`, 'ok');
      await refetchBoard();
    } catch (err) { toast(`${tr('No se pudo crear:')} ${humanError(err)}`, 'reject'); }
  });
  bar.appendChild(add);

  /* el pulso del día: lo urgente, en una línea, sin abrir nada */
  const board = state.board;
  const everyTicket = [
    ...Object.values(board.columns ?? {}).flat(),
    ...Object.values(board.laterals ?? {}).flat(),
  ];
  const overdue = everyTicket.filter(t => t.due_state === 'overdue').length;
  const running = everyTicket.filter(t => t.linked_job?.last_run?.status === 'running').length; // T1.14: brazos, no agénticas
  const toVerify = (board.columns?.done ?? []).length;
  const blocked = (board.laterals?.blocked ?? []).length;

  const bits = [];
  if (overdue) bits.push(`<span class="boardpulse__bad">▲ ${overdue} vencida${overdue > 1 ? 's' : ''}</span>`);
  if (running) bits.push(`<span class="boardpulse__live">● ${running} en ejecución</span>`);
  if (toVerify) bits.push(`<span>${toVerify} por verificar</span>`);
  if (blocked) bits.push(`<span>⛔ ${blocked} bloqueada${blocked > 1 ? 's' : ''}</span>`);

  /* T3.1: el pulso vive en la línea de controles del Tablero, a la derecha */
  const pulse = document.getElementById('board-pulse');
  pulse.hidden = !bits.length;
  pulse.innerHTML = bits.join('<span class="boardpulse__sep">·</span>');
}

/* ---------------- vistas ---------------- */

/* FIX #150 (el dueño: «que la vista de semana / hoy / futuras tuviera un calendario como
   vista alternativa»): UN solo conmutador Kanban↔Calendario, y el calendario es la
   otra cara de la pestaña temporal activa. Hoy→Día · Semana→Semana · Futuras→Mes ·
   Todas→Año. Bajar de Año a Mes o de Semana a Día mueve la pestaña, para que las dos
   caras siempre digan lo mismo. */
const VIEW_TO_CAL = { today: 'day', week: 'week', future: 'month', all: 'year' };
const CAL_TO_VIEW = { day: 'today', week: 'week', month: 'future', year: 'all' };

/* entrar en una vista del calendario por su nombre: pestaña + vista + fecha ancla */
function goCalendar(calView, calDate = null, extra = {}) {
  persist('calView', calView);
  const patch = { calView, view: CAL_TO_VIEW[calView] ?? getState().view, ...extra };
  /* T3.3: el Año es una ventana de 12 meses anclada en el mes de calDate; al ENTRAR se
     abre en enero–diciembre (lo que el dueño aprobó) y ‹ › la desplaza mes a mes */
  if (calView === 'year') calDate = `${(calDate ?? getState().calDate).slice(0, 4)}-01-01`;
  if (calDate) patch.calDate = calDate;
  setState(patch);
  ensureCalendar();
}

/* T3.3 (el dueño §2): con el Calendario activo las pestañas dicen lo que se ve — Mes y Año,
   no Futuras y Todas. Mismo conmutador de #150; solo cambia el rótulo. */
const VIEW_LABEL = { today: 'Hoy', week: 'Semana', future: 'Futuras', all: 'Todas' };
const CAL_LABEL  = { today: 'Hoy', week: 'Semana', future: 'Mes',     all: 'Año' };
function renderViewTabs(state) {
  const labels = state.boardMode === 'calendar' ? CAL_LABEL : VIEW_LABEL;
  document.querySelectorAll('#view-tabs .views__tab').forEach(b => {
    b.classList.toggle('is-active', b.dataset.view === state.view);
    b.textContent = labels[b.dataset.view] ?? b.textContent;
  });
}

/* ---------------- T3.2: tema (el dueño, queja 3ª) ----------------
   auto = lo que diga el sistema (y cambia con él); claro/oscuro fuerzan y persisten. */
const THEME_LABEL = { auto: 'auto', light: 'claro', dark: 'oscuro' };
const mqDark = typeof matchMedia === 'function' ? matchMedia('(prefers-color-scheme: dark)') : null;
function applyTheme(pref) {
  const resolved = pref === 'auto' ? (mqDark?.matches ? 'dark' : 'light') : pref;
  document.documentElement.dataset.theme = resolved;
  const btn = document.getElementById('btn-theme');
  if (btn) {
    btn.dataset.theme = pref;
    document.getElementById('theme-label').textContent = THEME_LABEL[pref];
    btn.title = pref === 'auto' ? `Tema: sigue al sistema (ahora ${resolved === 'dark' ? 'oscuro' : 'claro'}). Clic para cambiar` : `Tema: ${THEME_LABEL[pref]}. Clic para cambiar`;
    btn.setAttribute('aria-label', `Tema: ${THEME_LABEL[pref]}`);
  }
}
function initTheme() {
  applyTheme(getState().theme);
  mqDark?.addEventListener('change', () => { if (getState().theme === 'auto') applyTheme('auto'); });
  document.getElementById('btn-theme').addEventListener('click', () => {
    const order = ['auto', 'light', 'dark'];
    const next = order[(order.indexOf(getState().theme) + 1) % order.length];
    persist('theme', next);
    setState({ theme: next });
    applyTheme(next);
  });
}

/* ---------------- render raíz ---------------- */

function applySurface(state) {
  const isWf = state.surface === 'workflow';
  const isAg = state.surface === 'agents';
  const isCal = !isWf && !isAg && state.boardMode === 'calendar'; // T2.4: mismo Tablero, otro modo de verlo
  const offBoard = isWf || isAg;
  document.getElementById('board-bar').hidden = offBoard;                       // T3.1: la línea de controles es del Tablero
  document.getElementById('view-tabs').style.display = '';                       // FIX #150: Hoy/Semana/Futuras/Todas mandan en los dos modos
  document.getElementById('mode-tabs').hidden = false;
  document.getElementById('cal-tabs').hidden = true;                             // la vista del calendario la elige la pestaña temporal
  document.getElementById('project-bar').hidden = offBoard;
  document.getElementById('board').hidden = offBoard || isCal;
  if (offBoard || isCal) document.getElementById('laterals').hidden = true; // en kanban lo decide renderLaterals
  document.getElementById('cal-root').hidden = !isCal;
  document.getElementById('wf-root').hidden = !isWf;
  document.getElementById('ag-root').hidden = !isAg;
  /* Nueva tarea / Verificar cola son del Tablero; en Agentes manda «Nuevo job» */
  document.getElementById('btn-create').hidden = isAg;
  document.getElementById('btn-verify').hidden = isAg;
  /* #277: Ajustes NO es una superficie de trabajo — vive en la zona de acciones (T2.5, y es
     decisión del dueño: la columna central no se toca). Así que estando en Ajustes no se
     enciende ninguna de las tres superficies y se enciende el botón que se pulsó. Antes
     decía «Agentes», que es exactamente lo que él vio: «lanza el panel de agentes». */
  const inSettings = isSettings(state);
  document.querySelectorAll('#surface-tabs .views__tab').forEach(b => {
    b.classList.toggle('is-active', !inSettings && b.dataset.surface === state.surface);
  });
  document.getElementById('btn-settings').classList.toggle('is-active', inSettings);
  document.querySelectorAll('#mode-tabs .views__tab').forEach(b => {
    b.classList.toggle('is-active', b.dataset.mode === state.boardMode);
  });
  document.querySelectorAll('#cal-tabs .views__tab').forEach(b => {
    b.classList.toggle('is-active', b.dataset.cal === state.calView);
  });
}

/* #262: el catalogo se aplica DESPUES de pintar, en un solo sitio. render() tiene
   varios return (una superficie, un return), asi que se envuelve en vez de tocar cada
   rama: un solo punto de entrada es un solo punto donde puede fallar. */
function render(state) {
  renderEs(state);
  applyI18n();
}

function renderEs(state) {
  applySurface(state);
  renderSurfaceBadge(state);
  renderAgentsSurfaceBadge(state);
  if (state.surface === 'agents') {
    renderAgentsBar(state);
    renderAgentsBody(state);
    return;
  }
  if (state.surface === 'workflow') {
    const WF_CARDS = '#wf-board .card, #wf-laterals .card';
    const rects = captureRects(WF_CARDS);
    renderWorkflowSidebar(state); // T4.3
    renderWorkflowBar(state);
    renderHandoff(state);
    renderMilestoneBanner(state);
    renderPhaseStrip(state);
    renderWorkflowBoard(state);
    renderWorkflowPane(state);
    renderFindingsDrawer(state);
    flipFrom(rects, WF_CARDS);
    return;
  }
  if (state.boardMode === 'calendar') {
    renderViewTabs(state);     // FIX #150: la pestaña temporal es la misma en las dos caras
    renderProjectBar(state);   // el filtro de proyecto y el pulso del día valen en los dos modos
    renderCalendar(state);
    renderFindingsDrawer(state);
    return;
  }
  const rects = captureRects(); // FLIP: dónde estaba cada tarjeta antes del cambio
  renderViewTabs(state);
  renderProjectBar(state);
  renderBoard(state);
  renderLaterals(state);
  renderFindingsDrawer(state);
  flipFrom(rects);              // …y que se deslice hasta donde está ahora
}

/* ---------------- boot ---------------- */

async function main() {
  /* T1.14: desde una manual vinculada se salta a su job en Agentes */
  async function jumpToJob(jobId) {
    closePanel();
    setState({ selected: null, surface: 'agents', agTab: 'jobs', agJobFilter: 'all' });
    if (!getState().agJobs) await refetchAgents();
    const j = (getState().agJobs ?? []).find(x => x.id === jobId);
    if (!j) { toast(`El job #${jobId} no está en la lista (¿archivado o borrado?).`, 'info'); return; }
    setState({ selectedJob: jobId });
    openJobPanel(j);
  }

  initBoard({
    onOpen: (id) => {
      setState({ selected: id });
      const t = findTicket(id);
      if (t) openPanel(t, { ...getState().board, agents: getState().meta?.agents });
    },
    onOpenJob: jumpToJob,
    onDrop: (id, target) => {
      const t = findTicket(id);
      if (!t) return;
      const action = TRANSITIONS[t.status]?.[target];
      if (!action) {
        toastReject(`de «${t.status}» no se puede pasar a «${target}».`);
        flashCard(id);
        return;
      }
      requestTransition(t, target, action);
    },
  });

  initPanel({
    onTransition: requestTransition,
    onOpenJob: jumpToJob,
    /* T2.10: el tiempo se edita desde el panel; el motor valida (400 con motivo) */
    /* T2.3: el panel edita título, descripción, prioridad, responsable, criterio y vencimiento (PATCH) */
    onPatchFields: async (t, patch) => {
      try {
        await api().patchTicket(t.id, { agent: getState().agent, ...patch });
        toast(`«${patch.title ?? t.title}» guardada.`, 'ok');
        await refetchBoard();
        if (getState().surface === 'workflow') await refetchWorkflows();
        reopenSelected();
      } catch (err) {
        if (err instanceof ApiError && (err.status === 400 || err.status === 409)) toastReject(humanError(err));
        else toast(`${tr('No se pudo guardar:')} ${humanError(err)}`, 'reject');
      }
    },
    onPatchTime: async (t, patch) => {
      try {
        await api().patchTicket(t.id, { agent: getState().agent, ...patch });
        toast(`Tiempo de «${t.title}» guardado.`, 'ok');
        await refetchBoard();
        if (getState().surface === 'workflow') await refetchWorkflows();
        reopenSelected();
      } catch (err) {
        if (err instanceof ApiError && (err.status === 400 || err.status === 409)) toastReject(humanError(err));
        else toast(`${tr('No se pudo guardar:')} ${humanError(err)}`, 'reject');
      }
    },
    onClose: () => { closePanel(); setState({ selected: null, selectedJob: null }); }, // el panel es uno: ticket o job
  });

  document.querySelectorAll('#view-tabs .views__tab').forEach(b => {
    b.addEventListener('click', () => {
      const view = b.dataset.view;
      setState({ view });
      if (getState().boardMode === 'calendar') goCalendar(VIEW_TO_CAL[view], todayISO(), { calDaySel: null });
      else refetchBoard();
    });
  });

  /* T2.4: Kanban ↔ Calendario (persisten en localStorage) */
  document.querySelectorAll('#mode-tabs .views__tab').forEach(b => {
    b.addEventListener('click', () => {
      const boardMode = b.dataset.mode;
      persist('boardMode', boardMode);
      setState({ boardMode });
      if (boardMode === 'calendar') goCalendar(VIEW_TO_CAL[getState().view] ?? getState().calView);
      else refetchBoard(); // la pestaña pudo cambiar bajando de Año a Mes o de Semana a Día
    });
  });
  initCalendar({
    /* T5.2: los mismos handlers sirven al calendario de Agentes; se enrutan por la superficie */
    onNavigate: (d) => { if (inAgents()) { setState({ agCalDate: d }); ensureAgCalendar(); } else { setState({ calDate: d }); ensureCalendar(); } },
    onOpen: (id) => inAgents() ? jumpToJob(id) : openTicketById(id),
    /* T2.9: estampar desde el calendario = completar con evidencia; el motor la devuelve a su ciclo */
    onStamp: async (it) => {
      const t = findTicket(it.ticket_id) ?? { id: it.ticket_id, title: it.title, status: it.status, cadence_days: it.cadence_days };
      await requestTransition(t, 'done', 'complete');
    },
    /* T2.8: desde el Año se baja al Mes de ese día */
    onOpenMonth: (d) => inAgents() ? goAgCalendar('month', d, { agCalDaySel: null }) : goCalendar('month', d, { calDaySel: null }),
    /* T2.7: abrir un día al lado del Mes (el mes se encoge, no desaparece) */
    onSelectDay: (d) => inAgents() ? setState({ agCalDaySel: d }) : setState({ calDaySel: d }),
    /* T2.6: desde la cabecera de un día de la Semana se baja a ese Día */
    onOpenDay: (d) => inAgents() ? goAgCalendar('day', d) : goCalendar('day', d),
    /* T2.5: arrastrar a una hora → POST /schedule; estirar → duration_min.
       Las recurrentes se proyectan por preferred_time (agenda.py), así que a
       ellas se les cambia la hora preferida por PATCH (contrato T0.3). */
    onSchedule: async (d, patch) => {
      const agent = getState().agent;
      try {
        const movedDay = patch.date && d.date && patch.date !== d.date;
        if (d.cadence) {
          /* a otro día → mueve SOLO la próxima ocurrencia (la cadencia no cambia) */
          if (movedDay) await api().shiftNext(d.ticket_id, { agent, date: patch.date });
          const fields = {};
          if ('start' in patch && (patch.start || !movedDay || patch.preferredOnly)) fields.preferred_time = patch.start ?? '';
          if (patch.duration_min != null) fields.duration_min = patch.duration_min;
          if (Object.keys(fields).length) await api().patchTicket(d.ticket_id, { agent, ...fields });
        } else {
          const body = {};
          /* sin hora = todo el día en esa fecha (no se borra la fecha) */
          if ('start' in patch) { body.scheduled_at = `${patch.date}T${patch.start ?? '00:00'}`; body.all_day = !patch.start; }
          if (patch.duration_min != null) body.duration_min = patch.duration_min;
          await api().schedule(d.ticket_id, { agent, ...body });
        }
        const dayTxt = movedDay ? ` el ${fmtDayShort(patch.date)}` : '';
        toast((patch.preferredOnly || (d.cadence && 'start' in patch && !patch.start && !movedDay)) ? (patch.start ? `Hora preferida: ${patch.start}.` : 'Hora preferida borrada: vuelve a «sin hora», el ciclo no cambia.') : patch.start ? (movedDay ? `${tr('Programada el')} ${fmtDayShort(patch.date)} ${tr('a las')} ${patch.start}.` : `Programada a las ${patch.start}.`) : patch.duration_min != null ? `Duración: ${patch.duration_min} min.` : movedDay ? (d.cadence ? `${tr('Próxima ocurrencia movida al')} ${fmtDayShort(patch.date)}; ${tr('la cadencia no cambia.')}` : `${tr('Movida al')} ${fmtDayShort(patch.date)}, ${tr('todo el día.')}`) : 'Sin hora: queda para todo el día.', 'ok');
        await refetchCalendar();
        refetchBoard();
      } catch (err) {
        if (err instanceof ApiError && (err.status === 400 || err.status === 409)) toastReject(humanError(err));
        else toast(`${tr('No se pudo programar:')} ${humanError(err)}`, 'reject');
        flashDayItem(d.ticket_id);
      }
    },
  });

  /* superficie: Tablero ↔ Workflows ↔ Agentes */
  document.querySelectorAll('#surface-tabs .views__tab').forEach(b => {
    b.addEventListener('click', async () => {
      const surface = b.dataset.surface;
      setState({ surface });
      if (surface === 'agents') {
        if (!getState().agJobs) await refetchAgents();
        if (!getState().harnesses) {
          const [harnesses, mcpInherited] = await Promise.all([
            api().harnesses().catch(() => []),
            api().mcpInherited().catch(() => []),
          ]);
          setState({ harnesses, mcpInherited });
        }
        return;
      }
      if (surface === 'workflow' && !getState().workflows) {
        await refetchWorkflows();
        // autoselección: si hay UN workflow activo, se abre solo
        const ws = getState().workflows ?? [];
        const active = ws.filter(w => w.status === 'active');
        if (getState().workflowId == null && active.length === 1) {
          await selectWorkflow(active[0].id);
        }
      }
    });
  });

  async function selectWorkflow(id) {
    // otro workflow: filtro limpio y su propio sello de visita — nunca se mezclan
    const wfLastSeen = readLastSeen(id);
    setState({ workflowId: id, wfPhase: null, wfTab: 'tasks', wfHandoffs: null, wfActivity: null, wfDocuments: null, wfDecisions: null, wfLastSeen });
    try {
      const [workflow, findings, wfHandoffs, wfActivity, wfDocuments, wfDecisions] = await Promise.all([
        api().workflow(id), api().findings(id),
        api().handoffs(id).catch(() => null),
        api().activity(id, wfLastSeen).catch(() => null),
        api().documents(id).catch(() => null),
        api().decisions(id).catch(() => null),
      ]);
      setState({ workflow, findings, wfHandoffs, wfActivity, wfDocuments, wfDecisions });
    } catch (err) {
      toast(`${tr('No se pudo cargar el workflow:')} ${humanError(err)}`, 'reject');
    }
  }

  /* T4.3: Archivados con buscador; archivar / cancelar */
  async function refetchArchived() {
    try { setState({ wfArchived: await api().workflows({ status: 'archived' }) }); } // el buscador filtra en cliente
    catch (err) { setState({ wfArchived: [] }); toast(`${tr('No se pudo leer Archivados:')} ${humanError(err)}`, 'reject'); }
  }
  initWorkflow({
    onSelectWorkflow: selectWorkflow,
    onListMode: (wfListMode) => { setState({ wfListMode }); if (wfListMode === 'archived' && getState().wfArchived == null) refetchArchived(); },
    onSearch: (wfQuery) => setState({ wfQuery }),
    onUnarchive: async (wf) => {
      try {
        await api().patchWorkflow(wf.id, { agent: getState().agent, status: 'active' }); // contrato d6396d7: reactivar
        toast(`«${wf.name}» reactivado: vuelve a Activos.`, 'ok');
        await refetchWorkflows(); await refetchArchived();
      } catch (err) {
        if (err instanceof ApiError && (err.status === 409 || err.status === 400)) toastReject(humanError(err));
        else toast(`${tr('No se pudo reactivar:')} ${humanError(err)}`, 'reject');
      }
    },
    onArchive: async (wf) => {
      const rs = await reasonDialog(`Archivar «${wf.name}»`, 'Archivar', { min: 1, hint: 'una línea: por qué se cierra el cajón' });
      if (!rs) return;
      try {
        await api().patchWorkflow(wf.id, { agent: getState().agent, status: 'archived', reason: rs.note, ...(wf.status === 'active' ? { close: true } : {}) }); // d6396d7: un activo se cierra y archiva en un paso
        toast(`«${wf.name}» archivado. Lo encuentras en Archivados.`, 'ok');
        await refetchWorkflows(); if (getState().wfListMode === 'archived') await refetchArchived();
      } catch (err) {
        if (err instanceof ApiError && (err.status === 409 || err.status === 400)) toastReject(humanError(err));
        else toast(`${tr('No se pudo archivar:')} ${humanError(err)}`, 'reject');
      }
    },
    onCancel: async (wf) => {
      const rs = await reasonDialog(`Cancelar «${wf.name}»`, 'Cancelar workflow', { min: 10, hint: 'por qué se cancela — queda en el workflow' });
      if (!rs) return;
      try {
        await api().patchWorkflow(wf.id, { agent: getState().agent, status: 'cancelled', reason: rs.note });
        toast(`«${wf.name}» cancelado. Ya se puede archivar.`, 'ok');
        await refetchWorkflows();
      } catch (err) {
        if (err instanceof ApiError && (err.status === 409 || err.status === 400)) toastReject(humanError(err));
        else toast(`${tr('No se pudo cancelar:')} ${humanError(err)}`, 'reject');
      }
    },
    onSelectPhase: (wfPhase) => setState({ wfPhase }),
    onSelectTab: (wfTab) => setState({ wfTab }),
    /* T3.4: el dueño decide una bisagra desde la app; el motor es append-only (409 si ya está) */
    onDecide: async (d, { option, decision, rationale }) => {
      try {
        await api().decide(d.id, { agent: getState().agent, option, decision, rationale });
        toast(`Bisagra #${d.id} decidida: «${option ?? decision}». Registrada con su motivo.`, 'ok');
        await refetchWorkflows();
      } catch (err) {
        if (err instanceof ApiError && (err.status === 409 || err.status === 400)) toastReject(humanError(err));
        else toast(`${tr('No se pudo decidir:')} ${humanError(err)}`, 'reject');
        await refetchWorkflows(); // si otro la decidió antes, que se vea
      }
    },
    onDefer: async (d, until) => {
      const rs = await reasonDialog(`Aplazar «${d.title}»${until ? ' hasta ' + until : ''}`, 'Aplazar');
      if (!rs) return;
      try {
        await api().deferDecision(d.id, { agent: getState().agent, until, note: rs.note });
        toast(`Bisagra #${d.id} aplazada${until ? ' hasta ' + until : ''}.`, 'ok');
        await refetchWorkflows();
      } catch (err) {
        if (err instanceof ApiError && (err.status === 409 || err.status === 400)) toastReject(humanError(err));
        else toast(`${tr('No se pudo aplazar:')} ${humanError(err)}`, 'reject');
      }
    },
    /* T3.6: abrir un documento con la app del sistema (solo en Electron) */
    onOpenDocument: async (d) => {
      const open = window.agenticos?.openPath;
      if (typeof open !== 'function') { toast(`${tr('Abrir solo funciona en la app de escritorio. Ruta:')} ${d.path}`, 'info'); return; }
      try {
        const err = await open(d.path);
        if (err) toastReject(`No se pudo abrir «${d.name}»: ${err}`);
      } catch (e) { toast(`${tr('No se pudo abrir:')} ${humanError(e)}`, 'reject'); }
    },
    /* T3.5: sellar la visita — a partir de ahora solo cuenta lo nuevo */
    onMarkSeen: async () => {
      const { workflowId } = getState();
      const wfLastSeen = stampLastSeen(workflowId);
      setState({ wfLastSeen, wfActivity: null });
      try { setState({ wfActivity: await api().activity(workflowId, wfLastSeen) }); }
      catch { setState({ wfActivity: [] }); }
    },
    onOpenTicket: (t) => {
      setState({ selected: t.id });
      openPanel(t, getState().board ?? { projects: [] });
    },
    /* T3.1: mismo camino que el Tablero — el motor decide, el 409 se enseña */
    onDropTicket: (id, target) => {
      const t = findWorkflowTicket(id);
      if (!t) return;
      const action = TRANSITIONS[t.status]?.[target];
      if (!action) {
        toastReject(`de «${t.status}» no se puede pasar a «${target}».`);
        flashCard(id);
        return;
      }
      requestTransition(t, target, action);
    },
    onDispatchTicket: async (t, to, note) => {
      try {
        await api().dispatchTicket(t.id, { agent: getState().agent, to, note });
        toast(`«${t.plan_key ?? t.title}» despachada a «${to}». Avísale por relay.`, 'ok');
        await refetchWorkflows();
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) toastReject(humanError(err));
        else toast(humanError(err), 'reject');
      }
    },
    onToggleDrawer: () => setState({ drawerOpen: !getState().drawerOpen }),
    onToggleHandoff: () => setState({ handoffOpen: !getState().handoffOpen }),
    onOpenDispatch: async (t) => {
      const meta = getState().meta;
      const peers = [...new Set([...(meta.agents ?? []), 'code', 'verificador', 'adv-code', 'adv-seg'])];
      const out = await dispatchDialog(t, peers, t.suggested_agent ?? null);
      if (!out) return;
      try {
        await api().dispatchTicket(t.id, { agent: getState().agent, to: out.to, note: out.note });
        toast(`«${t.plan_key ?? t.title}» despachada a «${out.to}». Avísale por relay.`, 'ok');
        await refetchWorkflows();
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) toastReject(humanError(err));
        else toast(humanError(err), 'reject');
      }
    },
    onEditHandoff: async () => {
      const { workflowId, workflow, agent } = getState();
      let draft = workflow?.latest_handoff?.content ?? '';
      try {
        const res = await api().handoffDraft(workflowId);
        draft = res?.content ?? draft;
      } catch { /* sin draft del motor: se parte del último */ }
      const out = await handoffComposer(draft, workflow?.name ?? '');
      if (!out) return;
      try {
        await api().saveHandoff(workflowId, { agent, content: out.content });
        toast('Handoff guardado — la próxima sesión sabrá por dónde vamos.', 'ok');
        await refetchWorkflows();
      } catch (err) {
        if (err instanceof ApiError && (err.status === 409 || err.status === 400)) toastReject(humanError(err));
        else toast(humanError(err), 'reject');
      }
    },
    onDispatchFinding: async (f) => {
      try {
        await api().dispatchFinding(f.id, { agent: getState().agent });
        toast(`Hallazgo despachado como FIX — el ticket de corrección ya existe.`, 'ok');
        await refetchWorkflows();
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) toastReject(humanError(err));
        else toast(humanError(err), 'reject');
      }
    },
    onDismissFinding: async (f) => {
      const rs = await reasonDialog(`Descartar «${f.title}»`, 'Descartar');
      if (!rs) return;
      try {
        await api().dismissFinding(f.id, { agent: getState().agent, note: rs.note });
        toast('Hallazgo descartado con motivo.', 'ok');
        await refetchWorkflows();
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) toastReject(humanError(err));
        else toast(humanError(err), 'reject');
      }
    },
    onCopyDraft: async (m) => {
      try {
        const res = await api().memoryDraft(getState().workflowId, m.id);
        const text = typeof res === 'string' ? res : res.draft ?? JSON.stringify(res);
        await navigator.clipboard.writeText(text);
        toast('Borrador de memoria copiado — guárdalo con tu MCP de EcoDB.', 'ok');
      } catch (err) { toast(humanError(err), 'reject'); }
    },
    onMarkSaved: async (m) => {
      const rs = await reasonDialog('Marcar memoria guardada', 'Marcar');
      if (!rs) return; // el motivo aquí es el memory_id de EcoDB
      try {
        await api().markSaved(getState().workflowId, m.id, { agent: getState().agent, memory_id: rs.note });
        toast('Hito marcado: memoria EcoDB guardada.', 'ok');
        await refetchWorkflows();
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) toastReject(humanError(err));
        else toast(humanError(err), 'reject');
      }
    },
  });

  /* ---------------- Agentes (v3 T1.12/T1.13) ---------------- */
  const jobErr = (err, verb) => {
    if (err instanceof ApiError && (err.status === 409 || err.status === 400)) toastReject(humanError(err));
    else toast(`${tr(`No se pudo ${verb}:`)} ${humanError(err)}`, 'reject');
  };
  async function openJobModal(job = null) {
    let { harnesses, mcpInherited, meta, board } = getState();
    if (!harnesses) {
      [harnesses, mcpInherited] = await Promise.all([api().harnesses().catch(() => []), api().mcpInherited().catch(() => [])]);
      setState({ harnesses, mcpInherited });
    }
    if (!harnesses.length) { toast('El daemon todavía no expone ningún harness.', 'reject'); return null; }
    return jobModal({ job, harnesses, mcpInherited, mcpServers: getState().mcpServers ?? [], agents: meta?.agents ?? [], projects: board?.projects ?? [] });
  }
  /* T5.9: registro MCP (Agentes › MCP) */
  initMcpSettings({
    onSearch: (mcpQuery) => setState({ mcpQuery }),
    onImport: async () => {
      try {
        const r = await api().mcpImportLocal(getState().agent);
        toast(`Importados ${r.imported ?? 0} servidor${r.imported === 1 ? '' : 'es'} de la sesión local${r.updated ? ` · ${r.updated} actualizados` : ''}.`, 'ok');
        await refetchAgents();
      } catch (err) { toast(`${tr('No se pudo importar:')} ${humanError(err)}`, 'reject'); }
    },
    onToggle: async (s, enabled) => {
      try { await api().mcpPatch(s.id, { agent: getState().agent, enabled: enabled ? 1 : 0 }); await refetchAgents(); }
      catch (err) { toast(`${tr(`No se pudo cambiar «${s.name}»:`)} ${humanError(err)}`, 'reject'); await refetchAgents(); }
    },
    onDelete: async (s) => {
      const rs = await reasonDialog(`Borrar el MCP «${s.name}» del registro`, 'Borrar', { min: 1, hint: 'por qué se quita (queda en el toast, no hay historial de MCP)' });
      if (!rs) return;
      try { await api().mcpDelete(s.id, getState().agent); toast(`MCP «${s.name}» borrado.`, 'ok'); await refetchAgents(); }
      catch (err) { toast(`${tr('No se pudo borrar:')} ${humanError(err)}`, 'reject'); }
    },
    /* T2.5: la guía de alta del servidor, con la app del sistema (mismo camino que T3.6) */
    onOpenGuide: async (path) => {
      const open = window.agenticos?.openPath;
      if (typeof open !== 'function') { toast(`${tr('Abrir solo funciona en la app de escritorio. Ruta:')} ${path}`, 'info'); return; }
      try {
        const err = await open(path);
        if (err) toastReject(`${tr('No se pudo abrir la guía:')} ${err}`);
      } catch (e) { toast(`${tr('No se pudo abrir la guía:')} ${humanError(e)}`, 'reject'); }
    },
  });

  initAgents({
    onSelectTab: (agTab) => setState({ agTab }),
    /* T5.2 */
    onSetMode: (agMode) => { setState({ agMode }); if (agMode === 'calendar') ensureAgCalendar(); },
    onSetCalView: (v) => goAgCalendar(v, v === 'day' || v === 'week' ? getState().agCalDate : null, { agCalDaySel: null }),
    onSetDays: async (agScheduleDays) => { setState({ agScheduleDays }); await refetchAgents(); },
    onSetJobFilter: (agJobFilter) => setState({ agJobFilter }),
    onOpenJob: (id) => {
      const j = (getState().agJobs ?? []).find(x => x.id === id);
      if (!j) return;
      setState({ selectedJob: id, selected: null });
      openJobPanel(j);
    },
    onClose: () => { closePanel(); setState({ selectedJob: null }); },
    onNewJob: async () => {
      const payload = await openJobModal(null);
      if (!payload) return;
      try {
        const created = await api().createJob({ agent: getState().agent, ...payload });
        toast(`Job «${created.name ?? payload.name}» creado.`, 'ok');
        await refetchAgents();
      } catch (err) { jobErr(err, 'crear el job'); }
    },
    onEditJob: async (job) => {
      const payload = await openJobModal(job);
      if (!payload) return;
      try {
        await api().updateJob(job.id, { agent: getState().agent, ...payload });
        toast(`Job «${payload.name}» guardado.`, 'ok');
        await refetchAgents();
        reopenSelected();
      } catch (err) { jobErr(err, 'guardar el job'); }
    },
    onJobAction: async (job, action) => {
      const VERB = { launch: 'lanzar', pause: 'pausar', resume: 'reanudar', archive: 'archivar', duplicate: 'duplicar' };
      if (action === 'archive') {
        const rs = await reasonDialog(`Archivar «${job.name}»`, 'Archivar');
        if (!rs) return;
      }
      try {
        const res = await api().jobAction(job.id, action, { agent: getState().agent });
        if (action === 'launch') toast(`Run ${res?.run_id ?? ''} lanzado — «${job.name}».`, 'ok');
        else if (action === 'duplicate') toast(`Copia creada (pausada): «${res?.name ?? job.name + ' (copia)'}».`, 'ok');
        else toast(`Job «${job.name}»: ${VERB[action]} hecho.`, 'ok');
        if (action === 'archive') { closePanel(); setState({ selectedJob: null }); }
        await refetchAgents();
        reopenSelected();
      } catch (err) { jobErr(err, VERB[action] ?? action); }
    },
    onCancelRun: async (runId) => {
      try {
        await api().cancelRun(runId);
        toast('Run cancelado.', 'ok');
        await refetchAgents();
        reopenSelected();
      } catch (err) { jobErr(err, 'cancelar el run'); }
    },
    onVerifyRuns: async (btn) => {
      btn.disabled = true; const label = btn.textContent; btn.textContent = tr('Verificando…');
      try {
        const res = await api().verifyRuns();
        const n = res?.swept ?? res?.judged ?? 0;
        toast(n ? `Juez pasado: ${n} run${n === 1 ? '' : 's'} · ${res.pass ?? '?'} pasa · ${res.fail ?? '?'} falla.` : 'Nada que verificar: todos los runs ok ya tienen veredicto.', 'ok');
        await refetchAgents();
        reopenSelected();
      } catch (err) { jobErr(err, 'verificar'); }
      finally { btn.disabled = false; btn.textContent = label; }
    },
    onVerdict: async (run, verdict) => {
      let reason = null;
      if (verdict === 'fail') {
        const rs = await reasonDialog(`Veredicto: falla el run ${run.id}`, 'Falla');
        if (!rs) return;
        reason = rs.note;
      }
      try {
        await api().verdictRun(run.id, { agent: getState().agent, verdict, reason });
        toast(`Veredicto registrado: run ${run.id} ${verdict === 'pass' ? 'pasa' : 'falla'}.`, 'ok');
        await refetchAgents();
        reopenSelected();
      } catch (err) { jobErr(err, 'registrar el veredicto'); }
    },
  });

  document.getElementById('btn-create').addEventListener('click', async () => {
    const { meta, board } = getState();
    /* T2.1 (el dueño: «me dice que no tengo un agente que crea aunque le asigne uno»):
       faltaba `agent` — quien CREA, distinto de `owner`, que es quien responde. Y el
       modal hace ahora la llamada él mismo: solo se cierra con un 2xx, para que un
       error del motor se pinte bajo su campo en vez de tirar el formulario. */
    const created = await createTaskModal(meta, board?.projects ?? [], meta.agents,
      (ticket) => api().createTicket({ agent: getState().agent, ...ticket }));
    if (!created) return;
    toast(`Tarea «${created.title ?? ''}» creada.`, 'ok');
    await refetchBoard();
  });

  /* T2.5: «Ajustes» es la palabra que el dueño buscó y no existía. Vive en la zona de acciones
     —configurar no es trabajar, y la columna central de superficies no se toca (queja 1ª de
     la v3.1)— y lleva de un clic al registro MCP, que es hoy el único ajuste. */
  document.getElementById('btn-settings').addEventListener('click', async () => {
    closePanel();
    setState({ selected: null, surface: 'agents', agTab: 'mcp' });
    if (!getState().agJobs) await refetchAgents();
  });

  document.getElementById('btn-verify').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = tr('Verificando…');
    try {
      const res = await api().verifySweep();
      toast(sweepResultToastText(res), res?.bounced ? 'info' : 'ok');
      await refetchBoard();
    } catch (err) { toast(`${tr('Verificación fallida:')} ${humanError(err)}`, 'reject'); }
    finally { btn.disabled = false; btn.textContent = tr('Verificar cola'); }
  });

  initTheme(); // T3.2: antes de pintar nada, para no parpadear en claro

  /* #262: el idioma se resuelve y el catálogo se carga ANTES del primer pintado,
     por lo mismo que el tema: para que no se vea un instante en el otro idioma. */
  await initI18n();
  onRepaint(() => render(getState()));
  const langLabel = document.getElementById('lang-label');
  const pintaLang = () => { langLabel.textContent = getLang().toUpperCase(); };
  pintaLang();
  document.getElementById('btn-lang').addEventListener('click', async () => {
    await setLang(getLang() === 'es' ? 'en' : 'es');
    pintaLang();
  });

  /* conexión */
  const impl = await connect();
  const chip = document.getElementById('conn-chip');
  const label = document.getElementById('conn-label');
  if (impl.mode === 'live') {
    chip.classList.add('is-live');
    label.textContent = 'motor vivo';
    /* T0.7 (UI): versión del daemon y del esquema — y de la app si el preload la da */
    try {
      const h = await api().health();
      const v = h?.version ?? {};
      const bits = ['motor vivo'];
      if (v.daemon) bits.push(`daemon ${v.daemon}`);
      if (v.schema != null) bits.push(`esquema ${v.schema}`);
      const appV = window.agenticos?.appVersion;
      if (appV) bits.push(`app ${appV}`);
      label.textContent = bits.join(' · ');
      chip.title = 'versiones: app · daemon · esquema';
    } catch { /* health sin versión: se queda «motor vivo» */ }
  } else {
    label.textContent = 'fixtures — sin daemon';
  }

  const meta = await api().meta();
  /* #302: quien firma sale del roster que declara el daemon, no de un
     literal. meta.owner primero; si un daemon viejo no lo trae, el primer
     agente de la lista, que sigue siendo mejor que un nombre inventado. */
  setState({ mode: impl.mode, meta, agent: meta.owner ?? meta.agents?.[0] ?? null });

  subscribe(render);
  await refetchBoard();
  /* T3.1: los badges de Workflows/Agentes (bugs, bisagras, runs vivos) nacen con la barra,
     no en la primera visita — así las pestañas de sección no cambian de ancho al cambiar */
  refetchWorkflows(); refetchAgents();
  /* arranca en Calendario si así se dejó (con su pestaña): DESPUÉS de conectar la API, o el primer fetch cae al vacío */
  if (getState().boardMode === 'calendar') goCalendar(getState().calView);

  /* SSE / eventos del mock: refetch agrupado + panel al día */
  onBoardChanged(() => {
    scheduleRefetch();
    setTimeout(reopenSelected, 200);
  });
}

main();
