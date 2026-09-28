/* ============================================================
   transitions.js — espejo UI de la máquina de estados (§4).
   SOLO para affordance (dónde puede caer una tarjeta) y para los
   botones del panel. La verdad la tiene el motor: si discrepa,
   el 409 manda y la UI lo enseña. Vocabulario de acciones
   pendiente de confirmar con Hilo (flag en el handoff).
   ============================================================ */

import { t } from './i18n.js';

export const TRANSITIONS = {
  proposed:    { accepted: 'accept', rejected: 'reject' },
  accepted:    { in_progress: 'start', done: 'complete', blocked: 'block', rejected: 'reject' },
  in_progress: { done: 'complete', blocked: 'block', accepted: 'backlog' },
  /* v3.1 T2.1/T2.3 (el dueño §1.3): una hecha o verificada se DEVUELVE con motivo obligatorio;
     la historia (hecha, verificada, devuelta y por qué) se queda en el ticket */
  done:        { verified: 'verify', in_progress: 'rework', accepted: 'rework' },
  verified:    { in_progress: 'rework', accepted: 'rework' },
  blocked:     { in_progress: 'unblock', rejected: 'reject' },
  rejected:    {},
  expired:     { accepted: 'accept' },
};

export const ACTION_LABEL = {
  accept:   'Aceptar',
  start:    'Empezar',
  complete: 'Completar…',   // pide evidencia
  verify:   'Verificar',
  block:    'Bloquear…',    // pide motivo
  unblock:  'Desbloquear',
  reject:   'Rechazar…',
  reopen:   'Reabrir',
  rework:   'Devolver…',      // pide motivo (≥ 10 caracteres)
  backlog:  'Devolver a aceptadas',
};

export const COLUMNS = [
  { status: 'proposed',    title: 'Propuestas',  note: null,             empty: 'Sin propuestas de los agentes.' },
  { status: 'accepted',    title: 'Aceptadas',   note: null,             empty: '—' },
  { status: 'in_progress', title: 'En curso',    note: null,             empty: '—' },
  { status: 'done',        title: 'Hechas',      note: 'sin verificar',  empty: '—' },
  { status: 'verified',    title: 'Verificadas', note: null,             empty: '—' },
];

/* #314 (forma 4b): la plantilla pega la etiqueta a un dato —`FIX #${id} · ${STATUS_LABEL[s]}`—
   y el catálogo no casa esa cadena entera, así que se quedaba en español. Se envuelve la
   BÚSQUEDA aquí, una vez: cada consulta devuelve la etiqueta ya pasada por t(), con el idioma
   del momento. Una clave que no existe sigue dando undefined, y el `?? status` de fuera vale. */
export const STATUS_LABEL = new Proxy({
  proposed: 'propuesta', accepted: 'aceptada', in_progress: 'en curso',
  done: 'hecha (sin verificar)', verified: 'verificada',
  blocked: 'bloqueada', rejected: 'rechazada', expired: 'expirada',
}, { get: (o, k) => (Object.hasOwn(o, k) ? t(o[k]) : undefined) });
