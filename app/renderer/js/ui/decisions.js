/* ============================================================
   decisions.js — Bisagras (T3.4). Decisión 7 del dueño: sustituyen
   a los bisagra_*.md. Lo que el dueño tiene que decidir, con las
   opciones ENTERAS como botones (nunca A/B/C), un motivo, y lo ya
   decidido con quién, cuándo y por qué. Append-only: no se decide
   dos veces (el 409 del motor se enseña).
   ============================================================ */

import { t as tr } from '../i18n.js';
import { fmtWhen, fmtDate, esc } from '../util.js';

const EVENT = {
  asked: 'planteada',
  decided: 'decidida',
  deferred: 'aplazada',
  reopened: 'retomada',
  superseded: 'sustituida',
};
/* #698 (#657): desde dónde se decidió. Sin superficie (decisiones de antes del esquema 9, o de un
   cliente que no la manda) se dice así, y NUNCA se da por escritorio. */
const SURFACE = { desktop: 'desde el escritorio', mobile: 'desde el móvil', mcp: 'desde un agente (MCP)' };
const surfaceTag = (h) =>
  h.event !== 'decided'
    ? ''
    : ` <span class="hist__surface" data-surface="${esc(h.surface ?? 'none')}">${esc(tr(SURFACE[h.surface] ?? 'sin superficie'))}</span>`;

/* #738: cada aviso de cambio (cualquiera que toque un ticket) repinta la vista entera, y la
   tarjeta nueva nacía vacía: Pepe perdía la opción, el «por qué» y el cursor a mitad de escribir.
   Lo que lleva a medias vive aquí, por pregunta, fuera del DOM; se suelta cuando la pregunta
   sale decidida. `focus` = el campo donde estaba escribiendo, con su selección. */
const drafts = new Map(); // id → { option, free, why, until, focus: { field, start, end } | null }
const draftOf = (id) =>
  drafts.get(id) ??
  drafts
    .set(id, {
      option: null,
      free: '',
      why: '',
      until: '',
      focus: null,
      sust: [],
      sustKind: 'total',
      sustWhy: '',
      sustOpen: false,
      valid: '',
    })
    .get(id); // #919

export function pendingCount(list) {
  return (list ?? []).filter((d) => d.status !== 'decided').length;
}

