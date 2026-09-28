/* ============================================================
   i18n.js — #262: la interfaz en dos idiomas, con cambio en caliente.

   POR QUÉ ASÍ Y NO CON t() EN CADA LITERAL. El renderer pinta con plantillas
   donde el texto va partido por interpolaciones (`El job está ${j.status}.`),
   así que envolver 257 literales a mano o con un codemod la víspera de publicar
   es cambiar un problema de idioma por un riesgo de romper la app. Aquí se
   traduce lo YA PINTADO, contra un CATÁLOGO CERRADO:

     - solo se sustituye la cadena COMPLETA (tras trim), nunca un trozo;
     - solo si esa cadena está en el catálogo. Lo que no está, no se toca.

   Eso es lo que lo hace seguro con los datos: el título de un ticket o el
   nombre de un agente no están en el catálogo y por tanto son intraducibles
   por construcción. No hace falta confiar en una heurística.

   TRES FAMILIAS, y solo la primera es diccionario:
   1. texto fijo            → catálogo (locales/en.json)
   2. texto con números     → el mismo catálogo, con la cifra sacada a {n}
      («▲ 3 vencidas» y «▲ 2 vencidas» son una entrada, no dos)
   3. fechas, días y meses  → Intl, NUNCA catálogo. Un catálogo de meses se
      queda quieto mientras el calendario cambia, y además rompe en silencio:
      un «lunes» dentro de una interfaz en inglés no lanza ningún error.
      Por eso locale() es lo que se le pasa a Intl, y vive aquí.
   ============================================================ */

const KEY = 'agenticos.lang';
export const LANGS = ['es', 'en'];

/* es-ES / en-US para Intl. El idioma inicial lo pone el sistema, como pidió
   el dueño; lo que el usuario elija con el botón manda por encima y persiste. */
const LOCALE = { es: 'es-ES', en: 'en-US' };

let dict = Object.create(null);   // español → inglés (vacío mientras lang === 'es')
let lang = 'es';

function delSistema() {
  try {
    const n = (navigator.languages?.[0] || navigator.language || 'es').slice(0, 2).toLowerCase();
    return LANGS.includes(n) ? n : 'es';
  } catch { return 'es'; }
}

function leer() {
  try {
    const v = localStorage.getItem(KEY);
    return LANGS.includes(v) ? v : delSistema();
  } catch { return delSistema(); }   // ventana privada: la sesión vale igual
}

export function getLang() { return lang; }
export function locale() { return LOCALE[lang] ?? LOCALE.es; }

/* ---------------- fechas: Intl, nunca catálogo ----------------
   Los meses y los días vivían en arrays fijos en español (util.js, calendar.js) y
   salen CONCATENADOS con la cifra: `${d.getDate()} ${MESES[d.getMonth()]}` da «14 sep»,
   que como cadena completa no está —ni puede estar— en ningún catálogo. Por eso esto
   no es traducción, es formato: se lo pedimos a Intl con el locale activo.
   Se cachea por idioma porque construir un DateTimeFormat por celda de calendario se
   nota, y la caché se tira entera al cambiar de idioma. */
let cacheFechas = { lang: null };
function tablas() {
  if (cacheFechas.lang === lang) return cacheFechas;
  const fmt = (opts) => new Intl.DateTimeFormat(locale(), opts);
  const mesCorto = fmt({ month: 'short' });
  const mesLargo = fmt({ month: 'long' });
  const diaCorto = fmt({ weekday: 'short' });
  const diaLargo = fmt({ weekday: 'long' });
  const limpia = (s) => s.replace(/\.$/, '');   // es-ES devuelve «sept.» con punto
  /* 2026-01-15 y sucesivos: fechas cualesquiera de cada mes / de cada día de semana */
  const porMes = (f) => Array.from({ length: 12 }, (_, m) => limpia(f.format(new Date(2026, m, 15))));
  /* 4-ene-2026 es domingo → índice 0 = domingo, como getDay() */
  const porDia = (f) => Array.from({ length: 7 }, (_, w) => limpia(f.format(new Date(2026, 0, 4 + w))));
  cacheFechas = {
    lang,
    mesesCorto: porMes(mesCorto), mesesLargo: porMes(mesLargo),
    diasCorto: porDia(diaCorto),  diasLargo: porDia(diaLargo),
  };
  return cacheFechas;
}
export const mesCorto = (i) => tablas().mesesCorto[i] ?? '';
export const mesLargo = (i) => tablas().mesesLargo[i] ?? '';
export const diaCorto = (w) => tablas().diasCorto[w] ?? '';
export const diaLargo = (w) => tablas().diasLargo[w] ?? '';

