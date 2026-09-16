/* ============================================================
   modal.js — diálogos: crear tarea (manual), evidencia, motivo,
   proyecto, despacho, handoff. Todos devuelven Promise (null si
   cancela). T1.14: las tareas agénticas ya no se crean aquí — los
   jobs viven en Agentes (job_modal.js). Tablero = agentes con nombre.
   ============================================================ */

import { esc } from '../util.js';
import { fechaDiaMes, applyI18n } from '../i18n.js';

const root = () => document.getElementById('modal-root');

function openShell(html, { small = false } = {}) {
  const r = root();
  r.hidden = false;
  r.innerHTML = `<div class="modal${small ? ' modal--sm' : ''}" role="dialog" aria-modal="true">${html}</div>`;
  const modal = r.querySelector('.modal');
  /* #302 (lo encontro Eco barriendo el .exe): un modal se pinta AL ABRIRLO, o sea
     despues de que applyI18n haya recorrido el documento, asi que NINGUN modal se
     traducia. Con la interfaz en ingles, «Nuevo job agentico» salia con sus 35
     etiquetas en espaniol.
     Va aqui porque por este openShell pasan CINCO modales (crear tarea, evidencia,
     motivo, proyecto, despachar) y arreglarlos uno a uno seria repetir el mismo
     olvido cinco veces.
     CUIDADO: NO es el unico openShell de la app. job_modal.js tiene el suyo y
     necesita su propia llamada. Yo escribi aqui que era el unico sin comprobarlo,
     y el modal mas grande —35 etiquetas— se quedo sin traducir.
     (Yo ya le puse applyI18n a la CAJA DE ERRORES de este mismo modal esta maniana
     — arregle el sintoma que tenia delante y no mire el contenedor.) */
  applyI18n(modal);
  const first = modal.querySelector('input, select, textarea, button');
  first?.focus();
  return modal;
}

function close() {
  const r = root();
  r.hidden = true;
  r.innerHTML = '';
}

function wireCancel(modal, resolve) {
  modal.querySelector('[data-cancel]')?.addEventListener('click', () => { close(); resolve(null); });
  root().addEventListener('click', function onScrim(e) {
    if (e.target === root()) { close(); resolve(null); root().removeEventListener('click', onScrim); }
  });
  document.addEventListener('keydown', function onEsc(e) {
    if (e.key === 'Escape') { close(); resolve(null); document.removeEventListener('keydown', onEsc); }
  });
}

/* ================= crear tarea ================= */

/* v3.2 T2.1: el próximo día laborable, que es el ancla que usa el motor al crear */
function nextWorkday(from = new Date()) {
  const d = new Date(from);
  d.setDate(d.getDate() + 1);                                   // mañana…
  while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() + 1);  // …y si cae en finde, al lunes
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/* Un error del motor, bajo SU campo. Si no dice cuál, arriba del todo y visible. */
function showFieldError(modal, field, message) {
  modal.querySelectorAll('.field__error').forEach(e => e.remove());
  modal.querySelectorAll('.field.is-bad').forEach(e => e.classList.remove('is-bad'));
  const input = field ? modal.querySelector(`[data-field="${CSS.escape(field)}"]`) : null;
  const box = document.createElement('p');
  box.className = 'field__error';
  box.setAttribute('role', 'alert');
  box.textContent = message;
  if (input) {
    const wrap = input.closest('.field') ?? input.parentElement;
    wrap.classList.add('is-bad');
    wrap.appendChild(box);
    input.setAttribute('aria-invalid', 'true');
    input.focus();
  } else {
    box.classList.add('field__error--general');
    modal.querySelector('form').prepend(box);
    box.scrollIntoView({ block: 'nearest' });
  }
}

