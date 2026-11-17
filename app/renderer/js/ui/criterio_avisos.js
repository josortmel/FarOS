/* ============================================================
   criterio_avisos.js — #1136 («Primero los criterios», decisión 75; backend #1081 de Hilo).
   Al crear o editar una tarea, el motor revisa la letra del criterio de verificación y
   devuelve criterion_warnings: [{punto, aviso}]. NUNCA bloquea: la tarea ya está guardada.
   Aquí se pinta como una NOTA junto al campo del criterio (no como un error), para
   corregirlo allí mismo o dejarlo.

   Idioma: el aviso llega siempre en español. En inglés se traduce el texto entero si está
   en el catálogo; si el motor cambió el texto, lo estable es el punto (2, 4, 6), y se enseña
   el genérico de ese punto.
   ============================================================ */

import { esc } from '../util.js';
import { t as tr, getLang } from '../i18n.js';
import { VLEVEL } from '../transitions.js';

/* el genérico de cada punto, por si el texto del motor cambia (el punto 2 tiene dos casos) */
const POR_PUNTO = {
  '2-vacio': 'Sin criterio: quien verifique no tiene contra qué medir.',
  '2': 'Pide algo que un juez automático no puede comprobar.',
  '4': 'Habla de la suite sin el comando exacto.',
  '6': 'Dice que algo queda arreglado sin pedir el rojo en el padre.',
};

/* el motor nombra el nivel por su código («peer»); en pantalla, el nombre que enseña el selector
   de justo encima («otro miembro»), o la nota y el campo parecen hablar de cosas distintas */
const conNombreDelNivel = (s) => s.replace(/«(self|peer|auto|external)»/g, (_, c) => `«${tr(VLEVEL[c]?.[0] ?? c)}»`);

export function textoAviso(a, criterio) {
  const crudo = String(a?.aviso ?? '');
  if (getLang() === 'es') return conNombreDelNivel(crudo);
  const traducido = tr(crudo);
  if (traducido !== crudo) return conNombreDelNivel(traducido);
  const clave = String(a?.punto) + (a?.punto === 2 && !String(criterio ?? '').trim() ? '-vacio' : '');
  return tr(POR_PUNTO[clave] ?? crudo);
}

/* El estado que sobrevive a los repintados vive fuera del DOM (#738): el panel se rehace
   entero tras guardar y la nota tiene que seguir ahí. Solo se toca cuando la respuesta
   TRAE criterion_warnings (el motor solo los manda si cambió el criterio o el nivel). */
const porTarea = new Map();
export function recordar(id, res) {
  if (!res || !('criterion_warnings' in res)) return;
  const lista = res.criterion_warnings ?? [];
  if (lista.length) porTarea.set(Number(id), lista);
  else porTarea.delete(Number(id));
}
export const avisosDe = (id) => porTarea.get(Number(id)) ?? [];

/* La nota. `guardada`: la primera línea dice que la tarea ya está guardada (en el diálogo
   de nueva tarea, donde lo natural sería pensar que no se creó). Lista vacía = nada. */
export function notaHTML(avisos, criterio, { guardada = false } = {}) {
  if (!avisos?.length) return '';
  const items = avisos.map(a => `
      <li><span class="crit-note__punto">${esc(tr('Punto'))} ${esc(String(a.punto))}</span>
        <span class="crit-note__texto">${esc(textoAviso(a, criterio))}</span></li>`).join('');
  return `
    <div class="crit-note" role="note" data-i18n-skip>
      <p class="crit-note__head">${esc(tr(guardada ? 'La tarea ya está guardada. Una nota sobre el criterio:' : 'Una nota sobre el criterio:'))}</p>
      <ul class="crit-note__list">${items}</ul>
      <p class="crit-note__foot">${esc(tr('Puedes corregirlo aquí o dejarlo así: no bloquea nada.'))}</p>
    </div>`;
}