/* Y LA PARTE QUE NO SE ARREGLA CON TABLAS: el ORDEN y los nexos. El renderer componía
   «${dia}, ${numero} de ${mes} de ${año}», y ese «de» no existe en inglés — traducir las
   piezas por separado daría «Monday, 14 de September». Una fecha completa se le pide
   entera a Intl y se deja que cada idioma la ordene como es suya. */
const fmtCache = new Map();
function dtf(opts) {
  const k = lang + JSON.stringify(opts);
  let f = fmtCache.get(k);
  if (!f) { f = new Intl.DateTimeFormat(locale(), opts); fmtCache.set(k, f); }
  return f;
}
/* #262/#271: la inicial del dia para la rejilla semanal de un job. OJO AL INDICE:
   `weekdays` del motor va 0=lunes (estilo Python) y getDay()/Intl van 0=domingo. Un
   mapeo ingenuo desplaza la rejilla UNA COLUMNA y parece bien estando mal — el aviso
   es de Eco y es justo el fallo que no se ve mirando. De ahi el (i + 1) % 7.
   Se usa `narrow`, que en espanol da L M X J V S D y en ingles M T W T F S S. */
export const diaLetra = (i) => {
  const f = dtf({ weekday: 'narrow' });
  return f.format(new Date(2026, 0, 4 + ((i + 1) % 7)));  // 4-ene-2026 fue domingo
};

export const fechaLarga  = (d) => dtf({ weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }).format(d);
export const fechaDiaMes = (d) => dtf({ weekday: 'long', day: 'numeric', month: 'long' }).format(d);

/* Normaliza una cadena a su patrón: los números salen a {n} para que
   «3 vencidas» y «2 vencidas» compartan una sola entrada del catálogo.
   #314: y lo que va entre comillas angulares sale a «{s}». En toda la app las
   comillas angulares marcan un DATO (el nombre de un proyecto, el título de una
   tarea), y los avisos lo llevan en mitad de la frase: «Proyecto «x» creado.».
   Las citas salen PRIMERO, para que las cifras de un nombre sigan siendo del
   nombre. La cadena tiene que seguir casando ENTERA con una clave. */
const CITA = /«[^»]*»/g;
const sinCitas = (s) => s.replace(CITA, '«{s}»');
const patron = (s) => sinCitas(s).replace(/\d+/g, '{n}');

/* Traduce una cadena SUELTA. Devuelve la misma si no está en el catálogo —
   ese «no la conozco, no la toco» es la garantía de que los datos pasan
   intactos. */
export function t(s) {
  if (lang === 'es' || !s) return s;
  const crudo = String(s);
  const limpio = crudo.trim();
  if (!limpio) return crudo;
  const directo = dict[limpio];
  if (directo != null) return crudo.replace(limpio, directo);
  const p = patron(limpio);
  const conPatron = dict[p];
  if (conPatron == null) return crudo;
  /* reinserta las cifras y las citas originales, cada una en su orden */
  const citas = limpio.match(CITA) ?? [];
  const cifras = sinCitas(limpio).match(/\d+/g) ?? [];
  let i = 0, j = 0;
  const traducido = conPatron
    .replace(/\{n\}/g, () => cifras[i++] ?? '')
    .replace(/«\{s\}»/g, () => citas[j++] ?? '');
  /* con función: ahora el texto lleva datos, y un «$&» en un nombre no es un patrón */
  return crudo.replace(limpio, () => traducido);
}

