/* #1217 paso 2 (Pepe, 13-nov: «FarOS no es operable por mí»): el timbre de las franjas (#1184) en Ajustes.
   - Interruptor (como «máquina quieta», #695): PATCH /api/settings {timbre_activo}. El daemon lo lee en su
     siguiente vuelta (≤30 s) y al encenderlo no suena lo que pasó mientras estaba apagado: la pantalla lo dice.
   - Qué ha sonado un día y a quién (GET /api/timbre?fecha=): una fila por aviso, por hora. «sin_sesion» no es
     un fallo (el agente no tenía sesión abierta y no se le manda); se cuenta así, en gris, no en rojo.
   - Silenciados: cada agente silencia el suyo con la tool silenciar_timbre (1 h, hasta mañana, siempre);
     aquí Pepe ve quién y hasta cuándo, y se lo puede quitar (POST /api/timbre/silencio con hasta "").
   - Si el daemon tiene el timbre apagado por el entorno (FAROS_TIMBRE=0, solo en pruebas), el interruptor no
     manda y se avisa. */

import { esc } from '../util.js';
import { t as tr, locale } from '../i18n.js';

let handlers = null; // { onTimbre(bool), onFecha('YYYY-MM-DD'), onQuitarSilencio(agente) }
/* #1394 (Pepe, 23-nov): el historial del día es secundario y en un día normal son decenas de filas que
   empujan las Claves hacia abajo. Plegado de base con su número; abierto, sigue abierto al cambiar de día. */
let histAbierto = false;
export function initTimbreSettings(h) {
  handlers = h;
}

const hoy = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};
const hhmm = (iso) => (iso && iso.length >= 16 ? iso.slice(11, 16) : '');

/* Hasta cuándo dura un silencio («silencios» de /api/timbre: «siempre» o una hora ISO). Las fechas, con Intl. */
function hastaCuando(h) {
  if (h === 'siempre') return [tr('hasta que se lo quite')];
  const d = new Date(h);
  if (Number.isNaN(d.getTime())) return [String(h)];
  const manana = new Date();
  manana.setHours(0, 0, 0, 0);
  manana.setDate(manana.getDate() + 1);
  if (d.getTime() === manana.getTime()) return [tr('hasta mañana')];
  const hora = d.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' });
  if (d.toDateString() === new Date().toDateString()) return [tr('hasta las'), hora];
  return [tr('hasta el'), `${d.toLocaleDateString(locale(), { day: 'numeric', month: 'short' })} ${hora}`];
}

function silenciados(sil) {
  const quien = Object.keys(sil || {}).sort();
  if (!quien.length) return '';
  return `
    <div class="timbre__silencios">
      <h4 class="timbre__titulo">${esc(tr('Silenciados'))}</h4>
      <ul class="timbre__sil-lista">${quien
        .map((a) => {
          const [txt, cuando] = hastaCuando(sil[a]);
          return `
        <li class="timbre__sil">
          <span class="timbre__agente">${esc(a)}</span>
          <span class="timbre__res"><span>${esc(txt)}</span>${cuando ? ` <span>${esc(cuando)}</span>` : ''}</span>
          <button type="button" class="btn btn--ghost btn--sm" data-quitar="${esc(a)}">${esc(tr('Quitar'))}</button>
        </li>`;
        })
        .join('')}
      </ul>
    </div>`;
}

/* El resultado en palabras de Pepe. «error: …» trae el detalle del daemon, que no se traduce. */
function resultado(r) {
  if (r === 'enviado') return { cls: 'ok', txt: tr('avisado') };
  if (r === 'sin_sesion') return { cls: 'quiet', txt: tr('no tenía sesión abierta, no se le mandó') };
  if (r === 'sin_respuesta') return { cls: 'warn', txt: tr('el relay no contestó') };
  return { cls: 'fail', txt: tr('falló'), detalle: String(r || '').replace(/^error:\s*/, '') };
}