/* pendientes y aplazadas arriba (aplazadas al final del grupo), decididas debajo */
export function renderDecisionsPane(pane, state, handlers) {
  const list = state.wfDecisions;
  const head = document.createElement('div');
  head.className = 'pane__head';
  head.innerHTML = `<span class="pane__title">${state.paneTitle ?? tr('Bisagras')} · ${esc(state.workflow.name)}</span>
    <span class="pane__hint">Lo que necesita tu palabra. Las opciones son literales: pulsa la que va, escribe por qué. Queda registrado y no se decide dos veces.</span>`;
  pane.appendChild(head);
  if (handlers?.buscarVigentes) pane.appendChild(vigBlock(handlers)); // #919: solo en las Preguntas de la casa

  if (list == null) {
    pane.insertAdjacentHTML('beforeend', '<div class="pane__empty">cargando…</div>');
    return;
  }
  if (!list.length) {
    pane.insertAdjacentHTML(
      'beforeend',
      `<div class="pane__empty">${tr('Sin bisagras en este workflow. Los agentes las plantean por MCP (ask_decision).')}</div>`,
    );
    return;
  }

  const open = list
    .filter((d) => d.status !== 'decided')
    .sort((a, b) => (a.status === 'deferred') - (b.status === 'deferred') || a.created_at.localeCompare(b.created_at));
  const done = list
    .filter((d) => d.status === 'decided')
    .sort((a, b) => (b.decided_at ?? '').localeCompare(a.decided_at ?? ''));
  /* #919: la superada se apaga y baja a su grupo; la nueva dice a cuáles sustituye (la marca vive en la vieja) */
  const vivas = done.filter((d) => d.superseded_by == null);
  const superadas = done.filter((d) => d.superseded_by != null);
  const sustituye = new Map();
  for (const d of superadas) sustituye.set(d.superseded_by, [...(sustituye.get(d.superseded_by) ?? []), d]);

  if (open.length) {
    const g = document.createElement('div');
    g.className = 'dec__group';
    g.textContent = `Pendientes · ${open.length}`;
    pane.appendChild(g);
    for (const d of open) pane.appendChild(renderPending(d, state, handlers));
    /* #738: el cursor vuelve donde estaba, para seguir escribiendo sin hacer clic */
    for (const d of open) {
      const f = drafts.get(d.id)?.focus;
      const el = f && pane.querySelector(`#dec-${f.field}-${d.id}`);
      if (el) {
        el.focus({ preventScroll: true });
        el.setSelectionRange(f.start ?? el.value.length, f.end ?? el.value.length);
        break;
      }
    }
  }
  if (vivas.length) {
    const g = document.createElement('div');
    g.className = 'dec__group';
    g.textContent = `Decididas · ${vivas.length}`;
    pane.appendChild(g);
    for (const d of vivas) pane.appendChild(renderDecided(d, sustituye.get(d.id)));
  }
  if (superadas.length) {
    /* plegadas por defecto: es el filtro; se abren para auditar */
    const box = document.createElement('details');
    box.className = 'dec__superadas';
    box.innerHTML = `<summary class="dec__group">Superadas · ${superadas.length}</summary>`;
    for (const d of superadas) box.appendChild(renderDecided(d, sustituye.get(d.id)));
    pane.appendChild(box);
  }
  /* «superada por #N» / «sustituye a #N» llevan a la otra tarjeta, abriendo las superadas si hace falta.
     Una sola vez por panel: si el panel sobrevive a un repintado, no se apilan escuchas. */
  if (pane.dataset.decGoto) return;
  pane.dataset.decGoto = '1';
  pane.addEventListener('click', (ev) => {
    const a = ev.target.closest('[data-goto]');
    if (!a) return;
    ev.preventDefault();
    resaltarPregunta(pane, a.dataset.goto);
  });
}

/* Lleva a una tarjeta y la hace parpadear (abre las superadas si está dentro). También la usa la lupa (#1320). */
export function resaltarPregunta(pane, id) {
  const card = pane.querySelector(`.dec[data-id="${id}"]`);
  if (!card) return false;
  const box = card.closest('details');
  if (box) box.open = true;
  card.scrollIntoView({ block: 'center', behavior: 'smooth' });
  card.classList.remove('dec--flash');
  void card.offsetWidth;
  card.classList.add('dec--flash');
  return true;
}

const KIND = { total: 'del todo', parcial: 'en parte', nunca_valida: 'nunca fue válida' };
const TIPO = { humano: 'persona', agente: 'agente', equipo: 'equipo' };
/* decidido_por llega como TEXTO JSON en la lista y como objeto en GET /api/decisions/{id} (Hilo, 10-nov) */
function quienDecide(d) {
  let v = d.decidido_por;
  if (typeof v === 'string') {
    try {
      v = JSON.parse(v);
    } catch {
      v = null;
    }
  }
  return v && v.quien ? { quien: v.quien, tipo: v.tipo } : null;
}
const goto = (id) => `<a href="#" class="dec__goto" data-goto="${esc(id)}">#${esc(id)}</a>`;

