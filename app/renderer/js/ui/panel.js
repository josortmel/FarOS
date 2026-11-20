/* ============================================================
   panel.js — detalle de una tarea (manual: el Tablero es de los
   agentes con nombre). Qué es, ficha del Plan, brazo automático si
   la vincula un job, tiempo, acciones, historial. La máquina vive en
   Agentes (agents.js). Las acciones del panel son el camino de
   teclado para las transiciones (mismo /transition que el drag&drop).
   ============================================================ */

import { TRANSITIONS, ACTION_LABEL, STATUS_LABEL, VLEVEL } from '../transitions.js';
import { fmtWhen, fmtDate, fmtWhenScheduled, esc, projectColor } from '../util.js';
import { t as tr, applyI18n } from '../i18n.js'; // #314
import { api } from '../api.js';
import { notaHTML, avisosDe } from './criterio_avisos.js'; // #1136

let handlers = null; // { onTransition(t, target, action), onOpenJob(jobId), onPatchTime(t, patch), onPatchFields(t, patch), onTiempoLibre(t, libre), onClose }

/* ---------- T2.3: ficha editable (el dueño: «no tengo capacidad de cambiar nada») ---------- */

const PRIORITIES = ['alta', 'media', 'baja'];

function renderEditSection(t, board) {
  const s = document.createElement('section');
  s.className = 'panel__section';
  const closed = t.status === 'rejected' || t.status === 'expired';
  const agents = [...new Set([...(board.agents ?? []), t.owner].filter(Boolean))];
  s.innerHTML = `
    <div class="panel__section-title">Editar</div>
    <form class="edit-form" id="edit-form">
      <div class="field">
        <label for="ed-title">Título</label>
        <input type="text" id="ed-title" required value="${esc(t.title)}">
      </div>
      <div class="field">
        <label for="ed-desc">Descripción</label>
        <textarea id="ed-desc" rows="3">${esc(t.description ?? '')}</textarea>
      </div>
      <div class="field-row-3">
        <div class="field">
          <label for="ed-prio">Prioridad</label>
          <select id="ed-prio">${PRIORITIES.map((p) => `<option value="${p}"${t.priority === p ? ' selected' : ''}>${p}</option>`).join('')}</select>
        </div>
        <div class="field">
          <label for="ed-owner">Responsable</label>
          <select id="ed-owner">${agents.map((a) => `<option value="${esc(a)}"${t.owner === a ? ' selected' : ''}>${esc(a)}</option>`).join('')}${t.owner ? '' : '<option value="" selected>—</option>'}</select>
        </div>
        <div class="field">
          <label for="ed-due">Vence</label>
          <input type="date" id="ed-due" value="${esc((t.due_at ?? '').slice(0, 10))}">
        </div>
      </div>
      <div class="field">
        <label for="ed-crit">Criterio de verificación</label>
        <textarea id="ed-crit" rows="2">${esc(t.verify_criteria ?? '')}</textarea>
        ${notaHTML(avisosDe(t.id), t.verify_criteria)}
      </div>
      ${
        !t.cadence_days && t.kind !== 'agentic'
          ? `
      <div class="field">
        <label for="ed-vlevel">Nivel de verificación</label>
        <select id="ed-vlevel">${Object.keys(VLEVEL)
          .map(
            (v) => `<option value="${v}"${t.verification_level === v ? ' selected' : ''}>${esc(VLEVEL[v][0])}</option>`,
          )
          .join('')}</select>
        <span class="hint" id="ed-vlevel-txt">${esc(VLEVEL[t.verification_level]?.[1] ?? '')}</span>
      </div>`
          : ''
      }
      <div class="time-form__actions">
        <button type="submit" class="btn btn--ghost btn--sm"${closed ? ' disabled' : ''}>Guardar cambios</button>
        <span class="hint">el motor valida; si no deja, lo dice</span>
      </div>
    </form>`;
  s.querySelector('#ed-vlevel')?.addEventListener('change', (e) => {
    s.querySelector('#ed-vlevel-txt').textContent = VLEVEL[e.target.value]?.[1] ?? '';
  });
  s.querySelector('#edit-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const q = (id) => s.querySelector(id);
    const patch = {};
    const put = (k, v, cur) => {
      if ((v ?? '') !== (cur ?? '')) patch[k] = v;
    }; // solo lo que cambió
    put('title', q('#ed-title').value.trim(), t.title);
    put('description', q('#ed-desc').value.trim(), t.description);
    put('priority', q('#ed-prio').value, t.priority);
    put('owner', q('#ed-owner').value, t.owner);
    put('verify_criteria', q('#ed-crit').value.trim(), t.verify_criteria);
    put('due_at', q('#ed-due').value, (t.due_at ?? '').slice(0, 10));
    /* #414: subir el nivel lo hace cualquiera; bajarlo a «propia», solo el dueño (el motor lo dice) */
    if (q('#ed-vlevel')) put('verification_level', q('#ed-vlevel').value, t.verification_level);
    if (!Object.keys(patch).length) return;
    handlers.onPatchFields(t, patch);
  });
  return s;
}

