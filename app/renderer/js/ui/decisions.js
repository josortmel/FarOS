/* ============================================================
   decisions.js — Bisagras (T3.4). Decisión 7 del dueño: sustituyen
   a los bisagra_*.md. Lo que el dueño tiene que decidir, con las
   opciones ENTERAS como botones (nunca A/B/C), un motivo, y lo ya
   decidido con quién, cuándo y por qué. Append-only: no se decide
   dos veces (el 409 del motor se enseña).
   ============================================================ */

import { t as tr } from '../i18n.js';
import { fmtWhen, fmtDate, esc } from '../util.js';

const EVENT = { asked: 'planteada', decided: 'decidida', deferred: 'aplazada', reopened: 'retomada' };

export function pendingCount(list) {
  return (list ?? []).filter(d => d.status !== 'decided').length;
}

/* pendientes y aplazadas arriba (aplazadas al final del grupo), decididas debajo */
export function renderDecisionsPane(pane, state, handlers) {
  const list = state.wfDecisions;
  const head = document.createElement('div');
  head.className = 'pane__head';
  head.innerHTML = `<span class="pane__title">${tr('Bisagras')} · ${esc(state.workflow.name)}</span>
    <span class="pane__hint">Lo que necesita tu palabra. Las opciones son literales: pulsa la que va, escribe por qué. Queda registrado y no se decide dos veces.</span>`;
  pane.appendChild(head);

  if (list == null) { pane.insertAdjacentHTML('beforeend', '<div class="pane__empty">cargando…</div>'); return; }
  if (!list.length) { pane.insertAdjacentHTML('beforeend', '<div class="pane__empty">Sin bisagras en este workflow. Los agentes las plantean por MCP (ask_decision).</div>'); return; }

  const open = list.filter(d => d.status !== 'decided')
    .sort((a, b) => (a.status === 'deferred') - (b.status === 'deferred') || a.created_at.localeCompare(b.created_at));
  const done = list.filter(d => d.status === 'decided')
    .sort((a, b) => (b.decided_at ?? '').localeCompare(a.decided_at ?? ''));

  if (open.length) {
    const g = document.createElement('div');
    g.className = 'dec__group';
    g.textContent = `Pendientes · ${open.length}`;
    pane.appendChild(g);
    for (const d of open) pane.appendChild(renderPending(d, state, handlers));
  }
  if (done.length) {
    const g = document.createElement('div');
    g.className = 'dec__group';
    g.textContent = `Decididas · ${done.length}`;
    pane.appendChild(g);
    for (const d of done) pane.appendChild(renderDecided(d));
  }
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
    <div class="dec__actions">
      <button type="button" class="btn btn--primary dec__confirm" disabled>Decidir</button>
      <span class="dec__picked hint">elige una opción o escribe la tuya</span>
      <span class="dec__defer">
        <input type="date" class="dec__until" aria-label="Aplazar hasta">
        <button type="button" class="btn btn--ghost btn--sm dec__defer-btn">Aplazar…</button>
      </span>
    </div>`;

  const opts = card.querySelector('.dec__options');
  let picked = null; // { option } | { decision }
  const confirm = card.querySelector('.dec__confirm');
  const pickedTxt = card.querySelector('.dec__picked');
  const free = card.querySelector(`#dec-free-${d.id}`);
  const why = card.querySelector(`#dec-why-${d.id}`);

  const sync = () => {
    const freeTxt = free.value.trim();
    if (freeTxt) picked = { decision: freeTxt };
    else if (picked?.decision) picked = null;
    opts.querySelectorAll('.dec__opt').forEach(b => b.classList.toggle('is-picked', Boolean(picked?.option) && b.dataset.label === picked.option));
    confirm.disabled = !picked || !why.value.trim();
    pickedTxt.textContent = picked ? (picked.option ? `elegida: «${picked.option}»` : 'decisión propia') + (why.value.trim() ? '' : ' — falta el porqué') : 'elige una opción o escribe la tuya';
  };

  for (const o of d.options ?? []) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'dec__opt';
    b.dataset.label = o.label;
    b.innerHTML = `<span class="dec__opt-label">${esc(o.label)}</span><span class="dec__opt-text">${esc(o.text)}</span>`;
    b.addEventListener('click', () => { picked = { option: o.label }; free.value = ''; sync(); why.focus(); });
    opts.appendChild(b);
  }
  free.addEventListener('input', sync);
  why.addEventListener('input', sync);
  confirm.addEventListener('click', () => {
    if (!picked || !why.value.trim()) return;
    confirm.disabled = true;
    handlers.onDecide(d, { ...picked, rationale: why.value.trim() });
  });
  card.querySelector('.dec__defer-btn').addEventListener('click', () => {
    const until = card.querySelector('.dec__until').value || null;
    handlers.onDefer(d, until);
  });
  sync();
  return card;
}

function renderDecided(d) {
  const card = document.createElement('article');
  card.className = 'dec dec--decided';
  card.dataset.id = d.id;
  const chosen = (d.options ?? []).find(o => o.label === d.decision);
  card.innerHTML = `
    <div class="dec__top">
      <span class="dec__id">#${d.id}</span>
      <span class="dec__by">decidida por <b>${esc(d.decided_by ?? '—')}</b> · ${fmtWhen(d.decided_at)} · planteada por ${esc(d.asked_by)}</span>
    </div>
    <h3 class="dec__title">${esc(d.title)}</h3>
    <div class="dec__verdict">
      <span class="dec__verdict-label">${esc(d.decision ?? '')}</span>
      ${chosen && chosen.text !== d.decision ? `<span class="dec__verdict-text">${esc(chosen.text)}</span>` : (!chosen ? '<span class="dec__verdict-text">decisión propia, fuera de las opciones</span>' : '')}
    </div>
    ${d.rationale ? `<div class="dec__rationale">${esc(d.rationale)}</div>` : ''}
    <details class="dec__hist">
      <summary>historial · ${(d.history ?? []).length}</summary>
      <ul class="hist">${(d.history ?? []).map(h => `<li><time>${fmtWhen(h.changed_at)}</time><span><b>${esc(h.actor)}</b> ${esc(EVENT[h.event] ?? h.event)}${h.note ? ' — ' + esc(h.note) : ''}</span></li>`).join('')}</ul>
    </details>`;
  return card;
}
