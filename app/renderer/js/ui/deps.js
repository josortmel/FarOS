/* ============================================================
   deps.js — #1349: el bloque «Dependencias» de la ficha de una tarea de workflow.
   «Espera a» (deps) y «La esperan» (blocks): se ven, se añaden (búsqueda dentro del
   MISMO workflow) y se quitan (× sin confirmar: es reversible). El motor manda: si dice
   400, el bloque no se cierra y enseña una frase humana y debajo el texto crudo.
   Añadir/quitar «La esperan» se hace sobre LA OTRA tarea (POST/DELETE con su id y
   depends_on = esta). Tras cada cambio se vuelve a pedir la tarea y se repinta el bloque.
   ============================================================ */

import { STATUS_LABEL } from '../transitions.js';
import { esc } from '../util.js';
import { t as tr, applyI18n } from '../i18n.js';
import { api } from '../api.js';

const SE_PUEDE = ['proposed', 'accepted', 'blocked']; // estados en los que el motor deja añadir
const HECHAS = ['verified', 'done'];
const CERRADAS = ['verified', 'done', 'rejected', 'expired'];

const quitaTildes = (s) => String(s).normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();
/* el motor compone «[T1] Schema»; la clave ya va aparte */
const limpio = (d) => (d.plan_key ? String(d.title).replace(`[${d.plan_key}] `, '') : d.title);
const clave = (d) => d.plan_key ?? `#${d.id}`;

/* La frase humana de cada 400 del motor. `otra` = la tarea elegida; `grupo` = en cuál se añadía. */
export function depErrorSentence(raw, otra, grupo) {
  const r = String(raw ?? '');
  const n = otra ? limpio(otra) : '';
  if (/ciclo/.test(r) && n)
    return grupo === 'blocks'
      ? `No: esta tarea ya espera a «${n}», y se quedarían esperándose la una a la otra.`
      : `No: «${n}» ya espera a esta tarea, y se quedarían esperándose la una a la otra.`;
  if (/MISMO workflow/.test(r)) return 'Solo entre tareas del mismo workflow.';
  if (/no se añaden dependencias a una tarea en/.test(r))
    return 'Esta tarea ya ha empezado: las dependencias se ponen antes.';
  if (/no puede depender de sí misma/.test(r)) return 'Una tarea no puede esperarse a sí misma.';
  return 'No se ha podido guardar.';
}

const rawOf = (ex) => String(ex?.reason ?? ex?.message ?? ex);