function supersedeLines(d, reemplaza) {
  const out = [];
  if (d.superseded_by != null) {
    /* #262: el catálogo traduce textos ENTEROS, así que cada etiqueta va en su propio elemento y el motivo aparte */
    out.push(
      `<div class="dec__super dec__super--por"><span class="dec__super-k">${tr('superada por')}</span> ${goto(d.superseded_by)}` +
        ` · <span class="dec__super-kind">${esc(tr(KIND[d.supersede_kind ?? 'total'] ?? d.supersede_kind))}</span>` +
        (d.supersede_reason ? ` — <span class="dec__super-why">${esc(d.supersede_reason)}</span>` : '') +
        '</div>',
    );
  }
  if (reemplaza?.length) {
    out.push(
      `<div class="dec__super"><span class="dec__super-k">${tr('sustituye a')}</span> ${reemplaza.map((r) => goto(r.id)).join(', ')}</div>`,
    );
  }
  if (d.valid_until) {
    const dia = d.valid_until.slice(0, 10);
    const caducada = dia < new Date().toISOString().slice(0, 10);
    /* fmtDate no lleva año: para una fecha de otro año se añade, o «1 ene» no dice cuál */
    const anio = dia.slice(0, 4) !== new Date().toISOString().slice(0, 4) ? ` ${dia.slice(0, 4)}` : '';
    out.push(
      `<div class="dec__valid${caducada ? ' dec__valid--caducada' : ''}"><span>${tr(caducada ? 'caducó el' : 'vale hasta el')}</span> <time datetime="${esc(dia)}">${esc(fmtDate(dia) + anio)}</time></div>`,
    );
  }
  return out.join('');
}

function renderPending(d, state, handlers) {
  const card = document.createElement('article');
  card.className = `dec dec--${d.status}`;
  card.dataset.id = d.id;
  card.innerHTML = `
    <div class="dec__top">
      <span class="dec__id">#${d.id}</span>
      <span class="dec__by">planteada por <b>${esc(d.asked_by)}</b> · ${fmtWhen(d.created_at)}</span>
      ${d.status === 'deferred' ? `<span class="dec__deferred">aplazada${d.deferred_until ? ' hasta ' + esc(fmtDate(d.deferred_until)) : ''}</span>` : ''}
    </div>
    <h3 class="dec__title">${esc(d.title)}</h3>
    ${d.context ? `<p class="dec__context">${esc(d.context)}</p>` : ''}
    <div class="dec__options"></div>
    <div class="dec__free">
      <label for="dec-free-${d.id}">Otra decisión, con tus palabras</label>
      <input type="text" id="dec-free-${d.id}" placeholder="si ninguna opción es la tuya, escríbela entera">
    </div>
    <div class="dec__why">
      <label for="dec-why-${d.id}">Por qué</label>
      <textarea id="dec-why-${d.id}" rows="2" placeholder="el motivo es obligatorio: es lo que viaja a la siguiente sesión"></textarea>
    </div>
    ${sustBlock(d, state)}
    <div class="dec__actions">
      <button type="button" class="btn btn--primary dec__confirm" disabled>Decidir</button>
      <span class="dec__picked hint">elige una opción o escribe la tuya</span>
      <span class="dec__defer">
        <input type="date" class="dec__until" aria-label="Aplazar hasta">
        <button type="button" class="btn btn--ghost btn--sm dec__defer-btn">Aplazar…</button>
      </span>
    </div>`;

  const opts = card.querySelector('.dec__options');
  const draft = draftOf(d.id);
  let picked = draft.option ? { option: draft.option } : null; // { option } | { decision }
  const confirm = card.querySelector('.dec__confirm');
  const pickedTxt = card.querySelector('.dec__picked');
  const free = card.querySelector(`#dec-free-${d.id}`);
  const why = card.querySelector(`#dec-why-${d.id}`);
  const until = card.querySelector('.dec__until');
  free.value = draft.free;
  why.value = draft.why;
  until.value = draft.until;

  /* dónde escribe: se apunta al entrar y al moverse; se olvida solo si sale ÉL. Un campo arrancado
     por el repintado también pierde el foco, y Chromium lo avisa ANTES de sacarlo de la página
     (medido: isConnected aún true); por eso se mira en la vuelta siguiente, cuando el repintado
     ya terminó: si el campo sigue en la página y el foco no volvió a él, salió Pepe. */
  for (const [field, el] of [
    ['free', free],
    ['why', why],
    ['swhy', card.querySelector(`#dec-swhy-${d.id}`)],
  ]) {
    // swhy: #919
    const mark = () => {
      draft.focus = { field, start: el.selectionStart, end: el.selectionEnd };
    };
    for (const ev of ['focus', 'input', 'keyup', 'mouseup', 'select']) el.addEventListener(ev, mark);
    el.addEventListener('blur', () =>
      setTimeout(() => {
        if (el.isConnected && document.activeElement !== el && draft.focus?.field === field) draft.focus = null;
      }, 0),
    );
  }
  until.addEventListener('change', () => {
    draft.until = until.value;
  });
  wireSust(card, d, draft);

  const sync = () => {
    const freeTxt = free.value.trim();
    if (freeTxt) picked = { decision: freeTxt };
    else if (picked?.decision) picked = null;
    draft.free = free.value;
    draft.why = why.value;
    draft.option = picked?.option ?? null;
    opts
      .querySelectorAll('.dec__opt')
      .forEach((b) => b.classList.toggle('is-picked', Boolean(picked?.option) && b.dataset.label === picked.option));
    confirm.disabled = !picked || !why.value.trim();
    /* #314/reel: se escribe al pulsar, DESPUÉS de que applyI18n pasara, así que se traduce aquí */
    pickedTxt.textContent = picked
      ? tr(picked.option ? `elegida: «${picked.option}»` : 'decisión propia') +
        (why.value.trim() ? '' : ' ' + tr('— falta el porqué'))
      : tr('elige una opción o escribe la tuya');
  };

  for (const o of d.options ?? []) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'dec__opt';
    b.dataset.label = o.label;
    b.innerHTML = `<span class="dec__opt-label">${esc(o.label)}</span><span class="dec__opt-text">${esc(o.text)}</span>`;
    b.addEventListener('click', () => {
      picked = { option: o.label };
      free.value = '';
      sync();
      why.focus();
    });
    opts.appendChild(b);
  }
  free.addEventListener('input', sync);
  why.addEventListener('input', sync);
  confirm.addEventListener('click', () => {
    if (!picked || !why.value.trim()) return;
    confirm.disabled = true;
    handlers.onDecide(d, { ...picked, rationale: why.value.trim(), ...sustPayload(draft) });
  });
  card.querySelector('.dec__defer-btn').addEventListener('click', () => {
    const until = card.querySelector('.dec__until').value || null;
    handlers.onDefer(d, until);
  });
  sync();
  return card;
}