export function renderTimbreSetting(body, state) {
  const tb = state.timbre;
  if (tb == null) return; // daemon sin /api/timbre (anterior a la 3.3.32): no se pinta
  const on = !!tb.activo;
  const fecha = tb.fecha || hoy();
  const esHoy = fecha === hoy();
  const avisos = [...(tb.avisos || [])].sort((a, b) => String(a.hora).localeCompare(String(b.hora)));

  const root = document.createElement('section');
  root.className = 'settings__sec timbre';
  root.setAttribute('aria-labelledby', 'timbre-h');
  root.innerHTML = `
    <h3 class="settings__sub" id="timbre-h">${esc(tr('Timbre de las franjas'))}</h3>
    <label class="switch">
      <input type="checkbox" role="switch" id="timbre-sw" aria-describedby="timbre-desc"${on ? ' checked' : ''}>
      <span class="switch__name">${esc(tr('Timbre'))}</span>
      <span class="switch__state" aria-hidden="true">${esc(on ? tr('encendido') : tr('apagado'))}</span>
    </label>
    <p class="settings__lead" id="timbre-desc">${esc(tr('A la hora de cada franja, FarOS avisa por el relay a quien empieza una tarea. Al tocarlo se aplica en menos de medio minuto, y al volver a encenderlo no suena lo que pasó mientras estaba apagado.'))}</p>
    ${tb.apagado_por_entorno ? `<p class="timbre__aviso" role="note">${esc(tr('Este FarOS tiene el timbre apagado desde fuera (FAROS_TIMBRE=0): el interruptor no manda.'))}</p>` : ''}
    ${tb.ultima_vuelta ? `<p class="timbre__vuelta"><span>${esc(tr('Última comprobación:'))}</span> <time class="mono" datetime="${esc(tb.ultima_vuelta)}">${esc(hhmm(tb.ultima_vuelta))}</time></p>` : ''}
    ${silenciados(tb.silencios)}
    <details class="timbre__hist"${histAbierto ? ' open' : ''}>
      <summary class="timbre__titulo">${esc(esHoy ? tr('Lo que ha sonado hoy') : tr('Lo que sonó ese día'))} · ${avisos.length}</summary>
      <div class="timbre__dia">
        <label class="timbre__fecha">
          <span>${esc(tr('Día'))}</span>
          <input type="date" id="timbre-fecha" value="${esc(fecha)}" max="${esc(hoy())}">
        </label>
      </div>
      ${
        avisos.length
          ? `<ol class="timbre__lista">${avisos.map(fila).join('')}</ol>`
          : `<p class="timbre__vacio">${esc(esHoy ? tr('Hoy no ha sonado nada todavía.') : tr('Ese día no sonó nada.'))}</p>`
      }
    </details>`;

  const hist = root.querySelector('.timbre__hist');
  hist.addEventListener('toggle', () => {
    histAbierto = hist.open;
  });
  const sw = root.querySelector('#timbre-sw');
  sw.addEventListener('change', () => handlers?.onTimbre(sw.checked));
  const fe = root.querySelector('#timbre-fecha');
  fe.addEventListener('change', () => {
    if (fe.value) handlers?.onFecha(fe.value);
  });
  root
    .querySelectorAll('[data-quitar]')
    .forEach((b) => b.addEventListener('click', () => handlers?.onQuitarSilencio(b.dataset.quitar)));
  body.appendChild(root);
}

function fila(a) {
  const r = resultado(a.resultado);
  const tareas = (a.tareas || [])
    .map(
      (t) =>
        `<li><span class="mono">${esc(t.inicio || '')}</span> <span class="timbre__tarea">${esc(t.titulo || '')}</span></li>`,
    )
    .join('');
  return `
    <li class="timbre__fila is-${r.cls}">
      <time class="timbre__hora mono" datetime="${esc(a.hora || '')}">${esc(hhmm(a.hora))}</time>
      <span class="timbre__agente">${esc(a.agente || '')}</span>
      <span class="timbre__res"><span>${esc(r.txt)}</span>${r.detalle ? `<span class="timbre__sep" aria-hidden="true">·</span><span class="timbre__detalle">${esc(r.detalle)}</span>` : ''}</span>
      ${tareas ? `<ul class="timbre__tareas">${tareas}</ul>` : ''}
    </li>`;
}