export function renderDepsSection(t, { me } = {}) {
  const s = document.createElement('section');
  s.className = 'panel__section deps';
  s.hidden = !t.workflow_id; // sin workflow_id la ficha no lo enseña (se resuelve al pedir la tarea)
  let est = null; // { workflow_id, status, deps, blocks }
  let abierto = null; // 'deps' | 'blocks': el campo de búsqueda abierto
  let lista = null; // tareas del workflow, cargadas la primera vez que se abre
  let q = '';
  let activo = 0;
  let error = null; // { why, raw }
  let ocupado = false;

  const agente = () => me?.().agent;

  async function recargar() {
    try {
      est = await api().ticket(t.id);
    } catch {
      /* sin la tarea no hay bloque que pintar bien: se deja lo que había */
    }
    pintar();
  }

  const esquema = () => `
    <div class="panel__section-title">${esc(tr('Dependencias'))}</div>
    <div class="deps__skel" aria-hidden="true"><span></span><span></span></div>`;

  function fila(d, grupo) {
    const hecha = HECHAS.includes(d.status);
    const st = STATUS_LABEL[d.status] ?? d.status;
    return `<li class="dep ${hecha ? 'dep--hecha' : 'dep--falta'}" data-dep="${d.id}">
      <span class="dep__key" data-i18n-skip>${esc(clave(d))}</span>
      <span class="dep__title" data-i18n-skip>${esc(limpio(d))}</span>
      <span class="dep__st">${hecha ? '✓ ' : ''}${esc(st)}</span>
      <button type="button" class="dep__x" data-quitar="${d.id}" data-grupo="${grupo}"
        aria-label="${esc(tr(`Quitar la dependencia de «${clave(d)}»`))}">×</button>
    </li>`;
  }

  function candidatas(grupo) {
    const ya = new Set((est[grupo] ?? []).map((d) => d.id));
    const nq = quitaTildes(q.replace(/^#/, '')).trim();
    const toks = nq.split(/\s+/).filter(Boolean);
    return (lista ?? [])
      .filter((d) => d.id !== t.id && !ya.has(d.id))
      .filter((d) => {
        const hay = quitaTildes(`${limpio(d)} ${d.plan_key ?? ''} ${d.id}`);
        return toks.every((k) => hay.includes(k));
      })
      .map((d) => ({ ...d, bloqueada: grupo === 'blocks' && !SE_PUEDE.includes(d.status) }));
  }

  function buscador(grupo) {
    const cs = candidatas(grupo);
    const ok = cs.filter((d) => !d.bloqueada);
    if (activo >= ok.length) activo = Math.max(0, ok.length - 1);
    const sel = ok[activo]?.id;
    const items = lista
      ? cs.length
        ? cs
            .map(
              (
                d,
              ) => `<li role="option" id="dep-o-${d.id}" class="dep-opt${d.id === sel ? ' is-active' : ''}${d.bloqueada ? ' is-off' : ''}"
            data-pick="${d.id}" aria-selected="${d.id === sel}"${d.bloqueada ? ' aria-disabled="true"' : ''}>
            <span class="dep__key" data-i18n-skip>${esc(clave(d))}</span>
            <span class="dep__title" data-i18n-skip>${esc(limpio(d))}</span>
            <span class="dep__st">${d.bloqueada ? esc(tr('ya empezada')) : esc(STATUS_LABEL[d.status] ?? d.status)}</span>
          </li>`,
            )
            .join('')
        : `<li class="dep-opt dep-opt--vacio">${esc(tr('Ninguna tarea coincide.'))}</li>`
      : `<li class="dep-opt dep-opt--vacio">${esc(tr('cargando…'))}</li>`;
    return `<div class="dep-find">
      <input type="text" class="dep-find__in" role="combobox" aria-expanded="true" aria-controls="dep-l-${grupo}"
        aria-autocomplete="list"${sel ? ` aria-activedescendant="dep-o-${sel}"` : ''}
        placeholder="${esc(tr('Busca por título, clave o número…'))}"
        aria-label="${esc(tr('Busca por título, clave o número…'))}" value="${esc(q)}" autocomplete="off">
      <ul class="dep-find__list" role="listbox" id="dep-l-${grupo}">${items}</ul>
    </div>`;
  }

  function grupoHTML(grupo) {
    const filas = est[grupo] ?? [];
    const enMarcha = grupo === 'deps' && !SE_PUEDE.includes(est.status);
    const frena =
      grupo === 'blocks' && !CERRADAS.includes(est.status)
        ? filas.filter((d) => !CERRADAS.includes(d.status)).length
        : 0;
    const titulo = grupo === 'deps' ? tr('Espera a') : frena ? tr(`La esperan · frena a ${frena}`) : tr('La esperan');
    const vacio = grupo === 'deps' ? tr('No espera a ninguna.') : tr('Ninguna la espera.');
    return `<div class="deps__group deps__group--${grupo}${frena ? ' deps__group--frena' : ''}" data-grupo="${grupo}">
      <div class="deps__gtitle">${esc(titulo)}</div>
      ${filas.length ? `<ul class="deps__list">${filas.map((d) => fila(d, grupo)).join('')}</ul>` : `<p class="deps__empty">${esc(vacio)}</p>`}
      ${
        enMarcha
          ? `<p class="hint deps__run">${esc(
              est.status === 'in_progress'
                ? tr('Ya está en marcha: las dependencias se ponen antes de empezar.')
                : tr('Ya está terminada o cerrada: las dependencias se ponen antes de empezar.'), // #1349: hecha, verificada, rechazada o caducada no está «en marcha»
            )}</p>`
          : ''
      }
      ${
        abierto === grupo
          ? buscador(grupo)
          : `<button type="button" class="btn btn--ghost btn--sm dep__add" data-add="${grupo}"${enMarcha ? ' disabled' : ''}>+ ${esc(tr('Añadir'))}</button>`
      }
    </div>`;
  }

  function pintar() {
    if (!est) return;
    s.hidden = !est.workflow_id;
    if (s.hidden) return;
    const tenia = document.activeElement?.classList?.contains('dep-find__in');
    s.innerHTML = `<div class="panel__section-title">${esc(tr('Dependencias'))}</div>
      ${
        error
          ? `<div class="bd__err deps__err" role="alert"><div class="bd__why">${esc(tr(error.why))}</div>
             <div class="bd__raw" data-i18n-skip>${esc(error.raw)}</div></div>`
          : ''
      }
      ${grupoHTML('deps')}${grupoHTML('blocks')}`;
    applyI18n(s);
    const inp = abierto && tenia ? s.querySelector('.dep-find__in') : null;
    if (inp) {
      inp.focus();
      inp.setSelectionRange(inp.value.length, inp.value.length);
    }
  }

  async function abrir(grupo) {
    abierto = grupo;
    q = '';
    activo = 0;
    error = null;
    pintar();
    if (!lista) {
      try {
        const wf = await api().workflow(est.workflow_id);
        lista = [...Object.values(wf.tickets_by_phase ?? {}).flat(), ...(wf.stray_tickets ?? [])];
      } catch {
        lista = [];
      }
      if (abierto === grupo) pintar();
    }
    s.querySelector('.dep-find__in')?.focus();
  }

  function cerrar() {
    abierto = null;
    q = '';
    pintar();
  }

  async function aplicar(grupo, otra, accion) {
    if (ocupado) return;
    ocupado = true;
    error = null;
    try {
      const yo = t.id;
      if (accion === 'add') {
        if (grupo === 'deps') await api().addDep(yo, { agent: agente(), depends_on: otra.id });
        else await api().addDep(otra.id, { agent: agente(), depends_on: yo });
        abierto = null;
        q = '';
      } else if (grupo === 'deps') await api().removeDep(yo, otra.id, agente());
      else await api().removeDep(otra.id, yo, agente());
    } catch (ex) {
      const raw = rawOf(ex);
      error = { why: depErrorSentence(raw, otra, grupo), raw };
      pintar(); // el bloque NO se cierra: se queda el campo abierto con lo escrito
      ocupado = false;
      return;
    }
    ocupado = false;
    await recargar();
  }

  s.addEventListener('click', (e) => {
    const add = e.target.closest('[data-add]');
    if (add && !add.disabled) return void abrir(add.dataset.add);
    const x = e.target.closest('[data-quitar]');
    if (x) {
      const grupo = x.dataset.grupo;
      const otra = (est[grupo] ?? []).find((d) => d.id === Number(x.dataset.quitar));
      if (otra) aplicar(grupo, otra, 'quitar');
      return;
    }
    const op = e.target.closest('[data-pick]');
    if (op && !op.classList.contains('is-off')) {
      const otra = (lista ?? []).find((d) => d.id === Number(op.dataset.pick));
      if (otra) aplicar(abierto, otra, 'add');
    }
  });
  s.addEventListener('input', (e) => {
    if (!e.target.classList.contains('dep-find__in')) return;
    q = e.target.value;
    activo = 0;
    pintar();
  });
  s.addEventListener('keydown', (e) => {
    if (!e.target.classList.contains('dep-find__in')) return;
    const ok = candidatas(abierto).filter((d) => !d.bloqueada);
    if (e.key === 'Escape') {
      e.stopPropagation(); // cierra el campo, no la ficha
      const g = abierto;
      cerrar();
      s.querySelector(`[data-add="${g}"]`)?.focus();
    } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!ok.length) return;
      activo = (activo + (e.key === 'ArrowDown' ? 1 : -1) + ok.length) % ok.length;
      pintar();
      s.querySelector('.dep-opt.is-active')?.scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (ok[activo]) aplicar(abierto, ok[activo], 'add');
    }
  });

  /* esqueleto solo si ya se sabe que es de workflow (el daemon trae workflow_id); si no, oculto hasta saberlo */
  if (t.workflow_id) {
    s.innerHTML = esquema();
  }
  recargar();
  return s;
}
