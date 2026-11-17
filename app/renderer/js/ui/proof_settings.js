/* #601 (pantalla de #594, decisión 49): los repos donde el verificador automático puede correr la
   prueba («proof») que declara una tarea, en una copia desechable sin claves ni casa. Lo cambia
   cualquiera de la familia (Hilo, 16-oct: no da más poder del que ya tiene cualquiera en su sesión).
   El motor lo guarda como una lista separada por «;»; aquí se escribe uno por línea. */

import { esc } from '../util.js';
import { t as tr } from '../i18n.js';

let handlers = null; // { onSave(lista) }
export function initProofSettings(h) { handlers = h; }

/* Agentes se repinta con cada aviso del motor: lo escrito y el cursor se guardan aquí para no perderlos
   a media palabra (el mismo fallo que el cursor del buscador de MCP, #311). */
let borrador = null;
let foco = null;   // { start, end } si el campo tenía el foco
export function clearProofDraft() { borrador = null; foco = null; }

export function renderProofSettings(body, state) {
  const guardado = state.proofRepos;
  if (guardado == null) return;   // motor sin el ajuste: no se pinta nada
  const lineas = String(guardado).split(';').map(s => s.trim()).filter(Boolean).join('\n');
  const root = document.createElement('section');
  root.className = 'settings__sec proofrepos';
  root.setAttribute('aria-labelledby', 'proof-h');
  root.innerHTML = `
    <h3 class="settings__sub" id="proof-h">${esc(tr('Verificador automático: repos admitidos'))}</h3>
    <p class="settings__lead">${esc(tr('Una tarea completada con la evidencia «prueba que corre el verificador» declara un repo, un commit y una prueba. El verificador solo la corre si el repo está en esta lista, y lo hace en una copia desechable, sin claves ni casa. Uno por línea.'))}</p>
    <form id="proof-form" class="proofrepos__form">
      <label class="sr-only" for="proof-repos">${esc(tr('Repos admitidos'))}</label>
      <textarea id="proof-repos" class="roster__input mono" rows="3" spellcheck="false"
                placeholder="D:\\proyectos\\mi-repo">${esc(borrador ?? lineas)}</textarea>
      <button type="submit" class="btn btn--ghost btn--sm">${esc(tr('Guardar'))}</button>
    </form>`;
  const ta = root.querySelector('#proof-repos');
  const recuerda = () => { borrador = ta.value; foco = { start: ta.selectionStart, end: ta.selectionEnd }; };
  ta.addEventListener('input', recuerda);
  ta.addEventListener('keyup', recuerda);
  ta.addEventListener('click', recuerda);
  ta.addEventListener('blur', () => { foco = null; });
  root.querySelector('#proof-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const lista = ta.value.split('\n').map(s => s.trim()).filter(Boolean).join(';');
    handlers?.onSave(lista);
  });
  body.appendChild(root);
  if (foco) { ta.focus(); ta.setSelectionRange(foco.start, foco.end); }
}
