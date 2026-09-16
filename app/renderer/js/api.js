/* ============================================================
   api.js — único punto de contacto con el daemon (SPEC §5).
   Dos modos tras la misma interfaz:
   · live      → HTTP + SSE contra 127.0.0.1:8756 (token del preload)
   · fixtures  → motor mock en memoria con las MISMAS reglas del §4,
                 para desarrollar sin daemon. Se elige solo: si
                 /api/health no responde, fixtures.
   Nota SSE: EventSource no admite headers → usamos fetch+stream
   para poder mandar X-FarOS-Token sin tocar el API de Hilo.
   ============================================================ */

const runtime = (typeof window !== 'undefined' && window.agenticos) || null;
// fallback de desarrollo: ?base=…&token=… en la URL (el preload de Electron manda)
const params = new URLSearchParams(typeof location !== 'undefined' ? location.search : '');
const BASE  = runtime?.baseUrl ?? params.get('base') ?? 'http://127.0.0.1:8756';
const TOKEN = runtime?.token ?? params.get('token') ?? null;

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.reason || body?.error || `HTTP ${status}`);
    this.status = status;
    /* #262 + #270 — DOS COSAS SE LLAMABAN «code» Y SON DISTINTAS:
       · `kind`  es el TIPO de error del motor (invalid_request, transition_rejected,
         unauthorized). Aquí se llamaba `code` y no lo leía nadie — comprobado con grep
         sobre todo el renderer antes de tocarlo, no supuesto.
       · `code`  es ahora la CLAVE TRADUCIBLE del contrato de Hilo (auth.invalid_token,
         transition.not_owner…). Es opcional y lo seguirá siendo: solo 17 de 211 sitios
         la traen. Sin ella se cae a `reason`, que viene en español a propósito porque
         es la línea de log y el último recurso. */
    this.kind = body?.error ?? 'error';
    this.code = body?.code ?? null;
    this.reason = body?.reason ?? this.message;
    /* v3.2 T1.5/T2.1: el motor dice QUÉ campo falla para poder pintarlo bajo su campo
       en vez de escupir un toast y tirar el formulario (el dueño: «que me lo diga en el
       propio menú, no que me saque de la creación»). Contrato aditivo: puede no venir. */
    this.field = body?.field ?? null;
  }
}

/* ---------- bus de eventos (SSE real o mock, mismo canal) ---------- */
const bus = new EventTarget();
export function onBoardChanged(fn) {
  bus.addEventListener('board.changed', (e) => fn(e.detail));
}
function emitChange(detail, ids = {}) {
  bus.dispatchEvent(new CustomEvent('board.changed', { detail: { detail, ids } }));
}

/* ================================================================
   MODO LIVE
   ================================================================ */
async function http(path, opts = {}) {
  const res = await fetch(BASE + path, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ...(TOKEN ? { 'X-FarOS-Token': TOKEN } : {}),
      ...(opts.headers || {}),
    },
  });
  if (!res.ok) {
    let body = null;
    try { body = await res.json(); } catch { /* cuerpo no JSON */ }
    throw new ApiError(res.status, body);
  }
  if (res.status === 204) return null;
  const ct = res.headers.get('content-type') || '';
  return ct.includes('json') ? res.json() : res.text();
}

async function startSSE() {
  // fetch-stream para poder enviar el token por header
  try {
    const res = await fetch(BASE + '/api/events', {
      headers: TOKEN ? { 'X-FarOS-Token': TOKEN } : {},
    });
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
        const data = chunk.split('\n')
          .filter(l => l.startsWith('data:'))
          .map(l => l.slice(5).trim()).join('\n');
        if (!data) continue;
        try {
          const payload = JSON.parse(data);
          emitChange(payload.detail ?? 'board.changed', payload.ids ?? {});
        } catch { /* evento no JSON: refetch genérico */ emitChange('board.changed', {}); }
      }
    }
  } catch { /* stream caído */ }
  // reconexión con backoff suave
  setTimeout(startSSE, 3000);
}

