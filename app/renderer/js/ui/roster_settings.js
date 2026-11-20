/* ============================================================
   roster_settings.js — Ajustes › Agentes (#252, contrato de Hilo #245).
   El dueño, 25-sep: «lo único que quiero es poder editar los agentes que
   existen, porque vosotros 4 estáis bien pero otro usuario tendrá otros
   agentes permanentes». No es un perfil nuevo: es la lista del roster,
   con alta, cambio de nombre y suspensión, y vale al momento, sin reiniciar.

   Tres orígenes posibles del roster y la pantalla dice cuál manda:
   · file    → se edita y se guarda en roster.json
   · default → la lista de ejemplo; el primer cambio crea roster.json
   · env     → la fija FAROS_AGENTS (o la vieja AGENTICOS_AGENTS): aquí NO se edita, y se dice en vez
               de fingir que se guarda (criterio 3 del ticket)
   ============================================================ */

import { esc } from '../util.js';

let handlers = null; // { onAdd, onRenameStart, onRenameCancel, onRename, onSuspend, onReactivate, onDelete }
export function initRoster(h) {
  handlers = h;
}

/* Los borradores viven fuera del estado a propósito: teclear no repinta, y si un
   repintado llega por otro lado (SSE) el texto a medio escribir no se pierde. */
let draftAlta = '';
let draftNombre = { de: null, texto: '' };

const ORIGEN = {
  file: 'Se guarda en roster.json y vale al momento, sin reiniciar.',
  default: 'Es la lista de ejemplo. Al primer cambio se guarda en roster.json.',
};

function cuenta(a) {
  const open = a.open_tickets ?? 0,
    total = a.total_tickets ?? 0;
  if (!total) return 'sin tareas';
  if (!open) return `${total} en total, ninguna abierta`;
  return `${open} ${open === 1 ? 'abierta' : 'abiertas'} · ${total} en total`;
}

function errorDe(state, where) {
  const e = state.rosterError;
  return e && e.where === where ? `<p class="roster__err" role="alert">${esc(e.msg)}</p>` : '';
}

export function renderRoster(body, state) {
  const r = state.roster;
  const root = document.createElement('section');
  root.className = 'settings__sec roster';
  root.setAttribute('aria-labelledby', 'roster-h');

  if (r === undefined) {
    body.appendChild(root);
    return;
  } // aún cargando: sin parpadeo de «vacío»
  if (r === null) {
    root.innerHTML = `
      <h3 class="settings__sub" id="roster-h">Agentes</h3>
      <div class="pane__empty">Este daemon todavía no permite editar agentes: actualízalo para tener esta sección.</div>`;
    body.appendChild(root);
    return;
  }

  const editable = r.editable && r.source !== 'env';
  root.innerHTML = `
    <h3 class="settings__sub" id="roster-h">Agentes</h3>
    ${
      editable
        ? `<p class="settings__lead">Quién puede tener tareas. ${ORIGEN[r.source] ?? ''}</p>`
        : `<p class="roster__fixed" role="note">La lista la fija la variable de entorno <code>${esc(r.env_var ?? 'FAROS_AGENTS')}</code>: desde aquí no se puede cambiar.</p>`
    }
    <div class="roster__list" id="roster-list"></div>
    ${
      editable
        ? `
    <form class="roster__add" id="roster-add" novalidate>
      <label class="sr-only" for="roster-new">Nombre del agente nuevo</label>
      <input type="text" id="roster-new" class="roster__input" maxlength="40" autocomplete="off"
             placeholder="nombre del agente nuevo" value="${esc(draftAlta)}">
      <button type="submit" class="btn btn--primary btn--sm">Añadir agente</button>
    </form>
    ${errorDe(state, '__add')}`
        : ''
    }
    <div class="roster__sus" id="roster-sus" hidden>
      <h4 class="roster__sus-h">Suspendidos</h4>
      <p class="settings__lead">No reciben tareas nuevas ni salen en los selectores. Sus tareas cerradas los siguen nombrando.</p>
      <div class="roster__list" id="roster-sus-list"></div>
    </div>`;

  const list = root.querySelector('#roster-list');
  const listSus = root.querySelector('#roster-sus-list');
  /* el dueño primero: es la fila distinta y la que ancla la casa; el resto, como venga */
  const orden = [...r.agents].sort((x, y) => (y.name === r.owner) - (x.name === r.owner));
  for (const a of orden) (a.suspended ? listSus : list).appendChild(fila(a, r, state, editable));
  root.querySelector('#roster-sus').hidden = !listSus.children.length;

  if (editable) {
    const inp = root.querySelector('#roster-new');
    inp.addEventListener('input', (e) => {
      draftAlta = e.target.value;
    });
    root.querySelector('#roster-add').addEventListener('submit', async (e) => {
      e.preventDefault();
      const ok = await handlers.onAdd(draftAlta.trim());
      if (ok) draftAlta = '';
    });
  }
  body.appendChild(root);

  /* el foco va donde se está escribiendo: al renombrar, a su campo con el cursor al
     final; tras un error de alta, de vuelta al campo de alta */
  const foco =
    root.querySelector('#roster-rename') ??
    (state.rosterError?.where === '__add' ? root.querySelector('#roster-new') : null);
  if (foco) {
    foco.focus();
    const p = foco.value.length;
    foco.setSelectionRange(p, p);
  }
}