const ATTRS = ['title', 'placeholder', 'aria-label'];

/* Zonas que NO se traducen nunca, aunque coincidieran: son datos de la casa,
   no interfaz. Marcar aquí es más barato y más honesto que adivinar. */
/* #305: .mcprow__name protegia DE MAS. Ahi vive el nombre del servidor MCP —un
   identificador del usuario que NO se traduce, y por eso esta en la lista— pero el
   contenedor lleva ademas las etiquetas de estado, y una de ellas decia
   «deshabilitado». La proteccion tapaba una cadena que SI habia que traducir, y salia
   en mcp-registry.png, en la portada del repo. Ahora se protege el nombre en si
   (.mcprow__id) y el resto del contenedor se recorre. */
const SKIP = '[data-i18n-skip], code, pre, .mcprow__cmd, .mcprow__id';

/* #291: EL ORIGINAL DE LO QUE SE TRADUJO, para poder VOLVER.

   setLang ya repintaba al cambiar de idioma, y para el tablero bastaba: se
   rehace desde los datos, que estan en espaniol. Pero repintar() es
   render(getState()), y eso NO rehace el HTML estatico de index.html — la
   barra, las pestanias, los botones. Esos nodos applyI18n los reescribe EN EL
   SITIO y no habia quien los devolviera.

   Resultado, medido abriendo el repo recien parido: el conmutador era DE IDA
   SIN VUELTA. Espaniol -> ingles bien; ingles -> espaniol cambiaba la etiqueta
   del boton y dejaba el texto en ingles PARA SIEMPRE, hasta recargar. Quien
   pulsa dos veces se queda atascado en ingles, y el dueño pidio expresamente que
   los dos idiomas convivan y se cambien con un boton.

   Un WeakMap y no un atributo data-: los nodos de texto no tienen atributos, y
   ademas asi no se ensucia el DOM ni sobrevive nada a un repintado de verdad.
   Se guarda SOLO la primera vez: la segunda pasada ya veria el ingles. */
const originalTexto = new WeakMap();   // nodo de texto -> espaniol original
const originalAttr  = new WeakMap();   // elemento -> { attr: valor original }

/* Aplica el catálogo sobre lo ya pintado. Idempotente por definición: si una
   cadena ya está en inglés no está en el catálogo (que va de español a inglés)
   y se queda como está, así que volver a pasarlo no la rompe. */
export function applyI18n(root = document.body) {
  if (lang === 'es' || !root) return;
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const pendientes = [];
  let n;
  while ((n = w.nextNode())) {
    const crudo = n.textContent;
    if (!crudo || !crudo.trim()) continue;
    if (n.parentElement?.closest(SKIP)) continue;
    const traducido = t(crudo);
    if (traducido !== crudo) pendientes.push([n, traducido]);
  }
  for (const [nodo, texto] of pendientes) {
    if (!originalTexto.has(nodo)) originalTexto.set(nodo, nodo.textContent);
    nodo.textContent = texto;
  }

  const conAttr = root.querySelectorAll?.(`[${ATTRS.join('],[')}]`) ?? [];
  for (const el of conAttr) {
    if (el.closest(SKIP)) continue;
    for (const a of ATTRS) {
      const v = el.getAttribute(a);
      if (!v) continue;
      const traducido = t(v);
      if (traducido === v) continue;
      const guardados = originalAttr.get(el) ?? {};
      if (!(a in guardados)) { guardados[a] = v; originalAttr.set(el, guardados); }
      el.setAttribute(a, traducido);
    }
  }
  document.documentElement.lang = lang;
}

/* Deshace lo que applyI18n escribio encima. Solo toca lo que EL tradujo: lo
   que no esta en el WeakMap no se inventa. */
