/* ============================================================
   store.js — estado de la app, pub/sub mínimo.
   El tablero es un cliente honesto del motor: el estado real
   vive en el daemon; aquí solo la última foto + estado de UI.
   ============================================================ */

/* preferencias de UI que sobreviven al reinicio (T2.4): modo del tablero
   y vista del calendario. localStorage puede no existir (Electron raro,
   ventana privada): se lee y escribe con red, nunca se depende de él. */
const PERSIST = {
  boardMode: { key: 'agenticos.boardMode', ok: ['kanban', 'calendar'], def: 'kanban' },
  calView:   { key: 'agenticos.calView',   ok: ['day', 'week', 'month', 'year'], def: 'week' },
  theme:     { key: 'agenticos.theme',     ok: ['auto', 'light', 'dark'], def: 'auto' }, // T3.2
};
function readPersisted(name) {
  const p = PERSIST[name];
  try {
    const v = localStorage.getItem(p.key);
    return p.ok.includes(v) ? v : p.def;
  } catch { return p.def; }
}
export function persist(name, value) {
  const p = PERSIST[name];
  if (!p || !p.ok.includes(value)) return;
  try { localStorage.setItem(p.key, value); } catch { /* sin almacenamiento: la sesión vale igual */ }
}

function localToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

const state = {
  mode: null,            // 'live' | 'fixtures'
  meta: null,            // /api/meta
  board: null,           // último payload de /api/board
  view: 'today',         // pantalla de la mañana por defecto (GAP-1)
  project: null,         // filtro de proyecto (id o null)
  showLaterals: true,    // banda de bloqueadas visible
  selected: null,        // ticket_id abierto en el panel
  /* #302: la identidad que firma NO puede ser un literal. Estaba clavada con
     el nombre del duenio de esta casa, y en un clon limpio eso rompe la app
     entera: el roster neutro no contiene ese nombre y el daemon rechaza
     todo lo que se intente escribir. Nace vacia y la pone app.js con
     meta.owner en cuanto el daemon contesta. */
  agent: null,           // identidad que firma las acciones de la UI (de meta.owner)
  theme: readPersisted('theme'),   // T3.2: auto | light | dark

  /* T2.4 — el Tablero en el tiempo */
  boardMode: readPersisted('boardMode'), // 'kanban' | 'calendar'
  calView:   readPersisted('calView'),   // 'day' | 'week' | 'month' | 'year'
  calDate:   localToday(),               // fecha ancla (ISO local); siempre arranca en hoy
  calData:   null,                       // T2.5: payload de GET /api/calendar del rango en pantalla
  calRange:  null,                       // { from, to } que calData cubre (para no refetch de más)
  calDaySel: null,                       // T2.7: día abierto al lado del Mes (ISO) — el mes no desaparece

  /* v2 — superficie workflows */
  surface: 'board',      // 'board' | 'workflow' | 'agents'

  /* v3 — Agentes (T1.12): la ventana del trabajo headless */
  agTab: 'results',      // 'scheduled' | 'running' | 'results' | 'jobs' — la lectura de la mañana por defecto
  agJobs: null,          // GET /api/jobs (todos; el filtro es de UI)
  agJobFilter: 'active', // 'active' | 'paused' | 'archived' | 'all'
  agSchedule: null,      // GET /api/agents/schedule?days=
  /* T5.2: Agentes en el tiempo — propio, nunca mezclado con el Tablero (decisión 4 del dueño) */
  agMode: 'kanban',      // 'kanban' (pestañas de siempre) | 'calendar'
  agCalView: 'week',
  agCalDate: localToday(),
  agCalDaySel: null,
  agCalData: null,       // GET /api/agents/calendar?from&to
  agCalRange: null,
  agScheduleDays: 7,
  agResults: null,       // GET /api/agents/results
  agRunning: null,       // GET /api/agents/running
  harnesses: null,       // GET /api/harnesses (con modelos)
  mcpInherited: null,    // GET /api/mcp/inherited
  selectedJob: null,     // job abierto en su ficha
  workflows: null,       // lista para el selector
  workflowId: null,      // workflow activo
  workflow: null,        // payload de GET /api/workflows/{id}
  findings: null,        // mesa de bugs del workflow activo
  drawerOpen: false,
  handoffOpen: false,
  wfPhase: null,         // T3.1: filtro de fase del kanban del workflow (null = todas)

  /* T3.5 — la Oficina: pestañas, handoffs, actividad desde la última visita */
  wfTab: 'tasks',        // 'tasks' | 'handoffs' | 'activity' | 'documents'
  wfDocuments: null,     // GET /api/workflows/{id}/documents (T3.6)
  wfDecisions: null,     // GET /api/decisions?workflow_id= (T3.4)
  wfHandoffs: null,      // GET /api/workflows/{id}/handoffs
  wfActivity: null,      // GET /api/workflows/{id}/activity?since=wfLastSeen
  wfLastSeen: null,      // sello de la última visita a ESTE workflow (localStorage)
};

/* «desde mi última visita»: un sello por workflow, formato local del motor
   (YYYY-MM-DDTHH:MM:SS) para que /activity?since= compare bien */
export function localStamp(d = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
export function readLastSeen(workflowId) {
  try { return localStorage.getItem(`agenticos.wf.${workflowId}.lastSeen`); } catch { return null; }
}
export function stampLastSeen(workflowId) {
  const now = localStamp();
  try { localStorage.setItem(`agenticos.wf.${workflowId}.lastSeen`, now); } catch { /* sin almacenamiento */ }
  return now;
}

const listeners = new Set();

export function getState() { return state; }

export function setState(patch) {
  Object.assign(state, patch);
  for (const fn of listeners) fn(state);
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/* -- helpers de lectura -- */

export function findTicket(id) {
  if (!state.board) return null;
  for (const st of Object.keys(state.board.columns)) {
    const hit = state.board.columns[st].find(t => t.id === id);
    if (hit) return hit;
  }
  for (const st of Object.keys(state.board.laterals)) {
    const hit = state.board.laterals[st].find(t => t.id === id);
    if (hit) return hit;
  }
  return null;
}

/* ticket del workflow ACTIVO — y solo de ese: la Oficina nunca mezcla */
export function findWorkflowTicket(id) {
  const wf = state.workflow;
  if (!wf) return null;
  for (const arr of Object.values(wf.tickets_by_phase ?? {})) {
    const hit = arr.find(t => t.id === id);
    if (hit) return hit;
  }
  return (wf.stray_tickets ?? []).find(t => t.id === id) ?? null;
}

export function projectOf(t) {
  return state.board?.projects.find(p => p.id === t.project_id) ?? null;
}