export function createTaskModal(meta, projects, agents, onSubmit) {
  return new Promise((resolve) => {
    const projOpts = projects.filter(p => !p.archived)
      .map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('');
    const vlevels = (meta.verification_levels ?? ['self', 'peer', 'auto'])
      .map(v => `<option value="${esc(v)}"${v === (meta.defaults.verification_level ?? 'auto') ? ' selected' : ''}>${esc(v)}</option>`).join('');
    const agentOpts = ['', ...(agents ?? [])]
      .map(a => `<option value="${esc(a)}">${a ? esc(a) : 'sin responsable'}</option>`).join('');

    const modal = openShell(`
      <h2 class="modal__title">Nueva tarea</h2>
      <p class="hint" style="margin:-0.6rem 0 var(--sp-3)">Tarea para un agente con nombre. Lo automatizado con LLM se crea en <b>Agentes → Nuevo job</b>.</p>

      <form id="task-form">
        <div class="field">
          <label for="f-title">Título</label>
          <input type="text" id="f-title" data-field="title" required maxlength="200" autocomplete="off">
        </div>
        <div class="field">
          <label for="f-desc">Descripción</label>
          <textarea id="f-desc" rows="2"></textarea>
        </div>
        <div class="field-row">
          <div class="field">
            <label for="f-project">Proyecto</label>
            <select id="f-project" data-field="project_id">${projOpts}</select>
          </div>
          <div class="field">
            <label for="f-prio">Prioridad</label>
            <select id="f-prio" data-field="priority">
              <option value="baja">baja</option>
              <option value="media" selected>media</option>
              <option value="alta">alta</option>
            </select>
          </div>
        </div>

        <!-- T2.1 (el dueño: «todas las tareas deberían tener una hora»): CUÁNDO SE HACE es
             ahora un bloque con nombre propio, separado de la fecha límite. Antes había
             una sola «Fecha objetivo» y la tarea nacía sin sitio en el día. -->
        <fieldset class="field-group">
          <legend>Cuándo se hace</legend>
          <div class="field-row-3">
            <div class="field">
              <label for="f-when">Día</label>
              <input type="date" id="f-when" data-field="scheduled_at" value="${nextWorkday()}">
              <!-- el formato de un input[type=date] lo pinta el idioma del navegador y
                   «09/08/2026» es ambiguo. La fecha en palabras no depende de eso. -->
              <div class="hint" id="f-when-txt"></div>
            </div>
            <div class="field">
              <label for="f-time">Hora</label>
              <input type="time" id="f-time" value="14:00">
            </div>
            <div class="field">
              <label for="f-dur">Duración (min)</label>
              <input type="number" id="f-dur" data-field="duration_min" value="60" min="5" step="5">
            </div>
          </div>
          <label class="field__check"><input type="checkbox" id="f-allday"> todo el día (sin hora concreta)</label>
        </fieldset>

        <div class="field-row">
          <div class="field">
            <label for="f-due">Fecha límite</label>
            <input type="date" id="f-due" data-field="due_at">
            <div class="hint">opcional: cuándo deja de valer, no cuándo se hace</div>
          </div>
          <div class="field">
            <label for="f-owner">Responsable</label>
            <select id="f-owner" data-field="owner">${agentOpts}</select>
          </div>
        </div>
        <div class="field">
          <label for="f-vlevel">Nivel de verificación</label>
          <select id="f-vlevel" data-field="verification_level">${vlevels}</select>
        </div>
        <div class="field">
          <label for="f-criteria">Criterio de verificación</label>
          <input type="text" id="f-criteria" placeholder="qué prueba deja esta tarea cuando está bien hecha">
          <div class="hint">el verificador se corre contra esto</div>
        </div>

        <div class="modal__footer">
          <button type="button" class="btn btn--ghost" data-cancel>Cancelar</button>
          <button type="submit" class="btn btn--primary">Crear tarea</button>
        </div>
      </form>
    `);

    wireCancel(modal, resolve);

    const allday = modal.querySelector('#f-allday');
    const timeF = modal.querySelector('#f-time');
    const durF = modal.querySelector('#f-dur');
    const whenF = modal.querySelector('#f-when');
    const whenTxt = modal.querySelector('#f-when-txt');
    /* #262: fecha entera a Intl — el «de» del espanol no existe en ingles */
    const pintaDia = () => {
      if (!whenF.value) { whenTxt.textContent = 'sin día: lo pone el motor'; return; }
      const [y, m, d] = whenF.value.split('-').map(Number);
      const dt = new Date(y, m - 1, d);
      whenTxt.textContent = fechaDiaMes(dt);
    };
    whenF.addEventListener('input', pintaDia);
    pintaDia();
    allday.addEventListener('change', () => {
      timeF.disabled = allday.checked;
      durF.disabled = allday.checked;
    });

    modal.querySelector('#task-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const v = (id) => modal.querySelector(id).value.trim();
      const day = v('#f-when');

      /* T2.1: lo vacío NO se manda. Un `description: null` hace que el motor conteste
         500 (comprobado el 7-sep contra el daemon desechable), y en general un campo
         opcional sin valor es un campo que no existe, no un nulo que viaja. */
      const ticket = {
        title: v('#f-title'),
        priority: v('#f-prio'),
        verification_level: v('#f-vlevel'),
        kind: 'manual', // T1.14: el Tablero solo crea manuales
      };
      for (const [key, val] of [['description', v('#f-desc')], ['due_at', v('#f-due')],
                                ['owner', v('#f-owner')], ['verify_criteria', v('#f-criteria')]]) {
        if (val) ticket[key] = val;
      }
      /* T2.1: un select vacío daba `project_id: 0` y el motor contestaba «proyecto #0 no
         existe». Lo que no hay no se manda: el motor pone su proyecto por defecto. */
      const proj = Number(v('#f-project'));
      if (proj > 0) ticket.project_id = proj;

      /* T2.1: la tarea nace CON SITIO en el día. Sin día no se manda nada y el motor
         aplica su propio ancla; con día, hora concreta o «todo el día». */
      if (day) {
        ticket.scheduled_at = allday.checked ? `${day}T00:00` : `${day}T${v('#f-time') || '14:00'}`;
        if (allday.checked) ticket.all_day = true;
        else ticket.duration_min = Number(v('#f-dur')) || 60;
      }

      /* Sin onSubmit se comporta como antes (resolver el ticket y cerrar): así los
         llamantes viejos y los tests de fixtures siguen funcionando. */
      if (typeof onSubmit !== 'function') { close(); resolve(ticket); return; }

      const btn = modal.querySelector('#task-form button[type=submit]');
      const label = btn.textContent;
      btn.disabled = true; btn.textContent = 'Creando…';
      try {
        const created = await onSubmit(ticket);
        close();
        resolve(created ?? ticket);
      } catch (err) {
        /* el dueño: «si falta un campo debería decírmelo en el propio menú, no sacarme de
           la creación y obligarme a volver a crearlo». El formulario NO se toca. */
        showFieldError(modal, err?.field ?? null, err?.reason ?? err?.message ?? String(err));
      } finally {
        btn.disabled = false; btn.textContent = label;
      }
    });
  });
}