const live = {
  mode: 'live',
  health:   ()            => http('/api/health'),
  meta:     ()            => http('/api/meta'),
  board:    (view, project) => http(`/api/board?view=${view || 'all'}${project ? `&project=${project}` : ''}`),
  projects: ()            => http('/api/projects'),
  createProject: (p)      => http('/api/projects', { method: 'POST', body: JSON.stringify(p) }),
  patchProject:  (id, p)  => http(`/api/projects/${id}`, { method: 'PATCH', body: JSON.stringify(p) }),
  createTicket:  (t)      => http('/api/tickets', { method: 'POST', body: JSON.stringify(t) }),
  patchTicket:   (id, t)  => http(`/api/tickets/${id}`, { method: 'PATCH', body: JSON.stringify(t) }),
  ticket:    (id)         => http(`/api/tickets/${id}`),
  history:   (id)         => http(`/api/tickets/${id}/history`),
  transition:(id, body)   => http(`/api/tickets/${id}/transition`, { method: 'POST', body: JSON.stringify(body) }),
  patchJob:  (id, j)      => http(`/api/jobs/${id}`, { method: 'PATCH', body: JSON.stringify(j) }),
  launch:    (jobId)      => http(`/api/jobs/${jobId}/launch`, { method: 'POST' }),
  cancelRun: (runId)      => http(`/api/runs/${runId}/cancel`, { method: 'POST' }),
  runs:      (ticketId)   => http(`/api/runs?ticket_id=${ticketId}`),
  runOutput: (runId)      => http(`/api/runs/${runId}/output`),
  verifySweep: ()         => http('/api/verify/sweep', { method: 'POST' }),

  /* ---- v2: workflows, findings, hitos ---- */
  workflows:      (p = {})  => http(`/api/workflows${p.status || p.q ? '?' + new URLSearchParams(Object.fromEntries(Object.entries(p).filter(([, v]) => v))).toString() : ''}`),
  /* T4.1/T4.3: archivar (solo closed|cancelled → 409 si no) o cancelar con motivo */
  patchWorkflow:  (id, b)   => http(`/api/workflows/${id}`, { method: 'PATCH', body: JSON.stringify(b) }),
  workflow:       (id)      => http(`/api/workflows/${id}`),
  findings:       (id, st)  => http(`/api/workflows/${id}/findings${st ? `?status=${st}` : ''}`),
  dispatchTicket: (id, b)   => http(`/api/tickets/${id}/dispatch`, { method: 'POST', body: JSON.stringify(b) }),
  dispatchFinding:(id, b)   => http(`/api/findings/${id}/dispatch`, { method: 'POST', body: JSON.stringify(b) }),
  dismissFinding: (id, b)   => http(`/api/findings/${id}/dismiss`, { method: 'POST', body: JSON.stringify(b) }),
  milestones:     (id)      => http(`/api/workflows/${id}/milestones`),
  memoryDraft:    (id, mid) => http(`/api/workflows/${id}/milestones/${mid}/memory_draft`),
  markSaved:      (id, mid, b) => http(`/api/workflows/${id}/milestones/${mid}/mark_saved`, { method: 'POST', body: JSON.stringify(b) }),
  handoffDraft:   (id)      => http(`/api/workflows/${id}/handoff_draft`),
  saveHandoff:    (id, b)   => http(`/api/workflows/${id}/handoffs`, { method: 'POST', body: JSON.stringify(b) }),
  /* ---- v3 T3.5: todos los handoffs + actividad desde una fecha ---- */
  handoffs:       (id)      => http(`/api/workflows/${id}/handoffs`),
  activity:       (id, since, limit = 100) => http(`/api/workflows/${id}/activity?limit=${limit}${since ? `&since=${encodeURIComponent(since)}` : ''}`),

  /* ---- v3 T2.1/T2.2: el Tablero en el tiempo (ocurrencias + programar) ---- */
  calendar:       (from, to) => http(`/api/calendar?from=${from}&to=${to}`),
  schedule:       (id, b)   => http(`/api/tickets/${id}/schedule`, { method: 'POST', body: JSON.stringify(b) }),
  shiftNext:      (id, b)   => http(`/api/tickets/${id}/shift_next`, { method: 'POST', body: JSON.stringify(b) }),

  /* ---- v3 T3.3/T3.4: bisagras (decisiones del dueño) ---- */
  decisions:      (workflowId, status) => http(`/api/decisions?${workflowId != null ? `workflow_id=${workflowId}` : ''}${status ? `&status=${status}` : ''}`),
  decide:         (id, b)   => http(`/api/decisions/${id}/decide`, { method: 'POST', body: JSON.stringify(b) }),
  deferDecision:  (id, b)   => http(`/api/decisions/${id}/defer`, { method: 'POST', body: JSON.stringify(b) }),

  /* ---- v3 T3.6: documentos del workflow (se abren con el preload) ---- */
  documents:      (id)      => http(`/api/workflows/${id}/documents`),

  /* ---- v3 Agentes (T1.11): jobs como entidad propia ---- */
  jobs:           (status)  => http(`/api/jobs${status ? `?status=${status}` : ''}`),
  job:            (id)      => http(`/api/jobs/${id}`),
  createJob:      (j)       => http('/api/jobs', { method: 'POST', body: JSON.stringify(j) }),
  updateJob:      (id, j)   => http(`/api/jobs/${id}`, { method: 'PATCH', body: JSON.stringify(j) }),
  jobAction:      (id, action, b = {}) => http(`/api/jobs/${id}/${action}`, { method: 'POST', body: JSON.stringify(b) }), // launch|pause|resume|archive|duplicate
  jobRuns:        (jobId, limit = 30) => http(`/api/runs?job_id=${jobId}&limit=${limit}`),
  run:            (id)      => http(`/api/runs/${id}`),
  verdictRun:     (id, b)   => http(`/api/runs/${id}/verdict`, { method: 'POST', body: JSON.stringify(b) }),
  verifyRuns:     ()        => http('/api/verify/runs', { method: 'POST' }), // el juez, ahora (no esperar al sweep)
  agentsSchedule: (days = 7) => http(`/api/agents/schedule?days=${days}`),
  agentsCalendar: (from, to) => http(`/api/agents/calendar?from=${from}&to=${to}`), // T5.1
  agentsResults:  ()        => http('/api/agents/results'),
  agentsRunning:  ()        => http('/api/agents/running'),
  harnesses:      ()        => http('/api/harnesses'),
  harnessModels:  (name, refresh) => http(`/api/harnesses/${encodeURIComponent(name)}/models${refresh ? '?refresh=1' : ''}`),
  /* T5.4/T5.5: la sonda de $0 vuelve a medir el catálogo; si contesta sin modelos, se relee */
  harnessModelsRefresh: async (name) => {
    const r = await http(`/api/harnesses/${encodeURIComponent(name)}/models/refresh`, { method: 'POST', body: '{}' });
    return r?.models?.length ? r : http(`/api/harnesses/${encodeURIComponent(name)}/models`);
  },
  mcpInherited:   ()        => http('/api/mcp/inherited'),
  /* T5.6/T5.9: registro propio de MCP */
  mcpServers:     (q)       => http(`/api/mcp${q ? `?q=${encodeURIComponent(q)}` : ''}`),
  mcpImportLocal: (agent)   => http('/api/mcp/import_local', { method: 'POST', body: JSON.stringify({ agent }) }),
  mcpPatch:       (id, b)   => http(`/api/mcp/${id}`, { method: 'PATCH', body: JSON.stringify(b) }),
  mcpDelete:      (id, agent) => http(`/api/mcp/${id}?agent=${encodeURIComponent(agent)}`, { method: 'DELETE' }),
};

/* ================================================================
   MODO FIXTURES — motor mock con las reglas del §4
   ================================================================ */
const ACTIVE = ['proposed', 'accepted', 'in_progress', 'done', 'verified'];

// transiciones legales: estado origen → { estadoDestino: acción }
const LEGAL = {
  proposed:    { accepted: 'accept', rejected: 'reject' },
  accepted:    { in_progress: 'start', done: 'complete', blocked: 'block', rejected: 'reject' },
  in_progress: { done: 'complete', blocked: 'block', accepted: 'backlog' },
  done:        { verified: 'verify', in_progress: 'rework', accepted: 'rework' },
  verified:    { in_progress: 'rework', accepted: 'rework' },
  blocked:     { in_progress: 'unblock', rejected: 'reject' },
  rejected:    {},
  expired:     { accepted: 'accept' },
};

function todayISO() { return new Date().toISOString().slice(0, 10); }
/* el motor guarda horas LOCALES sin zona (YYYY-MM-DDTHH:MM:SS); el mock igual,
   o fmtWhen las enseña dos horas atrás */