/* Una fila. Activo: Renombrar + Suspender (o Borrar, si nunca tuvo tareas: un nombre
   creado por error no tiene historia que conservar). Suspendido: Reactivar + Renombrar. */
function fila(a, r, state, editable) {
  const esDueno = a.name === r.owner;
  const editando = editable && state.rosterEditing === a.name;
  const row = document.createElement('article');
  row.className = `rosterrow${esDueno ? ' is-owner' : ''}${editando ? ' is-editing' : ''}${a.suspended ? ' is-suspended' : ''}`;
  row.dataset.agent = a.name;

  if (editando) {
    if (draftNombre.de !== a.name) draftNombre = { de: a.name, texto: a.name };
    row.innerHTML = `
      <form class="rosterrow__edit" novalidate>
        <label class="sr-only" for="roster-rename">Nombre nuevo para «${esc(a.name)}»</label>
        <input type="text" id="roster-rename" class="roster__input" maxlength="40" autocomplete="off" value="${esc(draftNombre.texto)}">
        <button type="submit" class="btn btn--primary btn--sm">Guardar</button>
        <button type="button" class="btn btn--ghost btn--sm" data-cancel-edit>Cancelar</button>
      </form>
      <p class="rosterrow__note">Sus tareas, abiertas y cerradas, pasan al nombre nuevo.</p>
      ${errorDe(state, a.name)}`;
    const inp = row.querySelector('#roster-rename');
    inp.addEventListener('input', (e) => {
      draftNombre.texto = e.target.value;
    });
    row.querySelector('form').addEventListener('submit', (e) => {
      e.preventDefault();
      handlers.onRename(a, draftNombre.texto.trim());
    });
    row.querySelector('[data-cancel-edit]').addEventListener('click', () => {
      draftNombre = { de: null, texto: '' };
      handlers.onRenameCancel();
    });
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        draftNombre = { de: null, texto: '' };
        handlers.onRenameCancel();
      }
    });
    return row;
  }

  let acts = '';
  if (esDueno) acts = '<span class="rosterrow__lock">el dueño no se renombra ni se suspende</span>';
  else if (editable && a.suspended)
    acts = `
    <button type="button" class="btn btn--ghost btn--sm" data-rename>Renombrar</button>
    <button type="button" class="btn btn--ghost btn--sm" data-reactivate>Reactivar</button>`;
  else if (editable)
    acts = `
    <button type="button" class="btn btn--ghost btn--sm" data-rename>Renombrar</button>
    ${
      (a.total_tickets ?? 0)
        ? '<button type="button" class="btn btn--danger btn--sm" data-suspend>Suspender</button>'
        : '<button type="button" class="btn btn--danger btn--sm" data-delete>Borrar</button>'
    }`;
  row.innerHTML = `
    <div class="rosterrow__main">
      <span class="rosterrow__name" data-i18n-skip>${esc(a.name)}</span>
      ${esDueno ? '<span class="rosterrow__tag">dueño</span>' : ''}
      ${a.suspended ? '<span class="rosterrow__tag rosterrow__tag--sus">suspendido</span>' : ''}
      <span class="rosterrow__count">${cuenta(a)}</span>
    </div>
    <div class="rosterrow__acts">${acts}</div>
    ${errorDe(state, a.name)}`;
  row.querySelector('[data-rename]')?.addEventListener('click', () => handlers.onRenameStart(a.name));
  row.querySelector('[data-suspend]')?.addEventListener('click', () => handlers.onSuspend(a));
  row.querySelector('[data-reactivate]')?.addEventListener('click', () => handlers.onReactivate(a));
  row.querySelector('[data-delete]')?.addEventListener('click', () => handlers.onDelete(a));
  return row;
}

/* tras guardar un nombre, el borrador ya no vale */
export function clearRenameDraft() {
  draftNombre = { de: null, texto: '' };
}