/* ---------- T2.10: sección «Tiempo» editable ---------- */

function renderTimeSection(t) {
  const s = document.createElement('section');
  s.className = 'panel__section';
  const recurring = Boolean(t.cadence_days);
  const closed = t.status === 'verified' || t.status === 'rejected';
  s.innerHTML = `
    <div class="panel__section-title">Tiempo</div>
    ${fmtWhenScheduled(t) ? `<div class="time__now">${recurring && !t.scheduled_at ? '↻' : '📅'} ${esc(fmtWhenScheduled(t))}</div>` : ''}
    <label class="libre">
      <input type="checkbox" class="libre__check" id="tm-libre" role="switch" disabled${closed ? ' data-cerrada="1"' : ''}>
      <span class="libre__txt"><span class="libre__name">Tiempo libre</span>
        <span class="hint">su timbre suena como invitación, no como aviso de trabajo</span></span>
    </label>
    <form class="time-form" id="time-form">
      <div class="field-row-3">
        <div class="field">
          <label for="tm-at">Programada</label>
          <input type="datetime-local" id="tm-at" value="${esc(t.scheduled_at ?? '')}">
        </div>
        <div class="field">
          <label for="tm-dur">Duración (min)</label>
          <input type="number" id="tm-dur" min="1" step="1" value="${t.duration_min ?? ''}">
        </div>
        <div class="field field--check">
          <label for="tm-all"><input type="checkbox" id="tm-all"${t.all_day ? ' checked' : ''}> Todo el día</label>
        </div>
      </div>
      ${
        recurring
          ? `
      <div class="field-row">
        <div class="field">
          <label for="tm-pref">Hora preferida</label>
          <input type="time" id="tm-pref" value="${esc(t.preferred_time ?? '')}">
          <div class="hint">${tr(`cada ${t.cadence_days} d`)} · ${tr('próxima')} ${t.next_due ? esc(fmtDate(t.next_due)) : '—'}</div>
        </div>
        <div class="field">
          <label for="tm-next">Mover la próxima a</label>
          <input type="date" id="tm-next" value="${esc(t.next_due_override ?? '')}">
          <div class="hint">solo la próxima; la cadencia no cambia</div>
        </div>
      </div>`
          : ''
      }
      <div class="time-form__actions">
        <button type="submit" class="btn btn--ghost btn--sm"${closed ? ' disabled' : ''}>Guardar tiempo</button>
        <span class="hint">vacío = sin programar</span>
      </div>
    </form>`;
  s.querySelector('#time-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const q = (id) => s.querySelector(id);
    const patch = {
      scheduled_at: q('#tm-at').value || '',
      duration_min: q('#tm-dur').value ? Number(q('#tm-dur').value) : '',
      all_day: q('#tm-all').checked ? 1 : 0,
    };
    if (recurring) {
      patch.preferred_time = q('#tm-pref').value || '';
      patch.next_due_override = q('#tm-next').value || '';
    }
    handlers.onPatchTime(t, patch);
  });
  pintarLibre(s, t);
  return s;
}

/* #1348: «Tiempo libre» se lee de los ajustes (timbre_tiempo_libre.tareas) y se aplica al tocarlo. Si el motor dice que
   no (400 timbre.solo_las_suyas), la casilla vuelve a como estaba y app.js lo dice en una frase. */
