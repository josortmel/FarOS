/* ============================================================
   job_modal.js — crear / editar un job agéntico (T1.13).
   Correcciones del dueño, una por una:
   · modelos: se le PREGUNTAN al harness (GET /harnesses/{n}/models),
     con «refrescar»; nada de lista dura.
   · recurrencia: cada N días a horas · días de la semana a horas ·
     una vez · manual. Varias horas el mismo día, combinables.
   · presupuesto: solo si el harness factura por API; con suscripción
     no se muestra y se dice por qué.
   · MCP: heredados de la sesión local (casillas) + JSON extra +
     strict explícito. Carpetas permitidas (--add-dir). Avanzado.
   · aviso rojo si allowed_tools abre el disco (Read/Bash/Write/Edit).
   Resuelve el payload del contrato T1.1, o null si cancela.
   ============================================================ */

import { esc, fmtSchedule } from '../util.js';
import { diaLargo, diaLetra, applyI18n, t as tr } from '../i18n.js';
import { api } from '../api.js';

/* #262/#271 (lo encontro Eco): las iniciales salen de Intl con el locale activo.
   El INDICE se conserva tal cual porque ES el weekday del motor (0=lunes, estilo
   Python) y viaja como `value` del checkbox; solo cambia la LETRA, y el giro a la
   convencion de Intl (0=domingo) lo hace diaLetra. Cambiar el indice aqui
   desplazaria la seleccion sin que se note. */
const DIAS_IDX = [0, 1, 2, 3, 4, 5, 6];
const DANGEROUS = /\b(Read|Bash|Write|Edit|MultiEdit|NotebookEdit)\b/;

function openShell(html) {
  const root = document.getElementById('modal-root');
  root.innerHTML = `<div class="modal modal--job" role="dialog" aria-modal="true">${html}</div>`;
  root.hidden = false;
  const modal = root.querySelector('.modal');
  /* #302: este fichero tiene SU PROPIO openShell, distinto del de modal.js. Yo
     arregle el de modal.js y escribi al lado, tan tranquilo, "va aqui y no en
     cada modal porque openShell es el UNICO sitio por el que pasan todos".
     No lo era. Afirme una unicidad que no habia comprobado, en el comentario
     que explicaba por que mi arreglo era general — y el modal mas grande de la
     app, con 35 etiquetas, se quedo fuera.
     Medido: 35 restos antes, 30 despues de "arreglarlo". Los cinco que bajaron
     eran de otros modales. */
  applyI18n(modal);
  return modal;
}
function close() {
  const root = document.getElementById('modal-root');
  root.hidden = true;
  root.innerHTML = '';
}

/* normaliza el schedule que llega (string JSON o objeto, v1 «at» string o v3 «at» lista) */
function normSchedule(s) {
  if (!s) return { type: 'manual' };
  if (typeof s === 'string') { try { s = JSON.parse(s); } catch { return { type: 'manual' }; } }
  const at = Array.isArray(s.at) ? s.at : s.at ? [s.at] : [];
  if (s.type === 'recurring') return { type: 'recurring', every_days: s.every_days ?? 1, at: at.length ? at : ['08:00'] };
  if (s.type === 'weekly') return { type: 'weekly', weekdays: s.weekdays ?? [0, 1, 2, 3, 4], at: at.length ? at : ['08:00'] };
  if (s.type === 'once') return { type: 'once', run_at: s.run_at ?? '' };
  return { type: 'manual' };
}

