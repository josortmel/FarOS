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
import { initRoster, clearRenameDraft } from './ui/roster_settings.js';
import { initCredentials, USO as USO_CLAVE, clearCredentialDrafts } from './ui/credentials_settings.js';
import { initProofSettings, clearProofDraft } from './ui/proof_settings.js';   // #601
import { initVerifierSettings, clearRootsDraft } from './ui/verifier_settings.js';   // #695
import { initHoyWindow, renderHoyWindow } from './ui/hoy_window.js';   // #1319
import { initTimbreSettings } from './ui/timbre_settings.js';   // #1217
import { initI18n, applyI18n, setLang, getLang, onRepaint, locale, t as tr } from './i18n.js';
import { jobModal } from './ui/job_modal.js';
import { STATUS_LABEL, TRANSITIONS } from './transitions.js';
import { initBoard, renderBoard, renderLaterals, flashCard, captureRects, flipFrom } from './ui/board.js';
import { initPanel, openPanel, closePanel } from './ui/panel.js';
import { createTaskModal, evidenceDialog, clearEvidenceDraft, reasonDialog, projectDialog, sweepResultToastText, dispatchDialog, batchDispatchDialog, importPlanDialog, handoffComposer, suspendAgentDialog, deleteAgentDialog, deleteCredentialDialog } from './ui/modal.js';
import { initWorkflow, renderWorkflowSidebar, renderWorkflowBar, renderMilestoneBanner, renderPhaseStrip, renderWorkflowBoard, renderWorkflowPane, renderFindingsDrawer, renderSurfaceBadge, renderHandoff } from './ui/workflow.js';
import { toast, toastReject } from './ui/toast.js';
import { recordar as recordarAvisosCriterio } from './ui/criterio_avisos.js';   // #1136
import { renderDecisionsPane, resaltarPregunta } from './ui/decisions.js';   // #645
import { initLupa } from './ui/lupa.js';   // #1320
import { abrirBiblioteca, refrescarIdioma, salirDeBiblioteca, seguirRutas } from './ui/library.js';   // #851
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
    const [agJobs, agSchedule, agResults, agRunning, mcpServers, roster, credentials, credDeliveries, motorSettings, timbre] = await Promise.all([
      api().jobs(),
      api().agentsSchedule(agScheduleDays),
      api().agentsResults(),
      api().agentsRunning(),
      api().mcpServers().catch(() => []), // T5.9: daemon sin registro → lista vacía, sin ruido
      api().roster().catch(() => null),   // #252: daemon sin roster editable → la sección lo dice
      api().credentials().catch(() => null),   // #460: daemon sin Claves → la sección lo dice
      api().credentialDeliveries().catch(() => null),   // #499: daemon sin entregas → no se pinta
      api().settings().catch(() => null),   // #601: proof_repos; un daemon sin él no pinta la sección
      api().timbre(getState().timbreFecha).catch(() => null),   // #1217: daemon sin timbre → no se pinta
    ]);
    setState({ agJobs, agSchedule, agResults, agRunning, mcpServers, roster, credentials, credDeliveries, timbre,
               proofRepos: motorSettings && 'proof_repos' in motorSettings ? motorSettings.proof_repos : null,
               // #695: los dos ajustes del verificador; un daemon sin ellos no pinta su sección
               maquinaQuieta: motorSettings && 'maquina_quieta' in motorSettings ? motorSettings.maquina_quieta : null,
               readRoots: motorSettings && 'verifier_read_roots' in motorSettings ? motorSettings.verifier_read_roots : null });
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) { setState({ agJobs: [], agSchedule: { items: [] }, agResults: [], agRunning: [] }); return; }
    toast(`${tr('No se pudo cargar Agentes:')} ${humanError(err)}`, 'reject');
  }
}

/* #645: todas las preguntas de la casa (con y sin workflow) — las mismas que ve el móvil */
let decisionHandlers = {};
async function refetchAllDecisions() {
  try { setState({ allDecisions: await api().decisions(null) }); }
  catch { setState({ allDecisions: getState().allDecisions ?? [] }); }
}

function scheduleRefetch() {
  clearTimeout(refetchTimer);
  refetchTimer = setTimeout(() => {
    refetchBoard();
    if (getState().boardMode === 'calendar') refetchCalendar();
    if (getState().surface === 'workflow' || getState().workflows) refetchWorkflows();
    if (getState().surface === 'agents' || getState().agJobs) refetchAgents();
    refetchAllDecisions();   // #645: el contador de Preguntas vale en todas las superficies
    if (getState().surface === 'agents' && getState().agMode === 'calendar') refetchAgCalendar(); // T5.2
  }, 120); // agrupa ráfagas de SSE
}

