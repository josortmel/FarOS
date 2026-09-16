/* ============================================================
   mcp_settings.js — Agentes › MCP (v3.1 T5.9, el dueño §4): el registro
   propio de servidores MCP de AgenticOS. Lista con buscador, importar
   los de la sesión local (~/.claude.json), habilitar/deshabilitar y
   borrar. La edición fina de env/args no va aquí hoy (recorte de
   alcance del 4-sep: lo que el dueño ve primero). Los secretos llegan ya
   enmascarados del motor (••••).
   ============================================================ */

import { esc } from '../util.js';
import { t as tr } from '../i18n.js';

let cursorBusqueda = null;   // #311: posicion del cursor entre repintados del buscador
let handlers = null; // { onImport(), onToggle(server, enabled), onDelete(server), onSearch(q) }
export function initMcpSettings(h) { handlers = h; }

function cmdOf(s) {
  if (s.transport === 'stdio' || s.command) return [s.command, ...(s.args ?? [])].filter(Boolean).join(' ');
  return s.url ?? '';
}

/* T2.5 (el dueño: «no he encontrado la guía que me preparó Eco»): un servidor que necesita
   un alta manual antes de servir para algo lleva su guía AQUÍ, en su fila, que es donde
   se descubre que está deshabilitado. La ruta se dice siempre en texto; el botón de abrir
   solo aparece si el motor da la carpeta de documentos (/api/meta docs_dir) — un botón
   que no puede abrir nada es peor que no tenerlo. */
export const GUIDES = {
  gmail: { file: 'gmail_oauth.md', desktop: 'Guia_Gmail_AgenticOS.md',
           why: 'necesita el OAuth de Google Cloud (unos 10 min)' },
};
export function guideFor(s) {
  return GUIDES[String(s.name ?? '').toLowerCase()] ?? null;
}
function guideOf(s, state) {
  const g = guideFor(s);
  if (!g) return '';
  const dir = state.meta?.docs_dir;
  return `<div class="mcprow__guide">
    ${s.enabled ? '' : `<span class="mcprow__guide-why">${tr('deshabilitado hasta completar el alta:')} ${esc(g.why)}</span>`}
    <span class="mcprow__guide-path">Guía: <code>docs/${esc(g.file)}</code> · copia en el Escritorio: <code>${esc(g.desktop)}</code></span>
    ${dir ? `<button type="button" class="btn btn--ghost btn--sm" data-guide="${esc(dir)}/${esc(g.file)}">Abrir la guía</button>` : ''}
  </div>`;
}

export function renderMcpSettings(body, state) {
  const all = state.mcpServers ?? [];
  const q = (state.mcpQuery ?? '').trim().toLowerCase();
  const list = q ? all.filter(s => [s.name, s.transport, cmdOf(s), s.source, s.note].filter(Boolean).some(v => String(v).toLowerCase().includes(q))) : all;

  const root = document.createElement('section');
  root.className = 'mcp';
  /* #277: la pantalla dice su nombre. El que pulsa «Ajustes» tiene que leer «Ajustes» al
     llegar — si el destino se titula de otra forma, no sabe que ya está dentro. */
  root.innerHTML = `
    <div class="pane__head">
      <span class="pane__title"><span class="settings__gear" aria-hidden="true">⚙</span> Ajustes</span>
      <span class="pane__hint">Registro de servidores MCP — hoy el único ajuste de la app.</span>
    </div>
    <div class="mcp__bar">
      <input type="search" class="mcp__search" id="mcp-search" placeholder="buscar por nombre, comando, fuente…" value="${esc(state.mcpQuery ?? '')}" aria-label="Buscar MCP">
      <span class="mcp__count">${list.length}${q ? ` de ${all.length}` : ''} servidor${list.length === 1 ? '' : 'es'}</span>
      <button type="button" class="btn btn--ghost btn--sm" id="mcp-import" title="Lee ~/.claude.json y añade al registro los MCP que no estén">Importar de la sesión local</button>
    </div>
    <div class="mcp__list" id="mcp-list"></div>
    <p class="hint">Cada job elige sus MCP de esta lista (modal del job › MCP). Los secretos viven en la base y se enmascaran aquí.</p>`;

  const ul = root.querySelector('#mcp-list');
  if (!list.length) {
    ul.innerHTML = `<div class="pane__empty">${all.length ? 'Nada coincide con la búsqueda.' : 'Registro vacío. Importa los de la sesión local o da de alta uno por API.'}</div>`;
  }
  for (const s of list) {
    const row = document.createElement('article');
    row.className = `mcprow${s.enabled ? '' : ' is-off'}`;
    row.dataset.id = s.id;
    row.innerHTML = `
      <div class="mcprow__main">
        <div class="mcprow__name"><span class="mcprow__id">${esc(s.name)}</span> <span class="mcprow__tag">${esc(s.transport ?? '')}</span><span class="mcprow__tag mcprow__tag--src">${esc(s.source ?? '')}</span>${s.enabled ? '' : '<span class="mcprow__tag mcprow__tag--off">deshabilitado</span>'}</div>
        <code class="mcprow__cmd" title="${esc(cmdOf(s))}">${esc(cmdOf(s))}</code>
        ${s.note ? `<div class="mcprow__note">${esc(s.note)}</div>` : ''}
        ${guideOf(s, state)}
        ${Object.keys(s.env ?? {}).length ? `<div class="mcprow__env">env: ${Object.keys(s.env).map(k => `<span>${esc(k)}=${esc(String(s.env[k]))}</span>`).join(' ')}</div>` : ''}
      </div>
      <div class="mcprow__acts">
        <label class="mcprow__toggle"><input type="checkbox" data-toggle${s.enabled ? ' checked' : ''}> habilitado</label>
        <button type="button" class="btn btn--danger btn--sm" data-del>Borrar</button>
      </div>`;
    row.querySelector('[data-toggle]').addEventListener('change', (e) => handlers.onToggle(s, e.target.checked));
    row.querySelector('[data-del]').addEventListener('click', () => handlers.onDelete(s));
    row.querySelector('[data-guide]')?.addEventListener('click', (e) => handlers.onOpenGuide(e.currentTarget.dataset.guide));
    ul.appendChild(row);
  }
  /* #311: EL BUSCADOR ESCRIBIA AL REVES. Teclear repinta esta superficie entera, asi
     que el input se RECREA en cada letra; el render hacia focus() pero no devolvia el
     cursor a su sitio, y focus() sobre un input con valor lo deja en la posicion 0.
     Resultado: cada letra nueva se insertaba DELANTE. Escribir «calendar» daba
     «radnelac», medido con teclado real a 200 ms por tecla.
     Se recuerda la posicion en el propio evento —el unico sitio donde el input viejo
     sigue vivo— y se restaura al repintar. */
  root.querySelector('#mcp-search').addEventListener('input', (e) => {
    cursorBusqueda = e.target.selectionStart;
    handlers.onSearch(e.target.value);
  });
  root.querySelector('#mcp-import').addEventListener('click', () => handlers.onImport());
  body.appendChild(root);
  if (q) {
    const campo = root.querySelector('#mcp-search');
    campo.focus();
    const pos = cursorBusqueda ?? campo.value.length;
    campo.setSelectionRange(pos, pos);
  }
}
