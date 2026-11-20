/* ============================================================
   shell.js — la ÚNICA forma de abrir una ventana emergente (#357).
   Había dos openShell (modal.js y job_modal.js) y cada arreglo había
   que hacerlo dos veces: ya se olvidó dos veces (#302 traducción,
   #316 salidas). Y divergían en los dos sentidos: modal.js dejaba
   vivos el Escape y el clic de fondo al cerrar por Cancelar o al
   enviar (medido el 28-oct: 3 cancelaciones, 3 de cada), y el
   mousedown de fondo de job_modal no se quitaba nunca.
   Aquí: se pinta, se traduce, se enfoca el primer campo y las tres
   salidas (Cancelar, fondo, Escape) se enganchan UNA vez; finish()
   las desengancha todas, cierra y resuelve una sola vez, salga por
   donde salga, también al enviar.
   ============================================================ */

import { applyI18n } from '../i18n.js';

const root = () => document.getElementById('modal-root');
let current = null; // el finish de la ventana abierta, si la hay

/* variant: '' | 'sm' | 'job' (la clase modal--<variant>) */
export function openModal(html, resolve, { variant = '' } = {}) {
  current?.(null); // una ventana encima de otra: la de debajo se cierra y resuelve, sin restos
  /* #810: quién la abrió, para devolverle el foco al cerrar (patrón de diálogo del APG) */
  const opener = document.activeElement;
  const r = root();
  r.hidden = false;
  r.innerHTML = `<div class="modal${variant ? ' modal--' + variant : ''}" role="dialog" aria-modal="true">${html}</div>`;
  const modal = r.querySelector('.modal');
  /* #302: la ventana se pinta DESPUÉS de que applyI18n recorra el documento */
  applyI18n(modal);

  let done = false;
  const onEscape = (e) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      finish(null);
    }
  };
  /* el fondo: solo un mousedown EN el fondo; arrastrar desde un campo hasta fuera no cierra */
  /* #810: preventDefault para que ese mismo clic no se lleve el foco al body después de devolverlo */
  const onBackdrop = (e) => {
    if (e.target === r) {
      e.preventDefault();
      finish(null);
    }
  };
  function finish(value) {
    if (done) return;
    done = true;
    document.removeEventListener('keydown', onEscape, true);
    r.removeEventListener('mousedown', onBackdrop);
    if (current === finish) current = null;
    r.hidden = true;
    r.innerHTML = '';
    /* #810: el foco vuelve a quien la abrió. Si ya no está en la página porque su barra se repintó
       (la de Agentes lo hace con cada refresco), al que lleva su mismo id; si tampoco hay, el navegador
       decide (p. ej. la abrió algo de una ventana que esta sustituyó) */
    const back = opener?.isConnected ? opener : opener?.id ? document.getElementById(opener.id) : null;
    if (back && back !== document.body) back.focus();
    resolve(value);
  }
  document.addEventListener('keydown', onEscape, true);
  r.addEventListener('mousedown', onBackdrop);
  modal.querySelector('[data-cancel]')?.addEventListener('click', () => finish(null));
  current = finish;
  modal.finish = finish;

  /* #810: con el dedo, enfocar un campo saca el teclado de Android nada más abrir y tapa media
     ventana; el primer campo se enfoca solo con puntero fino (ratón, trackpad) */
  if (matchMedia('(pointer: fine)').matches) modal.querySelector('input, select, textarea, button')?.focus();
  return modal;
}