async function pintarLibre(s, t) {
  const box = s.querySelector('#tm-libre');
  let libre;
  try {
    const tareas = (await api().settings())?.timbre_tiempo_libre?.tareas ?? [];
    libre = tareas.map(Number).includes(Number(t.id));
  } catch {
    return;
  } // sin ajustes no se ofrece: mejor deshabilitada que mintiendo
  box.checked = libre;
  box.disabled = Boolean(box.dataset.cerrada);
  box.addEventListener('change', async () => {
    box.disabled = true;
    const ok = await handlers.onTiempoLibre?.(t, box.checked);
    if (!ok) box.checked = !box.checked;
    box.disabled = Boolean(box.dataset.cerrada);
  });
}

export function initPanel(h) {
  handlers = h;
  document.getElementById('panel-scrim').addEventListener('click', () => handlers.onClose());
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !document.getElementById('panel').hidden) handlers.onClose();
  });
}

let hideTimer = null; // cerrar y abrir en <240 ms dejaba el panel oculto con el velo puesto

export function closePanel() {
  const panel = document.getElementById('panel');
  const scrim = document.getElementById('panel-scrim');
  panel.classList.remove('is-open');
  scrim.hidden = true;
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => {
    panel.hidden = true;
  }, 240);
}

/* el único sitio que abre el panel (ticket o job): cancela el cierre pendiente */
export function showPanelShell() {
  const panel = document.getElementById('panel');
  const scrim = document.getElementById('panel-scrim');
  clearTimeout(hideTimer);
  panel.innerHTML = '';
  panel.hidden = false;
  scrim.hidden = false;
  requestAnimationFrame(() => panel.classList.add('is-open'));
  return panel;
}

/* ---------- T3.7: la ficha del Plan por secciones, no como blob ---------- */

const PLAN_SECTIONS = [
  ['objetivo', 'Objetivo'],
  ['archivos_a_tocar', 'Archivos'],
  ['accion', 'Acción'],
  ['pre', 'Pre'],
  ['post', 'Post'],
  ['tests', 'Tests'],
  ['criterio', 'Criterio'],
  ['rollback', 'Rollback'],
  ['depende_de', 'Depende de'],
];

function parsePlan(t) {
  if (!t.plan_data) return null;
  try {
    return typeof t.plan_data === 'string' ? JSON.parse(t.plan_data) : t.plan_data;
  } catch {
    return null;
  } // plan_data roto: se cae a la descripción plana, no a un error
}

function renderPlanSections(t, plan) {
  const s = document.createElement('section');
  s.className = 'panel__section plan';
  const facts = [
    plan.phase ? `${tr('fase')} <b>${esc(plan.phase)}</b>` : '',
    plan.estimacion ? `${tr('tamaño')} <b>${esc(plan.estimacion)}</b>` : '',
    plan.agente_sugerido ? `${tr('sugerido')} <b>${esc(plan.agente_sugerido)}</b>` : '',
    plan.verification_level
      ? `${tr('verificación')} <b>${esc(tr(VLEVEL[plan.verification_level]?.[0] ?? plan.verification_level))}</b>`
      : '',
  ]
    .filter(Boolean)
    .join(' · ');
  s.innerHTML = `<div class="panel__section-title">${tr('Ficha del Plan')}${plan.key ? ` · ${esc(plan.key)}` : ''}</div>
    ${facts ? `<div class="plan__facts">${facts}</div>` : ''}`;

  const unmet = new Map((t.unmet_deps ?? []).map((d) => [d.plan_key ?? `#${d.id}`, d]));
  const dl = document.createElement('dl');
  dl.className = 'plan__grid';
  for (const [field, label] of PLAN_SECTIONS) {
    const v = plan[field];
    if (v == null || v === '' || (Array.isArray(v) && !v.length)) continue;
    const dt = document.createElement('dt');
    dt.textContent = label;
    const dd = document.createElement('dd');
    if (field === 'archivos_a_tocar') {
      dd.innerHTML = `<ul class="plan__list plan__list--files">${[]
        .concat(v)
        .map((f) => `<li><code>${esc(f)}</code></li>`)
        .join('')}</ul>`;
    } else if (field === 'depende_de') {
      dd.innerHTML = `<ul class="plan__list plan__list--deps">${[]
        .concat(v)
        .map((k) => {
          const d = unmet.get(k);
          return d
            ? `<li class="plan__dep plan__dep--unmet"><span class="plan__dep-key">${esc(k)}</span> ${esc(STATUS_LABEL[d.status] ?? d.status)} — ${tr('espera')}</li>`
            : `<li class="plan__dep plan__dep--met"><span class="plan__dep-key">${esc(k)}</span> ✓</li>`;
        })
        .join('')}</ul>`;
    } else if (field === 'criterio') {
      dd.innerHTML = `<div class="panel__criteria">${esc(String(v))}</div>`;
    } else {
      dd.textContent = Array.isArray(v) ? v.join('\n') : String(v);
    }
    dl.append(dt, dd);
  }
  s.appendChild(dl);
  return s;
}