export function restoreI18n(root = document.body) {
  if (!root) return;
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = w.nextNode())) {
    const orig = originalTexto.get(n);
    if (orig === undefined) continue;
    if (n.textContent !== orig) n.textContent = orig;
    /* #298 (Lienzo sobre el WeakMap de Hilo): SE BORRA AL RESTAURAR, y esto es lo que
       faltaba. applyI18n solo guarda «la primera vez», con el razonamiento de que una
       segunda pasada ya veria el ingles — cierto para dos idas seguidas, FALSO en cuanto
       hay una vuelta en medio. Despues de restaurar el nodo vuelve a estar en espaniol,
       asi que si alguien lo cambia EN EL SITIO (nodo.nodeValue = ...) la siguiente ida
       encuentra la entrada vieja, no la actualiza, y la vuelta RESUCITA un texto que ya
       no existia. Reproducido: un nodo que decia «Nueva tarea» volvia del ingles como
       «Verificar cola», que era lo que ponia dos cambios de idioma antes.
       Borrando aqui, cada ida guarda lo que hay de verdad. */
    originalTexto.delete(n);
  }
  for (const el of root.querySelectorAll?.(`[${ATTRS.join('],[')}]`) ?? []) {
    const guardados = originalAttr.get(el);
    if (!guardados) continue;
    for (const [a, v] of Object.entries(guardados)) el.setAttribute(a, v);
    originalAttr.delete(el);   // mismo motivo: un atributo que cambie en espaniol
  }
}

async function cargar(l) {
  if (l === 'es') { dict = Object.create(null); return; }
  try {
    const r = await fetch(`locales/${l}.json`);
    dict = r.ok ? await r.json() : Object.create(null);
  } catch {
    /* sin catálogo la app se ve en español, que es peor pero no está rota */
    dict = Object.create(null);
  }
}

/* Cambia el idioma en caliente: no recarga la ventana, repinta. Quien llame
   pasa su propia función de repintado — i18n no sabe nada del render. */
let repintar = () => {};
export function onRepaint(fn) { repintar = fn; }

export async function setLang(l) {
  if (!LANGS.includes(l) || l === lang) return lang;
  lang = l;
  try { localStorage.setItem(KEY, l); } catch { /* sin almacenamiento: vale la sesión */ }
  /* AQUÍ y no dentro de applyI18n: applyI18n sale antes de tiempo cuando el idioma es
     español, así que al volver de inglés el atributo se quedaba en «en». Y de ese
     atributo cuelgan ahora las reglas de caja de las fechas en el CSS — el rótulo se
     quedaba sin su lowercase. Un efecto que solo ocurre en una rama no va dentro de la
     rama: va donde ocurre el cambio. */
  document.documentElement.lang = lang;
  avisarMain();
  await cargar(l);
  /* del inglés al español hay que volver a pintar de cero: el catálogo va en
     un solo sentido y no sabe deshacer. Del español al inglés bastaría con
     aplicar, pero se repinta igual para no tener dos caminos distintos. */
  /* Volver al espaniol es DESHACER, no traducir: el catalogo va en un solo
     sentido. Primero se devuelve lo estatico a su original y luego se repinta
     lo dinamico, que nace ya en espaniol. */
  if (lang === 'es') restoreI18n();
  repintar();
  applyI18n();
  return lang;
}

/* Arranque: se resuelve el idioma y se carga el catálogo ANTES del primer
   pintado, para que no se vea el español un instante. */
/* #314: el menu de la bandeja vive en el proceso principal y solo sabe el idioma si se lo
   decimos. Fuera de Electron (fixtures, navegador) no hay puente y no pasa nada. */
function avisarMain() {
  try { window.agenticos?.setLang?.(lang); } catch { /* sin puente */ }
}

export async function initI18n() {
  lang = leer();
  avisarMain();
  await cargar(lang);
  document.documentElement.lang = lang;
  return lang;
}
