/* criterio_reglas.js — #1136: espejo de faros/criterios.py (#1081) para el modo demo.
   Sin imports a propósito: lo importa api.js. */

/* Espejo de faros/criterios.py para el modo demo: mismas reglas deterministas, mismos textos.
   Un mock más permisivo esconde el fallo justo donde el dueño lo ve (lección del día 129). */
const _COMANDO = /`[^`]+`|\b(?:python|pytest|npm|npx|node|uv|make|bash|sh|cargo|go|dotnet|gradle|mvn|docker|ruff|mypy|pwsh|powershell)\b\s+\S/i;
const _SUITE = /\b(?:la\s+suite|suite\s+(?:completa|entera)|(?:los\s+)?tests?\s+(?:pasan|en\s+verde)|todo\s+en\s+verde|en\s+verde|the\s+suite|tests?\s+pass|all\s+green)\b/i;
const _ARREGLO = /\b(?:arreglad[oa]s?|corregid[oa]s?|ya\s+no\s+(?:falla|pasa|ocurre)|fixed|no\s+longer)\b/i;
const _TEST = /\btests?\b/i;
const _ROJO = /\b(?:rojo|padre|antes\s+del?\s+(?:arreglo|cambio)|red|parent)\b/i;
const _FUERA_DEL_JUEZ = /\b(?:en\s+producci[oó]n|que\s+lo\s+vea\s+\w+|lo\s+confirma\s+\w+|en\s+el\s+m[oó]vil|in\s+production)\b/i;

export function revisarDemo(criterio, nivel = 'self') {
  const texto = String(criterio ?? '').trim();
  nivel = String(nivel ?? 'self').trim() || 'self';
  if (!texto) {
    return nivel !== 'self'
      ? [{ punto: 2, aviso: `sin criterio y con verificación «${nivel}»: quien verifique no tiene contra qué medir. Escribe qué se comprueba y con qué.` }]
      : [];
  }
  const avisos = [];
  if (_SUITE.test(texto) && !_COMANDO.test(texto)) {
    avisos.push({ punto: 4, aviso: 'habla de «la suite» o de tests en verde sin el comando exacto; pon el comando que corre el repo (y qué excluye, si excluye algo).' });
  }
  if (_ARREGLO.test(texto) && _TEST.test(texto) && !_ROJO.test(texto)) {
    avisos.push({ punto: 6, aviso: 'dice que algo queda arreglado pero no pide el rojo: el test tiene que fallar por una aserción en el commit padre (o con solo el test aplicado al padre) y pasar en el commit.' });
  }
  if ((nivel === 'auto' || nivel === 'external') && _FUERA_DEL_JUEZ.test(texto)) {
    avisos.push({ punto: 2, aviso: 'pide algo que un juez automático no puede tocar (producción, que lo vea una persona); saldrá «incierto» y subirá a una persona. Si es lo que quieres, ponle verificación «peer».' });
  }
  return avisos;
}
