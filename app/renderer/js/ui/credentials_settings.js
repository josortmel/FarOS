/* ============================================================
   credentials_settings.js — Ajustes › Claves (#460, 7-oct-2026).
   El dueño: FarOS no tenía dónde meter la clave de DeepSeek, y la quería
   «no de una forma tan única y dura: imagínate que creamos varios bots de
   Telegram». Así que es una LISTA abierta: cualquier número de claves de
   cada tipo, con su nombre y, si se quiere, de dónde viene.
   · El motor PRUEBA la clave antes de guardarla (DeepSeek, Anthropic,
     Telegram) y si el servicio la rechaza no se guarda: el error sale bajo
     el formulario. Las de tipo «otra» se guardan sin probar.
   · De cada tipo, FarOS usa UNA: la marcada «en uso».
   · El valor no vuelve nunca del motor («••••»): aquí solo se escribe.
     El texto tecleado vive en un borrador del módulo, nunca en el estado.
   ============================================================ */

import { esc, fmtWhen } from '../util.js';
import { t as tr } from '../i18n.js';
import { api } from '../api.js';

let handlers = null; // { onAdd(b), onUse(c, on), onDeliverable(c, on), onTest(c), onStartValue(id), onCancelValue(), onValue(c, v), onDelete(c) }
export function initCredentials(h) {
  handlers = h;
}

export const TIPOS = { deepseek: 'DeepSeek', anthropic: 'Anthropic', telegram: 'Bot de Telegram', otra: 'Otra' };
export const USO = {
  deepseek: 'FarOS la usa para DeepSeek',
  anthropic: 'FarOS la usa para Anthropic',
  telegram: 'FarOS la usa para avisar por Telegram',
};

/* Los borradores viven fuera del estado: teclear no repinta, y un repintado que llegue
   por otro lado no se lleva lo escrito. Tras guardar, el valor se borra del borrador. */
let draft = { name: '', kind: 'deepseek', value: '', origin: '' };
let draftValor = { id: null, texto: '' };
export function clearCredentialDrafts() {
  draft = { name: '', kind: draft.kind, value: '', origin: '' };
  draftValor = { id: null, texto: '' };
}

const botonAlta = (kind) => tr(kind === 'otra' ? 'Guardar' : 'Probar y guardar');

/* #1302 «Copiar» (deseo del dueño, 16-nov: «un botón para que se copien automáticamente para pegarlas»).
   El valor no pasa por aquí: main.js lo pide a la entrega (#499, deja su fila en Entregas) y lo deja en el
   portapapeles, que se limpia solo (window.agenticos.copiarClave, de Hilo). Aquí, la cuenta atrás en el propio
   botón y la línea de debajo; viven fuera del estado para que un repintado no se las lleve. Sale en TODAS las
   claves: si faltara en las no entregables, parecería que no funciona; al pulsarlo dice por qué y cómo.
   En el navegador del móvil no hay copiarClave: copiarEnNavegador (Pepe aceptó que ahí el valor pase por la página). */
const copias = new Map(); // id → { hasta: ms, cuenta: bool } (sin cuenta: el navegador no puede limpiar el portapapeles)
const avisos = new Map(); // id → { tipo: 'no_entregable' } | { tipo: 'error', mensaje }
let reloj = null;
const SIN_CUENTA_MS = 3000; // en el navegador, «Copiada» un momento y vuelve a «Copiar»