function localNow() {
  const d = new Date(), p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function makeMock() {
  let db = null;   // { projects, tickets:Map, meta }
  let nextId = 500; let nextRunId = 60;

  async function load() {
    const [board, meta] = await Promise.all([
      fetch('fixtures/board.json').then(r => r.json()),
      fetch('fixtures/meta.json').then(r => r.json()),
    ]);
    const tickets = new Map();
    for (const st of Object.keys(board.columns)) for (const t of board.columns[st]) tickets.set(t.id, t);
    for (const st of Object.keys(board.laterals)) for (const t of board.laterals[st]) tickets.set(t.id, t);
    db = { projects: board.projects, tickets, meta, history: new Map() };
    for (const t of tickets.values()) {
      db.history.set(t.id, [{ at: t.created_at, agent: t.created_by ?? t.owner, action: 'create', note: null }]);
    }
  }

  function dueState(t) {
    const today = todayISO();
    // recurrentes: due se calcula por cadencia (last_done_at + cadence_days)
    if (t.cadence_days && !t.due_at) {
      if (!t.last_done_at) return 'due';
      const next = new Date(new Date(t.last_done_at).getTime() + t.cadence_days * 864e5)
        .toISOString().slice(0, 10);
      if (next < today) return 'overdue';
      if (next === today) return 'due';
      return 'ok';
    }
    if (!t.due_at) return 'ok';
    if (t.due_at < today) return 'overdue';
    if (t.due_at === today) return 'due';
    return 'ok';
  }

  function inView(t, view) {
    const ds = dueState(t);
    // today = pantalla de la mañana (GAP-1 de Eco): recurrentes due/overdue
    // + en curso + cola done (por verificar) + vencimientos de hoy
    if (view === 'today') {
      return ds !== 'ok' || t.status === 'in_progress' || t.status === 'done'
        || (t.due_at && t.due_at <= todayISO());
    }
    if (view === 'week') {
      if (!t.due_at) return t.status === 'in_progress' || t.status === 'done' || ds !== 'ok';
      const week = new Date(Date.now() + 7 * 864e5).toISOString().slice(0, 10);
      return t.due_at <= week;
    }
    if (view === 'future') return !t.due_at || dueState(t) === 'ok';
    return true; // all
  }

  function boardPayload(view = 'all', project = null) {
    const all = [...db.tickets.values()]
      .filter(t => !project || t.project_id === Number(project))
      .filter(t => inView(t, view))
      .map(t => ({ ...t, due_state: dueState(t) }));
    const columns = {};
    for (const status of ACTIVE) columns[status] = all.filter(t => t.status === status);
    return {
      view, project_id: project,
      projects: db.projects,
      columns,
      laterals: {
        blocked:  all.filter(t => t.status === 'blocked'),
        rejected: all.filter(t => t.status === 'rejected'),
        expired:  all.filter(t => t.status === 'expired'),
      },
    };
  }

  // #270: el mock tiene que emitir EL MISMO contrato que el daemon, no uno
  // parecido. Emitia `code` nunca y tipos que el daemon no manda
  // ('bad_request', 'not_found'); el daemon manda 'invalid_request' (400),
  // 'transition_rejected' (409) y 'unauthorized' (401). Consecuencia medida
  // por Lienzo: en modo fixtures el toast salia medio en espanyol aunque la
  // app estuviera en ingles, porque sin `code` la traduccion cae a `reason`.
  // Y es el modo en el que se capturan TODAS las pantallas del README.
  function reject409(reason, code) {
    return Promise.reject(new ApiError(409, { error: 'transition_rejected', reason, code }));
  }

  function wfTicket(id) {
    const wf = db.wfDetail;
    if (!wf) return null;
    for (const arr of Object.values(wf.tickets_by_phase ?? {})) {
      const t = arr.find(x => x.id === id);
      if (t) return t;
    }
    return (wf.stray_tickets ?? []).find(x => x.id === id) ?? null;
  }

  /* el resumen por fase se recalcula de las tarjetas: la tira de fases no puede mentir */
  function refreshPhaseSummary(wf) {
    wf.phases_summary = wf.phases.map(phase => {
      const arr = wf.tickets_by_phase?.[phase] ?? [];
      const n = (st) => arr.filter(t => t.status === st).length;
      return {
        phase, total: arr.length, verified: n('verified'), done: n('done'), in_progress: n('in_progress'),
        blocked: n('blocked'), accepted: n('accepted'),
        ready: arr.filter(t => t.ready && t.status === 'accepted' && !t.dispatched_to).length,
        dispatched: arr.filter(t => t.dispatched_to).length,
      };
    });
    const total = wf.phases_summary.reduce((a, p) => a + p.total, 0);
    const verified = wf.phases_summary.reduce((a, p) => a + p.verified, 0);
    wf.pct_global = total ? Math.round(100 * verified / total) : 0;
  }

  return {
    mode: 'fixtures',
    async health() { if (!db) await load(); return { ok: true, mode: 'fixtures' }; },
    async meta()   { if (!db) await load(); return db.meta; },
    async board(view, project) { if (!db) await load(); return boardPayload(view, project); },
    async projects() { return db.projects; },

    async createProject(p) {
      const proj = { id: ++nextId, archived: 0, position: db.projects.length, color: '#8a5a2b', ...p };
      db.projects.push(proj);
      emitChange('project.changed', { project_id: proj.id });
      return proj;
    },
    async patchProject(id, patch) {
      const p = db.projects.find(x => x.id === Number(id));
      Object.assign(p, patch);
      emitChange('project.changed', { project_id: p.id });
      return p;
    },

    async createTicket(t) {
      /* T2.1: el mock valida lo MISMO que el motor y con el mismo `field`. Un mock más
         permisivo esconde el fallo justo dondel dueño lo ve (lección del día 129, FIX #192). */
      if (!t.agent) {
        return Promise.reject(new ApiError(400, { error: 'invalid_request', reason: 'falta agent (quién crea)', field: 'agent', code: 'request.agent_required' }));
      }
      if (!String(t.title ?? '').trim()) {
        return Promise.reject(new ApiError(400, { error: 'invalid_request', reason: 'el título no puede estar vacío', field: 'title' }));
      }
      if (t.scheduled_at && !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(t.scheduled_at)) {
        return Promise.reject(new ApiError(400, { error: 'invalid_request', reason: 'scheduled_at debe ser YYYY-MM-DDTHH:MM', field: 'scheduled_at' }));
      }
      const id = ++nextId;
      const ticket = {
        id, status: 'proposed', kind: 'manual', priority: 'media', due_at: null,
        owner: t.owner ?? null, created_by: t.created_by ?? db.meta.owner ?? 'owner',
        source: 'proposed', verification_level: db.meta.defaults.verification_level ?? 'self',
        verify_criteria: null, blocked_reason: null, evidence_type: null, evidence: null,
        verified_by: null, cadence_days: null, last_done_at: null, closed_at: null,
        created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
        ...t,
      };
      if (t.kind === 'agentic' && t.job) {
        ticket.job = { id: ++nextId, harness: 'claude-cli', enabled: 1, permission_mode: 'dontAsk', ...t.job };
        ticket.last_run = null;
      }
      db.tickets.set(id, ticket);
      db.history.set(id, [{ at: ticket.created_at, agent: ticket.owner, action: 'create', note: null }]);
      emitChange('ticket.created', { ticket_id: id });
      return ticket;
    },

    async patchTicket(id, patch) {
      const t = db.tickets.get(Number(id)) ?? wfTicket(Number(id));
      const { agent, ...fields } = patch;
      if (!t) {
        const viaCal = await this._calPatch(id, fields); // T2.5: tickets que solo viven en el calendario de fixtures
        return viaCal ?? Promise.reject(new ApiError(404, { error: 'invalid_request', reason: 'El ticket no existe.', code: 'ticket.not_found' }));
      }
      // contrato T0.3: "" limpia; formatos validados como el motor (400 con motivo)
      if (fields.scheduled_at && !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(fields.scheduled_at)) {
        return Promise.reject(new ApiError(400, { error: 'invalid_request', reason: 'scheduled_at debe ser YYYY-MM-DDTHH:MM' }));
      }
      if (fields.duration_min !== '' && fields.duration_min != null && !(Number(fields.duration_min) > 0)) {
        return Promise.reject(new ApiError(400, { error: 'invalid_request', reason: 'duration_min debe ser un entero > 0' }));
      }
      for (const k of Object.keys(fields)) if (fields[k] === '') fields[k] = null;
      Object.assign(t, fields, { updated_at: new Date().toISOString() });
      if (t.cadence_days) {
        t.next_due = t.next_due_override
          ?? (t.last_done_at ? new Date(new Date(t.last_done_at).getTime() + t.cadence_days * 864e5).toISOString().slice(0, 10) : todayISO());
      }
      /* T3.3: la hora preferida / duración de una recurrente también mueven su proyección en el calendario */
      if ('preferred_time' in fields || 'duration_min' in fields) await this._calPatch(id, fields).catch(() => null);
      emitChange('ticket.transition', { ticket_id: t.id });
      return t;
    },

    async ticket(id)  {
      const t = db.tickets.get(Number(id));
      if (t) return t;
      /* T2.5: tickets que solo viven en el calendario de fixtures — el panel los abre igual */
      const it = (await this._cal()).items.find(i => i.ticket_id === Number(id));
      if (!it) return Promise.reject(new ApiError(404, { error: 'invalid_request', reason: 'El ticket no existe.', code: 'ticket.not_found' }));
      return { id: it.ticket_id, kind: 'manual', title: it.title, owner: it.owner, project_id: it.project_id, status: it.status,
               priority: it.priority, due_state: it.due_state, last_change: it.last_change, cadence_days: it.cadence_days,
               preferred_time: it.preferred_time, duration_min: it.duration_min, all_day: it.all_day, plan_key: it.plan_key,
               scheduled_at: it.kind === 'scheduled' && it.start ? `${it.date}T${it.start}` : null,
               due_at: it.kind === 'due' ? it.date : null, created_by: it.owner, created_at: `${it.date}T00:00:00`, description: null };
    },
    async history(id) { return db.history.get(Number(id)) ?? []; },

    async transition(id, { action, agent, evidence_type, evidence, note, to, reason }) {
      /* T2.9: tickets que solo viven en el calendario de fixtures — estampar una
         recurrente la devuelve a su ciclo (la próxima salta una cadencia) */
      if (!db.tickets.get(Number(id)) && !wfTicket(Number(id))) {
        const cal = await this._cal();
        const mine = cal.items.filter(i => i.ticket_id === Number(id));
        if (mine.length) {
          if (action !== 'complete') return reject409(`desde el calendario solo se estampa (complete); «${action}» va por el panel`);
          if (!evidence_type || !evidence) return Promise.reject(new ApiError(400, { error: 'invalid_request', reason: 'complete requiere evidence_type y evidence', code: 'complete.evidence_required' }));
          const rec = mine[0].cadence_days;
          if (rec) {
            const n = Number(rec);
            for (const it of mine) {
              const d = new Date(it.date + 'T00:00:00'); d.setDate(d.getDate() + n);
              it.date = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
              it.due_state = 'ok'; delete it.overdue_since;
              it.last_change = { at: localNow(), by: agent, from: it.status, to: it.status };
            }
            emitChange('ticket.transition', { ticket_id: Number(id) });
            return { id: Number(id), status: mine[0].status, recurring_stamped: true, last_done_at: todayISO() };
          }
          for (const it of mine) it.status = 'done';
          cal.items = cal.items.filter(i => i.ticket_id !== Number(id)); // cerrada: el motor no la lista
          emitChange('ticket.transition', { ticket_id: Number(id) });
          return { id: Number(id), status: 'done' };
        }
      }
      // T3.1: el kanban de la Oficina también transiciona — el mock busca en
      // el tablero y, si no está, en el workflow cargado
      const t = db.tickets.get(Number(id)) ?? wfTicket(Number(id));
      if (!t) return reject409('El ticket no existe.', 'ticket.not_found');
      if (!db.history.has(t.id)) db.history.set(t.id, [{ at: t.created_at, agent: t.created_by ?? t.owner, action: 'create', note: null }]);
      const legal = LEGAL[t.status] ?? {};
      const target = to ?? Object.keys(legal).find(k => legal[k] === action);
      if (!target || !legal[target]) {
        return reject409(`Transición no válida: de «${t.status}» no se puede pasar a «${to ?? action}».`, 'transition.illegal');
      }
      if (target === 'done' && !evidence_type) {
        return reject409('Completar exige evidencia tipada. Sin evidencia no hay «hecha».', 'complete.evidence_required');
      }
      /* T2.1 (motor de Hilo, espejado): rework = devolver con motivo; recurrentes no se devuelven */
      if (action === 'rework') {
        if (!reason || reason.trim().length < 10) return Promise.reject(new ApiError(400, { error: 'invalid_request', reason: 'rework requiere un motivo de al menos 10 caracteres' }));
        if (t.cadence_days) return Promise.reject(new ApiError(400, { error: 'invalid_request', reason: 'las recurrentes no se devuelven' }));
        const prev = t.status;
        Object.assign(t, { status: target, closed_at: null, verified_by: null, last_change: { at: localNow(), by: agent, from: prev, to: target } });
        db.history.get(t.id).push({ at: localNow(), agent, action: 'rework', note: `[rechazo] ${reason.trim()}`, from: prev, to: target });
        /* T2.4/T1.3 espejado: una devuelta SIN ancla temporal recibe due_at = hoy, o se
           queda fuera de Hoy/Semana y parece que ha desaparecido (el dueño §3). */
        let anchored = false;
        if (!t.due_at && !t.scheduled_at) {
          t.due_at = localNow().slice(0, 10);
          anchored = true;
          db.history.get(t.id).push({ at: localNow(), agent, action: 'anchor', note: '[ancla] devuelta: hoy' });
        }
        emitChange('ticket.transition', { ticket_id: t.id });
        return { ...t, anchored };
      }
      if (target === 'verified' && t.verification_level === 'peer' && agent === t.owner) {
        return reject409(`Verificador y dueño no pueden ser el mismo (${agent}). Lo verifica otro, o el bot.`);
      }
      if (target === 'blocked' && !note) {
        return reject409('Bloquear exige motivo en la nota.');
      }
      const prev = t.status;
      // recurrentes (§4): completar estampa last_done_at, NO cierra el ticket
      if (target === 'done' && t.cadence_days) {
        t.last_done_at = new Date().toISOString();
        db.history.get(t.id).push({ at: t.last_done_at, agent, action: 'complete', note: note ?? 'recurrente estampada', from: prev, to: prev });
        emitChange('ticket.transition', { ticket_id: t.id });
        return { ok: true, status: prev, recurring_stamped: true };
      }
      t.status = target;
      t.updated_at = new Date().toISOString();
      if (evidence_type) { t.evidence_type = evidence_type; t.evidence = evidence; }
      if (target === 'verified') { t.verified_by = agent; t.closed_at = t.updated_at; }
      if (target === 'blocked') t.blocked_reason = note;
      if (prev === 'blocked' && target !== 'blocked') t.blocked_reason = null;
      db.history.get(t.id).push({
        at: t.updated_at, agent, action: legal[target],
        note: note ?? null, from: prev, to: target,
      });
      emitChange('ticket.transition', { ticket_id: t.id });
      return { ok: true, status: target };
    },

    async patchJob(jobId, patch) {
      const t = [...db.tickets.values()].find(x => x.job?.id === Number(jobId));
      Object.assign(t.job, patch);
      emitChange('ticket.transition', { ticket_id: t.id });
      return t.job;
    },

    async launch(jobId) {
      const t = [...db.tickets.values()].find(x => x.job?.id === Number(jobId));
      if (!t) return reject409('Job no encontrado.');
      if (t.last_run?.status === 'running') return reject409('Ya hay un run en curso de este job. Anti-solape.');
      const runId = ++nextRunId;
      t.last_run = { id: runId, status: 'running', started_at: new Date().toISOString(), finished_at: null, result_summary: null, cost_usd: null };
      emitChange('run.started', { ticket_id: t.id, run_id: runId });
      // el mock termina el run a los 4 s con resultado plausible
      setTimeout(() => {
        t.last_run = {
          ...t.last_run, status: 'ok', finished_at: new Date().toISOString(),
          result_summary: `[mock] Run ${runId} completado. Resultado de ejemplo del job «${t.title}».`,
          cost_usd: 0.02,
        };
        if (['accepted', 'in_progress'].includes(t.status)) {
          t.status = 'done'; t.evidence_type = 'run_output'; t.evidence = String(runId);
          db.history.get(t.id).push({ at: t.last_run.finished_at, agent: 'runner', action: 'complete', note: `run ${runId} ok`, to: 'done' });
        }
        emitChange('run.finished', { ticket_id: t.id, run_id: runId });
      }, 4000);
      return { run_id: runId };
    },

    async cancelRun(runId) {
      // v3: runs de jobs propios primero; los agénticos legacy del tablero después
      if (db.runs) for (const arr of db.runs.values()) {
        const r = arr.find(x => x.id === Number(runId));
        if (r) {
          if (r.status !== 'running') return reject409('El run ya había terminado.');
          Object.assign(r, { status: 'cancelled', finished_at: localNow(), exit_code: null });
          emitChange('run.finished', { job_id: r.job_id, run_id: r.id });
          return { ok: true };
        }
      }
      const t = [...db.tickets.values()].find(x => x.last_run?.id === Number(runId));
      if (t?.last_run?.status === 'running') {
        t.last_run.status = 'cancelled';
        t.last_run.finished_at = new Date().toISOString();
        emitChange('run.finished', { ticket_id: t.id, run_id: Number(runId) });
      }
      return { ok: true };
    },

    async runs(ticketId) {
      const t = db.tickets.get(Number(ticketId));
      return t?.last_run ? [t.last_run] : [];
    },

    async runOutput(runId) {
      if (db.runs) for (const [jobId, arr] of db.runs) {
        const r = arr.find(x => x.id === Number(runId));
        if (r) {
          const j = db.jobs.get(jobId);
          if (r.status === 'running') return `(run ${r.id} en curso — «${j?.name}» · sin output todavía)`;
          return `# Output del run ${r.id} — ${j?.name}\n\n${r.result_summary ?? '(sin resultado)'}\n\n---\nmeta: status=${r.status} · exit=${r.exit_code ?? '—'} · cost=${r.cost_usd ?? '—'} (${r.cost_kind ?? '—'}) · ${r.started_at} → ${r.finished_at ?? '…'}${r.verdict ? `\nveredicto: ${r.verdict} — ${r.verdict_reason ?? ''}` : ''}\n\n[fixtures] El output real vendrá de ${r.output_path ?? 'artifacts/'}`;
        }
      }
      const t = [...db.tickets.values()].find(x => x.last_run?.id === Number(runId));
      const r = t?.last_run;
      if (!r || r.status === 'running') return '(run en curso — sin output todavía)';
      return `# Output del run ${runId} — ${t.title}\n\n${r.result_summary ?? '(sin resultado)'}\n\n---\nmeta: status=${r.status} · cost=$${r.cost_usd ?? '—'} · ${r.started_at} → ${r.finished_at}\n\n[fixtures] El output real vendrá de artifacts/run_${runId}/output.md`;
    },

    /* ---- v2 mock: shapes REALES del motor (fixtures de Hilo 1-sep) ---- */
    async _wf() {
      if (!db.wfDetail) {
        [db.wfDetail, db.wfList] = await Promise.all([
          fetch('fixtures/workflow.json').then(r => r.json()),
          fetch('fixtures/workflows_list.json').then(r => r.json()),
        ]);
      }
      return db;
    },
    async workflows(p = {}) {
      const { wfDetail, wfList } = await this._wf();
      // el mock recalcula open_findings y pct — el badge y el chip no pueden mentir
      refreshPhaseSummary(wfDetail);
      const k = (p.q ?? '').toLowerCase();
      return wfList.filter(x => (p.status === 'archived' ? x.status === 'archived' : x.status !== 'archived') && (!k || x.name.toLowerCase().includes(k))).map(x => ({
        ...x,
        open_findings: x.id === wfDetail.id
          ? wfDetail.findings.filter(f => f.status === 'open').length
          : x.open_findings,
        pct_global: x.id === wfDetail.id ? wfDetail.pct_global : x.pct_global,
      }));
    },
    async workflow(id) {
      const { wfDetail, wfList } = await this._wf(); refreshPhaseSummary(wfDetail);
      /* T4.3: el detalle de fixtures es uno; para otro id se viste con su fila de la lista (nombre, estado) */
      const w = wfList.find(x => x.id === Number(id));
      return w && w.id !== wfDetail.id ? { ...wfDetail, id: w.id, name: w.name, status: w.status, pct_global: w.pct_global } : wfDetail;
    },
    async patchWorkflow(id, { agent, status, reason, close }) {
      const { wfDetail, wfList } = await this._wf();
      const w = wfList.find(x => x.id === Number(id));
      if (!w) return Promise.reject(new ApiError(404, { reason: 'workflow no existe' }));
      if (status === 'archived' && w.status === 'active' && close) { w.status = 'archived'; w.archived_at = localNow(); emitChange('workflow.changed', { id: w.id }); return w; }
      if (status === 'archived' && !['closed', 'cancelled'].includes(w.status)) return reject409(`solo se archiva un workflow cerrado o cancelado (este está «${w.status}»)`);
      if (status === 'active' && w.status !== 'archived') return reject409(`solo se reactiva un workflow archivado (este está «${w.status}»)`);
      if (status === 'cancelled' && (!reason || reason.trim().length < 10)) return Promise.reject(new ApiError(400, { error: 'invalid_request', reason: 'cancelar requiere un motivo de al menos 10 caracteres' }));
      w.status = status; if (status === 'archived') w.archived_at = localNow();
      if (wfDetail.id === w.id) wfDetail.status = status;
      emitChange('workflow.changed', { id: w.id });
      return w;
    },
    async findings(id, st) {
      const { wfDetail } = await this._wf();
      return st ? wfDetail.findings.filter(f => f.status === st) : wfDetail.findings;
    },
    async dispatchTicket(id, { agent, to, note }) {
      const { wfDetail } = await this._wf();
      for (const arr of Object.values(wfDetail.tickets_by_phase)) {
        const t = arr.find(x => x.id === Number(id));
        if (t) {
          if (!t.ready) return reject409(`«${t.plan_key ?? t.title}» no está lista: dependencias sin verificar.`);
          t.dispatched_to = to; t.dispatched_at = new Date().toISOString(); t.owner = to;
          emitChange('ticket.dispatched', { ticket_id: t.id });
          return { ok: true };
        }
      }
      return reject409('El ticket no existe en el workflow.');
    },
    async dispatchFinding(id, { agent, note }) {
      const { wfDetail } = await this._wf();
      const f = wfDetail.findings.find(x => x.id === Number(id));
      if (!f) return reject409('El hallazgo no existe.');
      if (agent !== wfDetail.coordinator && !(db.meta.agents ?? []).includes(agent)) {
        return reject409(`Solo el coordinador (${wfDetail.coordinator}) o la familia despachan hallazgos.`);
      }
      if (f.status !== 'open') return reject409(`El hallazgo ya está ${f.status}.`);
      f.status = 'dispatched';
      f.correction_ticket = { id: 900 + f.id, plan_key: null, status: 'accepted' };
      emitChange('finding.dispatched', { finding_id: f.id });
      return { ok: true, correction_ticket_id: f.correction_ticket.id };
    },
    async dismissFinding(id, { agent, note }) {
      const { wfDetail } = await this._wf();
      const f = wfDetail.findings.find(x => x.id === Number(id));
      if (!f) return reject409('El hallazgo no existe.');
      if (!note) return reject409('Descartar exige motivo.');
      f.status = 'dismissed'; f.resolved_note = note; f.resolved_at = new Date().toISOString();
      emitChange('finding.resolved', { finding_id: f.id });
      return { ok: true };
    },
    async milestones(id) { const { wfDetail } = await this._wf(); return wfDetail.milestones; },
    async memoryDraft(id, mid) {
      const { wfDetail } = await this._wf();
      const m = wfDetail.milestones.find(x => x.id === Number(mid));
      return { draft: `[mock] Borrador de memoria EcoDB — fase «${m?.phase}» de ${wfDetail.name} completada.` };
    },
    async markSaved(id, mid, { memory_id }) {
      const { wfDetail } = await this._wf();
      const m = wfDetail.milestones.find(x => x.id === Number(mid));
      if (m) m.ecodb_saved = 1;
      wfDetail.milestones_pending_ecodb = (wfDetail.milestones_pending_ecodb ?? []).filter(x => x.id !== Number(mid));
      emitChange('workflow.milestone', { milestone_id: Number(mid) });
      return { ok: true };
    },
    async handoffDraft(id) {
      const { wfDetail } = await this._wf();
      return { content: (wfDetail.latest_handoff?.content ?? 'HANDOFF — (borrador vacío)') + '\n\n[Editar: notas del coordinador]' };
    },
    async saveHandoff(id, { agent, content }) {
      const { wfDetail } = await this._wf();
      if (!(db.meta.agents ?? []).includes(agent) && agent !== wfDetail.coordinator) {
        return reject409(`Solo el coordinador (${wfDetail.coordinator}) o la familia guardan handoffs.`);
      }
      if (!(content ?? '').trim()) return reject409('handoff vacío');
      const h = {
        id: (wfDetail.latest_handoff?.id ?? 0) + 1, workflow_id: wfDetail.id,
        content, created_by: agent, created_at: localNow(),
      };
      wfDetail.latest_handoff = h;
      wfDetail.handoffs = [h, ...(wfDetail.handoffs ?? [])];
      emitChange('workflow.handoff', { workflow_id: wfDetail.id });
      return h;
    },
    /* T2.5: calendario — fixtures/calendar.json es el payload real de
       GET /api/calendar (2026-08-31 → 2026-09-13) más tres con hora para
       poder arrastrar y estirar. Las mismas reglas que agenda.py: recurrentes
       por preferred_time (no por scheduled_at); las demás por scheduled_at. */
    async _cal() {
      if (!db.cal) db.cal = await fetch('fixtures/calendar.json').then(r => r.json());
      return db.cal;
    },
    async calendar(from, to) {
      const cal = await this._cal();
      return { from, to, lanes: cal.lanes, slots: cal.slots, items: cal.items.filter(i => i.date >= from && i.date <= to) };
    },
    _calEnd(start, dur) {
      if (!start || !dur) return null;
      const m = Number(start.slice(0, 2)) * 60 + Number(start.slice(3, 5)) + Number(dur);
      return m < 24 * 60 ? `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}` : '23:59';
    },
    async schedule(id, { agent, scheduled_at, duration_min, all_day, note }) {
      const cal = await this._cal();
      const mine = cal.items.filter(i => i.ticket_id === Number(id));
      if (!mine.length) return Promise.reject(new ApiError(404, { error: 'invalid_request', reason: `ticket #${id} no existe` , code: 'ticket.not_found' }));
      if (['done', 'verified', 'rejected', 'expired'].includes(mine[0].status)) return reject409(`ticket #${id} está cerrado (${mine[0].status}) — no se reprograma`);
      if (scheduled_at && !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(scheduled_at)) return Promise.reject(new ApiError(400, { error: 'invalid_request', reason: 'scheduled_at debe ser YYYY-MM-DDTHH:MM' }));
      if (duration_min != null && duration_min !== '' && !(Number(duration_min) > 0)) return Promise.reject(new ApiError(400, { error: 'invalid_request', reason: 'duration_min debe ser un entero > 0' }));
      for (const it of mine) {
        if (it.kind === 'recurrence') continue; // agenda.py: las recurrentes se proyectan por preferred_time
        if (scheduled_at) { it.kind = 'scheduled'; it.date = scheduled_at.slice(0, 10); it.start = scheduled_at.slice(11, 16); it.all_day = false; }
        if (scheduled_at === '') { it.kind = 'due'; it.start = null; it.end = null; }
        if (duration_min != null) it.duration_min = duration_min === '' ? null : Number(duration_min);
        if (all_day != null) { it.all_day = Boolean(all_day); if (it.all_day) { it.start = null; it.end = null; } }
        it.end = this._calEnd(it.start, it.duration_min);
      }
      emitChange('ticket.updated', { ticket_id: Number(id) });
      return { id: Number(id), scheduled_at, duration_min, all_day };
    },
    /* mover SOLO la próxima ocurrencia de una recurrente (T2.2): la cadencia no cambia */
    async shiftNext(id, { agent, date, note }) {
      const cal = await this._cal();
      const mine = cal.items.filter(i => i.ticket_id === Number(id) && i.kind === 'recurrence');
      if (!mine.length) return Promise.reject(new ApiError(400, { error: 'invalid_request', reason: `ticket #${id} no es recurrente` }));
      if (date && date < todayISO()) return Promise.reject(new ApiError(400, { error: 'invalid_request', reason: `la próxima ocurrencia no puede ir al pasado (${date})` }));
      const real = mine.find(i => !i.ghost) ?? mine[0];
      const cadence = Number(real.cadence_days);
      const delta = Math.round((new Date(date + 'T00:00:00') - new Date(real.date + 'T00:00:00')) / 864e5);
      for (const it of mine) { // la próxima se mueve; las proyectadas se recolocan desde ella
        const d = new Date(it.date + 'T00:00:00'); d.setDate(d.getDate() + delta);
        it.date = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
        delete it.overdue_since;
      }
      emitChange('ticket.updated', { ticket_id: Number(id) });
      return { id: Number(id), next_due: date, cadence_days: cadence };
    },
    /* la hora preferida y la duración de una recurrente van por PATCH (contrato T0.3) */
    async _calPatch(id, fields) {
      const cal = await this._cal();
      const mine = cal.items.filter(i => i.ticket_id === Number(id));
      if (!mine.length) return null;
      for (const it of mine) {
        if ('preferred_time' in fields) { it.preferred_time = fields.preferred_time || null; it.start = it.preferred_time; }
        if ('duration_min' in fields) it.duration_min = fields.duration_min ? Number(fields.duration_min) : null;
        it.end = this._calEnd(it.start, it.duration_min);
      }
      emitChange('ticket.updated', { ticket_id: Number(id) });
      return { id: Number(id), ...fields };
    },

    /* T3.4: bisagras — mismas reglas que decisions.py: no se decide dos veces */
    async _dec() {
      if (!db.decisions) db.decisions = await fetch('fixtures/decisions.json').then(r => r.json());
      return db.decisions;
    },
    async decisions(workflowId, status) {
      const all = await this._dec();
      return all.filter(d => (workflowId == null || d.workflow_id === Number(workflowId)) && (!status || d.status === status));
    },
    async decide(id, { agent, option, decision, rationale }) {
      const d = (await this._dec()).find(x => x.id === Number(id));
      if (!d) return Promise.reject(new ApiError(404, { reason: `decisión #${id} no existe` }));
      if (d.status === 'decided') return reject409(`decisión #${id} ya está decidida por ${d.decided_by} (${d.decision})`);
      if (option && !d.options.some(o => o.label === option)) return Promise.reject(new ApiError(400, { reason: `opción desconocida: ${option}` }));
      if (!option && !(decision ?? '').trim()) return Promise.reject(new ApiError(400, { reason: 'decide requiere option (label) o decision (texto libre)' }));
      if (!(rationale ?? '').trim()) return Promise.reject(new ApiError(400, { reason: 'decide requiere rationale (por qué)' }));
      Object.assign(d, { status: 'decided', decided_by: agent, decision: option ?? decision, rationale, decided_at: localNow(), deferred_until: null });
      d.history.push({ event: 'decided', actor: agent, changed_at: d.decided_at, note: `${d.decision} — ${rationale}` });
      emitChange('decision.changed', { decision_id: d.id });
      return d;
    },
    async deferDecision(id, { agent, until, note }) {
      const d = (await this._dec()).find(x => x.id === Number(id));
      if (!d) return Promise.reject(new ApiError(404, { reason: `decisión #${id} no existe` }));
      if (d.status === 'decided') return reject409(`decisión #${id} ya está decidida`);
      Object.assign(d, { status: 'deferred', deferred_until: until ? String(until).slice(0, 10) : null });
      d.history.push({ event: 'deferred', actor: agent, changed_at: localNow(), note: `${until ? 'hasta ' + String(until).slice(0, 10) + '. ' : ''}${note ?? ''}` });
      emitChange('decision.changed', { decision_id: d.id });
      return d;
    },

    /* T3.6: documentos del workflow — en fixtures, los del plan */
    async documents(id) {
      const { wfDetail } = await this._wf();
      return [
        { kind: 'plan', name: 'agenticos-v3.plan.json', path: 'C:\\work\\FarOS\\plans\\agenticos-v3.plan.json', size: 62860, modified: '2026-09-02T08:48' },
        { kind: 'spec', name: 'SPEC_v3.md', path: 'C:\\work\\FarOS\\docs\\SPEC_v3.md', size: 18120, modified: '2026-09-02T08:40' },
        { kind: 'review', name: 'eco_adv_producto_checklist_v3.md', path: 'C:\\work\\FarOS\\reviews\\eco_adv_producto_checklist_v3.md', size: 5024, modified: '2026-09-02T08:57' },
      ].filter(() => wfDetail);
    },
    /* T3.5: lista completa (más reciente primero) y actividad desde una fecha */
    async handoffs(id) {
      const { wfDetail } = await this._wf();
      if (!wfDetail.handoffs) wfDetail.handoffs = wfDetail.latest_handoff ? [wfDetail.latest_handoff] : [];
      return wfDetail.handoffs;
    },
    async activity(id, since, limit = 100) {
      const { wfDetail } = await this._wf();
      const all = wfDetail.recent_activity ?? [];
      return (since ? all.filter(a => a.at >= since) : all).slice(0, limit);
    },

    /* ---- v3 Agentes mock: jobs propios, runs con veredicto, harnesses ---- */
    async _ag() {
      if (!db.jobs) {
        const [jobs, runs, harnesses, mcp] = await Promise.all([
          fetch('fixtures/agents_jobs.json').then(r => r.json()),
          fetch('fixtures/agents_runs.json').then(r => r.json()),
          fetch('fixtures/harnesses.json').then(r => r.json()),
          fetch('fixtures/mcp_inherited.json').then(r => r.json()),
        ]);
        db.jobs = new Map(jobs.map(j => [j.id, j]));
        db.runs = new Map(Object.entries(runs).map(([k, v]) => [Number(k), v]));
        db.harnesses = harnesses; db.mcp = mcp;
        db.nextJobId = Math.max(...jobs.map(j => j.id)) + 1;
        db.nextRunId = 100;
      }
      return db;
    },
    _jobView(j) {
      const runs = db.runs.get(j.id) ?? [];
      const last = runs[0] ?? null;
      return { ...j, last_run: last ? { ...last } : null };
    },
    async jobs(status) {
      await this._ag();
      return [...db.jobs.values()].filter(j => !status || j.status === status).map(j => this._jobView(j));
    },
    async job(id) { await this._ag(); const j = db.jobs.get(Number(id)); return j ? this._jobView(j) : Promise.reject(new ApiError(404, { reason: 'El job no existe.' })); },
    async createJob(j) {
      await this._ag();
      if (!(j.name ?? '').trim()) return Promise.reject(new ApiError(400, { reason: 'name vacío' }));
      if (!(j.prompt ?? '').trim()) return Promise.reject(new ApiError(400, { reason: 'prompt vacío' }));
      const h = db.harnesses.find(x => x.name === j.harness);
      const job = {
        id: db.nextJobId++, status: 'active', harness: 'claude-cli', model: 'haiku', permission_mode: 'dontAsk',
        allowed_tools: null, mcp_config: null, inherit_mcp: [], strict_mcp: 0, add_dirs: [], effort: null,
        fallback_model: null, system_prompt: null, json_schema: null, max_budget_usd: null, timeout_s: 1800,
        schedule: { type: 'manual' }, verify_level: 'auto', verify_criteria: null, ticket_id: null, project_id: null,
        created_at: localNow(), next_fire: null, total_cost_usd: 0, total_equivalent_usd: 0,
        ...j, billing_mode: h?.billing ?? 'subscription',
      };
      if (job.billing_mode !== 'api') job.max_budget_usd = null; // T1.7: el tope solo existe en api
      db.jobs.set(job.id, job); db.runs.set(job.id, []);
      emitChange('job.changed', { job_id: job.id });
      return this._jobView(job);
    },
    async updateJob(id, patch) {
      await this._ag();
      const j = db.jobs.get(Number(id));
      if (!j) return Promise.reject(new ApiError(404, { reason: 'El job no existe.' }));
      const { agent, ...fields } = patch;
      Object.assign(j, fields, { updated_at: localNow() });
      emitChange('job.changed', { job_id: j.id });
      return this._jobView(j);
    },
    async jobAction(id, action) {
      await this._ag();
      const j = db.jobs.get(Number(id));
      if (!j) return Promise.reject(new ApiError(404, { reason: 'El job no existe.' }));
      if (action === 'launch') {
        const runs = db.runs.get(j.id);
        if (runs[0]?.status === 'running') return reject409('Ya hay un run en curso de este job. Anti-solape.');
        if (j.status !== 'active') return reject409(`El job está ${j.status}: reanúdalo para lanzarlo.`);
        const run = { id: db.nextRunId++, job_id: j.id, status: 'running', started_at: localNow(), finished_at: null, cost_usd: null, cost_kind: null, verdict: null, verdict_reason: null, verified_at: null, result_summary: null, exit_code: null, session_id: null, output_path: null };
        runs.unshift(run);
        emitChange('run.started', { job_id: j.id, run_id: run.id });
        setTimeout(() => {
          if (run.status !== 'running') return;
          Object.assign(run, { status: 'ok', finished_at: localNow(), cost_usd: 0.03, cost_kind: j.billing_mode === 'api' ? 'billed' : 'equivalent', exit_code: 0, result_summary: `[mock] Run ${run.id} completado — «${j.name}».` });
          emitChange('run.finished', { job_id: j.id, run_id: run.id });
          if (j.verify_level !== 'none') setTimeout(() => {
            Object.assign(run, { verdict: 'pass', verdict_reason: '[mock] cumple el criterio.', verified_at: localNow() });
            emitChange('verify.done', { job_id: j.id, run_id: run.id });
          }, 2500);
        }, 4000);
        return { run_id: run.id };
      }
      if (action === 'pause')   { if (j.status !== 'active') return reject409(`El job está ${j.status}.`); j.status = 'paused'; j.next_fire = null; }
      if (action === 'resume')  { if (j.status !== 'paused') return reject409(`El job está ${j.status}.`); j.status = 'active'; }
      if (action === 'archive') { j.status = 'archived'; j.next_fire = null; }
      if (action === 'duplicate') {
        const copy = { ...j, id: db.nextJobId++, name: `${j.name} (copia)`, status: 'paused', next_fire: null, created_at: localNow(), total_cost_usd: 0, total_equivalent_usd: 0 };
        db.jobs.set(copy.id, copy); db.runs.set(copy.id, []);
        emitChange('job.changed', { job_id: copy.id });
        return this._jobView(copy);
      }
      emitChange('job.changed', { job_id: j.id });
      return this._jobView(j);
    },
    async jobRuns(jobId) { await this._ag(); return db.runs.get(Number(jobId)) ?? []; },
    async run(id) { await this._ag(); for (const arr of db.runs.values()) { const r = arr.find(x => x.id === Number(id)); if (r) return r; } return Promise.reject(new ApiError(404, { reason: 'El run no existe.' })); },
    async verdictRun(id, { agent, verdict, reason }) {
      const r = await this.run(id);
      if (r.status === 'running') return reject409('El run sigue en curso.');
      if (!['pass', 'fail'].includes(verdict)) return Promise.reject(new ApiError(400, { reason: 'verdict debe ser pass|fail' }));
      Object.assign(r, { verdict, verdict_reason: reason ?? null, verified_at: localNow() });
      emitChange('verify.done', { job_id: r.job_id, run_id: r.id });
      return r;
    },
    async verifyRuns() {
      await this._ag();
      let judged = 0;
      for (const [jobId, arr] of db.runs) {
        const j = db.jobs.get(jobId);
        for (const r of arr) if (r.status === 'ok' && !r.verdict && j?.verify_level !== 'none') {
          Object.assign(r, { verdict: 'pass', verdict_reason: '[mock] cumple el criterio.', verified_at: localNow() }); judged++;
        }
      }
      if (judged) emitChange('verify.done', {});
      return { swept: judged, pass: judged, fail: 0 };
    },
    async agentsSchedule(days = 7) {
      await this._ag();
      const from = todayISO();
      const to = new Date(Date.now() + days * 864e5).toISOString().slice(0, 10);
      const items = [];
      for (const j of db.jobs.values()) {
        if (j.status !== 'active' || !j.next_fire) continue;
        // proyección sencilla: next_fire y, para recurring/weekly, las siguientes dentro del rango
        let t = new Date(j.next_fire);
        const end = new Date(to + 'T23:59:59');
        const s = j.schedule;
        for (let n = 0; n < 60 && t <= end; n++) {
          items.push({ job_id: j.id, name: j.name, fire_at: `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}T${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}:00`, harness: j.harness, model: j.model, schedule: s });
          if (s.type === 'recurring' && s.at?.length > 1) {
            const idx = s.at.indexOf(`${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`);
            if (idx >= 0 && idx < s.at.length - 1) { const [h, m] = s.at[idx + 1].split(':').map(Number); t = new Date(t); t.setHours(h, m, 0, 0); continue; }
            const [h, m] = s.at[0].split(':').map(Number); t = new Date(t); t.setDate(t.getDate() + (s.every_days ?? 1)); t.setHours(h, m, 0, 0); continue;
          }
          if (s.type === 'recurring') { t = new Date(t); t.setDate(t.getDate() + (s.every_days ?? 1)); continue; }
          if (s.type === 'weekly') { t = new Date(t); do { t.setDate(t.getDate() + 1); } while (!(s.weekdays ?? []).includes((t.getDay() + 6) % 7)); continue; }
          break; // once / manual: una sola
        }
      }
      items.sort((a, b) => a.fire_at.localeCompare(b.fire_at));
      return { from, to, items };
    },
    async agentsResults() {
      await this._ag();
      return [...db.jobs.values()].filter(j => j.status !== 'archived').map(j => ({ job: this._jobView(j), last_run: (db.runs.get(j.id) ?? [])[0] ?? null }));
    },
    async agentsRunning() {
      await this._ag();
      const out = [];
      for (const j of db.jobs.values()) for (const r of db.runs.get(j.id) ?? []) if (r.status === 'running') out.push({ ...r, job: this._jobView(j) });
      return out;
    },
    async harnesses() { await this._ag(); return db.harnesses; },
    async harnessModels(name, refresh) {
      await this._ag();
      const h = db.harnesses.find(x => x.name === name);
      if (!h) return Promise.reject(new ApiError(404, { reason: `harness ${name} desconocido` }));
      if (refresh) h.models_refreshed_at = localNow();
      return { harness: name, models: h.models, families: h.families ?? null, source: h.models_source, refreshed_at: h.models_refreshed_at };
    },
    async harnessModelsRefresh(name) { return this.harnessModels(name, true); },
    async mcpInherited() { await this._ag(); return db.mcp; },
    /* T5.2 mock: fixtures/agents_calendar.json = GET /api/agents/calendar real del 4-sep */
    async agentsCalendar(from, to) {
      if (!db.agCal) db.agCal = await fetch('fixtures/agents_calendar.json').then(r => r.json());
      return { ...db.agCal, from, to, items: db.agCal.items.filter(i => i.date >= from && i.date <= to) };
    },
    /* T5.9 mock del registro: fixtures/mcp_servers.json (payload real del 4-sep) */
    async _reg() { if (!db.reg) db.reg = await fetch('fixtures/mcp_servers.json').then(r => r.json()); return db.reg; },
    async mcpServers(q) {
      const reg = await this._reg();
      const k = (q ?? '').toLowerCase();
      return k ? reg.filter(s => [s.name, s.command, s.url, s.note].filter(Boolean).some(v => String(v).toLowerCase().includes(k))) : reg;
    },
    async mcpImportLocal(agent) {
      const reg = await this._reg();
      const have = new Set(reg.map(s => s.name));
      let imported = 0;
      for (const m of db.mcp ?? []) {
        if (have.has(m.name)) continue;
        reg.push({ id: reg.length + 100, name: m.name, transport: m.transport ?? 'stdio', command: m.command ?? 'npx', args: m.args ?? [], url: m.url ?? null, env: {}, headers: {}, enabled: true, note: '', source: 'import', created_at: localNow(), updated_at: localNow() });
        imported++;
      }
      emitChange('mcp.changed', { imported });
      return { imported, updated: 0 };
    },
    async mcpPatch(id, { agent, ...fields }) {
      const reg = await this._reg();
      const s = reg.find(x => x.id === Number(id));
      if (!s) return Promise.reject(new ApiError(404, { reason: 'servidor no existe' }));
      Object.assign(s, fields, { updated_at: localNow() });
      emitChange('mcp.changed', { id: s.id });
      return s;
    },
    async mcpDelete(id) {
      const reg = await this._reg();
      const i = reg.findIndex(x => x.id === Number(id));
      if (i < 0) return Promise.reject(new ApiError(404, { reason: 'servidor no existe' }));
      reg.splice(i, 1);
      emitChange('mcp.changed', { id: Number(id), deleted: true });
      return { ok: true };
    },

    async verifySweep() {
      const done = [...db.tickets.values()].filter(t => t.status === 'done');
      let verified = 0, bounced = 0;
      for (const t of done) {
        if (t.evidence_type) {
          t.status = 'verified';
          db.history.get(t.id).push({ at: new Date().toISOString(), agent: 'verifier-bot', action: 'verify', note: 'pass [mock]', to: 'verified' });
          verified++;
        } else {
          t.status = 'in_progress'; bounced++;
          db.history.get(t.id).push({ at: new Date().toISOString(), agent: 'verifier-bot', action: 'reopen', note: 'fail: sin evidencia [mock]', to: 'in_progress' });
        }
      }
      emitChange('verify.done', {});
      return { swept: done.length, verified, bounced };
    },
  };
}

/* ================================================================
   Selección de modo
   ================================================================ */
let impl = null;

export async function connect() {
  try {
    const res = await fetch(BASE + '/api/health', {
      headers: TOKEN ? { 'X-FarOS-Token': TOKEN } : {},
      signal: AbortSignal.timeout(1500),
    });
    if (res.ok) { impl = live; startSSE(); return impl; }
  } catch { /* daemon no vive: fixtures */ }
  impl = makeMock();
  await impl.health();
  return impl;
}

export function api() {
  if (!impl) throw new Error('api no conectada: llama a connect() primero');
  return impl;
}
