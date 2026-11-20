/* ============================================================
   modal.js — diálogos: crear tarea (manual), evidencia, motivo,
   proyecto, despacho, handoff. Todos devuelven Promise (null si
   cancela). T1.14: las tareas agénticas ya no se crean aquí — los
   jobs viven en Agentes (job_modal.js). Tablero = agentes con nombre.
   ============================================================ */

import { esc } from '../util.js';
import { fechaDiaMes, applyI18n, t as tr } from '../i18n.js';
import { VLEVEL } from '../transitions.js'; // #574
import { openModal } from './shell.js'; // #357
import { notaHTML } from './criterio_avisos.js'; // #1136

/* #357: openShell, close y wireCancel viven en shell.js (una sola pieza para todas las ventanas,
   también la de job_modal.js). La historia del #302 está allí. */

/* ================= crear tarea ================= */

/* v3.2 T2.1: el próximo día laborable, que es el ancla que usa el motor al crear */
function nextWorkday(from = new Date()) {
  const d = new Date(from);
  d.setDate(d.getDate() + 1); // mañana…
  while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() + 1); // …y si cae en finde, al lunes
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/* Un error del motor, bajo SU campo. Si no dice cuál, arriba del todo y visible. */
function showFieldError(modal, field, message) {
  modal.querySelectorAll('.field__error').forEach((e) => e.remove());
  modal.querySelectorAll('.field.is-bad').forEach((e) => e.classList.remove('is-bad'));
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

export function createTaskModal(meta, projects, agents, onSubmit, onPatch) {
  return new Promise((resolve0) => {
    /* #1136: una vez creada, cerrar de cualquier forma (Escape, fuera, «Dejarlo así») devuelve
       la tarea creada y no «cancelado»: la tarea existe aunque el criterio lleve una nota */
    let guardada = null;
    const resolve = (v) => resolve0(v ?? guardada);
    const projOpts = projects
      .filter((p) => !p.archived)
      .map((p) => `<option value="${p.id}">${esc(p.name)}</option>`)
      .join('');
    // #574: el nombre del nivel, no su código (el value sigue siendo el código)
    const vlevels = (meta.verification_levels ?? ['self', 'peer', 'auto'])
      .map(
        (v) =>
          `<option value="${esc(v)}"${v === (meta.defaults.verification_level ?? 'auto') ? ' selected' : ''}>${esc(VLEVEL[v]?.[0] ?? v)}</option>`,
      )
      .join('');
    const agentOpts = ['', ...(agents ?? [])]
      .map((a) => `<option value="${esc(a)}">${a ? esc(a) : 'sin responsable'}</option>`)
      .join('');

    const modal = openModal(
      `
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
          <div class="hint" id="f-vlevel-txt"></div>
        </div>
        <div class="field">
          <label for="f-criteria">Criterio de verificación</label>
          <input type="text" id="f-criteria" data-field="verify_criteria" placeholder="qué prueba deja esta tarea cuando está bien hecha">
          <div class="hint">el verificador se corre contra esto</div>
          <div id="f-crit-note"></div>
        </div>

        <div class="modal__footer">
          <button type="button" class="btn btn--ghost" data-cancel>Cancelar</button>
          <button type="submit" class="btn btn--primary">Crear tarea</button>
        </div>
      </form>
    `,
      resolve,
    );

    const allday = modal.querySelector('#f-allday');
    const timeF = modal.querySelector('#f-time');
    const durF = modal.querySelector('#f-dur');
    const whenF = modal.querySelector('#f-when');
    const whenTxt = modal.querySelector('#f-when-txt');
    /* #262: fecha entera a Intl — el «de» del espanol no existe en ingles */
    const pintaDia = () => {
      if (!whenF.value) {
        whenTxt.textContent = 'sin día: lo pone el motor';
        return;
      }
      const [y, m, d] = whenF.value.split('-').map(Number);
      const dt = new Date(y, m - 1, d);
      whenTxt.textContent = fechaDiaMes(dt);
    };
    whenF.addEventListener('input', pintaDia);
    pintaDia();
    /* #574: debajo del nivel, qué pasa con él (tr: se escribe después del recorrido de i18n) */
    const vlevelF = modal.querySelector('#f-vlevel');
    const vlevelTxt = modal.querySelector('#f-vlevel-txt');
    const pintaNivel = () => {
      vlevelTxt.textContent = tr(VLEVEL[vlevelF.value]?.[1] ?? '');
    };
    vlevelF.addEventListener('change', pintaNivel);
    pintaNivel();
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
      for (const [key, val] of [
        ['description', v('#f-desc')],
        ['due_at', v('#f-due')],
        ['owner', v('#f-owner')],
        ['verify_criteria', v('#f-criteria')],
      ]) {
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
      if (typeof onSubmit !== 'function') {
        modal.finish(ticket);
        return;
      }

      const btn = modal.querySelector('#task-form button[type=submit]');
      if (guardada) {
        await guardarCriterio(btn);
        return;
      } // #1136: segunda vuelta, la tarea ya existe
      const label = btn.textContent;
      btn.disabled = true;
      btn.textContent = 'Creando…';
      try {
        const created = await onSubmit(ticket);
        const avisos = created?.criterion_warnings ?? [];
        if (!avisos.length || typeof onPatch !== 'function') {
          modal.finish(created ?? ticket);
          return;
        }
        guardada = created;
        modoNota(avisos);
        return;
      } catch (err) {
        /* el dueño: «si falta un campo debería decírmelo en el propio menú, no sacarme de
           la creación y obligarme a volver a crearlo». El formulario NO se toca. */
        showFieldError(modal, err?.field ?? null, err?.reason ?? err?.message ?? String(err));
      } finally {
        btn.disabled = false;
        if (!guardada) btn.textContent = label; // en modo nota el botón ya dice otra cosa
      }
    });

    /* #1136: la tarea se ha creado y el motor deja una nota sobre el criterio. El diálogo NO
       se cierra: lo ya guardado se queda quieto (deshabilitado) y solo el criterio y su nivel
       se pueden tocar. «Guardar el criterio» manda un PATCH; «Dejarlo así» cierra. */
    function pintarNota(avisos) {
      const caja = modal.querySelector('#f-crit-note');
      caja.innerHTML = notaHTML(avisos, modal.querySelector('#f-criteria').value, { guardada: true });
      caja.firstElementChild?.scrollIntoView({ block: 'nearest' }); // en escritorio quedaba cortada al fondo del diálogo
    }
    function modoNota(avisos) {
      modal.querySelector('#task-form').classList.add('is-saved');
      modal.querySelectorAll('#task-form input, #task-form select, #task-form textarea').forEach((el) => {
        if (el.id !== 'f-criteria' && el.id !== 'f-vlevel') el.disabled = true;
      });
      pintarNota(avisos);
      const ok = modal.querySelector('#task-form button[type=submit]');
      ok.textContent = tr('Guardar el criterio');
      const cancel = modal.querySelector('#task-form [data-cancel]');
      cancel.textContent = tr('Dejarlo así'); // y cerrar devuelve la tarea (resolve de arriba)
      modal.querySelector('#f-criteria').focus();
    }
    async function guardarCriterio(btn) {
      const tk = guardada.ticket ?? guardada;
      const criterio = modal.querySelector('#f-criteria').value.trim();
      const patch = { verify_criteria: criterio, verification_level: modal.querySelector('#f-vlevel').value };
      btn.disabled = true;
      btn.textContent = tr('Guardando…');
      try {
        const res = await onPatch(tk.id, patch);
        const avisos = res?.criterion_warnings ?? [];
        guardada = { ...guardada, criterion_warnings: avisos };
        if (!avisos.length) {
          modal.finish(guardada);
          return;
        }
        pintarNota(avisos);
      } catch (err) {
        showFieldError(modal, err?.field ?? 'verify_criteria', err?.reason ?? err?.message ?? String(err));
      } finally {
        btn.disabled = false;
        btn.textContent = tr('Guardar el criterio');
      }
    }
  });
}