export async function openPanel(t, board) {
  const proj = board.projects.find((p) => p.id === t.project_id);
  const plan = parsePlan(t);
  const panel = showPanelShell();

  /* ---------- cabecera ---------- */
  const head = document.createElement('header');
  head.className = 'panel__head';

  const badges = [];
  if (t.due_state === 'overdue')
    badges.push(
      `<span class="badge badge--overdue">${tr('▲ vencida')}${t.due_at ? ' · ' + fmtDate(t.due_at) : ''}</span>`,
    );
  else if (t.due_state === 'due') badges.push(`<span class="badge badge--due">● hoy</span>`);
  if (t.status === 'blocked') badges.push(`<span class="badge badge--blocked">⛔ bloqueada</span>`);
  if (t.priority === 'alta') badges.push(`<span class="badge badge--prio">! alta</span>`);

  head.innerHTML = `
    <div class="panel__topline">
      <span class="card__project">
        <span class="card__project-dot" style="background:${esc(projectColor(proj))}"></span>${esc(proj?.name ?? '—')}
      </span>
      ${badges.join('')}
      <button class="panel__close" aria-label="Cerrar panel">✕</button>
    </div>
    <h2 class="panel__title">${esc(t.title)}</h2>
    ${t.description && !plan ? `<p class="panel__desc">${esc(t.description)}</p>` : ''}
    ${t.last_change ? `<div class="panel__change">→ ${esc(STATUS_LABEL[t.last_change.to] ?? t.last_change.to)} · ${fmtWhen(t.last_change.at)}${t.last_change.by ? ' · ' + esc(t.last_change.by) : ''}</div>` : ''}
    <div class="panel__facts">
      <span>estado <b>${STATUS_LABEL[t.status] ?? t.status}</b></span>
      <span>responsable <b>${esc(t.owner ?? '—')}</b></span>
      ${t.verification_level ? `<span title="${esc(VLEVEL[t.verification_level]?.[1] ?? '')}">verificación <b>${esc(VLEVEL[t.verification_level]?.[0] ?? t.verification_level)}</b></span>` : ''}
      <span>creada por <b>${esc(t.created_by ?? '—')}</b> · ${fmtWhen(t.created_at)}</span>
      ${t.cadence_days ? `<span>recurrente <b>${tr(`cada ${t.cadence_days} d`)}</b> · ${tr('última')} ${t.last_done_at ? fmtWhen(t.last_done_at) : tr('nunca')}</span>` : ''}
      ${t.verified_by ? `<span>verificada por <b>${esc(t.verified_by)}</b></span>` : ''}
      ${t.evidence_type ? `<span>evidencia <b>${esc(t.evidence_type)}</b>${t.evidence ? ' · ' + esc(String(t.evidence).slice(0, 60)) : ''}</span>` : ''}
    </div>`;
  head.querySelector('.panel__close').addEventListener('click', () => handlers.onClose());
  panel.appendChild(head);

  const body = document.createElement('div');
  body.className = 'panel__body';
  panel.appendChild(body);

  /* ---------- T3.7: ficha del Plan por secciones ---------- */
  if (plan) body.appendChild(renderPlanSections(t, plan));

  /* ---------- criterio de verificación (sin Plan: el del ticket) ---------- */
  if (t.verify_criteria && !(plan && plan.criterio)) {
    const s = document.createElement('section');
    s.className = 'panel__section';
    s.innerHTML = `
      <div class="panel__section-title">Criterio de verificación</div>
      <div class="panel__criteria">${esc(t.verify_criteria)}</div>`;
    body.appendChild(s);
  }

  /* ---------- acciones (camino de teclado) ---------- */
  const legal = TRANSITIONS[t.status] ?? {};
  const targets = Object.keys(legal);
  if (targets.length) {
    const s = document.createElement('section');
    s.className = 'panel__section';
    s.innerHTML = `<div class="panel__section-title">Mover</div>`;
    const row = document.createElement('div');
    row.className = 'panel__actions';
    for (const target of targets) {
      const action = legal[target];
      const recambio = action === 'block' && t.status === 'blocked'; // #482: cambiar el motivo, no bloquear otra vez
      const btn = document.createElement('button');
      btn.className =
        !recambio && (action === 'reject' || action === 'block' || action === 'rework')
          ? 'btn btn--danger'
          : 'btn btn--ghost';
      btn.textContent = recambio
        ? 'Cambiar motivo…'
        : action === 'rework'
          ? `${tr('Devolver a')} ${STATUS_LABEL[target] ?? target}…`
          : (ACTION_LABEL[action] ?? action);
      btn.addEventListener('click', () => handlers.onTransition(t, target, action));
      row.appendChild(btn);
    }
    s.appendChild(row);
    body.appendChild(s);
  }

  /* ---------- #481 (decisión 47 de Pepe): quedarse la verificación EN la tarea ----------
     Lo que antes se repartía en la sala («me la quedo yo») queda en la tarea: quién la tiene, y
     «Me la quedo» / «Soltar». El motor manda: si otro la coge a la vez, el segundo ve «ya la tiene X».
     No en las «self» (se sellan solas), ni en las recurrentes o agénticas, ni para el dueño de la tarea. */
  if (
    !t.closed_at &&
    t.verification_level &&
    t.verification_level !== 'self' &&
    !t.cadence_days &&
    t.kind !== 'agentic'
  ) {
    const { agent: me, owner: casa } = handlers.me?.() ?? {};
    const quien = t.verify_claimed_by;
    const s = document.createElement('section');
    s.className = 'panel__section panel__claim';
    s.innerHTML = `
      <div class="panel__section-title">${esc(tr('Verificación'))}</div>
      <p class="claim__who">${
        quien
          ? `${esc(tr('La verificación la tiene'))} <b>${esc(quien)}</b>${t.verify_claimed_at ? ' · ' + fmtWhen(t.verify_claimed_at) : ''}`
          : esc(tr('Nadie se ha quedado todavía esta verificación.'))
      }</p>`;
    const row = document.createElement('div');
    row.className = 'claim__actions'; // NO .panel__actions: la capa de móvil se lleva esa a su barra (mobile.js)
    const mk = (label, accion) => {
      const b = document.createElement('button');
      b.className = 'btn btn--ghost';
      b.dataset.claim = accion;
      b.textContent = tr(label);
      b.addEventListener('click', () => handlers.onClaim?.(t, accion));
      row.appendChild(b);
    };
    if (!quien && me && me !== t.owner) mk('Me la quedo', 'claim');
    if (quien && (quien === me || me === casa)) mk('Soltar', 'unclaim');
    if (row.children.length) s.appendChild(row);
    body.appendChild(s);
  }

  /* ---------- T1.14: brazo automático (manual vinculada a un job) ---------- */
  if (t.linked_job) {
    const lj = t.linked_job;
    const lr = lj.last_run;
    const s = document.createElement('section');
    s.className = 'panel__section';
    s.innerHTML = `
      <div class="panel__section-title">Brazo automático</div>
      <div class="arm">
        <div class="arm__name">${esc(lj.name)} <span class="jobstate jobstate--${esc(lj.status ?? 'active')}">${lj.status === 'paused' ? 'pausado' : lj.status === 'archived' ? 'archivado' : 'activo'}</span></div>
        <div class="arm__run">${
          lr
            ? `run ${lr.id} · ${esc(lr.status)}${lr.finished_at ? ' · ' + fmtWhen(lr.finished_at) : ''}${lr.verdict ? ` · <b class="arm__verdict--${lr.verdict}">${esc(tr({ pass: 'pasa', fail: 'falla', uncertain: 'incierto' }[lr.verdict] ?? lr.verdict))}</b>` : ''}`
            : 'sin runs todavía'
        }</div>
        <div class="hint">run ok + veredicto «pasa» → esta tarea se completa sola con evidencia run_output (T1.10)</div>
      </div>`;
    const go = document.createElement('button');
    go.className = 'btn btn--ghost btn--sm';
    go.textContent = 'Ver el job en Agentes →';
    go.addEventListener('click', () => handlers.onOpenJob?.(lj.id));
    s.querySelector('.arm').appendChild(go);
    body.appendChild(s);
  }

  /* ---------- T2.3: ficha editable ---------- */
  body.appendChild(renderEditSection(t, board));

  /* ---------- T2.10: tiempo ---------- */
  body.appendChild(renderTimeSection(t));

  /* ---------- historial ---------- */
  const hs = document.createElement('section');
  hs.className = 'panel__section';
  hs.innerHTML = `<div class="panel__section-title">Historial</div>`;
  const list = document.createElement('ul');
  list.className = 'hist';
  list.innerHTML = '<li>cargando…</li>';
  hs.appendChild(list);
  body.appendChild(hs);
  /* #314: el panel se pinta DESPUÉS del recorrido de applyI18n (la clase del #291), y
     con la interfaz en inglés salía entero en español. Se pasa aquí, y otra vez sobre
     el historial, que llega más tarde. */
  applyI18n(panel);

  api()
    .history(t.id)
    .then((payload) => {
      // shape canónico del daemon: {ticket, history:[{old_status,new_status,changed_by,changed_at,note}]}
      const items = Array.isArray(payload) ? payload : (payload?.history ?? []);
      list.innerHTML = '';
      if (!items.length) {
        list.innerHTML = '<li>—</li>';
        return;
      }
      for (const h of [...items].reverse()) {
        const li = document.createElement('li');
        const when = h.changed_at ?? h.at ?? h.timestamp ?? h.created_at;
        const agent = h.changed_by ?? h.agent ?? h.by ?? '';
        const to = h.new_status ?? h.to;
        const from = h.old_status ?? h.from;
        const move = to
          ? `${from ? esc(STATUS_LABEL[from] ?? from) + ' → ' : ''}${esc(STATUS_LABEL[to] ?? to)}`
          : esc(h.action ?? h.event ?? '');
        /* #644: el daemon pega «[en nombre de <dueño>]» a la nota (#640). Se saca de la nota y se
           enseña como etiqueta propia, traducida, para que no salga en español con la app en inglés */
        const dueno = /\s*\[en nombre de ([^\]]+)\]\s*/.exec(h.note ?? '');
        const note = dueno ? h.note.replace(dueno[0], ' ').trim() : h.note;
        const marca = dueno ? ` <span class="prov prov--dueno">${esc(tr('en nombre de'))} ${esc(dueno[1])}</span>` : '';
        li.innerHTML = `<time>${fmtWhen(when)}</time><span><b>${esc(agent)}</b> ${move}${provChip(h.provenance)}${marca}${note ? ' — ' + esc(note) : ''}</span>`;
        list.appendChild(li);
      }
    })
    .catch(() => {
      list.innerHTML = '<li>(historial no disponible)</li>';
    })
    .finally(() => applyI18n(list));
}

