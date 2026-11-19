"""La CUARTA forma de texto sin traducir: la etiqueta PEGADA a un dato en el mismo nodo.

    `Mesa de bugs - ${name}`   ·   `${glyph} ${label}`   ·   `en #${n}`

El catalogo traduce NODOS DE TEXTO COMPLETOS. Si la etiqueta viaja pegada al dato, el nodo
es «Mesa de bugs - platform-v3» y ninguna clave lo alcanza — aunque «Mesa de bugs» este en
el catalogo, que es lo que despisto a dos personas a la vez el 16-sep-2026: una media el
BOTON (nodo suelto, traducia) y otra la CABECERA (nodo pegado, no traducia), y las dos
tenian razon.

POR QUE HACE FALTA ESTE FICHERO Y NO VALE NINGUNO DE LOS OTROS: ese dia enumeramos la
misma clase con cuatro miradas y ninguna ve lo de las otras —
    1. texto entre etiquetas        -> extractor de HTML
    2. mapas {clave: 'etiqueta'}    -> extractor de objetos
    3. llamadas tr('...') sin clave -> el detector de Hilo
    4. ETIQUETA PEGADA A UN DATO    -> este
El 4 no aparece en ninguno de los tres primeros: no es una clave, no es un mapa y no es
una llamada a tr(). Solo existe en el DOM pintado, y solo si el dato existe.

EL ARREGLO de cada hallazgo es SIEMPRE el mismo: partir en etiqueta + dato.
    `${tr('Mesa de bugs')} - ${name}`
Etiqueta al catalogo, dato fuera.

Uso:  python scripts/dev/buscar_etiqueta_pegada.py
Sale 1 si encuentra alguna.
"""
import io, json, re, sys, pathlib

RAIZ = pathlib.Path(__file__).resolve().parents[2]
ACC = ''.join(chr(c) for c in (225, 233, 237, 243, 250, 241, 193, 201, 205, 211, 218, 209))
ES = re.compile('[' + ACC + r']|\b(de|del|la|el|los|las|en|con|para|por|sin|una|un|mesa|alta|'
                r'media|baja|hoy|ayer|desde|hasta|tarea|tareas|pendiente|pendientes|corregido|'
                r'despachar|descartar|nada|bisagra|bisagras)\b', re.I)
# OJO: NO meter aqui palabras iguales en ingles (job, run, output, error, decision).
# Las meti en la primera version y el recuento subio de 47 a 62, con «Run + dato» y
# «job + dato» dentro, que estan en INGLES y son correctos. Un detector que cuenta de
# mas ensenia a ignorarlo igual que uno que cuenta de menos.
# una etiqueta de verdad EMPIEZA su nodo; si va detras de letras es un trozo de frase que
# el patron corto por la mitad, y eso es ruido mio, no un hallazgo.
ARRANQUE = ('>', '`', '\n', '', ' ', '"', "'", ';')

PEGADO = re.compile(r'([A-Za-z' + ACC + r'][A-Za-z' + ACC + r' ,·—–#:\.\-]{2,40}?)'
                    r'\s*(?:<[^<>`]*>\s*)*\$\{')
# #389 (4c): se acepta MARCADO entre la etiqueta y el dato —«Pendientes <b>${n}</b>»—.
# Ahi el nodo de texto es la etiqueta sola, y es ella la que tiene que estar en el catalogo.

# #389 (4b): lo que ya va dentro de tr()/t2()/tb()/t() pide traduccion por su cuenta.
YA_TRADUCE = re.compile(r'\b(?:tr|t2|tb|t)\(\s*`$')

# #389: la MISMA normalizacion que t() en i18n.js. Las citas «…» salen a «{s}» PRIMERO,
# luego las cifras a {n}. Si no se pregunta asi, el detector da por «sin clave» nodos que
# el catalogo ya cubre con un patron, y un detector que cuenta de mas ensenia a ignorarlo.
CITA = re.compile(r'«[^»]*»')


def patron(s):
    return re.sub(r'\d+', '{n}', CITA.sub('«{s}»', s))


def nodo(t, i):
    """El nodo de texto que empieza en i, con cada ${…} cambiado por un dato ('0').
    Acaba donde acaba el nodo en el DOM: en la siguiente etiqueta HTML o al cerrar la
    plantilla. Las llaves se cuentan para saltar ${…} anidados."""
    out, n = [], len(t)
    while i < n:
        c = t[i]
        if c in '`<\n':
            break
        if t.startswith('${', i):
            prof, i = 1, i + 2
            while i < n and prof:
                prof += {'{': 1, '}': -1}.get(t[i], 0)
                i += 1
            out.append('0')
            continue
        out.append(c)
        i += 1
    return ''.join(out).strip()


# #437: PEGADO tiene que empezar por letra, asi que en «</code> · copia en el Escritorio: <code>»
# arrancaba en «copia» y preguntaba por un nodo que no existe: en el DOM el separador va
# DENTRO del nodo («· copia en el Escritorio:»). Contaba de mas (esa clave existe) y de menos
# («· verificacion» no casa con la clave «verificacion»). Se retrocede sobre el separador
# hasta donde empieza el nodo de verdad.
SEPARADOR = ' ·—–'
BORDE = ('>', '`', '\n', ';', '"', "'")


def inicio_nodo(t, i):
    j = i
    while j > 0 and t[j - 1] in SEPARADOR:
        j -= 1
    return j if j == 0 or t[j - 1] in BORDE else i


def cubierto(literal, cat):
    return literal in cat or patron(literal) in cat


def hallazgos_en(t, cat):
    """(linea, etiqueta) de cada etiqueta espaniola pegada a un dato cuyo NODO no tiene clave."""
    res = []
    for m in PEGADO.finditer(t):
        etq = m.group(1).strip()
        if len(etq) < 3 or not ES.search(etq):
            continue
        ini = inicio_nodo(t, m.start())
        if (t[ini:m.start()] + etq).strip() in cat:
            continue
        antes = t[max(0, m.start() - 1):m.start()]
        if antes not in ARRANQUE:
            continue
        if antes == '`' and YA_TRADUCE.search(t[max(0, m.start() - 12):m.start()]):
            continue
        if cubierto(nodo(t, ini), cat):
            continue
        res.append((t[:m.start()].count('\n') + 1, etq))
    return res


def main():
    cat = json.load(io.open(RAIZ / 'app/renderer/locales/en.json', encoding='utf-8'))
    hallazgos = []
    for f in sorted((RAIZ / 'app/renderer/js').rglob('*.js')):
        if f.name == 'i18n.js':
            continue
        t = io.open(f, encoding='utf-8', errors='replace').read()
        hallazgos += [(str(f.relative_to(RAIZ)), ln, e) for ln, e in hallazgos_en(t, cat)]

    if not hallazgos:
        print('OK: ninguna etiqueta espaniola pegada a un dato sin clave propia.')
        return 0

    print(f'ETIQUETAS PEGADAS A UN DATO, EN ESPANIOL, SIN CLAVE PROPIA: {len(hallazgos)} (suelo, no total)\n')
    ult = None
    for f, ln, e in hallazgos:
        if f != ult:
            print(f'  {f}')
            ult = f
        print(f"     :{ln:<5} '{e}' + dato")
    print('\nArreglo: partir en etiqueta + dato.  `${tr(\'<etiqueta>\')} <sep> ${dato}`')
    return 1


if __name__ == '__main__':
    sys.exit(main())
