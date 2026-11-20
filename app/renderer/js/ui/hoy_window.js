/* #1319 (Lienzo): «Qué sale en Hoy». Un botón de ajustes pegado a la pestaña «Hoy» y, debajo, tres casillas
   que son el ajuste today_window del motor ({overdue, due_today, in_progress}). Cada casilla se aplica AL MOMENTO:
   PATCH /api/settings con las tres y recarga del tablero. Si el PATCH falla la casilla vuelve a como estaba.
   Las propuestas y las que esperan verificación salen siempre (#604, v3.1): no son casillas, se dicen en una línea.
   El valor vive en el daemon: se lee con GET /api/settings al arrancar y cada vez que se abre el panel.
   El panel va colgado de <body> y en posición fija (la barra móvil tiene overflow-x y lo recortaría). */
import { t as tr } from '../i18n.js';

const KEYS = [
  ['overdue', 'Atrasadas'],
  ['due_today', 'Las de hoy'],
  ['in_progress', 'Empezadas'],
];
const TODOS = { overdue: true, due_today: true, in_progress: true };

let win = { ...TODOS };
let pop = null;
let btn = null;
let leer = async () => {};
let leido = false;

const normaliza = (w) => ({ ...TODOS, ...(w && typeof w === 'object' ? w : {}) });
const filtrado = () => KEYS.some(([k]) => !win[k]);

function pintaBoton() {
  if (!btn) return;
  btn.classList.toggle('has-dot', filtrado());
  btn.dataset.filtrado = filtrado() ? '1' : '0';
}

function colocar() {
  if (!pop || pop.hidden || !btn) return;
  const r = btn.getBoundingClientRect();
  const w = pop.offsetWidth,
    vw = document.documentElement.clientWidth;
  pop.style.top = `${Math.round(r.bottom + 8)}px`;
  pop.style.left = `${Math.round(Math.max(8, Math.min(r.left, vw - w - 8)))}px`;
}

function pintaPanel() {
  KEYS.forEach(([k]) => {
    const i = pop.querySelector(`input[data-k="${k}"]`);
    if (i) i.checked = !!win[k];
  });
}

export function cerrarHoyWindow({ devolverFoco = false } = {}) {
  if (!pop || pop.hidden) return;
  pop.hidden = true;
  btn.setAttribute('aria-expanded', 'false');
  if (devolverFoco) btn.focus();
}

async function abrir(api) {
  pop.hidden = false;
  btn.setAttribute('aria-expanded', 'true');
  pintaPanel();
  colocar();
  pop.querySelector('input')?.focus();
  /* lo guardado manda: si otro lo cambió desde fuera, se ve al abrir */
  try {
    win = normaliza((await api.settings())?.today_window);
    pintaBoton();
    pintaPanel();
  } catch {
    /* sin lectura se queda lo último conocido */
  }
}

function construir() {
  pop = document.createElement('div');
  pop.id = 'hoy-pop';
  pop.className = 'hoypop';
  pop.setAttribute('role', 'dialog');
  pop.setAttribute('aria-label', tr('Qué sale en Hoy'));
  pop.hidden = true;
  pop.innerHTML = `
    <p class="hoypop__title">Qué sale en Hoy</p>
    ${KEYS.map(
      ([k, txt]) => `
      <label class="hoypop__row"><input type="checkbox" data-k="${k}"><span>${txt}</span></label>`,
    ).join('')}
    <p class="hoypop__note">Las propuestas y las que esperan verificación salen siempre.</p>`;
  document.body.appendChild(pop);
}

export function initHoyWindow({ api, apply, fail }) {
  btn = document.getElementById('hoy-filter');
  if (!btn) return;
  construir();
  pintaBoton();
  /* api() aún no está conectada cuando se llama a init: se lee la primera vez que el botón se pinta en Hoy */
  leer = async () => {
    try {
      win = normaliza((await api().settings())?.today_window);
      leido = true;
      pintaBoton();
    } catch {
      leido = false; /* se reintenta al repintar */
    }
  };

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (pop.hidden) abrir(api());
    else cerrarHoyWindow();
  });
  pop.addEventListener('change', async (e) => {
    const i = e.target.closest('input[data-k]');
    if (!i) return;
    const antes = { ...win };
    win = { ...win, [i.dataset.k]: i.checked };
    pintaBoton();
    try {
      await apply({ ...win });
    } catch (err) {
      win = antes;
      pintaBoton();
      pintaPanel();
      fail(err);
    }
  });
  document.addEventListener('pointerdown', (e) => {
    if (pop.hidden || pop.contains(e.target) || btn.contains(e.target)) return;
    cerrarHoyWindow();
  });
  document.addEventListener(
    'keydown',
    (e) => {
      if (e.key === 'Escape' && !pop.hidden) {
        e.stopPropagation();
        cerrarHoyWindow({ devolverFoco: true });
      }
    },
    true,
  );
  pop.addEventListener('focusout', (e) => {
    const a = e.relatedTarget;
    if (a && !pop.contains(a) && a !== btn) cerrarHoyWindow();
  });
  window.addEventListener('resize', colocar);
}

/* Solo vale en Hoy + Kanban: en el Calendario el Día no usa el ajuste. */
export function renderHoyWindow(state) {
  if (!btn) return;
  const ver = state.view === 'today' && state.boardMode !== 'calendar';
  btn.hidden = !ver;
  if (ver && !leido) {
    leido = true;
    leer().then(() => {});
  }
  if (!ver) cerrarHoyWindow();
}
