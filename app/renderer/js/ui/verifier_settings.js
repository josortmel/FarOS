/* #695 (pantalla de #600 y #483): dos ajustes del verificador automático que solo se tocaban por la API.
   - «Máquina quieta» (maquina_quieta): mientras está activada, la cola del verificador no corre. Es un
     INTERRUPTOR (role=switch sobre un checkbox nativo) porque se aplica al tocarlo; el nombre no cambia
     nunca y el estado lo dicen la posición, el color y una palabra aparte que el lector no repite
     (W3C APG, «Switch Pattern»; estudio de oficio de Lienzo del 22-oct).
   - Carpetas de lectura (verifier_read_roots): de dónde puede leer el juez de un run los ficheros que la
     evidencia nombra por su ruta. El motor lo guarda separado por «;»; aquí, una por línea y con Guardar,
     igual que los repos admitidos (#601). */

import { esc } from '../util.js';
import { t as tr } from '../i18n.js';

let handlers = null; // { onQuiet(bool), onSaveRoots(lista) }
export function initVerifierSettings(h) {
  handlers = h;
}

/* Agentes se repinta con cada aviso del motor: lo escrito y el cursor se guardan aquí (como en #601). */
let borrador = null;
let foco = null;
export function clearRootsDraft() {
  borrador = null;
  foco = null;
}

export function renderQuietSetting(body, state) {
  if (state.maquinaQuieta == null) return; // motor sin el ajuste: no se pinta
  const on = !!state.maquinaQuieta;
  const root = document.createElement('section');
  root.className = 'settings__sec quiet';
  root.setAttribute('aria-labelledby', 'quiet-h');
  root.innerHTML = `
    <h3 class="settings__sub" id="quiet-h">${esc(tr('Verificador automático: máquina quieta'))}</h3>
    <label class="switch">
      <input type="checkbox" role="switch" id="quiet-sw" aria-describedby="quiet-desc"${on ? ' checked' : ''}>
      <span class="switch__name">${esc(tr('Máquina quieta'))}</span>
      <span class="switch__state" aria-hidden="true">${esc(on ? tr('activada') : tr('desactivada'))}</span>
    </label>
    <p class="settings__lead" id="quiet-desc">${esc(tr('Mientras esté activada, el verificador no juzga nada, para que las medidas que necesitan el ordenador quieto no se mezclen con su trabajo. Se aplica al tocarlo y la cola se reanuda sola al desactivarla.'))}</p>`;
  const sw = root.querySelector('#quiet-sw');
  sw.addEventListener('change', () => handlers?.onQuiet(sw.checked));
  body.appendChild(root);
}

export function renderReadRootsSetting(body, state) {
  const guardado = state.readRoots;
  if (guardado == null) return;
  const lineas = String(guardado)
    .split(';')
    .map((s) => s.trim())
    .filter(Boolean)
    .join('\n');
  const root = document.createElement('section');
  root.className = 'settings__sec readroots';
  root.setAttribute('aria-labelledby', 'roots-h');
  root.innerHTML = `
    <h3 class="settings__sub" id="roots-h">${esc(tr('Verificador automático: carpetas que puede leer'))}</h3>
    <p class="settings__lead">${esc(tr('Cuando la evidencia de un run nombra ficheros por su ruta, el verificador solo los lee si están dentro de una de estas carpetas. Vacío: no lee ninguno. Una por línea.'))}</p>
    <form id="roots-form" class="proofrepos__form">
      <label class="sr-only" for="read-roots">${esc(tr('Carpetas que puede leer'))}</label>
      <textarea id="read-roots" class="roster__input mono" rows="3" spellcheck="false"
                placeholder="F:\\obsidian\\…">${esc(borrador ?? lineas)}</textarea>
      <button type="submit" class="btn btn--ghost btn--sm">${esc(tr('Guardar'))}</button>
    </form>`;
  const ta = root.querySelector('#read-roots');
  const recuerda = () => {
    borrador = ta.value;
    foco = { start: ta.selectionStart, end: ta.selectionEnd };
  };
  ta.addEventListener('input', recuerda);
  ta.addEventListener('keyup', recuerda);
  ta.addEventListener('click', recuerda);
  ta.addEventListener('blur', () => {
    foco = null;
  });
  root.querySelector('#roots-form').addEventListener('submit', (e) => {
    e.preventDefault();
    handlers?.onSaveRoots(
      ta.value
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean)
        .join(';'),
    );
  });
  body.appendChild(root);
  if (foco) {
    ta.focus();
    ta.setSelectionRange(foco.start, foco.end);
  }
}