function textoCopia(id) {
  const c = copias.get(id);
  const s = c ? Math.ceil((c.hasta - Date.now()) / 1000) : 0;
  if (s <= 0) return tr('Copiar');
  if (!c.cuenta) return tr('Copiada');
  return `${tr('Copiada')} · ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; // 5 min (Pepe): «4:59», no «299»
}

/* Safari solo deja escribir en el portapapeles dentro del toque: el ClipboardItem se crea en el mismo clic y el valor
   le llega después, cuando contesta el daemon. Donde no hay ClipboardItem, writeText tras la respuesta. */
async function copiarEnNavegador(c, dueno) {
  let pedir;
  try {
    pedir = api().credentialDeliver({
      name: c.name,
      requested_by: dueno || 'owner',
      purpose: 'copiar al portapapeles desde el móvil',
    });
  } catch (e) {
    return { ok: false, motivo: 'error', mensaje: e?.message };
  }
  pedir.catch(() => {}); // el fallo se lee abajo; que no salga como promesa sin atender
  try {
    if (window.ClipboardItem && navigator.clipboard?.write) {
      await navigator.clipboard.write([
        new ClipboardItem({
          'text/plain': pedir.then((r) => new Blob([r.value], { type: 'text/plain' })),
        }),
      ]);
    } else {
      await navigator.clipboard.writeText((await pedir).value);
    }
    return { ok: true, nombre: c.name, se_borra_en: null };
  } catch {
    try {
      await pedir;
    } catch (e) {
      return e?.code === 'credential.not_deliverable'
        ? { ok: false, motivo: 'no_entregable' }
        : { ok: false, motivo: e?.code || 'error', mensaje: e?.reason || e?.message };
    }
    return { ok: false, motivo: 'portapapeles', mensaje: tr('Este navegador no ha dejado copiar.') };
  }
}

function tic() {
  for (const [id, { hasta }] of copias) {
    if (Date.now() >= hasta) copias.delete(id);
    const b = document.querySelector(`[data-credential="${id}"] [data-copy]`);
    if (b) {
      b.textContent = textoCopia(id);
      b.classList.toggle('is-copied', copias.has(id));
    }
  }
  if (!copias.size) {
    clearInterval(reloj);
    reloj = null;
  }
}

function avisoCopia(c, puedeEntregar, dueno) {
  const a = avisos.get(c.id);
  if (!a) return '';
  if (a.tipo === 'error') return `<p class="roster__err credrow__copia" role="alert">${esc(a.mensaje)}</p>`;
  return `<p class="rosterrow__note credrow__copia" role="status">${tr('Solo se copian las claves que pueden entregarse.')}
    ${
      puedeEntregar
        ? `<button type="button" class="btn btn--ghost btn--sm" data-permit>${tr('Permitir que se entregue')}</button>`
        : `${tr('Lo decide')} <span data-i18n-skip>${esc(dueno)}</span>.`
    }</p>`;
}

async function copiar(c, row, puedeEntregar, dueno) {
  avisos.delete(c.id);
  let r;
  try {
    r =
      typeof window.agenticos?.copiarClave === 'function'
        ? await window.agenticos.copiarClave(c.name)
        : await copiarEnNavegador(c, dueno);
  } catch {
    r = null;
  }
  if (r?.ok) {
    const s = Number(r.se_borra_en);
    copias.set(
      c.id,
      s > 0 ? { hasta: Date.now() + s * 1000, cuenta: true } : { hasta: Date.now() + SIN_CUENTA_MS, cuenta: false },
    );
    reloj ??= setInterval(tic, 250);
    tic();
  } else {
    avisos.set(
      c.id,
      r?.motivo === 'no_entregable'
        ? { tipo: 'no_entregable' }
        : { tipo: 'error', mensaje: r?.mensaje || tr('No se ha copiado nada.') },
    );
  }
  const sitio = row.querySelector('[data-copy-aviso]');
  if (sitio) sitio.innerHTML = avisoCopia(c, puedeEntregar, dueno);
}

function errorDe(state, where) {
  const e = state.credError;
  return e && e.where === where ? `<p class="roster__err" role="alert">${esc(e.msg)}</p>` : '';
}

function estado(c) {
  if (c.empty) return '<span class="credrow__estado credrow__estado--mal">vacía: pégala otra vez</span>';
  if (c.test_ok === null) return '<span class="credrow__estado">sin probar</span>';
  const cuando = c.tested_at ? ` <span class="credrow__cuando" data-i18n-skip>${esc(fmtWhen(c.tested_at))}</span>` : '';
  if (c.test_ok)
    return (
      `<span class="credrow__estado">probada</span>${cuando}` +
      (c.test_detail ? ` <span class="credrow__detalle">${esc(c.test_detail)}</span>` : '')
    );
  return (
    `<span class="credrow__estado credrow__estado--mal">falló la prueba</span>${cuando}` +
    (c.test_detail ? ` <span class="credrow__detalle">${esc(c.test_detail)}</span>` : '')
  );
}

export function renderCredentials(body, state) {
  const list = state.credentials;
  const root = document.createElement('section');
  root.className = 'settings__sec creds';
  root.setAttribute('aria-labelledby', 'cred-h');
  if (list === undefined) {
    body.appendChild(root);
    return;
  } // cargando: sin parpadeo de «vacío»
  if (list === null) {
    root.innerHTML = `
      <h3 class="settings__sub" id="cred-h">Claves</h3>
      <div class="pane__empty">Este motor todavía no tiene la lista de claves: actualízalo para tener esta sección.</div>`;
    body.appendChild(root);
    return;
  }
  const busy = state.credBusy;
  root.innerHTML = `
    <h3 class="settings__sub" id="cred-h">Claves</h3>
    <p class="settings__lead">Claves de servicios de fuera. Se prueban antes de guardarse (las de tipo «otra», no) y aquí nunca se enseñan. De cada tipo, FarOS usa la marcada «en uso».</p>
    <p class="settings__lead">Una clave marcada «se entrega bajo demanda» se le da al proceso que la pida, y cada petición queda abajo, en Entregas. Lo decide el dueño de la casa. Saca la clave de los transcripts y de los ficheros; no la esconde de un programa de tu usuario que vaya a buscarla.</p>
    <div class="roster__list" id="cred-list"></div>
    <form class="roster__add creds__add" id="cred-add" novalidate>
      <label class="sr-only" for="cred-name">Nombre de la clave</label>
      <input type="text" id="cred-name" class="roster__input" maxlength="60" autocomplete="off"
             placeholder="nombre (p. ej. «Bot de Eco»)" value="${esc(draft.name)}">
      <label class="sr-only" for="cred-kind">Tipo de clave</label>
      <select id="cred-kind" class="roster__input creds__kind">
        ${Object.entries(TIPOS)
          .map(([k, l]) => `<option value="${k}"${k === draft.kind ? ' selected' : ''}>${esc(l)}</option>`)
          .join('')}
      </select>
      <label class="sr-only" for="cred-value">La clave</label>
      <input type="password" id="cred-value" class="roster__input" autocomplete="new-password" spellcheck="false"
             placeholder="pega la clave" value="${esc(draft.value)}">
      <label class="sr-only" for="cred-origin">De dónde viene</label>
      <input type="text" id="cred-origin" class="roster__input" maxlength="300" autocomplete="off"
             placeholder="de dónde viene (opcional)" value="${esc(draft.origin)}">
      <button type="submit" class="btn btn--primary btn--sm" id="cred-save"${busy === '__add' ? ' disabled' : ''}>${busy === '__add' ? 'Probando…' : botonAlta(draft.kind)}</button>
    </form>
    ${errorDe(state, '__add')}`;

  const ul = root.querySelector('#cred-list');
  if (!list.length) ul.innerHTML = '<div class="pane__empty">Todavía no hay ninguna clave.</div>';
  for (const c of list) ul.appendChild(fila(c, state));
  root.appendChild(entregas(state.credDeliveries));

  const f = root.querySelector('#cred-add');
  f.querySelector('#cred-name').addEventListener('input', (e) => {
    draft.name = e.target.value;
  });
  f.querySelector('#cred-value').addEventListener('input', (e) => {
    draft.value = e.target.value;
  });
  f.querySelector('#cred-origin').addEventListener('input', (e) => {
    draft.origin = e.target.value;
  });
  f.querySelector('#cred-kind').addEventListener('change', (e) => {
    draft.kind = e.target.value;
    f.querySelector('#cred-save').textContent = botonAlta(draft.kind);
  });
  f.addEventListener('submit', async (e) => {
    e.preventDefault();
    const ok = await handlers.onAdd({
      name: draft.name.trim(),
      kind: draft.kind,
      value: draft.value,
      origin: draft.origin.trim(),
    });
    if (ok) clearCredentialDrafts();
  });
  body.appendChild(root);
  const foco =
    root.querySelector('#cred-newvalue') ??
    (state.credError?.where === '__add' ? root.querySelector('#cred-value') : null);
  foco?.focus();
}

/* #499: el registro de entregas, el más nuevo primero. El motor no devuelve el valor nunca:
   aquí va quién pidió qué clave, para qué, cuándo y si se le dio (o por qué no). */
function entregas(lista) {
  const sec = document.createElement('div');
  sec.className = 'creds__entregas';
  if (!Array.isArray(lista)) return sec; // daemon sin #499: nada que pintar
  sec.innerHTML = `<h4 class="settings__sub creds__entregas-h">Entregas</h4>`;
  if (!lista.length) {
    sec.insertAdjacentHTML('beforeend', '<div class="pane__empty">Todavía nadie ha pedido una clave.</div>');
    return sec;
  }
  const ul = document.createElement('ul');
  ul.className = 'entregas';
  for (const d of lista) {
    const li = document.createElement('li');
    li.className = `entrega${d.ok ? '' : ' entrega--no'}`;
    li.innerHTML = `
      <time data-i18n-skip>${esc(fmtWhen(d.at))}</time>
      <span class="entrega__que"><b data-i18n-skip>${esc(d.name ?? '—')}</b> · <span data-i18n-skip>${esc(d.requested_by ?? '—')}</span>${d.purpose ? ` · <span class="entrega__para" data-i18n-skip>${esc(d.purpose)}</span>` : ''}</span>
      <span class="entrega__res">${d.ok ? 'entregada' : 'negada'}</span>
      ${!d.ok && d.reason ? `<span class="entrega__motivo" data-i18n-skip>${esc(d.reason)}</span>` : ''}`;
    ul.appendChild(li);
  }
  sec.appendChild(ul);
  return sec;
}

function fila(c, state) {
  const editando = state.credEditing === c.id;
  const ocupada = state.credBusy === c.id;
  const usable = c.kind !== 'otra';
  /* #499: solo el dueño de la casa cambia «entregable»; a los demás se les enseña y se les dice quién */
  const dueno = state.meta?.owner;
  const puedeEntregar = !dueno || state.agent === dueno;
  const row = document.createElement('article');
  row.className = `rosterrow credrow${c.in_use ? ' is-inuse' : ''}${editando ? ' is-editing' : ''}`;
  row.dataset.credential = c.id;

  let acts = '';
  if (!editando) {
    if (!c.empty)
      acts += `<button type="button" class="btn btn--ghost btn--sm credrow__copiar${copias.has(c.id) ? ' is-copied' : ''}" data-copy>${textoCopia(c.id)}</button>`;
    if (usable && !c.in_use) acts += '<button type="button" class="btn btn--ghost btn--sm" data-use>Usar esta</button>';
    if (usable && c.in_use)
      acts += '<button type="button" class="btn btn--ghost btn--sm" data-unuse>Dejar de usar</button>';
    if (usable && !c.empty)
      acts += `<button type="button" class="btn btn--ghost btn--sm" data-test${ocupada ? ' disabled' : ''}>${ocupada ? 'Probando…' : 'Probar de nuevo'}</button>`;
    acts += '<button type="button" class="btn btn--ghost btn--sm" data-value>Cambiar valor</button>';
    acts += '<button type="button" class="btn btn--danger btn--sm" data-del>Borrar</button>';
  }
  if (editando && draftValor.id !== c.id) draftValor = { id: c.id, texto: '' };
  row.innerHTML = `
    <div class="rosterrow__main">
      <span class="rosterrow__name" data-i18n-skip>${esc(c.name)}</span>
      <span class="rosterrow__tag">${esc(TIPOS[c.kind] ?? c.kind)}</span>
      ${c.in_use ? '<span class="rosterrow__tag credrow__tag--uso">en uso</span>' : ''}
      ${c.deliverable ? '<span class="rosterrow__tag credrow__tag--entrega">entregable</span>' : ''}
      <span class="rosterrow__count">${estado(c)}</span>
    </div>
    <div class="rosterrow__acts">${acts}</div>
    <div data-copy-aviso>${editando ? '' : avisoCopia(c, puedeEntregar, dueno)}</div>
    ${
      'deliverable' in c && !editando
        ? `
    <label class="credrow__entregable"${puedeEntregar ? '' : ` title="${esc(tr('Lo decide') + ' ' + dueno)}"`}>
      <input type="checkbox" data-deliver${c.deliverable ? ' checked' : ''}${puedeEntregar ? '' : ' disabled'}>
      se entrega bajo demanda
    </label>`
        : ''
    }
    ${c.origin ? `<p class="rosterrow__note credrow__origen" data-i18n-skip>${esc(c.origin)}</p>` : ''}
    ${c.in_use && USO[c.kind] ? `<p class="rosterrow__note">${USO[c.kind]}</p>` : ''}
    ${
      editando
        ? `
    <form class="rosterrow__edit" novalidate>
      <label class="sr-only" for="cred-newvalue">Valor nuevo para «${esc(c.name)}»</label>
      <input type="password" id="cred-newvalue" class="roster__input" autocomplete="new-password" spellcheck="false"
             placeholder="pega la clave nueva" value="${esc(draftValor.texto)}">
      <button type="submit" class="btn btn--primary btn--sm"${ocupada ? ' disabled' : ''}>${ocupada ? 'Probando…' : botonAlta(c.kind)}</button>
      <button type="button" class="btn btn--ghost btn--sm" data-cancel-value>Cancelar</button>
    </form>`
        : ''
    }
    ${errorDe(state, c.id)}`;

  row.querySelector('[data-copy]')?.addEventListener('click', () => copiar(c, row, puedeEntregar, dueno));
  /* la línea de debajo se repinta sola tras copiar: su botón se escucha en la fila */
  row.addEventListener('click', (e) => {
    if (!e.target.closest('[data-permit]')) return;
    avisos.delete(c.id);
    handlers.onDeliverable(c, true);
  });
  row.querySelector('[data-use]')?.addEventListener('click', () => handlers.onUse(c, true));
  row.querySelector('[data-unuse]')?.addEventListener('click', () => handlers.onUse(c, false));
  row.querySelector('[data-deliver]')?.addEventListener('change', (e) => handlers.onDeliverable(c, e.target.checked));
  row.querySelector('[data-test]')?.addEventListener('click', () => handlers.onTest(c));
  row.querySelector('[data-value]')?.addEventListener('click', () => handlers.onStartValue(c.id));
  row.querySelector('[data-del]')?.addEventListener('click', () => handlers.onDelete(c));
  if (editando) {
    const inp = row.querySelector('#cred-newvalue');
    inp.addEventListener('input', (e) => {
      draftValor.texto = e.target.value;
    });
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        draftValor = { id: null, texto: '' };
        handlers.onCancelValue();
      }
    });
    row.querySelector('form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const ok = await handlers.onValue(c, draftValor.texto);
      if (ok) draftValor = { id: null, texto: '' };
    });
    row.querySelector('[data-cancel-value]').addEventListener('click', () => {
      draftValor = { id: null, texto: '' };
      handlers.onCancelValue();
    });
  }
  return row;
}