/* ================= evidencia (completar) ================= */

/* #601: los tipos de evidencia por su nombre (salían file_path, command_output…) */
const EV_NAMES = {
  file_path: 'ruta de un fichero',
  command_output: 'salida de un comando',
  memory_id: 'memoria de EcoDB',
  peer_attestation: 'lo da por bueno otro miembro',
  proof: 'prueba que corre el verificador',
};
/* #601: lo escrito se guarda por tarea hasta que completar sale bien: si el motor rechaza la
   evidencia (un «proof» mal formado, un repo no admitido), al volver a abrir el diálogo sigue ahí */
const evDrafts = new Map();
export function clearEvidenceDraft(id) {
  evDrafts.delete(id);
}

function proofPlantilla(repos) {
  return JSON.stringify(
    { repo: repos[0] ?? '', commit: '', prueba: 'python -m pytest tests/…', rojo_en_padre: true },
    null,
    2,
  );
}

export function evidenceDialog(meta, ticket, { proofRepos = [] } = {}) {
  return new Promise((resolve) => {
    const draft = evDrafts.get(ticket.id);
    const types = (meta.evidence_types ?? ['file_path', 'command_output', 'memory_id', 'peer_attestation'])
      .filter((t) => t !== 'run_output') // run_output lo pone el runner, no la mano
      .map(
        (t) =>
          `<option value="${esc(t)}"${draft?.evidence_type === t ? ' selected' : ''}>${esc(tr(EV_NAMES[t] ?? t))}</option>`,
      )
      .join('');

    const modal = openModal(
      `
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
        <div class="field" id="ev-value-field">
          <label for="ev-value">Evidencia</label>
          <input type="text" id="ev-value" required placeholder="ruta, salida de comando, id…">
        </div>
        <p class="hint ev-proof-hint" id="ev-proof-hint" hidden></p>
        <div class="field">
          <label for="ev-note">Nota</label>
          <input type="text" id="ev-note" placeholder="opcional">
        </div>
        <div class="modal__footer">
          <button type="button" class="btn btn--ghost" data-cancel>Cancelar</button>
          <button type="submit" class="btn btn--primary">Completar</button>
        </div>
      </form>
    `,
      resolve,
      { variant: 'sm' },
    );

    /* #601: «proof» es un JSON (repo, commit, prueba, rojo_en_padre…): un cuadro de varias líneas con la
       plantilla y lo que el código comprueba antes del juez. Los demás tipos, una línea. */
    const hint = modal.querySelector('#ev-proof-hint');
    const ponValor = (tipo, valor) => {
      const old = modal.querySelector('#ev-value');
      const proof = tipo === 'proof';
      if ((old.tagName === 'TEXTAREA') !== proof) {
        const nuevo = document.createElement(proof ? 'textarea' : 'input');
        nuevo.id = 'ev-value';
        nuevo.required = true;
        if (proof) {
          nuevo.rows = 7;
          nuevo.className = 'mono';
          nuevo.spellcheck = false;
        } else {
          nuevo.type = 'text';
          nuevo.placeholder = old.placeholder || 'ruta, salida de comando, id…';
        }
        old.replaceWith(nuevo);
      }
      const el = modal.querySelector('#ev-value');
      if (valor != null) el.value = valor;
      else if (proof && !el.value.trim()) el.value = proofPlantilla(proofRepos);
      hint.hidden = !proof;
      hint.textContent = proof
        ? `${tr('Antes que el juez, y en una copia sin claves, el código comprueba que el commit existe y está en master, que los ficheros existen y que la prueba sale con 0; con «rojo_en_padre», además, que esa misma prueba falla sobre el código del commit anterior.')} ${
            proofRepos.length
              ? `${tr('Repos admitidos:')} ${proofRepos.join(' · ')}`
              : tr('No hay ningún repo admitido todavía: añádelo en Agentes › Ajustes.')
          }`
        : '';
    };
    modal.querySelector('#ev-type').addEventListener('change', (e) => ponValor(e.target.value, null));
    ponValor(modal.querySelector('#ev-type').value, draft?.evidence ?? null);
    if (draft?.note) modal.querySelector('#ev-note').value = draft.note;

    modal.querySelector('#ev-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const out = {
        evidence_type: modal.querySelector('#ev-type').value,
        evidence: modal.querySelector('#ev-value').value.trim(),
        note: modal.querySelector('#ev-note').value.trim() || null,
      };
      evDrafts.set(ticket.id, out);
      modal.finish(out);
    });
  });
}