/* #919: al decidir, a qué decisiones anteriores sustituye y hasta cuándo vale. Plegado: lo normal es no tocarlo.
   Candidatas: las decididas que siguen vivas (una superada ya no se puede volver a sustituir: el daemon da 400). */
function sustBlock(d, state) {
  const draft = draftOf(d.id);
  const cand = (state.wfDecisions ?? []).filter(
    (x) => x.status === 'decided' && x.superseded_by == null && x.id !== d.id,
  );
  const lista = cand.length
    ? cand
        .map(
          (x) =>
            `<label class="dec__sust-item"><input type="checkbox" value="${x.id}"${draft.sust.includes(x.id) ? ' checked' : ''}>` +
            ` <span class="dec__id">#${x.id}</span> <span class="dec__sust-t">${esc(x.title)}</span></label>`,
        )
        .join('')
    : `<p class="hint">${tr('No hay decisiones vivas que sustituir.')}</p>`;
  return `
    <details class="dec__sust"${draft.sustOpen || draft.sust.length ? ' open' : ''}>
      <summary>${tr('Sustituye a una decisión anterior')}</summary>
      <div class="dec__sust-list">${lista}</div>
      <div class="dec__sust-kind" role="radiogroup">
        <label><input type="radio" name="dec-skind-${d.id}" value="total"${draft.sustKind === 'total' ? ' checked' : ''}> <span>${tr('del todo')}</span></label>
        <label><input type="radio" name="dec-skind-${d.id}" value="parcial"${draft.sustKind === 'parcial' ? ' checked' : ''}> <span>${tr('en parte')}</span></label>
      </div>
      <label for="dec-swhy-${d.id}">${tr('Por qué la sustituye')}</label>
      <input type="text" id="dec-swhy-${d.id}" value="${esc(draft.sustWhy)}">
    </details>
    <div class="dec__valid-in">
      <label for="dec-valid-${d.id}">${tr('Vale hasta (opcional)')}</label>
      <input type="date" id="dec-valid-${d.id}" value="${esc(draft.valid)}">
    </div>`;
}