/* ================= evidencia (completar) ================= */

export function evidenceDialog(meta, ticket) {
  return new Promise((resolve) => {
    const types = (meta.evidence_types ?? ['file_path', 'command_output', 'memory_id', 'peer_attestation'])
      .filter(t => t !== 'run_output') // run_output lo pone el runner, no la mano
      .map(t => `<option value="${esc(t)}">${esc(t)}</option>`).join('');

    const modal = openShell(`
      <h2 class="modal__title">Completar «${esc(ticket.title)}»</h2>
      <p style="font-size:var(--text-small);color:var(--color-text-soft);margin-bottom:var(--sp-3);">
        Completar exige evidencia. Sin evidencia el motor no acepta «hecha».
      </p>
      ${ticket.verify_criteria ? `<div class="panel__criteria" style="margin-bottom:var(--sp-3);">Criterio: ${esc(ticket.verify_criteria)}</div>` : ''}
      <form id="ev-form">
        <div class="field">
          <label for="ev-type">Tipo de evidencia</label>
          <select id="ev-type">${types}</select>
        </div>
        <div class="field">
          <label for="ev-value">Evidencia</label>
          <input type="text" id="ev-value" required placeholder="ruta, salida de comando, id…">
        </div>
        <div class="field">
          <label for="ev-note">Nota</label>
          <input type="text" id="ev-note" placeholder="opcional">
        </div>
        <div class="modal__footer">
          <button type="button" class="btn btn--ghost" data-cancel>Cancelar</button>
          <button type="submit" class="btn btn--primary">Completar</button>
        </div>
      </form>
    `, { small: true });

    wireCancel(modal, resolve);
    modal.querySelector('#ev-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const out = {
        evidence_type: modal.querySelector('#ev-type').value,
        evidence: modal.querySelector('#ev-value').value.trim(),
        note: modal.querySelector('#ev-note').value.trim() || null,
      };
      close();
      resolve(out);
    });
  });
}

/* ================= motivo (bloquear / rechazar) ================= */

export function reasonDialog(title, cta, { min = 1, hint = 'por qué — quedará en el historial' } = {}) {
  return new Promise((resolve) => {
    const modal = openShell(`
      <h2 class="modal__title">${esc(title)}</h2>
      <form id="rs-form">
        <div class="field">
          <label for="rs-note">Motivo${min > 1 ? ` <span class="hint">(mínimo ${min} caracteres)</span>` : ''}</label>
          <textarea id="rs-note" required rows="3" placeholder="${esc(hint)}"></textarea>
        </div>
        <div class="modal__footer">
          <button type="button" class="btn btn--ghost" data-cancel>Cancelar</button>
          <button type="submit" class="btn btn--danger" disabled>${esc(cta)}</button>
        </div>
      </form>
    `, { small: true });

    const ta = modal.querySelector('#rs-note');
    const ok = modal.querySelector('button[type=submit]');
    ta.addEventListener('input', () => { ok.disabled = ta.value.trim().length < min; }); // sin motivo no se envía
    wireCancel(modal, resolve);
    modal.querySelector('#rs-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const note = ta.value.trim();
      if (note.length < min) return;
      close();
      resolve({ note });
    });
  });
}

/* ================= nuevo proyecto ================= */

/* crear (sin argumento) o editar (T4.8: con el proyecto): nombre, color y
   archivar. Resuelve {name, color} · {archived: 1} · null si cancela. */