/* ================= motivo (bloquear / rechazar) ================= */

export function reasonDialog(
  title,
  cta,
  { min = 1, hint = 'por qué — quedará en el historial', value = '', tone = 'danger' } = {},
) {
  return new Promise((resolve) => {
    const modal = openModal(
      `
      <h2 class="modal__title">${esc(title)}</h2>
      <form id="rs-form">
        <div class="field">
          <label for="rs-note">Motivo${min > 1 ? ` <span class="hint">(mínimo ${min} caracteres)</span>` : ''}</label>
          <textarea id="rs-note" required rows="3" placeholder="${esc(hint)}"></textarea>
        </div>
        <div class="modal__footer">
          <button type="button" class="btn btn--ghost" data-cancel>Cancelar</button>
          <button type="submit" class="btn btn--${tone === 'danger' ? 'danger' : 'primary'}" disabled>${esc(cta)}</button>
        </div>
      </form>
    `,
      resolve,
      { variant: 'sm' },
    );

    const ta = modal.querySelector('#rs-note');
    const ok = modal.querySelector('button[type=submit]');
    /* #482: al cambiar un motivo se parte del que hay, para corregirlo y no reescribirlo a ciegas */
    if (value) {
      ta.value = value;
      ok.disabled = value.trim().length < min;
      ta.select();
    }
    ta.addEventListener('input', () => {
      ok.disabled = ta.value.trim().length < min;
    }); // sin motivo no se envía
    modal.querySelector('#rs-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const note = ta.value.trim();
      if (note.length < min) return;
      modal.finish({ note });
    });
  });
}