/* #377 (decisión 21): la procedencia de cada verificación, que el daemon guarda en la fila del
   historial. Las clases se distinguen por la FORMA y no solo por el color: una persona (el dueño
   de la casa, `external`), rellena; un modelo de otra familia (`external:<modelo>`, #465),
   rellena en cálido con el modelo como dato; misma familia (un Claude), con contorno;
   autoverificada (el responsable sellándose lo suyo), contorno discontinuo; una herramienta,
   monoespaciada. Los nombres van entre comillas angulares, que i18n deja intactas.
   «externa» no se usa como etiqueta: desde #465 es también un NIVEL (DeepSeek), y llamar igual a
   Pepe y a un modelo es justo lo que esta etiqueta tiene que separar. Una clase que aún no
   conozco se enseña tal cual. */
const PROV = {
  external: ['prov--ext', 'persona', 'Verificada por una persona: fuera de la familia de modelos'],
  'same-family': ['prov--fam', 'misma familia', 'Verificada por otro Claude de la casa'],
  'same-family:self': ['prov--self', 'autoverificada', 'Sellada por su propio responsable, en una tarea de nivel self'],
};
function provChip(p) {
  if (!p) return '';
  if (p.startsWith('deterministic:')) {
    return ` <span class="prov prov--det" title="Comprobada por una herramienta, sin modelo">herramienta «${esc(p.slice(14))}»</span>`;
  }
  if (p.startsWith('external:')) {
    return ` <span class="prov prov--mod" title="Verificada por un modelo de otra familia">otro modelo «${esc(p.slice(9))}»</span>`;
  }
  const d = PROV[p];
  if (!d) return ` <span class="prov">${esc(p)}</span>`;
  return ` <span class="prov ${d[0]}" title="${d[2]}">${d[1]}</span>`;
}