function wireSust(card, d, draft) {
  const box = card.querySelector('.dec__sust');
  box?.addEventListener('toggle', () => {
    draft.sustOpen = box.open;
  });
  card.querySelectorAll('.dec__sust-item input').forEach((cb) =>
    cb.addEventListener('change', () => {
      const id = Number(cb.value);
      draft.sust = cb.checked ? [...new Set([...draft.sust, id])] : draft.sust.filter((x) => x !== id);
    }),
  );
  card.querySelectorAll(`input[name="dec-skind-${d.id}"]`).forEach((r) =>
    r.addEventListener('change', () => {
      if (r.checked) draft.sustKind = r.value;
    }),
  );
  card.querySelector(`#dec-swhy-${d.id}`)?.addEventListener('input', (e) => {
    draft.sustWhy = e.target.value;
  });
  card.querySelector(`#dec-valid-${d.id}`)?.addEventListener('change', (e) => {
    draft.valid = e.target.value;
  });
}

function sustPayload(draft) {
  const out = {};
  if (draft.sust.length) {
    out.supersedes = draft.sust;
    out.supersede_kind = draft.sustKind;
    if (draft.sustWhy.trim()) out.supersede_reason = draft.sustWhy.trim();
  }
  if (draft.valid) out.valid_until = draft.valid;
  return out;
}

function renderDecided(d, reemplaza) {
  drafts.delete(d.id); // #738: decidida, el borrador ya no hace falta
  const card = document.createElement('article');
  card.className = `dec dec--decided${d.superseded_by != null ? ' dec--superada' : ''}`;
  card.dataset.id = d.id;
  const chosen = (d.options ?? []).find((o) => o.label === d.decision);
  /* #919: quién decide de verdad (decidido_por) puede no ser quien pulsó (decided_by): se dicen los dos */
  const q = quienDecide(d);
  const por =
    q && q.quien !== d.decided_by
      ? `<b>${esc(q.quien)}</b> (<span>${esc(tr(TIPO[q.tipo] ?? q.tipo ?? ''))}</span>) · <span>${tr('la registró')}</span> <b>${esc(d.decided_by ?? '—')}</b>`
      : `<b>${esc(d.decided_by ?? q?.quien ?? '—')}</b>`;
  card.innerHTML = `
    <div class="dec__top">
      <span class="dec__id">#${d.id}</span>
      <span class="dec__by">decidida por ${por} · ${fmtWhen(d.decided_at)} · <span>planteada por</span> <b>${esc(d.asked_by)}</b></span>
    </div>
    ${supersedeLines(d, reemplaza)}
    <h3 class="dec__title">${esc(d.title)}</h3>
    <div class="dec__verdict">
      <span class="dec__verdict-label">${esc(d.decision ?? '')}</span>
      ${chosen && chosen.text !== d.decision ? `<span class="dec__verdict-text">${esc(chosen.text)}</span>` : !chosen ? '<span class="dec__verdict-text">decisión propia, fuera de las opciones</span>' : ''}
    </div>
    ${d.rationale ? `<div class="dec__rationale"><span class="dec__rationale-k">por qué</span> — ${esc(d.rationale)}</div>` : ''}
    <details class="dec__hist">
      <summary>historial · ${(d.history ?? []).length}</summary>
      <ul class="hist">${(d.history ?? []).map((h) => `<li><time>${fmtWhen(h.changed_at)}</time><span><b>${esc(h.actor)}</b> <span>${esc(EVENT[h.event] ?? h.event)}</span>${surfaceTag(h)}${h.note ? ' — ' + esc(h.note) : ''}</span></li>`).join('')}</ul>
    </details>`;
  return card;
}