/* ================= nuevo proyecto ================= */

/* crear (sin argumento) o editar (T4.8: con el proyecto): nombre, color y
   archivar. Resuelve {name, color} · {archived: 1} · null si cancela. */
export function projectDialog(project = null) {
  const editing = Boolean(project);
  return new Promise((resolve) => {
    const modal = openModal(
      `
      <h2 class="modal__title">${editing ? 'Editar proyecto' : 'Nuevo proyecto'}</h2>
      <form id="pj-form">
        <div class="field">
          <label for="pj-name">Nombre</label>
          <input type="text" id="pj-name" required maxlength="80" autocomplete="off" value="${editing ? esc(project.name) : ''}">
        </div>
        <div class="field">
          <label for="pj-color">Color</label>
          <input type="color" id="pj-color" value="${editing && /^#[0-9a-f]{6}$/i.test(project.color ?? '') ? esc(project.color) : '#e8590c'}" style="width:4rem;height:2.4rem;padding:2px;border:1px solid var(--color-rule-strong);border-radius:6px;background:var(--color-card);">
          ${editing && project.color && !/^#[0-9a-f]{6}$/i.test(project.color) ? `<div class="hint">${tr('color actual:')} ${esc(project.color)} ${tr('(no es hex; se sustituye si guardas)')}</div>` : ''}
        </div>
        <div class="modal__footer">
          ${editing ? '<button type="button" class="btn btn--danger" data-archive style="margin-right:auto">Archivar proyecto</button>' : ''}
          <button type="button" class="btn btn--ghost" data-cancel>Cancelar</button>
          <button type="submit" class="btn btn--primary">${editing ? 'Guardar' : 'Crear proyecto'}</button>
        </div>
      </form>
    `,
      resolve,
      { variant: 'sm' },
    );

    if (editing) {
      modal.querySelector('[data-archive]').addEventListener('click', () => {
        modal.finish({ archived: 1 });
      });
    }
    modal.querySelector('#pj-form').addEventListener('submit', (e) => {
      e.preventDefault();
      modal.finish({
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
      .map((a) => `<option value="${esc(a)}"${a === suggested ? ' selected' : ''}>${esc(a)}</option>`)
      .join('');
    const modal = openModal(
      `
      <h2 class="modal__title">Despachar «${esc(t.plan_key ?? t.title)}»</h2>
      <form id="dp-form">
        <div class="field">
          <label for="dp-to">A quién</label>
          <select id="dp-to">${opts}</select>
          ${suggested ? `<div class="hint">${tr('el Plan sugiere')} ${esc(suggested)}</div>` : ''}
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
    `,
      resolve,
      { variant: 'sm' },
    );

    modal.querySelector('#dp-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const out = {
        to: modal.querySelector('#dp-to').value,
        note: modal.querySelector('#dp-note').value.trim() || null,
      };
      modal.finish(out);
    });
  });
}

/* ================= despachar el lote de tareas listas (#1317) ================= */

/* #1317: el 400 del lote, en una frase que Pepe entienda. El motor manda
   «dispatch_batch: items[i] (ticket #N) — causa» (workflows.py dispatch_batch); aquí se reconoce la causa y
   se escribe entera, con la tarea por su clave y su título. Lo crudo se enseña debajo, como detalle.
   Una frase ENTERA por caso (el catálogo casa la cadena completa; sin trozos pegados). */
const YA_NO_LISTA = {
  proposed: (n) =>
    `Ya no está lista: «${n}» está propuesta (alguien la ha movido mientras tenías esto abierto). Quítala del lote y vuelve a despachar.`,
  in_progress: (n) =>
    `Ya no está lista: «${n}» está en curso (alguien la ha movido mientras tenías esto abierto). Quítala del lote y vuelve a despachar.`,
  done: (n) =>
    `Ya no está lista: «${n}» está hecha (alguien la ha movido mientras tenías esto abierto). Quítala del lote y vuelve a despachar.`,
  verified: (n) =>
    `Ya no está lista: «${n}» está verificada (alguien la ha movido mientras tenías esto abierto). Quítala del lote y vuelve a despachar.`,
  rejected: (n) =>
    `Ya no está lista: «${n}» está rechazada (alguien la ha movido mientras tenías esto abierto). Quítala del lote y vuelve a despachar.`,
  expired: (n) =>
    `Ya no está lista: «${n}» está expirada (alguien la ha movido mientras tenías esto abierto). Quítala del lote y vuelve a despachar.`,
};
const WF_NO_ACTIVO = {
  paused: 'El workflow está en pausa: no se despacha.',
  closed: 'El workflow está cerrado: no se despacha.',
  cancelled: 'El workflow está cancelado: no se despacha.',
  archived: 'El workflow está archivado: no se despacha.',
};
export function batchErrorSentence(raw, nombre) {
  const estado = /dispatch ilegal desde '(\w+)'/.exec(raw)?.[1];
  if (estado && nombre && YA_NO_LISTA[estado]) return YA_NO_LISTA[estado](nombre);
  const deps = /dependencias sin verificar \(([^)]*)\)/.exec(raw)?.[1];
  if (deps && nombre) return `Todavía espera a «${deps}».`;
  const wf = /está (\w+) — no se despacha/.exec(raw)?.[1];
  if (wf && WF_NO_ACTIVO[wf]) return WF_NO_ACTIVO[wf];
  return 'Algo del lote no cuadra.';
}

/* Variante del dispatchDialog: una fila por tarea lista (casilla + clave y título + a quién).
   `submit(items, note)` es quien llama a la API y devuelve su resultado; si lanza, el diálogo NO se
   cierra y enseña el motivo encima del pie (con la tarea culpable). Resuelve el resultado de submit
   o null si se cancela. Sin agente sugerido: el select arranca en «elige a quién» y la casilla
   queda desmarcada y bloqueada hasta que se elija uno. */
/* el motor compone «[T1] Schema»; la clave ya va aparte */
const tituloLimpio = (t) => (t.plan_key ? t.title.replace(`[${t.plan_key}] `, '') : t.title);

export function batchDispatchDialog(tickets, agents, submit, errorText = (e) => String(e?.reason ?? e?.message ?? e)) {
  return new Promise((resolve) => {
    const n0 = tickets.length;
    const peers = [...new Set([...agents, ...tickets.map((t) => t.suggested_agent).filter(Boolean)])];
    const rows = tickets
      .map((t, i) => {
        const sug = t.suggested_agent || '';
        const opts =
          (sug ? '' : `<option value="" disabled selected>elige a quién</option>`) +
          peers.map((a) => `<option value="${esc(a)}"${a === sug ? ' selected' : ''}>${esc(a)}</option>`).join('');
        return `
        <div class="bd__row" role="group" aria-label="${esc(t.plan_key ?? t.title)}" data-i="${i}">
          <input type="checkbox" id="bd-ck-${i}" ${sug ? 'checked' : 'disabled'}>
          <label class="bd__what" for="bd-ck-${i}">
            <span class="bd__key">${esc(t.plan_key ?? `#${t.id}`)}</span>
            <span class="bd__title">${esc(tituloLimpio(t))}</span>
          </label>
          <select id="bd-to-${i}" aria-label="A quién">${opts}</select>
        </div>`;
      })
      .join('');
    const modal = openModal(
      `
      <h2 class="modal__title">${n0 === 1 ? 'Despachar 1 tarea lista' : `Despachar ${n0} tareas listas`}</h2>
      <form id="bd-form">
        <div class="bd__rows">${rows}</div>
        <div class="field">
          <label for="bd-note">Nota del lote</label>
          <input type="text" id="bd-note" placeholder="opcional — queda en el historial">
        </div>
        <p class="hint" style="margin-bottom:var(--sp-3);">El aviso al peer va por relay — FarOS no notifica.</p>
        <div class="bd__err" id="bd-err" role="alert" hidden></div>
        <div class="modal__footer">
          <button type="button" class="btn btn--ghost" data-cancel>Cancelar</button>
          <button type="submit" class="btn btn--primary" id="bd-go"></button>
        </div>
      </form>
    `,
      resolve,
      { variant: 'batch' },
    );

    const go = modal.querySelector('#bd-go');
    const err = modal.querySelector('#bd-err');
    const row = (i) => ({
      ck: modal.querySelector(`#bd-ck-${i}`),
      sel: modal.querySelector(`#bd-to-${i}`),
      el: modal.querySelector(`.bd__row[data-i="${i}"]`),
    });
    const chosen = () => tickets.map((t, i) => ({ t, i, ...row(i) })).filter((r) => r.ck.checked && r.sel.value);
    let busy = false;
    const refresh = () => {
      const n = chosen().length;
      /* una frase entera por número: el catálogo la casa con «Despachar {n}» */
      go.textContent = tr(`Despachar ${n}`);
      go.disabled = busy || n === 0;
    };
    tickets.forEach((_, i) => {
      const { ck, sel } = row(i);
      sel.addEventListener('change', () => {
        ck.disabled = !sel.value;
        ck.checked = !!sel.value;
        refresh();
      });
      ck.addEventListener('change', () => {
        /* quitar la culpable es un toque: desmarcar basta, y el aviso de esa fila se va */
        if (!ck.checked && err.dataset.row === String(i)) {
          err.hidden = true;
          row(i).el.classList.remove('is-bad');
          delete err.dataset.row;
        }
        refresh();
      });
    });
    refresh();

    modal.querySelector('#bd-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const picked = chosen();
      if (busy || !picked.length) return;
      busy = true;
      refresh();
      err.hidden = true;
      modal.querySelectorAll('.bd__row.is-bad').forEach((r) => r.classList.remove('is-bad'));
      const note = modal.querySelector('#bd-note').value.trim();
      const items = picked.map((r) => ({ ticket_id: r.t.id, to: r.sel.value, ...(note ? { note } : {}) }));
      try {
        const res = await submit(items, note || null);
        modal.finish(res ?? true);
      } catch (ex) {
        busy = false;
        refresh();
        /* «items[i] (ticket #N)»: i es la posición en LO ENVIADO → la fila culpable */
        const raw = String(ex?.reason ?? ex?.message ?? '');
        const m = /items\[(\d+)\]/.exec(raw);
        const bad = m ? picked[Number(m[1])] : null;
        const nombre = bad ? `${bad.t.plan_key ?? `#${bad.t.id}`} · ${tituloLimpio(bad.t)}` : null;
        /* arriba la frase humana; debajo, lo que dijo el motor, pequeño y en mono */
        err.innerHTML =
          `<div class="bd__why">${esc(tr(batchErrorSentence(raw, nombre)))}</div>` +
          `<div class="bd__raw" data-i18n-skip>${esc(errorText(ex))}</div>`;
        err.hidden = false;
        if (bad) {
          bad.el.classList.add('is-bad');
          err.dataset.row = String(bad.i);
        } else delete err.dataset.row;
        applyI18n(err);
      }
    });
  });
}

