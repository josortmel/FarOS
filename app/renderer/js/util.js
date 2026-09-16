/* util.js — helpers de formato */

/* #262: los meses y los dias ya no son arrays fijos en espanol — salen de Intl con el
   locale activo (i18n.js). Un array de meses es una DECLARACION que no se entera de que
   la interfaz cambio de idioma, y ademas rompe en silencio: un «sep» dentro de una UI en
   ingles no lanza ningun error. */
import { mesCorto as MES, diaCorto as DIA_CORTO, diaLetra, t as t2 } from './i18n.js';

export function fmtWhen(iso) {
  if (!iso) return '—';
  if (iso.length === 10) return fmtDate(iso); // solo fecha (last_done_at, next_due): sin hora inventada
  const d = new Date(iso);
  const now = new Date();
  const hhmm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) return `${t2('hoy')} ${hhmm}`;   // #262: etiqueta aparte del dato
  const ayer = new Date(now.getTime() - 864e5);
  if (d.toDateString() === ayer.toDateString()) return `${t2('ayer')} ${hhmm}`;
  return `${d.getDate()} ${MES(d.getMonth())} ${hhmm}`;
}

export function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso.length === 10 ? iso + 'T00:00:00' : iso);
  return `${d.getDate()} ${MES(d.getMonth())}`;
}

/* ---- T2.10: tiempo en la tarjeta, en palabras de la Casa ---- */


/* «jue 3» / «hoy» / «mañana» — de un ISO local (fecha o fecha-hora) */
export function fmtDayShort(iso) {
  if (!iso) return '';
  const d = new Date(iso.length === 10 ? iso + 'T00:00:00' : iso);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const day = new Date(d); day.setHours(0, 0, 0, 0);
  const diff = Math.round((day - today) / 864e5);
  if (diff === 0) return t2('hoy');
  if (diff === 1) return t2('mañana');
  if (diff === -1) return t2('ayer');
  const base = `${DIA_CORTO(d.getDay())} ${d.getDate()}`;
  return d.getMonth() === today.getMonth() && d.getFullYear() === today.getFullYear()
    ? base : `${base} ${MES(d.getMonth())}`;
}

/* «09:00–10:00» / «09:00» / «todo el día» */
export function fmtTimeRange(scheduledAt, durationMin, allDay) {
  if (allDay) return t2('todo el día');
  if (!scheduledAt || scheduledAt.length < 16) return '';
  const start = scheduledAt.slice(11, 16);
  if (!durationMin) return start;
  const [h, m] = start.split(':').map(Number);
  const end = new Date(2000, 0, 1, h, m + Number(durationMin));
  return `${start}–${String(end.getHours()).padStart(2, '0')}:${String(end.getMinutes()).padStart(2, '0')}`;
}

/* la línea «cuándo está programada» de una tarea; '' si no hay nada que decir */
export function fmtWhenScheduled(t) {
  if (t.scheduled_at) return `${fmtDayShort(t.scheduled_at)} · ${fmtTimeRange(t.scheduled_at, t.duration_min, t.all_day)}`;
  if (t.all_day && t.due_at) return `${fmtDayShort(t.due_at)} · ${t2('todo el día')}`;
  /* #262: «próxima» es interfaz y lo que le sigue es dato, en el MISMO nodo de texto — que
     es justo lo que un catálogo de cadena completa no puede alcanzar. Se traduce la palabra
     aquí, que es donde se sabe cuál es cuál. Si no, «próxima» se queda como la única palabra
     en español de la pantalla más mirada de la app. */
  if (t.cadence_days && t.next_due) return `${t2('próxima')} ${fmtDayShort(t.next_due)}${t.preferred_time ? ' · ' + t.preferred_time : ''}`;
  return '';
}

/* schedule v3 (T1.5) en palabras: manual · once · recurring (varias horas) · weekly */
/* #262/#271: la inicial la da Intl con el locale activo; el desfase de indice
   (0=lunes en el motor, 0=domingo en Intl) lo resuelve diaLetra. */