/* ---------------- #919: «Lo que vale hoy» ----------------
   GET /api/decisions/vigentes tarda 12-25 s (EcoDB hace dos búsquedas) y cada aviso de cambio repinta el panel
   entero: la búsqueda vive aquí, fuera del DOM, como los borradores de #738. Cada búsqueda lleva su número y una
   respuesta vieja no pisa a una nueva. El orden es el de la respuesta (relevancia), no la fecha. */
const vig = { tema: '', superadas: false, estado: 'quieto', res: null, err: null, seq: 0, buscado: '', foco: null };

function vigBlock(handlers) {
  const box = document.createElement('section');
  box.className = 'dec-vig';
  box.setAttribute('aria-label', tr('Lo que vale hoy'));
  box.innerHTML = `
    <form class="dec-vig__form">
      <label for="dec-vig-tema" class="dec-vig__k">${tr('Lo que vale hoy')}</label>
      <div class="dec-vig__row">
        <input type="search" id="dec-vig-tema" autocomplete="off" value="${esc(vig.tema)}">
        <button type="submit" class="btn btn--primary btn--sm">${tr('Buscar')}</button>
      </div>
      <label class="dec-vig__sup"><input type="checkbox"${vig.superadas ? ' checked' : ''}> <span>${tr('Incluir las superadas')}</span></label>
    </form>
    <div class="dec-vig__out" aria-live="polite"></div>`;
  const input = box.querySelector('#dec-vig-tema');
  const sup = box.querySelector('.dec-vig__sup input');
  input.addEventListener('input', () => {
    vig.tema = input.value;
  });
  for (const ev of ['focus', 'input', 'keyup', 'mouseup'])
    input.addEventListener(ev, () => {
      vig.foco = { start: input.selectionStart, end: input.selectionEnd };
    });
  input.addEventListener('blur', () =>
    setTimeout(() => {
      if (input.isConnected && document.activeElement !== input) vig.foco = null;
    }, 0),
  );
  sup.addEventListener('change', () => {
    vig.superadas = sup.checked;
  });
  box.querySelector('form').addEventListener('submit', (ev) => {
    ev.preventDefault();
    buscar(handlers);
  });
  paintVig(box.querySelector('.dec-vig__out'));
  if (vig.foco)
    setTimeout(() => {
      if (input.isConnected) {
        input.focus({ preventScroll: true });
        input.setSelectionRange(vig.foco?.start ?? input.value.length, vig.foco?.end ?? input.value.length);
      }
    }, 0);
  return box;
}

async function buscar(handlers) {
  const tema = vig.tema.trim();
  if (tema.length < 3) {
    vig.estado = 'corto';
    repaintVig();
    return;
  }
  const n = ++vig.seq;
  Object.assign(vig, { estado: 'cargando', buscado: tema, err: null });
  repaintVig();
  try {
    const res = await handlers.buscarVigentes(tema, vig.superadas);
    if (n !== vig.seq) return; // llegó tarde: ya hay otra búsqueda
    Object.assign(vig, { estado: 'listo', res });
  } catch (e) {
    if (n !== vig.seq) return;
    Object.assign(vig, { estado: 'error', err: e?.body?.reason ?? e?.message ?? String(e) });
  }
  repaintVig();
}

const repaintVig = () => document.querySelectorAll('.dec-vig__out').forEach(paintVig);

const refId = (id) => {
  const m = /^faros:decision:(\d+)$/.exec(String(id));
  return m ? m[1] : null;
};
const refLink = (id) => {
  const n = refId(id);
  return n
    ? `<a href="#" class="dec__goto" data-goto="${n}">#${n}</a>`
    : `<span class="dec-vig__mem">${tr('memoria de EcoDB')}</span>`;
};

// el daemon corta cada línea a 200 caracteres, a veces a mitad de palabra: se acaba en la última palabra entera y con «…»
const LINEA_MAX = 200;
function lineaVig(s) {
  s = s ?? '';
  if (s.length < LINEA_MAX) return s;
  const corte = s.lastIndexOf(' ');
  return (corte > LINEA_MAX * 0.75 ? s.slice(0, corte) : s).replace(/[\s,.;:·—-]+$/, '') + '…';
}