/* ================= importar un Plan JSON como workflow (#1318) ================= */

/* El 400 del motor en una frase que Pepe entienda; lo crudo va debajo, como detalle.
   «ya existe» y «plan inválido: …» (se quita el prefijo y el motivo empieza con mayúscula). */
export function importPlanErrorSentence(raw) {
  const ya = /^workflow (['"])(.*)\1 ya existe/s.exec(raw);
  if (ya) return `Ya hay un workflow que se llama «${ya[2]}». Cámbiale el nombre en el plan si es otro.`;
  const inval = /^plan inválido:\s*(.+)$/is.exec(raw);
  if (inval) return inval[1].charAt(0).toUpperCase() + inval[1].slice(1);
  return 'El motor no ha aceptado el plan.';
}

/* El texto, leído en el navegador: {vacio} | {error:{msg,line,col}} | {plan}. */
export function parsePlanText(text) {
  if (!text.trim()) return { vacio: true };
  try {
    return { plan: JSON.parse(text) };
  } catch (ex) {
    const full = String(ex?.message ?? ex);
    let line = null,
      col = null;
    const lc = /line (\d+) column (\d+)/.exec(full);
    if (lc) {
      line = Number(lc[1]);
      col = Number(lc[2]);
    } else {
      /* «Unexpected end of JSON input» no trae posición: es el final del texto */
      const pos = /position (\d+)/.exec(full) ?? (/end of JSON/i.test(full) ? [null, text.length] : null);
      if (pos) {
        const antes = text.slice(0, Number(pos[1])).split('\n');
        line = antes.length;
        col = antes[antes.length - 1].length + 1;
      }
    }
    return { error: { msg: full.replace(/\s*\(line \d+ column \d+\)/, ''), line, col } };
  }
}

/* `submit({plan, repo_root})` llama a la API y devuelve su resultado; si lanza, el diálogo NO se cierra y
   enseña el motivo encima del pie. Resuelve el resultado de submit o null si se cancela. */
export function importPlanDialog(submit, errorText = (e) => String(e?.reason ?? e?.message ?? e)) {
  return new Promise((resolve) => {
    const modal = openModal(
      `
      <h2 class="modal__title">Importar plan</h2>
      <form id="ip-form" novalidate>
        <div class="ip__pick">
          <button type="button" class="btn btn--ghost" id="ip-pick">Elegir fichero .json</button>
          <input type="file" id="ip-file" accept=".json,application/json" hidden>
          <span class="hint ip__or">o pega el plan aquí</span>
        </div>
        <div class="field">
          <label for="ip-text">Plan (JSON)</label>
          <textarea id="ip-text" class="mono ip__text" rows="9" spellcheck="false" autocomplete="off"
            placeholder='{ "workflow": { "name": "…" }, "phases": ["F1"], "tasks": [ … ] }'></textarea>
          <p class="field__error" id="ip-parse" role="alert" hidden></p>
        </div>
        <p class="ip__preview" id="ip-preview" role="status" hidden></p>
        <div class="field">
          <label for="ip-repo">Carpeta del repo (ruta absoluta)</label>
          <input type="text" id="ip-repo" placeholder="opcional" autocomplete="off" spellcheck="false">
        </div>
        <p class="hint" style="margin-bottom:var(--sp-3);">Las tareas entran aceptadas, listas para despachar. Un workflow con un nombre que ya existe no se vuelve a importar.</p>
        <div class="bd__err" id="ip-err" role="alert" hidden></div>
        <div class="modal__footer">
          <button type="button" class="btn btn--ghost" data-cancel>Cancelar</button>
          <button type="submit" class="btn btn--primary" id="ip-go" disabled>Importar</button>
        </div>
      </form>
    `,
      resolve,
      { variant: 'import' },
    );

    const q = (s) => modal.querySelector(s);
    const text = q('#ip-text'),
      parseBox = q('#ip-parse'),
      preview = q('#ip-preview'),
      err = q('#ip-err'),
      go = q('#ip-go');
    let plan = null,
      busy = false;
    const refresh = () => {
      go.disabled = busy || plan == null;
    };

    const onText = () => {
      err.hidden = true;
      const r = parsePlanText(text.value);
      plan = r.plan ?? null;
      parseBox.hidden = !r.error;
      preview.hidden = !('plan' in r);
      text.closest('.field').classList.toggle('is-bad', !!r.error);
      if (r.error) {
        const pos =
          r.error.line != null ? ` <span>${esc(tr(`línea ${r.error.line}, columna ${r.error.col}`))}</span>` : '';
        parseBox.innerHTML = `<span>El texto no es un JSON válido:</span> <span data-i18n-skip>${esc(r.error.msg)}</span>${pos}`;
        applyI18n(parseBox);
      } else if (plan !== null) {
        const o = plan && typeof plan === 'object' && !Array.isArray(plan) ? plan : {};
        const nombre = String(o.workflow?.name ?? '').trim() || '(sin nombre)';
        const f = Array.isArray(o.phases) ? o.phases.length : 0,
          n = Array.isArray(o.tasks) ? o.tasks.length : 0;
        const coord = String(o.workflow?.coordinator ?? '').trim();
        preview.innerHTML =
          `<b data-i18n-skip>«${esc(nombre)}»</b> · <span>${f === 1 ? '1 fase' : `${f} fases`}</span> · ` +
          `<span>${n === 1 ? '1 tarea' : `${n} tareas`}</span> · ` +
          (coord ? `<span>coordina</span> <b data-i18n-skip>${esc(coord)}</b>` : '<span>coordina tú</span>');
        applyI18n(preview);
      }
      refresh();
    };
    text.addEventListener('input', onText);

    q('#ip-pick').addEventListener('click', () => q('#ip-file').click());
    q('#ip-file').addEventListener('change', async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      try {
        text.value = await file.text();
      } catch {
        text.value = '';
      }
      e.target.value = ''; // elegir el mismo fichero otra vez vuelve a leerlo
      onText();
    });

    q('#ip-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      if (busy || plan == null) return;
      busy = true;
      refresh();
      err.hidden = true;
      const repo_root = q('#ip-repo').value.trim() || undefined;
      try {
        const res = await submit({ plan, repo_root });
        modal.finish(res ?? true);
      } catch (ex) {
        busy = false;
        refresh();
        const raw = String(ex?.reason ?? ex?.message ?? '');
        /* arriba la frase humana; debajo, lo que dijo el motor, pequeño y en mono */
        err.innerHTML =
          `<div class="bd__why">${esc(tr(importPlanErrorSentence(raw)))}</div>` +
          `<div class="bd__raw" data-i18n-skip>${esc(errorText(ex))}</div>`;
        err.hidden = false;
        applyI18n(err);
        err.scrollIntoView({ block: 'nearest' });
      }
    });
    text.focus();
  });
}