export function projectDialog(project = null) {
  const editing = Boolean(project);
  return new Promise((resolve) => {
    const modal = openShell(`
      <h2 class="modal__title">${editing ? 'Editar proyecto' : 'Nuevo proyecto'}</h2>
      <form id="pj-form">
        <div class="field">
          <label for="pj-name">Nombre</label>
          <input type="text" id="pj-name" required maxlength="80" autocomplete="off" value="${editing ? esc(project.name) : ''}">
        </div>
        <div class="field">
          <label for="pj-color">Color</label>
          <input type="color" id="pj-color" value="${editing && /^#[0-9a-f]{6}$/i.test(project.color ?? '') ? esc(project.color) : '#e8590c'}" style="width:4rem;height:2.4rem;padding:2px;border:1px solid var(--color-rule-strong);border-radius:6px;background:var(--color-card);">
          ${editing && project.color && !/^#[0-9a-f]{6}$/i.test(project.color) ? `<div class="hint">color actual: ${esc(project.color)} (no es hex; se sustituye si guardas)</div>` : ''}
        </div>
        <div class="modal__footer">
          ${editing ? '<button type="button" class="btn btn--danger" data-archive style="margin-right:auto">Archivar proyecto</button>' : ''}
          <button type="button" class="btn btn--ghost" data-cancel>Cancelar</button>
          <button type="submit" class="btn btn--primary">${editing ? 'Guardar' : 'Crear proyecto'}</button>
        </div>
      </form>
    `, { small: true });

    wireCancel(modal, resolve);
    if (editing) {
      modal.querySelector('[data-archive]').addEventListener('click', () => {
        close();
        resolve({ archived: 1 });
      });
    }
    modal.querySelector('#pj-form').addEventListener('submit', (e) => {
      e.preventDefault();
      close();
      resolve({
        name: modal.querySelector('#pj-name').value.trim(),
        color: modal.querySelector('#pj-color').value,
      });
    });
  });
}

/* ================= despachar tarea de workflow ================= */

export function dispatchDialog(t, agents, suggested) {
  return new Promise((resolve) => {
    const opts = agents
      .map(a => `<option value="${esc(a)}"${a === suggested ? ' selected' : ''}>${esc(a)}</option>`).join('');
    const modal = openShell(`
      <h2 class="modal__title">Despachar «${esc(t.plan_key ?? t.title)}»</h2>
      <form id="dp-form">
        <div class="field">
          <label for="dp-to">A quién</label>
          <select id="dp-to">${opts}</select>
          ${suggested ? `<div class="hint">el Plan sugiere ${esc(suggested)}</div>` : ''}
        </div>
        <div class="field">
          <label for="dp-note">Nota del lote</label>
          <input type="text" id="dp-note" placeholder="opcional — queda en el historial">
        </div>
        <p class="hint" style="margin-bottom:var(--sp-3);">El aviso al peer va por relay — FarOS no notifica.</p>
        <div class="modal__footer">
          <button type="button" class="btn btn--ghost" data-cancel>Cancelar</button>
          <button type="submit" class="btn btn--primary">Despachar</button>
        </div>
      </form>
    `, { small: true });

    wireCancel(modal, resolve);
    modal.querySelector('#dp-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const out = {
        to: modal.querySelector('#dp-to').value,
        note: modal.querySelector('#dp-note').value.trim() || null,
      };
      close();
      resolve(out);
    });
  });
}

/* ================= composer de handoff ================= */

export function handoffComposer(draftContent, workflowName) {
  return new Promise((resolve) => {
    const modal = openShell(`
      <h2 class="modal__title">Handoff — ${esc(workflowName)}</h2>
      <p class="hint" style="margin-bottom:var(--sp-3);">
        El borrador lo redacta el motor desde el estado real. Edita la sección
        [Editar: …] con lo que el estado no sabe: decisiones, bloqueos, preguntas.
      </p>
      <form id="ho-form">
        <div class="field">
          <textarea id="ho-content" class="mono" rows="18" required></textarea>
        </div>
        <div class="modal__footer">
          <button type="button" class="btn btn--ghost" data-cancel>Cancelar</button>
          <button type="submit" class="btn btn--primary">Guardar handoff</button>
        </div>
      </form>
    `);
    modal.querySelector('#ho-content').value = draftContent ?? '';

    wireCancel(modal, resolve);
    modal.querySelector('#ho-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const content = modal.querySelector('#ho-content').value.trim();
      if (!content) return;
      close();
      resolve({ content });
    });
  });
}

/* ================= resultado del sweep ================= */

export function sweepResultToastText(r) {
  if (!r) return 'Verificación lanzada.';
  if (r.swept === 0) return 'No hay tareas en la cola de verificación.';
  return `Verificadas ${r.verified} de ${r.swept} · rebotadas ${r.bounced}.`;
}