function filaVig(f, superada) {
  const n = refId(f.id);
  const origen =
    f.fuente === 'FarOS'
      ? `<span class="dec-vig__src dec-vig__src--faros">FarOS</span> ${n ? `<a href="#" class="dec__goto" data-goto="${n}">#${n}</a>` : ''}`
      : `<span class="dec-vig__src dec-vig__src--ecodb">EcoDB</span>`;
  const extra = [];
  if (f.sustituye_a?.length) extra.push(`<span>${tr('sustituye a')}</span> ${f.sustituye_a.map(refLink).join(', ')}`);
  if (superada)
    extra.push(
      `<span>${tr('superada por')}</span> ${refLink(f.superada_por)}${f.motivo ? ` — <span class="dec__super-why">${esc(f.motivo)}</span>` : ''}`,
    );
  if (f.valid_until)
    extra.push(
      `<span>${tr('vale hasta el')}</span> <time datetime="${esc(f.valid_until)}">${esc(fmtDate(f.valid_until))}</time>`,
    );
  return `<li class="dec-vig__fila${superada ? ' dec-vig__fila--superada' : ''}">
      <div class="dec-vig__meta">${origen} · <time datetime="${esc(f.fecha ?? '')}">${esc(f.fecha ? fmtDate(f.fecha) : '—')}</time>${f.agente ? ` · <b>${esc(f.agente)}</b>` : ''}</div>
      <p class="dec-vig__linea">${esc(lineaVig(f.linea))}</p>
      ${extra.length ? `<div class="dec-vig__extra">${extra.join(' · ')}</div>` : ''}
    </li>`;
}

function paintVig(out) {
  if (vig.estado === 'quieto') {
    out.innerHTML = `<p class="hint">${tr('Escribe un tema y busca: sale lo decidido en FarOS y lo acordado en EcoDB que sigue valiendo.')}</p>`;
    return;
  }
  if (vig.estado === 'corto') {
    out.innerHTML = `<p class="hint">${tr('Escribe al menos tres letras.')}</p>`;
    return;
  }
  if (vig.estado === 'cargando') {
    out.innerHTML = `<p class="dec-vig__cargando"><span class="dec-vig__punto" aria-hidden="true"></span> <span>${tr('Buscando en FarOS y en EcoDB. Tarda unos 20 segundos; puedes seguir usando la app.')}</span></p>`;
    return;
  }
  if (vig.estado === 'error') {
    out.innerHTML = `<p class="dec-vig__error"><span>${tr('No se pudo buscar:')}</span> ${esc(vig.err)}</p>`;
    return;
  }
  const r = vig.res ?? {};
  const vigentes = r.vigentes ?? [],
    superadas = r.superadas ?? [],
    errores = r.errores ?? [],
    avisos = r.avisos ?? [];
  const partes = [];
  partes.push(
    vigentes.length
      ? `<ol class="dec-vig__lista">${vigentes.map((f) => filaVig(f, false)).join('')}</ol>`
      : `<p class="hint">${tr('Nada vale hoy sobre ese tema.')}</p>`,
  );
  if (errores.length)
    partes.push(
      `<div class="dec-vig__errores"><span class="dec__super-k">${tr('errores')}</span><ul>${errores
        .map(
          (e) =>
            `<li>${e.ciclo ? `<span>${tr('ciclo de sustituciones')}</span> ${e.ciclo.map(refLink).join(' → ')}` : `<span>${tr('la sustituta no se puede leer')}</span> ${refLink(e.de)}`}</li>`,
        )
        .join('')}</ul></div>`,
    );
  if (superadas.length)
    partes.push(
      `<details class="dec-vig__sup-lista"><summary>Superadas · ${superadas.length}</summary><ol class="dec-vig__lista">${superadas.map((f) => filaVig(f, true)).join('')}</ol></details>`,
    );
  if (avisos.length)
    partes.push(`<ul class="dec-vig__avisos">${avisos.map((a) => `<li>${esc(a)}</li>`).join('')}</ul>`);
  out.innerHTML = partes.join('');
}