/* ================= suspender o borrar un agente (#252) ================= */

/* SUSPENDER (Pepe, 30-sep: «prefiero suspenderlo»). Resuelve {reassignTo} (null si no tenía
   abiertas) o null si cancela. Con tareas abiertas no se puede suspender sin decir a quién
   pasan: el motor da 409 si no.
   SIN DESTINATARIO PRESELECCIONADO, a propósito. Google Workspace, al borrar un usuario,
   hace BUSCAR a quién pasan sus datos («In the Search for a user field, enter the name or
   email address») en vez de proponer uno. Aquí se proponía al dueño, y un Enter por inercia le
   echaba encima todo el trabajo del que se va. «Suspender» no se activa hasta que se elige.
   Y dice lo que pasa con el nombre, como Google dice lo que pasa con lo no transferido
   («Any data that's not transferred is deleted»): una consecuencia se escribe entera. */
export function suspendAgentDialog(agent, others) {
  const n = agent.open_tickets ?? 0;
  return new Promise((resolve) => {
    const opts =
      `<option value="" disabled selected>elige a quién</option>` +
      others.map((a) => `<option value="${esc(a)}">${esc(a)}</option>`).join('');
    const modal = openModal(
      `
      <h2 class="modal__title">Suspender a «${esc(agent.name)}»</h2>
      <form id="ra-form">
        ${
          n
            ? `
        <p class="roster-dlg__lead">«${esc(agent.name)}» tiene ${n} ${n === 1 ? 'tarea abierta' : 'tareas abiertas'}.</p>
        <div class="field">
          <label for="ra-to">Pasan a</label>
          <select id="ra-to">${opts}</select>
        </div>`
            : `
        <p class="roster-dlg__lead">«${esc(agent.name)}» no tiene tareas abiertas.</p>`
        }
        <p class="hint">Deja de aparecer en los selectores y no recibe tareas nuevas. Sigue en la lista y sus tareas cerradas lo siguen nombrando. Se puede reactivar.</p>
        <div class="modal__footer">
          <button type="button" class="btn btn--ghost" data-cancel>Cancelar</button>
          <button type="submit" class="btn btn--danger"${n ? ' disabled' : ''}>Suspender</button>
        </div>
      </form>
    `,
      resolve,
      { variant: 'sm' },
    );

    const sel = modal.querySelector('#ra-to');
    sel?.addEventListener('change', () => {
      modal.querySelector('button[type=submit]').disabled = !sel.value;
    });
    modal.querySelector('#ra-form').addEventListener('submit', (e) => {
      e.preventDefault();
      if (n && !sel.value) return;
      const out = { reassignTo: n ? sel.value : null };
      modal.finish(out);
    });
  });
}