/* ---------------- transiciones ---------------- */

/* #414 (decisión 48): el motor manda `warning` en español; aquí va la frase del catálogo */
const AVISO_PIDE_OTRO = 'Aviso: el criterio pide que verifique otra persona, pero el nivel es «propia» y se sella sola. Súbelo a «otro miembro» en la ficha.';

async function requestTransition(t, target, action) {
  const { agent } = getState();
  const body = { action, agent, to: target };

  // completar exige evidencia — manual: se pide a la mano
  if (target === 'done') {
    /* #601: con «proof», la plantilla lleva el primer repo admitido (Ajustes › proof_repos) */
    const proofRepos = (getState().meta?.evidence_types ?? []).includes('proof')
      ? await api().settings().then(s => String(s.proof_repos ?? '').split(';').map(x => x.trim()).filter(Boolean)).catch(() => [])
      : [];
    const ev = await evidenceDialog(getState().meta, t, { proofRepos });
    if (!ev) return; // canceló: la tarjeta no se movió
    Object.assign(body, ev);
  }

  // #482: en una bloqueada, «bloquear» es cambiar el motivo: el diálogo parte del que hay
  const recambio = target === 'blocked' && t.status === 'blocked';
  if (recambio) {
    const rs = await reasonDialog(`${tr('Cambiar el motivo del bloqueo:')} «${t.title}»`, 'Guardar el motivo', { value: t.blocked_reason ?? '', tone: 'neutral' });
    if (!rs) return;
    body.note = rs.note;
  }

  // #482: pausar (en curso → aceptadas) exige nota de ≥ 10 caracteres; queda en el historial como [pausa]
  if (action === 'pause') {
    const rs = await reasonDialog(`${tr('Pausar:')} «${t.title}»`, 'Pausar', {
      min: 10, hint: 'por qué no está en curso de verdad — se guarda en el historial', tone: 'neutral',
    });
    if (!rs) return;
    body.note = rs.note;
  }

  // bloquear y rechazar exigen motivo
  if (!recambio && (target === 'blocked' || target === 'rejected')) {
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
    if (target === 'done') clearEvidenceDraft(t.id);  // #601: salió bien; el borrador ya no hace falta
    /* el motor devuelve el ticket: una recurrente estampada sigue «accepted» con last_done_at de hoy */
    if (res?.recurring_stamped || (t.cadence_days && target === 'done' && res?.status === 'accepted')) {
      toast(`Recurrente estampada: «${t.title}» vuelve a su ciclo.`, 'ok');
    } else if (recambio) {
      toast(`${tr('Motivo cambiado; sigue bloqueada:')} «${t.title}»`, 'ok');
    } else if (action === 'pause') {
      toast(`${tr('Pausada; vuelve a aceptadas:')} «${t.title}»`, 'ok');
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
    /* #414: una «propia» cuyo criterio pide a otra persona se sella igual, pero el motor avisa */
    if (res?.warning) toast(tr(AVISO_PIDE_OTRO), 'info', 12000);
    /* #518: cerrar la tarea apaga el job programado que cuelga de ella. El motor lo dice en
       job_warning, en español; aquí va la etiqueta del catálogo y el job como dato. */
    if (res?.job_warning) {
      const m = /#(\d+) «(.+?)»/.exec(res.job_warning);
      toast(`${tr('Este job ya no se dispara porque cuelga de la tarea que has cerrado. Desenlázalo o páusalo en Agentes:')} ${m ? `#${m[1]} «${m[2]}»` : ''}`, 'info', 12000);
    }
    closePanel();
    setState({ selected: null });
    await refetchBoard();
    /* que se vea CUÁL volvió: sin esto vuelve a ser un cambio en silencio */
    if (action === 'rework') { flashCard(t.id); flashDayItem(t.id); }
    if (getState().boardMode === 'calendar') await refetchCalendar(); // T2.9: estampar desde el calendario
    if (getState().surface === 'workflow') await refetchWorkflows(); // el kanban de la Oficina también se mueve
  } catch (err) {
    /* el motor dice que no: 409 (no se puede ahora) o 400 (#718: el dato está mal y nombra el campo).
       Ninguno de los dos es la red, y decir «Error de red» manda a mirar el cable */
    if (err instanceof ApiError && (err.status === 409 || err.status === 400)) {
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
  renderHoyWindow(state);   // #1319: el botón de «Qué sale en Hoy» solo en Hoy + Kanban
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
    /* #497: esto se escribe DESPUES del recorrido de traduccion (cada clic, cada cambio del
       sistema), asi que pasa por tr() aqui mismo. Y como lo traducido aqui no queda en el
       WeakMap de i18n, la vuelta al espaniol no lo restaura: por eso el boton de idioma
       vuelve a llamar a applyTheme. */
    document.getElementById('theme-label').textContent = tr(THEME_LABEL[pref]);
    btn.title = tr(pref === 'auto' ? `Tema: sigue al sistema (ahora ${resolved === 'dark' ? 'oscuro' : 'claro'}). Clic para cambiar` : `Tema: ${THEME_LABEL[pref]}. Clic para cambiar`);
    btn.setAttribute('aria-label', tr(`Tema: ${THEME_LABEL[pref]}`));
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

/* ---------------- Preguntas (#645) ---------------- */

function renderDecBadge(state) {
  const n = (state.allDecisions ?? []).filter(d => d.status !== 'decided').length;
  const b = document.getElementById('dec-badge');
  b.textContent = n || '';
  b.hidden = !n;
  b.setAttribute('aria-label', tr(`${n} sin contestar`));
}

function renderDecRoot(state) {
  const root = document.getElementById('dec-root');
  root.innerHTML = '';
  /* las pendientes todas; de las decididas, las 10 últimas: aquí importa lo que falta */
  const all = state.allDecisions;
  const list = all == null ? null : [
    ...all.filter(d => d.status !== 'decided'),
    ...all.filter(d => d.status === 'decided').sort((a, b) => (b.decided_at ?? '').localeCompare(a.decided_at ?? '')).slice(0, 10),
  ];
  /* #1320: la que se abrió desde la lupa sale aunque sea antigua, mientras se siga en Preguntas, y se resalta una vez */
  const foco = all?.find(d => d.id === decFoco);
  if (foco && !list.includes(foco)) list.push(foco);
  renderDecisionsPane(root, { wfDecisions: list, workflow: { name: tr('la casa') }, paneTitle: tr('Preguntas') }, decisionHandlers);
  if (foco && decResaltar) { decResaltar = false; requestAnimationFrame(() => resaltarPregunta(root, foco.id)); }
}
let decFoco = null, decResaltar = false;   // #1320

/* ---------------- render raíz ---------------- */

function applySurface(state) {
  const isWf = state.surface === 'workflow';
  const isAg = state.surface === 'agents';
  const isDec = state.surface === 'decisions';                   // #645
  const isLib = state.surface === 'library';                     // #851
  const isCal = !isWf && !isAg && !isDec && !isLib && state.boardMode === 'calendar'; // T2.4: mismo Tablero, otro modo de verlo
  const offBoard = isWf || isAg || isDec || isLib;
  document.getElementById('board-bar').hidden = offBoard;                       // T3.1: la línea de controles es del Tablero
  document.getElementById('view-tabs').style.display = '';                       // FIX #150: Hoy/Semana/Futuras/Todas mandan en los dos modos
  document.getElementById('mode-tabs').hidden = false;
  document.getElementById('project-bar').hidden = offBoard;
  document.getElementById('board').hidden = offBoard || isCal;
  if (offBoard || isCal) document.getElementById('laterals').hidden = true; // en kanban lo decide renderLaterals
  document.getElementById('cal-root').hidden = !isCal;
  document.getElementById('wf-root').hidden = !isWf;
  document.getElementById('ag-root').hidden = !isAg;
  document.getElementById('dec-root').hidden = !isDec;
  document.getElementById('lib-root').hidden = !isLib;
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
  renderDecBadge(state);
  if (state.surface !== 'decisions') decFoco = null;   // #1320: la pregunta de la lupa solo se queda mientras se mira
  if (state.surface === 'library') { refrescarIdioma(); return; }   // #851: se pinta sola; un aviso del daemon no recarga lo que Pepe lee
  if (state.surface === 'decisions') {
    renderDecRoot(state);
    return;
  }
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
    /* #481: quién actúa y quién es el dueño de la casa (el dueño puede soltar la de cualquiera) */
    me: () => ({ agent: getState().agent, owner: getState().meta?.owner }),
    onClaim: async (t, action) => {
      try {
        await api().transition(t.id, { agent: getState().agent, action });
        toast(action === 'claim'
          ? `${tr('Te quedas la verificación de')} «${t.title}»`
          : `${tr('Verificación soltada:')} «${t.title}»`, 'ok');
        await refetchBoard();
        if (getState().surface === 'workflow') await refetchWorkflows();
        reopenSelected();
      } catch (err) {
        /* el 409 del motor («ya la tiene X») es el caso normal de dos a la vez: se dice tal cual */
        if (err instanceof ApiError && (err.status === 400 || err.status === 409)) toastReject(humanError(err));
        else toast(`${tr('No se pudo guardar:')} ${humanError(err)}`, 'reject');
        await refetchBoard();
        reopenSelected();
      }
    },
    /* T2.10: el tiempo se edita desde el panel; el motor valida (400 con motivo) */
    /* T2.3: el panel edita título, descripción, prioridad, responsable, criterio y vencimiento (PATCH) */
    onPatchFields: async (t, patch) => {
      try {
        const res = await api().patchTicket(t.id, { agent: getState().agent, ...patch });
        recordarAvisosCriterio(t.id, res);   // #1136: el panel repintado enseña la nota junto al criterio
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
    /* #1348: marcar o desmarcar una tarea como tiempo libre; devuelve si el motor dijo que sí */
    onTiempoLibre: async (t, libre) => {
      try {
        await api().tiempoLibre({ agent: getState().agent, ticket_id: t.id, libre });
        toast(libre ? tr(`«${t.title}» es tiempo libre: su timbre suena como invitación.`) : tr(`«${t.title}» vuelve a ser trabajo.`), 'ok');
        return true;
      } catch (err) {
        if (err instanceof ApiError && err.code === 'timbre.solo_las_suyas') {
          toastReject(tr(`Esta tarea es de «${t.owner}»: solo «${t.owner}» o el dueño de la casa pueden marcarla como tiempo libre.`));
        } else if (err instanceof ApiError && (err.status === 400 || err.status === 409)) toastReject(humanError(err));
        else toast(`${tr('No se pudo guardar:')} ${humanError(err)}`, 'reject');
        return false;
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
        /* #924: soltada en el carril de otro → pasa a ese agente (PATCH owner) y después, la hora */
        const handedTo = patch.owner && patch.owner !== d.owner ? patch.owner : null;
        if (handedTo) await api().patchTicket(d.ticket_id, { agent, owner: handedTo });
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
        if (handedTo) toast(`${tr('Pasada a')} ${handedTo}${patch.start ? ` ${tr('a las')} ${patch.start}` : ''}.${d.cadence ? ' ' + tr('Toda la serie cambia de dueño.') : ''}`, 'ok');
        else toast((patch.preferredOnly || (d.cadence && 'start' in patch && !patch.start && !movedDay)) ? (patch.start ? `Hora preferida: ${patch.start}.` : 'Hora preferida borrada: vuelve a «sin hora», el ciclo no cambia.') : patch.start ? (movedDay ? `${tr('Programada el')} ${fmtDayShort(patch.date)} ${tr('a las')} ${patch.start}.` : `Programada a las ${patch.start}.`) : patch.duration_min != null ? `Duración: ${patch.duration_min} min.` : movedDay ? (d.cadence ? `${tr('Próxima ocurrencia movida al')} ${fmtDayShort(patch.date)}; ${tr('la cadencia no cambia.')}` : `${tr('Movida al')} ${fmtDayShort(patch.date)}, ${tr('todo el día.')}`) : 'Sin hora: queda para todo el día.', 'ok');
        await refetchCalendar();
        refetchBoard();
      } catch (err) {
        if (err instanceof ApiError && (err.status === 400 || err.status === 409)) toastReject(humanError(err));
        else toast(`${tr('No se pudo programar:')} ${humanError(err)}`, 'reject');
        /* #924: si el cambio de dueño entró y lo que falló fue la hora, la pantalla ya no dice la
           verdad: se vuelve a leer, y la tarjeta queda donde el motor la tiene */
        await refetchCalendar();
        flashDayItem(d.ticket_id);
      }
    },
  });

  /* #645: decidir y aplazar desde Preguntas, con el mismo motor que la bisagra de un workflow */
  decisionHandlers = {
    buscarVigentes: (tema, superadas) => api().decisionsVigentes(tema, superadas),   // #919
    onDecide: async (d, { option, decision, rationale, ...extra }) => {   // extra: #919 (sustituye a, vale hasta)
      try {
        await api().decide(d.id, { agent: getState().agent, option, decision, rationale, surface: 'desktop', ...extra });   // #698
        toast(tr(`Decidida: «${option ?? decision}». Registrada con su porqué.`), 'ok');
      } catch (err) {
        if (err instanceof ApiError && (err.status === 409 || err.status === 400)) toastReject(humanError(err));
        else toast(`${tr('No se pudo decidir:')} ${humanError(err)}`, 'reject');
      }
      await refetchAllDecisions();
      if (getState().workflows) await refetchWorkflows();
    },
    onDefer: async (d, until) => {
      const rs = await reasonDialog(`Aplazar «${d.title}»${until ? ' hasta ' + until : ''}`, 'Aplazar');
      if (!rs) return;
      try {
        await api().deferDecision(d.id, { agent: getState().agent, until, note: rs.note });
        toast(until ? `${tr('Aplazada hasta')} ${until}.` : tr('Aplazada.'), 'ok');
      } catch (err) {
        if (err instanceof ApiError && (err.status === 409 || err.status === 400)) toastReject(humanError(err));
        else toast(`${tr('No se pudo aplazar:')} ${humanError(err)}`, 'reject');
      }
      await refetchAllDecisions();
    },
  };

  /* superficie: Tablero ↔ Workflows ↔ Agentes ↔ Preguntas */
  document.querySelectorAll('#surface-tabs .views__tab').forEach(b => {
    b.addEventListener('click', async () => {
      const surface = b.dataset.surface;
      setState({ surface });
      if (surface === 'library') { abrirBiblioteca(); return; }
      salirDeBiblioteca();
      if (surface === 'decisions') { await refetchAllDecisions(); return; }
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
  /* los peers a los que se puede despachar: la familia + los de siempre (despacho suelto y lote) */
  const dispatchPeers = () => [...new Set([...(getState().meta.agents ?? []), 'code', 'verificador', 'adv-code', 'adv-seg'])];
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
    onDecide: async (d, { option, decision, rationale, ...extra }) => {   // extra: #919
      try {
        await api().decide(d.id, { agent: getState().agent, option, decision, rationale, surface: 'desktop', ...extra });   // #698
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
      const peers = dispatchPeers();
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
    /* #1317: despachar de golpe las listas del workflow. El diálogo llama a `submit`; si el motor da 400
       (lote atómico: ninguna se despacha) el diálogo sigue abierto con el motivo. */
    onOpenDispatchBatch: async (tickets) => {
      const { workflowId, agent } = getState();
      const out = await batchDispatchDialog(
        tickets, dispatchPeers(),
        (items) => api().dispatchBatch(workflowId, { agent, items }),
        humanError,
      );
      if (!out || out === true) return;
      const porPeer = new Map();
      for (const d of out.dispatched ?? []) porPeer.set(d.to, (porPeer.get(d.to) ?? 0) + 1);
      const reparto = [...porPeer].map(([to, c]) => (porPeer.size > 1 ? `${to} (${c})` : to)).join(', ');
      const n = out.count ?? out.dispatched?.length ?? 0;
      toast(n === 1 ? `1 despachada a «${reparto}». Avisa por relay.` : `${n} despachadas a «${reparto}». Avisa por relay.`, 'ok');   // toast() ya pasa por el catálogo
      await refetchWorkflows();
    },
    /* #1318: importar un Plan. El diálogo llama a `submit`; si el motor da 400 (valida todo antes de escribir:
       no se importa nada) el diálogo sigue abierto con el motivo. Si va bien: se cierra y se abre el nuevo. */
    onOpenImportPlan: async () => {
      const { agent } = getState();
      const out = await importPlanDialog(
        ({ plan, repo_root }) => api().importPlan({ agent, plan, repo_root }),
        humanError,
      );
      if (!out || out === true) return;
      const n = Object.values(out.tickets_by_phase ?? {}).reduce((a, arr) => a + arr.length, 0);
      await refetchWorkflows();
      setState({ wfListMode: 'active' });
      await selectWorkflow(out.id);
      toast(n === 1 ? `«${out.name}» importado: 1 tarea, aceptada y lista para despachar.` : `«${out.name}» importado: ${n} tareas, aceptadas y listas para despachar.`, 'ok');
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

  /* #252: Ajustes › Agentes. Tras cada escritura se piden de nuevo el roster Y meta:
     los selectores de dueño leen meta.agents, y un agente recién creado tiene que
     poder recibir una tarea sin recargar (criterio del ticket). El error de nombre se
     pinta bajo su campo, no en un toast que te saca de lo que estabas escribiendo. */
  async function afterRosterWrite() {
    const [roster, meta] = await Promise.all([api().roster().catch(() => null), api().meta()]);
    setState({ roster, meta, rosterEditing: null, rosterError: null });
    await refetchBoard();
  }
  const rosterFail = (where, err) => {
    if (err instanceof ApiError && (err.field === 'name' || err.status === 409 || err.status === 422)) {
      setState({ rosterError: { where, msg: humanError(err) } });
    } else toast(`${tr('No se pudo cambiar la lista de agentes:')} ${humanError(err)}`, 'reject');
  };
  initRoster({
    onAdd: async (name) => {
      if (!name) { setState({ rosterError: { where: '__add', msg: tr('Escribe un nombre.') } }); return false; }
      try {
        await api().rosterAdd(name);
        toast(`Agente «${name}» añadido.`, 'ok');
        await afterRosterWrite();
        return true;
      } catch (err) { rosterFail('__add', err); return false; }
    },
    onRenameStart: (name) => setState({ rosterEditing: name, rosterError: null }),
    onRenameCancel: () => setState({ rosterEditing: null, rosterError: null }),
    onRename: async (a, to) => {
      if (!to || to === a.name) { clearRenameDraft(); setState({ rosterEditing: null, rosterError: null }); return; }
      try {
        await api().rosterRename(a.name, to);
        clearRenameDraft();
        toast(`«${a.name}» ahora se llama «${to}».`, 'ok');
        await afterRosterWrite();
      } catch (err) { rosterFail(a.name, err); }
    },
    /* Pepe, 30-sep: «prefiero suspenderlo». Los destinatarios son los ACTIVOS: a un
       suspendido no se le pueden pasar tareas. */
    onSuspend: async (a) => {
      const { roster } = getState();
      const others = (roster?.agents ?? []).filter(x => !x.suspended && x.name !== a.name).map(x => x.name);
      const r = await suspendAgentDialog(a, others);
      if (!r) return;
      try {
        const res = await api().rosterSuspend(a.name, true, r.reassignTo);
        toast(res?.reassigned
          ? `Agente «${a.name}» suspendido. Sus tareas abiertas pasan a «${r.reassignTo}».`
          : `Agente «${a.name}» suspendido.`, 'ok');
        await afterRosterWrite();
      } catch (err) { rosterFail(a.name, err); }
    },
    onReactivate: async (a) => {
      try {
        await api().rosterSuspend(a.name, false);
        toast(`Agente «${a.name}» reactivado.`, 'ok');
        await afterRosterWrite();
      } catch (err) { rosterFail(a.name, err); }
    },
    onDelete: async (a) => {
      if (!await deleteAgentDialog(a)) return;
      try {
        await api().rosterRemove(a.name);
        toast(`Agente «${a.name}» borrado.`, 'ok');
        await afterRosterWrite();
      } catch (err) { rosterFail(a.name, err); }
    },
  });

  /* #460: Ajustes › Claves. Tras cada escritura se relee la lista entera (el motor mueve
     la marca «en uso» de las demás). El error va bajo su formulario: guardar una clave que
     el servicio rechaza no te saca de lo que estabas haciendo. Probar tarda (hasta 10 s):
     mientras, el botón dice «Probando…». */
  async function afterCredWrite(extra = {}) {
    const [credentials, credDeliveries] = await Promise.all([
      api().credentials().catch(() => null), api().credentialDeliveries().catch(() => null)]);
    setState({ credentials, credDeliveries, credError: null, credBusy: null, ...extra });
  }
  const credFail = (where, err) => setState({ credBusy: null, credError: { where, msg: humanError(err) } });
  /* #601: los repos donde el verificador puede correr una «proof» */
  initProofSettings({
    onSave: async (lista) => {
      try {
        await api().patchSettings({ agent: getState().agent, proof_repos: lista });
        clearProofDraft();
        toast(tr('Repos admitidos guardados.'), 'ok');
        await refetchAgents();
      } catch (err) {
        if (err instanceof ApiError && (err.status === 400 || err.status === 409)) toastReject(humanError(err));
        else toast(`${tr('No se pudo guardar:')} ${humanError(err)}`, 'reject');
      }
    },
  });
  /* #695: «máquina quieta» se aplica al tocarla; las carpetas de lectura, con Guardar. Si el motor
     rechaza el cambio, se vuelve a leer lo guardado y el interruptor regresa a su sitio. */
  const settingsFail = async (err) => {
    if (err instanceof ApiError && (err.status === 400 || err.status === 409)) toastReject(humanError(err));
    else toast(`${tr('No se pudo guardar:')} ${humanError(err)}`, 'reject');
    await refetchAgents();
  };
  initVerifierSettings({
    onQuiet: async (on) => {
      try {
        await api().patchSettings({ agent: getState().agent, maquina_quieta: on });
        toast(on ? tr('Máquina quieta activada: el verificador no corre.') : tr('Máquina quieta desactivada: el verificador vuelve a correr.'), 'ok');
        await refetchAgents();
      } catch (err) { await settingsFail(err); }
    },
    onSaveRoots: async (lista) => {
      try {
        await api().patchSettings({ agent: getState().agent, verifier_read_roots: lista });
        clearRootsDraft();
        toast(tr('Carpetas guardadas.'), 'ok');
        await refetchAgents();
      } catch (err) { await settingsFail(err); }
    },
  });
  /* #1319: «Qué sale en Hoy» se aplica al tocar cada casilla: PATCH con las tres y la misma recarga que el cambio de vista */
  initHoyWindow({
    api,
    apply: async (today_window) => {
      await api().patchSettings({ agent: getState().agent, today_window });
      await refetchBoard();
    },
    fail: (err) => {
      if (err instanceof ApiError && (err.status === 400 || err.status === 409)) toastReject(humanError(err));
      else toast(`${tr('No se pudo guardar:')} ${humanError(err)}`, 'reject');
    },
  });
  /* #1320: la lupa. Cada resultado se abre donde vive: la tarea en su ficha (sobre la superficie que haya),
     la pregunta en Preguntas, el workflow en Workflows y el job en Agentes. En el móvil, Preguntas y la
     barra de abajo son de mobile.js: se le avisa con un evento. */
  const enMovil = () => { const n = document.querySelector('.mob-tabs'); return !!n && getComputedStyle(n).display !== 'none'; };
  const avisarMovil = (k) => { if (enMovil()) window.dispatchEvent(new CustomEvent('faros:superficie', { detail: { k } })); };
  initLupa({
    api,
    onOpen: async (kind, it) => {
      if (kind === 'ticket') { await openTicketById(it.id); return; }
      closePanel(); setState({ selected: null, selectedJob: null });
      if (kind === 'decision') {
        if (enMovil()) { window.dispatchEvent(new CustomEvent('faros:pregunta', { detail: { id: it.id } })); return; }
        salirDeBiblioteca();
        await refetchAllDecisions();          // primero los datos: un solo pintado con la tarjeta ya dentro
        decFoco = it.id; decResaltar = true;
        setState({ surface: 'decisions' });
        return;
      }
      if (kind === 'workflow') {
        salirDeBiblioteca();
        const archivado = it.status === 'archived';
        setState({ surface: 'workflow', wfListMode: archivado ? 'archived' : 'active' });
        avisarMovil('wf');
        if (!getState().workflows) await refetchWorkflows();
        if (archivado && getState().wfArchived == null) await refetchArchived();
        await selectWorkflow(it.id);
        return;
      }
      if (kind === 'job') { salirDeBiblioteca(); avisarMovil('ag'); await jumpToJob(it.id); }
    },
  });
  /* #1217: el timbre se aplica al tocarlo, como «máquina quieta»; el día elegido se queda en el estado
     para que los repintados de Agentes no lo devuelvan a hoy. */
  initTimbreSettings({
    onTimbre: async (on) => {
      try {
        await api().patchSettings({ agent: getState().agent, timbre_activo: on });
        toast(on ? tr('Timbre encendido: suena en la próxima franja.') : tr('Timbre apagado: no suena hasta que lo enciendas.'), 'ok');
        await refetchAgents();
      } catch (err) { await settingsFail(err); }
    },
    onQuitarSilencio: async (agente) => {
      try {
        await api().timbreSilencio({ agent: getState().agent, agente, hasta: '' });
        toast(tr('Silencio quitado: vuelve a sonarle.'), 'ok');
        await refetchAgents();
      } catch (err) { await settingsFail(err); }
    },
    onFecha: async (fecha) => {
      setState({ timbreFecha: fecha });
      try { setState({ timbre: await api().timbre(fecha) }); }
      catch (err) { toast(`${tr('No se pudo cargar el timbre:')} ${humanError(err)}`, 'reject'); }
    },
  });
  initCredentials({
    onAdd: async (b) => {
      if (!b.name) { setState({ credError: { where: '__add', msg: tr('Ponle un nombre a la clave.') } }); return false; }
      if (!b.value.trim()) { setState({ credError: { where: '__add', msg: tr('Pega la clave.') } }); return false; }
      setState({ credBusy: '__add', credError: null });
      try {
        const c = await api().credentialCreate(getState().agent, b);
        clearCredentialDrafts();   // ANTES de repintar: si no, el campo vuelve a salir con la clave dentro
        toast(c.in_use ? `Clave «${c.name}» guardada. FarOS ya la usa.` : `Clave «${c.name}» guardada.`, 'ok');
        await afterCredWrite();
        return true;
      } catch (err) { credFail('__add', err); return false; }
    },
    onUse: async (c, on) => {
      try {
        await api().credentialPatch(c.id, getState().agent, { in_use: on });
        toast(on ? `FarOS usa ahora «${c.name}».` : `FarOS ya no usa «${c.name}».`, 'ok');
        await afterCredWrite();
      } catch (err) { credFail(c.id, err); }
    },
    /* #499: entregable bajo demanda; solo lo acepta el dueño de la casa (403 si no) */
    onDeliverable: async (c, on) => {
      try {
        await api().credentialPatch(c.id, getState().agent, { deliverable: on });
        toast(on ? `«${c.name}» ${tr('se entrega bajo demanda.')}` : `«${c.name}» ${tr('ya no se entrega.')}`, 'ok');
        await afterCredWrite();
      } catch (err) { credFail(c.id, err); }
    },
    onTest: async (c) => {
      setState({ credBusy: c.id, credError: null });
      try {
        const r = await api().credentialTest(c.id, getState().agent);
        toast(r.test_ok ? `«${c.name}» vale.` : `«${c.name}» no ha pasado la prueba.`, r.test_ok ? 'ok' : 'reject');
        await afterCredWrite();
      } catch (err) { credFail(c.id, err); }
    },
    onStartValue: (id) => setState({ credEditing: id, credError: null }),
    onCancelValue: () => setState({ credEditing: null, credError: null }),
    onValue: async (c, v) => {
      if (!v.trim()) { setState({ credError: { where: c.id, msg: tr('Pega la clave.') } }); return false; }
      setState({ credBusy: c.id, credError: null });
      try {
        await api().credentialPatch(c.id, getState().agent, { value: v });
        toast(`Clave «${c.name}» cambiada.`, 'ok');
        await afterCredWrite({ credEditing: null });
        return true;
      } catch (err) { credFail(c.id, err); return false; }
    },
    onDelete: async (c) => {
      if (!await deleteCredentialDialog(c, USO_CLAVE[c.kind] ?? '')) return;
      try {
        await api().credentialDelete(c.id, getState().agent);
        toast(`Clave «${c.name}» borrada.`, 'ok');
        await afterCredWrite();
      } catch (err) { credFail(c.id, err); }
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
      (ticket) => api().createTicket({ agent: getState().agent, ...ticket }),
      (id, patch) => api().patchTicket(id, { agent: getState().agent, ...patch }));   // #1136: corregir el criterio allí mismo
    if (!created) return;
    /* el motor devuelve {ticket, possible_duplicates, warning?}: el título está en ticket (salía «») */
    const tk = created.ticket ?? created;
    toast(`Tarea «${tk.title ?? ''}» creada.`, 'ok');
    if (created.warning) toast(tr(AVISO_PIDE_OTRO), 'info', 12000);  // #414
    await refetchBoard();
  });

  /* T2.5: «Ajustes» es la palabra que el dueño buscó y no existía. Vive en la zona de acciones
     —configurar no es trabajar, y la columna central de superficies no se toca (queja 1ª de
     la v3.1)— y lleva de un clic a los ajustes: agentes (#252) y registro MCP. */
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
    applyTheme(getState().theme);   // #497
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
  refetchAllDecisions();   // #645: el contador de Preguntas, desde el primer pintado
  /* T3.1: los badges de Workflows/Agentes (bugs, bisagras, runs vivos) nacen con la barra,
     no en la primera visita — así las pestañas de sección no cambian de ancho al cambiar */
  refetchWorkflows(); refetchAgents();
  /* arranca en Calendario si así se dejó (con su pestaña): DESPUÉS de conectar la API, o el primer fetch cae al vacío */
  if (getState().boardMode === 'calendar') goCalendar(getState().calView);
  seguirRutas();   // #851: un enlace a la Biblioteca abre en frío

  /* SSE / eventos del mock: refetch agrupado + panel al día */
  onBoardChanged(() => {
    scheduleRefetch();
    setTimeout(reopenSelected, 200);
  });
}

main();