function fmtHours(at) {
  const hs = Array.isArray(at) ? at : at ? [at] : [];
  if (!hs.length) return '';
  if (hs.length <= 3) return hs.join(t2(' y '));
  return t2(`${hs.length} veces (${hs[0]}–${hs[hs.length - 1]})`);
}
export function fmtSchedule(s) {
  if (!s) return '—';
  if (typeof s === 'string') { try { s = JSON.parse(s); } catch { return s; } }
  if (s.type === 'manual') return t2('manual (solo botón)');
  if (s.type === 'once') return `${t2('una vez')} · ${fmtWhen(s.run_at)}`;
  if (s.type === 'recurring') {
    const d = s.every_days === 1 ? t2('diaria') : s.every_days === 7 ? t2('semanal') : t2(`cada ${s.every_days} días`);
    return `${d} · ${fmtHours(s.at)}`;
  }
  if (s.type === 'weekly') {
    const days = (s.weekdays ?? []).map(i => diaLetra(i) ?? '?').join(' ');
    return `${days || t2('sin días')} · ${fmtHours(s.at)}`;
  }
  return JSON.stringify(s);
}

/* el output de un run llega como texto (fixtures) o como {run_id, output} (motor v3) */
export function runOutputText(res) {
  if (res == null) return '';
  if (typeof res === 'string') return res;
  if ('output' in res) return res.output ?? '(sin output todavía)';
  return JSON.stringify(res, null, 2);
}

/* coste en dólares legible: el motor manda 0.21757325 */
export function fmtUsd(n) {
  if (n == null || Number.isNaN(Number(n))) return '';
  const x = Number(n);
  return x >= 0.01 || x === 0 ? x.toFixed(2) : x.toFixed(3);
}

/* «en 2 h 10 min» / «hace 3 min» — para próximas ejecuciones y runs vivos */
export function fmtRelative(iso) {
  if (!iso) return '';
  const ms = new Date(iso) - Date.now();
  const abs = Math.abs(ms), m = Math.round(abs / 6e4);
  let txt;
  if (m < 1) return t2('ahora');
  if (m < 60) txt = `${m} min`;
  else if (m < 60 * 36) txt = `${Math.floor(m / 60)} h${m % 60 ? ' ' + (m % 60) + ' min' : ''}`;
  else txt = `${Math.round(m / 1440)} d`;
  /* #262: «en»/«hace» son interfaz y la cantidad es dato. La cadena entera va al catalogo
     con la cifra sacada a {n} — «en 14 d» y «en 3 d» son una sola entrada. */
  return t2(ms > 0 ? `en ${txt}` : `hace ${txt}`);
}

/* color de proyecto con respaldo: los proyectos migrados pueden venir sin
   color — paleta de marca determinista por id, nunca un punto invisible */
const PROJECT_FALLBACK = [
  'oklch(58% 0.12 50)',   /* naranja apagado — el primero es de la casa */
  'oklch(52% 0.10 240)',  /* azul acero */
  'oklch(50% 0.10 150)',  /* verde bosque */
  'oklch(52% 0.11 310)',  /* malva */
  'oklch(48% 0.09 85)',   /* ocre */
  'oklch(50% 0.10 15)',   /* teja */
];
export function projectColor(p) {
  if (p?.color) return p.color;
  return PROJECT_FALLBACK[(p?.id ?? 0) % PROJECT_FALLBACK.length];
}

export function esc(s) {
  const div = document.createElement('div');
  div.textContent = s ?? '';
  return div.innerHTML;
}

/* errores paral dueño, no para ingenieros: la razón del motor se respeta
   (es texto humano); lo que se traduce es la tripa de red */
export function humanError(err) {
  /* #262 + #270: si el motor manda una clave traducible (err.code, contrato de Hilo) y
     el catálogo la conoce, manda ella. Si no la manda —solo 17 de 211 sitios la traen— o
     no está en el catálogo, se cae a `reason`, que viene en español a propósito. La caída
     tiene que ser silenciosa: un error a medio traducir sigue siendo un error legible. */
  const porCodigo = err?.code ? t2('error.' + err.code) : null;
  if (porCodigo && porCodigo !== 'error.' + err.code) return porCodigo;

  const raw = err?.reason ?? err?.message ?? String(err);
  /* estos dos no vienen del motor: los fabrica el navegador cuando no hay nadie al otro
     lado, así que no tienen código y se traducen por cadena, como el resto de la interfaz */
  if (/failed to fetch|networkerror|err_connection/i.test(raw)) {
    return t2('el motor no responde — ¿está la app arrancada?');
  }
  if (/timeout|timed out/i.test(raw)) return t2('el motor tardó demasiado en responder');
  return t2(raw);
}