/* BORRAR: solo existe para un nombre que nunca tuvo tareas (creado por error). Resuelve
   true o null. Con historia, el motor da 409 y la fila ofrece Suspender en su lugar. */
export function deleteAgentDialog(agent) {
  return new Promise((resolve) => {
    const modal = openModal(
      `
      <h2 class="modal__title">Borrar a «${esc(agent.name)}»</h2>
      <form id="da-form">
        <p class="roster-dlg__lead">«${esc(agent.name)}» no ha tenido nunca ninguna tarea.</p>
        <p class="hint">Se borra de la lista del todo. Si lo necesitas otra vez, se vuelve a añadir.</p>
        <div class="modal__footer">
          <button type="button" class="btn btn--ghost" data-cancel>Cancelar</button>
          <button type="submit" class="btn btn--danger">Borrar</button>
        </div>
      </form>
    `,
      resolve,
      { variant: 'sm' },
    );
    modal.querySelector('#da-form').addEventListener('submit', (e) => {
      e.preventDefault();
      modal.finish(true);
    });
  });
}

/* #460: BORRAR UNA CLAVE. Si FarOS la está usando, lo dice: al borrarla, ese tipo se
   queda sin clave hasta que se marque otra. Resuelve true o null. */
export function deleteCredentialDialog(c, usoTexto) {
  return new Promise((resolve) => {
    const modal = openModal(
      `
      <h2 class="modal__title">Borrar la clave «${esc(c.name)}»</h2>
      <form id="dc-form">
        ${
          c.in_use
            ? `<p class="roster-dlg__lead">${esc(usoTexto)}</p>
             <p class="hint">Al borrarla, FarOS se queda sin esa clave hasta que marques otra.</p>`
            : '<p class="roster-dlg__lead">Se borra de la lista. Si la necesitas otra vez, la vuelves a pegar.</p>'
        }
        <div class="modal__footer">
          <button type="button" class="btn btn--ghost" data-cancel>Cancelar</button>
          <button type="submit" class="btn btn--danger">Borrar</button>
        </div>
      </form>
    `,
      resolve,
      { variant: 'sm' },
    );
    modal.querySelector('#dc-form').addEventListener('submit', (e) => {
      e.preventDefault();
      modal.finish(true);
    });
  });
}

/* ================= composer de handoff ================= */

export function handoffComposer(draftContent, workflowName) {
  return new Promise((resolve) => {
    const modal = openModal(
      `
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
    `,
      resolve,
    );
    modal.querySelector('#ho-content').value = draftContent ?? '';

    modal.querySelector('#ho-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const content = modal.querySelector('#ho-content').value.trim();
      if (!content) return;
      modal.finish({ content });
    });
  });
}

/* ================= resultado del sweep ================= */

export function sweepResultToastText(r) {
  if (!r) return 'Verificación lanzada.';
  if (r.swept === 0) return 'No hay tareas en la cola de verificación.';
  return `Verificadas ${r.verified} de ${r.swept} · rebotadas ${r.bounced}.`;
}