export function jobModal({ job = null, harnesses, mcpInherited, mcpServers = [], agents = [], projects = [] }) {
  const editing = Boolean(job);
  const j = job ?? {};
  const h0 = harnesses.find(h => h.name === (j.harness ?? 'claude-cli')) ?? harnesses.find(h => h.installed) ?? harnesses[0];
  let schedule = normSchedule(j.schedule);
  let models = h0?.models ?? [];

  return new Promise((resolve) => {
    const harnessOpts = harnesses.map(h => `<option value="${esc(h.name)}"${h.name === h0?.name ? ' selected' : ''}${h.installed ? '' : ' disabled'}>${esc(h.name)}${h.installed ? '' : ' (no instalado)'}${h.version ? ' · ' + esc(h.version) : ''}</option>`).join('');
    const projOpts = `<option value="">— sin proyecto —</option>` + projects.filter(p => !p.archived).map(p => `<option value="${p.id}"${j.project_id === p.id ? ' selected' : ''}>${esc(p.name)}</option>`).join('');
    /* T5.9 (el dueño §4): los MCP del job salen del REGISTRO propio (mcp_servers, lista de nombres),
       para claude-cli y opencode. Si el registro está vacío, se cae a los heredados de la sesión. */
    const fromRegistry = mcpServers.length > 0;
    const chosen = new Set(fromRegistry ? (j.mcp_servers ?? []) : (j.inherit_mcp ?? []));
    const mcpBoxes = (fromRegistry ? mcpServers : (mcpInherited ?? [])).map(m => `
      <label data-mcp-name="${esc(m.name.toLowerCase())}"><input type="checkbox" name="mcp" value="${esc(m.name)}"${chosen.has(m.name) ? ' checked' : ''}${fromRegistry && m.enabled === false ? ' disabled' : ''}> ${esc(m.name)} <small>${esc(m.scope ?? m.source ?? '')}${m.transport ? ' · ' + esc(m.transport) : ''}${fromRegistry && m.enabled === false ? ' · ' + tr('deshabilitado') : ''}</small></label>`).join('');

    const modal = openShell(`
      <h2 class="modal__title">${editing ? 'Editar job' : 'Nuevo job agéntico'}</h2>
      <form id="job-form" novalidate>
        <div class="field">
          <label for="j-name">Nombre</label>
          <input type="text" id="j-name" required maxlength="120" autocomplete="off" value="${esc(j.name ?? '')}" placeholder="Resume mis correos de la mañana">
        </div>
        <div class="field">
          <label for="j-prompt">Prompt</label>
          <textarea id="j-prompt" class="mono" rows="6" required placeholder="Qué tiene que hacer el agente, con qué y cómo entregar.">${esc(j.prompt ?? '')}</textarea>
          <div class="hint">el motor antepone el preámbulo del agente genérico (job, criterio, cómo entregar) — T1.9</div>
        </div>

        <div class="field-row">
          <div class="field">
            <label for="j-harness">Harness</label>
            <select id="j-harness">${harnessOpts}</select>
          </div>
          <div class="field">
            <label for="j-model">Modelo <span class="models-src" id="j-models-src"></span></label>
            <!-- T5.5 (el dueño, queja 6ª): familia → modelo CONCRETO. El alias («sonnet») va el primero y se
                 dice que es alias; lo que se guarda es siempre el id elegido. -->
            <div class="models-row">
              <select id="j-family" aria-label="Familia de modelos"></select>
              <select id="j-model"></select>
              <button type="button" class="btn btn--ghost btn--sm" id="j-refresh" title="Volver a preguntar al harness qué modelos tiene (sonda de $0)">↻</button>
            </div>
            <div class="hint" id="j-model-note"></div>
          </div>
        </div>

        <div class="field">
          <label>Programación</label>
          <div class="sched">
            <div class="sched__modes" role="tablist">
              <button type="button" data-mode="manual">Manual</button>
              <button type="button" data-mode="once">Una vez</button>
              <button type="button" data-mode="recurring">Cada N días</button>
              <button type="button" data-mode="weekly">Días de la semana</button>
            </div>
            <div id="sched-body"></div>
            <div class="sched__human" id="sched-human"></div>
          </div>
        </div>

        <div class="field" id="j-budget-field">
          <label for="j-budget">Presupuesto máx. (USD)</label>
          <input type="number" id="j-budget" step="0.05" min="0" placeholder="sin tope" value="${j.max_budget_usd ?? ''}">
          <div class="hint">solo para harness que facturan por API: el motor rechaza el lanzamiento al superarlo</div>
        </div>
        <div class="billing-note" id="j-billing-note" hidden></div>

        <div class="field-row-3">
          <div class="field">
            <label for="j-timeout">Timeout (s)</label>
            <input type="number" id="j-timeout" min="30" step="30" value="${j.timeout_s ?? 1800}">
          </div>
          <div class="field">
            <label for="j-verify">Verificación</label>
            <select id="j-verify">
              <option value="auto"${(j.verify_level ?? 'auto') === 'auto' ? ' selected' : ''}>automática (juez por run)</option>
              <option value="none"${j.verify_level === 'none' ? ' selected' : ''}>ninguna</option>
            </select>
          </div>
          <div class="field">
            <label for="j-project">Proyecto</label>
            <select id="j-project">${projOpts}</select>
          </div>
        </div>
        <div class="field">
          <label for="j-criteria">Criterio del verificador</label>
          <input type="text" id="j-criteria" value="${esc(j.verify_criteria ?? '')}" placeholder="Qué tiene que cumplir el resultado para darlo por bueno">
        </div>

        <div class="field">
          <label for="j-mcp-q">MCP <span class="models-src">${fromRegistry ? '· del registro (Agentes › MCP)' : '· heredados de la sesión local'}</span></label>
          <input type="search" id="j-mcp-q" placeholder="buscar servidor…" aria-label="Buscar MCP" autocomplete="off">
          <div class="mcp-list" id="j-mcp">${mcpBoxes || '<span class="hint">registro vacío: impórtalos en Agentes › MCP</span>'}</div>
          <div class="hint">marcar uno lo monta en este job (--mcp-config propio) y añade su allowlist mcp__nombre__*</div>
        </div>
        <div class="field">
          <label for="j-tools">Tools permitidas (allowed_tools, separadas por coma)</label>
          <input type="text" id="j-tools" value="${esc(j.allowed_tools ?? '')}" placeholder="mcp__gmail__search,mcp__gmail__read">
          <div class="tool-warning" id="j-tools-warning" hidden>⚠ Read, Bash, Write o Edit abren el disco fuera del sandbox del cwd. Medido: solo las tools mcp__* mantienen el sandbox. Si lo pones, sabes lo que haces.</div>
        </div>

        <details class="advanced">
          <summary>Avanzado</summary>
          <div class="field">
            <label for="j-dirs">Carpetas permitidas (--add-dir, una por línea)</label>
            <textarea id="j-dirs" class="mono" rows="2">${esc((j.add_dirs ?? []).join('\n'))}</textarea>
            <div class="tool-warning" id="j-dirs-warning" hidden>⚠ Estas carpetas quedan legibles para el agente (y escribibles si lleva Write o Edit), fuera del sandbox del cwd. Cada línea es una puerta más. Si la abres, sabes lo que haces.</div>
          </div>
          <div class="field">
            <label for="j-cwd">Directorio de trabajo (cwd)</label>
            <input type="text" id="j-cwd" value="${esc(j.cwd ?? '')}" placeholder="por defecto: workspace del job">
          </div>
          <div class="field-row-3">
            <div class="field">
              <label for="j-perm">Permisos</label>
              <select id="j-perm">${['dontAsk', 'acceptEdits', 'plan', 'bypassPermissions'].map(p => `<option${(j.permission_mode ?? 'dontAsk') === p ? ' selected' : ''}>${p}</option>`).join('')}</select>
            </div>
            <div class="field">
              <label for="j-effort">Effort</label>
              <select id="j-effort">${['', 'low', 'medium', 'high', 'max'].map(e => `<option value="${e}"${(j.effort ?? '') === e ? ' selected' : ''}>${e || '— por defecto —'}</option>`).join('')}</select>
            </div>
            <div class="field">
              <label for="j-fallback">Modelo de respaldo</label>
              <select id="j-fallback"></select>
            </div>
          </div>
          <div class="field">
            <label for="j-system">System prompt propio</label>
            <textarea id="j-system" class="mono" rows="3">${esc(j.system_prompt ?? '')}</textarea>
          </div>
          <div class="field">
            <label for="j-schema">Esquema JSON de salida (--json-schema)</label>
            <textarea id="j-schema" class="mono" rows="3" placeholder='{"type":"array","items":{"type":"string"}}'>${esc(j.json_schema ?? '')}</textarea>
          </div>
          <div class="field">
            <label for="j-mcpjson">MCP extra (JSON de mcp_config)</label>
            <textarea id="j-mcpjson" class="mono" rows="3" placeholder='{"mcpServers":{"gmail":{...}}}'>${esc(j.mcp_config ?? '')}</textarea>
            <label style="margin-top:0.4rem"><input type="checkbox" id="j-strict"${j.strict_mcp ? ' checked' : ''}> --strict-mcp-config (solo los MCP de este job; excluye los heredados)</label>
          </div>
          <div class="field">
            <label for="j-ticket">Ticket manual vinculado (#id, opcional)</label>
            <input type="number" id="j-ticket" min="1" value="${j.ticket_id ?? ''}" placeholder="brazo automático de esa tarea: run ok + pasa → la completa">
          </div>
        </details>

        <div class="modal__footer">
          <button type="button" class="btn btn--ghost" data-cancel>Cancelar</button>
          <button type="submit" class="btn btn--primary">${editing ? 'Guardar job' : 'Crear job'}</button>
        </div>
      </form>
    `);

    const q = (sel) => modal.querySelector(sel);
    const capOf = (name) => harnesses.find(h => h.name === name) ?? {};

    /* ---- modelos: siempre del harness. T5.5: en dos niveles (familia → id concreto) ---- */
    const modelLabel = (m) => `${m.label ?? m.id}${m.alias_of ? '' : m.tier ? ` · ${m.tier}` : ''}${m.validated ? ' ✓' : ''}${m.validated_at ? ` ${m.validated_at}` : ''}`;
    function familiesOf() {
      /* el catálogo trae familias; si no (opencode, lista tal cual), una sola «todos» */
      const withFam = models.filter(m => m.family);
      if (!withFam.length) return [{ family: '', label: 'todos', models }];
      const byFam = new Map();
      for (const m of models) { const k = m.family ?? ''; if (!byFam.has(k)) byFam.set(k, []); byFam.get(k).push(m); }
      return [...byFam.entries()].map(([family, ms]) => ({
        family, label: family ? family[0].toUpperCase() + family.slice(1) : 'otros',
        models: [...ms].sort((a, b) => Number(Boolean(b.alias_of)) - Number(Boolean(a.alias_of))), // alias primero
      }));
    }
    function fillModels(keepFamily = false) {
      const famSel = q('#j-family'); const sel = q('#j-model'); const fb = q('#j-fallback');
      const current = sel.value || j.model;
      const fams = familiesOf();
      const single = fams.length === 1 && !fams[0].family;
      famSel.hidden = single;
      const curFam = models.find(m => m.id === current)?.family ?? null;
      const chosen = keepFamily && famSel.value !== undefined && fams.some(f => f.family === famSel.value) ? famSel.value
        : (curFam ?? fams[0]?.family ?? '');
      famSel.innerHTML = fams.map(f => `<option value="${esc(f.family)}"${f.family === chosen ? ' selected' : ''}>${esc(f.label)}</option>`).join('');
      const list = (fams.find(f => f.family === chosen) ?? fams[0])?.models ?? [];
      sel.innerHTML = list.map(m => `<option value="${esc(m.id)}"${m.id === current ? ' selected' : ''}>${esc(modelLabel(m))}</option>`).join('')
        || '<option value="">(el harness no devolvió modelos)</option>';
      if (!models.some(m => m.id === current) && current && editing) {
        // el job usa un modelo que el harness ya no lista: se conserva, pero se dice
        sel.insertAdjacentHTML('afterbegin', `<option value="${esc(current)}" selected>${esc(current)} (no listado por el harness)</option>`);
      }
      noteModel();
      fb.innerHTML = '<option value="">— ninguno —</option>' + models.map(m => `<option value="${esc(m.id)}"${m.id === (j.fallback_model ?? '') ? ' selected' : ''}>${esc(m.label ?? m.id)}</option>`).join('');
      const h = capOf(q('#j-harness').value);
      /* #305: el catalogo traduce cadenas COMPLETAS y esto es una plantilla con texto
         interpolado, asi que «· fuente: help+measured · 12:32» no casa nunca con una
         clave fija. Se parte: la etiqueta al catalogo, el dato fuera. */
      q('#j-models-src').textContent = h.models_source ? `· ${tr('fuente:')} ${h.models_source}${h.models_refreshed_at ? ' · ' + h.models_refreshed_at.slice(11, 16) : ''}` : '';
    }
    /* lo que se va a guardar, dicho en claro: alias (cambia solo) o id fijo */
    function noteModel() {
      const m = models.find(x => x.id === q('#j-model').value);
      q('#j-model-note').textContent = !m ? '' : m.alias_of
        ? `${tr('alias: hoy apunta a')} ${m.alias_of} ${tr('y cambiará cuando Anthropic suba versión')}`
        : `id fijo: ${m.id}${m.note ? ' · ' + m.note : ''}`;
    }
    async function loadModels(refresh) {
      const name = q('#j-harness').value;
      q('#j-refresh').disabled = true; q('#j-refresh').textContent = '…';
      try {
        const res = refresh ? await api().harnessModelsRefresh(name) : await api().harnessModels(name);
        models = res.models ?? [];
        const h = capOf(name);
        if (h) { h.models = models; h.models_source = res.source ?? h.models_source; h.models_refreshed_at = res.refreshed_at ?? h.models_refreshed_at; }
      } catch {
        models = capOf(name).models ?? [];
      }
      q('#j-refresh').disabled = false; q('#j-refresh').textContent = '↻';
      fillModels();
    }

    /* ---- capacidades del harness: presupuesto, avanzado ---- */
    function applyHarness() {
      const h = capOf(q('#j-harness').value);
      const api_ = h.billing === 'api';
      q('#j-budget-field').hidden = !api_;
      const note = q('#j-billing-note');
      note.hidden = api_;
      note.textContent = h.billing === 'local'
        ? 'Harness local: no factura. El presupuesto no aplica.'
        : `${h.name ?? 'Este harness'} ${tr('consume la suscripción: no se cobra por uso, el tope en USD no aplica. El coste que reporte se guarda como equivalente informativo.')}`;
        /* #302: la MARCA del harness es un dato y no se traduce; la frase si. */
      const cap = h.capabilities ?? {};
      for (const [id, key] of [['#j-effort', 'effort'], ['#j-fallback', 'fallback_model'], ['#j-schema', 'json_schema'], ['#j-dirs', 'add_dir'], ['#j-perm', 'permission_mode'], ['#j-tools', 'allowed_tools']]) {
        const el = q(id);
        const supported = cap[key] !== false;
        el.disabled = !supported;
        el.title = supported ? '' : `${h.name} no soporta esta opción`;
      }
      q('#j-mcp').querySelectorAll('input').forEach(i => { i.disabled = cap.inherit_mcp === false; });
    }
    q('#j-mcp-q').addEventListener('input', (e) => {
      const k = e.target.value.trim().toLowerCase();
      q('#j-mcp').querySelectorAll('label[data-mcp-name]').forEach(l => { l.hidden = Boolean(k) && !l.dataset.mcpName.includes(k); });
    });
    q('#j-harness').addEventListener('change', () => { applyHarness(); loadModels(false); });
    q('#j-refresh').addEventListener('click', () => loadModels(true));
    q('#j-family').addEventListener('change', () => { q('#j-model').value = ''; fillModels(true); });
    q('#j-model').addEventListener('change', noteModel);

    /* ---- aviso rojo de tools ---- */
    const toolsWarn = () => { q('#j-tools-warning').hidden = !DANGEROUS.test(q('#j-tools').value); };
    q('#j-tools').addEventListener('input', toolsWarn);
    /* FIX #140 (finding #9): add_dirs amplía el acceso (--add-dir) — mismo aviso, mismo tono */
    const dirsWarn = () => { q('#j-dirs-warning').hidden = !q('#j-dirs').value.trim(); };
    q('#j-dirs').addEventListener('input', dirsWarn);
    dirsWarn(); // un job editado con carpetas ya abiertas avisa desde el principio

    /* ---- programación: tres modos + varias horas ---- */
    function timeRow(value = '08:00') {
      const span = document.createElement('span');
      span.className = 'sched__time';
      span.innerHTML = `<input type="time" value="${esc(value)}" required><button type="button" aria-label="quitar hora" title="quitar">✕</button>`;
      span.querySelector('button').addEventListener('click', () => { if (q('#sched-body').querySelectorAll('.sched__time').length > 1) { span.remove(); syncSchedule(); } });
      span.querySelector('input').addEventListener('change', syncSchedule);
      return span;
    }
    function timesBlock(at) {
      const wrap = document.createElement('div');
      wrap.className = 'sched__times';
      for (const t of at) wrap.appendChild(timeRow(t));
      const add = document.createElement('button');
      add.type = 'button'; add.className = 'btn btn--ghost btn--sm'; add.textContent = '+ hora';
      add.addEventListener('click', () => { wrap.insertBefore(timeRow('20:00'), add); syncSchedule(); });
      wrap.appendChild(add);
      return wrap;
    }
    function renderSchedule() {
      modal.querySelectorAll('.sched__modes button').forEach(b => b.classList.toggle('is-active', b.dataset.mode === schedule.type));
      const body = q('#sched-body');
      body.innerHTML = '';
      if (schedule.type === 'once') {
        body.innerHTML = `<div class="field"><label for="s-runat">Cuándo</label><input type="datetime-local" id="s-runat" value="${esc((schedule.run_at ?? '').slice(0, 16))}" required></div>`;
        q('#s-runat').addEventListener('change', syncSchedule);
      } else if (schedule.type === 'recurring') {
        body.innerHTML = `<div class="field"><label for="s-every">Cada</label><span style="display:inline-flex;align-items:center;gap:0.5em"><input type="number" id="s-every" min="1" step="1" value="${schedule.every_days}" style="width:5rem;padding:0.35rem 0.5rem;border:1px solid var(--color-rule-strong);border-radius:6px;background:var(--color-card);font:inherit;font-size:var(--text-small)"> días, a las</span></div>`;
        q('#s-every').addEventListener('change', syncSchedule);
        body.appendChild(timesBlock(schedule.at));
      } else if (schedule.type === 'weekly') {
        const days = document.createElement('div');
        days.className = 'sched__days';
        DIAS_IDX.forEach((i) => {
          const l = document.createElement('label');
          l.innerHTML = `<input type="checkbox" value="${i}"${schedule.weekdays.includes(i) ? ' checked' : ''}>${esc(diaLetra(i))}`;
          /* #262: weekdays 0..6 = lunes..domingo (Python); Intl usa 0=domingo */
          l.title = diaLargo((i + 1) % 7);
          l.querySelector('input').addEventListener('change', syncSchedule);
          days.appendChild(l);
        });
        body.appendChild(days);
        body.appendChild(timesBlock(schedule.at));
      } else {
        body.innerHTML = `<div class="hint">solo corre con «Lanzar ahora»</div>`;
      }
      q('#sched-human').textContent = fmtSchedule(schedule);
      /* #302: este bloque se REPINTA cada vez que cambia el tipo de programacion,
         o sea DESPUES del applyI18n del shell. Sin esto, «solo corre con Lanzar
         ahora» y las etiquetas de «Cada N dias» volvian al espaniol al cambiar de
         modo, con la interfaz en ingles. La regla de todo el dia: lo que se pinta
         despues del recorrido hay que recorrerlo otra vez. */
      applyI18n(body);
    }
    function syncSchedule() {
      const body = q('#sched-body');
      if (schedule.type === 'once') schedule.run_at = q('#s-runat')?.value ?? '';
      if (schedule.type === 'recurring') schedule.every_days = Math.max(1, Number(q('#s-every')?.value) || 1);
      if (schedule.type === 'recurring' || schedule.type === 'weekly') {
        schedule.at = [...body.querySelectorAll('.sched__time input')].map(i => i.value).filter(Boolean).sort();
      }
      if (schedule.type === 'weekly') schedule.weekdays = [...body.querySelectorAll('.sched__days input:checked')].map(i => Number(i.value));
      q('#sched-human').textContent = fmtSchedule(schedule);
    }
    modal.querySelectorAll('.sched__modes button').forEach(b => b.addEventListener('click', () => {
      const mode = b.dataset.mode;
      const keepAt = schedule.at ?? ['08:00'];
      schedule = mode === 'recurring' ? { type: 'recurring', every_days: schedule.every_days ?? 1, at: keepAt }
        : mode === 'weekly' ? { type: 'weekly', weekdays: schedule.weekdays ?? [0, 1, 2, 3, 4], at: keepAt }
        : mode === 'once' ? { type: 'once', run_at: schedule.run_at ?? '' }
        : { type: 'manual' };
      renderSchedule();
    }));

    /* ---- arranque ---- */
    applyHarness(); fillModels(); renderSchedule(); toolsWarn();
    if (!models.length) loadModels(false);

    modal.querySelector('[data-cancel]').addEventListener('click', () => { close(); resolve(null); });
    modal.querySelector('#job-form').addEventListener('submit', (e) => {
      e.preventDefault();
      syncSchedule();
      const v = (sel) => q(sel).value.trim();
      const errors = [];
      if (!v('#j-name')) errors.push('el nombre es obligatorio');
      if (!v('#j-prompt')) errors.push('el prompt es obligatorio');
      if (!q('#j-model').value) errors.push('elige un modelo (pide la lista al harness con ↻)');
      if (schedule.type === 'once' && !schedule.run_at) errors.push('«una vez» necesita fecha y hora');
      if ((schedule.type === 'recurring' || schedule.type === 'weekly') && !schedule.at.length) errors.push('añade al menos una hora');
      if (schedule.type === 'weekly' && !schedule.weekdays.length) errors.push('marca al menos un día');
      let schema = v('#j-schema') || null, mcpjson = v('#j-mcpjson') || null;
      for (const [label, val] of [['esquema JSON', schema], ['MCP extra', mcpjson]]) {
        if (val) { try { JSON.parse(val); } catch { errors.push(`${label}: no es JSON válido`); } }
      }
      if (errors.length) {
        let box = q('#job-errors');
        if (!box) { box = document.createElement('div'); box.id = 'job-errors'; box.className = 'tool-warning'; q('.modal__footer').before(box); }
        box.innerHTML = errors.map(esc).join('<br>');
        /* #291: esta caja se pinta al ENVIAR, despues de que applyI18n
           recorriera el documento, asi que sus nodos nunca pasaban por el
           catalogo: las ocho validaciones salian en espaniol con la
           interfaz en ingles. Se traduce el trozo recien pintado con el
           mismo mecanismo de siempre, que acepta raiz. */
        applyI18n(box);
        return;
      }
      const h = capOf(v('#j-harness'));
      const payload = {
        name: v('#j-name'),
        prompt: v('#j-prompt'),
        harness: v('#j-harness'),
        model: q('#j-model').value,
        schedule,
        timeout_s: Number(v('#j-timeout')) || 1800,
        verify_level: v('#j-verify'),
        verify_criteria: v('#j-criteria') || null,
        project_id: v('#j-project') ? Number(v('#j-project')) : null,
        ticket_id: v('#j-ticket') ? Number(v('#j-ticket')) : null,
        permission_mode: v('#j-perm'),
        allowed_tools: v('#j-tools') || null,
        inherit_mcp: fromRegistry ? [] : [...q('#j-mcp').querySelectorAll('input:checked')].map(i => i.value),
        mcp_servers: fromRegistry ? [...q('#j-mcp').querySelectorAll('input:checked')].map(i => i.value) : [],
        mcp_config: mcpjson,
        strict_mcp: q('#j-strict').checked ? 1 : 0,
        add_dirs: v('#j-dirs') ? v('#j-dirs').split('\n').map(s => s.trim()).filter(Boolean) : [],
        cwd: v('#j-cwd') || null,
        effort: v('#j-effort') || null,
        fallback_model: q('#j-fallback').value || null,
        system_prompt: v('#j-system') || null,
        json_schema: schema,
        max_budget_usd: h.billing === 'api' && v('#j-budget') ? Number(v('#j-budget')) : null,
      };
      close();
      resolve(payload);
    });
  });
}
