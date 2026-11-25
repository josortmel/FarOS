"""Comprobación determinista de URL para el #378 (Prima, 6-oct-2026). Sale de la revista del 6-oct (run 28 del job 13):
el verificador automático de FarOS le dio pass y 6 de 8 ítems llevaban la URL de una portada o un índice. Un verificador
LLM que solo lee ficheros no puede cazarlo; esto es la capa determinista: lo que puede hacer el código lo hace el código.

Para cada línea de ítem de una nota de la revista («— título — URL — fecha — …»):
  1. portada: la URL es la raíz de un dominio o un índice conocido → no_hecho, sin abrir nada.
  2. abre la URL (siguiendo redirecciones) y saca el <title> de la página final.
  3. si la URL final es la raíz del dominio (un slug inventado que redirige a portada) → no_hecho.
  4. si ninguna palabra significativa del título del ítem está en el <title> → incierto (puede ser un título traducido).
  5. si no → hecho.

15-oct-2026 (Prima, tras el FAIL de la run 40 y el «sin bajar el estándar» de Pepe): tres comprobaciones más, todas
deterministas, para que una URL inventada o un dato falso no puedan pasar:
  6. la fecha del ítem tiene que existir (AAAA-MM-DD) y no ser futura → si no, no_hecho.
  7. una URL que da 404 o 410 no existe → no_hecho (antes salía «incierto»).
  8. si la página publica su fecha (meta/JSON-LD/<time>), la del ítem tiene que caer entre un día antes y tres días
     después (arXiv: enviado el 7, listado el 9) → si no, no_hecho.
  9. cada cifra del texto del ítem («qué cambia…»/«por qué importa…») tiene que estar en la página → si no, no_hecho.
15-oct-2026, tarde: la SEMANAL (job 17, «[SE QUEDÓ] título — URL — señal: …») no lleva fecha por ítem por diseño, así
  que la 6 y la 8 no se le aplican (antes daba no_hecho a todas sus líneas). Todo lo demás sí, y la 9 se aplica a la
  «señal:», que según su prompt es lo que se vio en ESA URL.

19-oct-2026 (#378, parte 2): pasa a faros/ para que verifier.py lo llame antes del juez (Hilo: junto a faros/prueba.py,
  solo biblioteca estándar). La ruta antigua del vault queda como envoltorio, porque los jobs 13 y 17 la tienen fijada.
  La lógica no cambia: solo se separa en comprobar_linea / comprobar_nota para poder probarla sin red.

Uso: python urls_deterministas.py <nota.md>
     python urls_deterministas.py --breve <nota.md>   (7-oct: solo titulo recortado, url, resultado y motivo, para que
     la evidencia de un job largo, como la semanal, no llegue cortada al juez de FarOS)

23-oct-2026 (#720, tras el FAIL del juez a la semanal del 23-oct, run 53): las URL SECUNDARIAS de una línea (las que
  la «señal:» o el «qué cambia…» citan además de la principal, con o sin https://) salen como filas hijas
  («secundaria»: true) con su propio resultado: abre → hecho; 404/410 → no_hecho; otro fallo → incierto. Solo se
  comprueba que existen: una secundaria puede ser un índice citado a propósito («el blog de LiteLLM no lo menciona»).
  Antes de pedir NADA (principal o secundaria) la URL pasa url_segura(): solo http/https, sin usuario:clave, con
  host, y el host no puede resolver a una IP privada, de loopback, link-local, reservada o multicast; lo mismo en
  cada redirección. Cada secundaria tiene su timeout (TIMEOUT_SECUNDARIA) para que un blog lento no pare la nota.
"""

import html
import ipaddress
import json
import re
import socket
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

INDICES = {"/blog", "/news", "/papers", "/new", "/ai", "/ai/", "/topic/artificial-intelligence/", "/news/"}
VACIAS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "into",
    "that",
    "this",
    "como",
    "para",
    "your",
    "you",
    "are",
    "los",
    "las",
    "del",
}
TIMEOUT_PRINCIPAL = 25
TIMEOUT_SECUNDARIA = 15
MAX_URL = 2048


def es_portada(url):
    u = urlparse(url)
    ruta = u.path or "/"
    return (
        ruta == "/"
        or ruta.rstrip("/") in {i.rstrip("/") for i in INDICES}
        or bool(re.fullmatch(r"/list/[^/]+/new/?", ruta))
    )


def forma_valida(url):
    """None si la URL tiene forma aceptable; si no, el motivo. Sin red."""
    if len(url) > MAX_URL:
        return "la URL es demasiado larga"
    u = urlparse(url)
    if u.scheme not in ("http", "https"):
        return f"esquema no permitido ({u.scheme or 'ninguno'})"
    if u.username or u.password or "@" in u.netloc:
        return "la URL lleva usuario o clave"
    if not u.hostname:
        return "la URL no tiene host"
    try:
        ip = ipaddress.ip_address(u.hostname)
    except ValueError:
        ip = None
    if ip is not None and _ip_prohibida(ip):
        return f"el host es una IP no pública ({ip})"
    if u.hostname == "localhost" or u.hostname.endswith(".localhost") or u.hostname.endswith(".local"):
        return "el host es local"
    return None


def _ip_prohibida(ip):
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or getattr(ip, "is_site_local", False)
    )


def url_segura(url, resolver=None):
    """None si se puede pedir; si no, el motivo. Comprueba la forma y que el host no resuelva a una IP no pública."""
    motivo = forma_valida(url)
    if motivo:
        return motivo
    resolver = resolver or socket.getaddrinfo
    host = urlparse(url).hostname
    try:
        ips = {info[4][0] for info in resolver(host, None)}
    except (socket.gaierror, OSError) as e:
        return f"el host no resuelve ({type(e).__name__})"
    for s in ips:
        try:
            ip = ipaddress.ip_address(s.split("%")[0])
        except ValueError:
            return f"el host resuelve a algo que no es una IP ({s})"
        if _ip_prohibida(ip):
            return f"el host resuelve a una IP no pública ({ip})"
    return None


class _RedireccionSegura(urllib.request.HTTPRedirectHandler):
    def __init__(self, resolver=None):
        super().__init__()
        self.resolver = resolver

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        motivo = url_segura(newurl, self.resolver)
        if motivo:
            raise urllib.error.URLError(f"redirección bloqueada: {motivo}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def abrir(url, timeout=TIMEOUT_PRINCIPAL, resolver=None):
    motivo = url_segura(url, resolver)
    if motivo and motivo.startswith("el host no resuelve"):
        # revisión de Hilo (23-oct): un fallo de DNS es de red (incierto), no prueba de que la URL no exista
        raise urllib.error.URLError(motivo)
    if motivo:
        raise ValueError(f"URL bloqueada antes de pedirla: {motivo}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    opener = urllib.request.build_opener(_RedireccionSegura(resolver))
    with opener.open(req, timeout=timeout) as r:
        final = r.geturl()
        cuerpo = r.read(400_000).decode("utf-8", errors="ignore")
    m = re.search(r"<title[^>]*>(.*?)</title>", cuerpo, re.S | re.I)
    return final, html.unescape(m.group(1)).strip() if m else "", cuerpo


def palabras(t):
    return {w for w in re.findall(r"[a-z0-9áéíóúñ]{4,}", t.lower()) if w not in VACIAS}


FECHA_PAGINA = [
    r'<meta[^>]+(?:property|name|itemprop)=["\'](?:article:published_time|og:published_time|datePublished|date|citation_date|citation_publication_date|citation_online_date|dc\.date|publish-date|parsely-pub-date)["\'][^>]*content=["\']([^"\']+)',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name|itemprop)=["\'](?:article:published_time|og:published_time|datePublished|date|citation_date|citation_publication_date|citation_online_date)["\']',
    r'"datePublished"\s*:\s*"([^"]+)"',
    r'<time[^>]+datetime=["\']([^"\']+)',
]


def fecha_de_pagina(cuerpo):
    """Primera fecha AAAA-MM-DD que la página declara como de publicación, o None."""
    for pat in FECHA_PAGINA:
        m = re.search(pat, cuerpo, re.I)
        if m:
            f = re.search(r"(\d{4})[-/](\d{2})[-/](\d{2})", m.group(1))
            if f:
                try:
                    return date(int(f.group(1)), int(f.group(2)), int(f.group(3)))
                except ValueError:
                    pass
    return None


def texto_visible(cuerpo):
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", cuerpo)
    return html.unescape(re.sub(r"<[^>]+>", " ", t))


def cifras(texto):
    """Cifras con significado del texto del ítem: con decimales o de 2+ dígitos. Normalizadas (coma → punto)."""
    out = set()
    for n in re.findall(r"(?<![\w.,])(\d+(?:[.,]\d+)?)(?![\w])", texto):
        n = n.replace(",", ".")
        if "." in n or len(n) >= 2:
            out.add(n)
    return out


def cifra_en(n, visible):
    # la página puede escribir 53.05, 53,05 o 53.05%; también vale que el entero de un decimal esté como tal (≈)
    variantes = {n, n.replace(".", ",")}
    if n.endswith(".0"):
        variantes.add(n[:-2])
    return any(re.search(r"(?<![\d.,])" + re.escape(v) + r"(?![\d])", visible) for v in variantes)


MESES = {
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}


def fecha_de_listado(resto, fecha_item):
    """«(listado del 21 oct)»: la fecha en que arXiv lo publicó en /new, que es la que cuenta para la ventana."""
    m = re.search(r"(?i)\(listado del (\d{1,2}) ([a-z]{3})", resto)
    if not m or fecha_item is None or m.group(2).lower() not in MESES:
        return None
    mes, anio = MESES[m.group(2).lower()], fecha_item.year
    if mes < fecha_item.month:  # enviado en diciembre, listado en enero
        anio += 1
    try:
        return date(anio, mes, int(m.group(1)))
    except ValueError:
        return None


def _url_normal(url, titulo=""):
    """La URL normalizada; si el título lleva versión (vX.Y.Z), también la versión: un changelog es UN fichero para
    todas sus versiones, y la misma URL con otra versión es otro ítem (caso real: v2.1.294 el 8-oct, v2.1.295 el 9)."""
    version = re.search(r"\bv?(\d+\.\d+\.\d+)\b", titulo)
    return url.rstrip("/").lower() + (f"#{version.group(1)}" if version else "")


def urls_de_nota(texto):
    """Las URL de los ítems de una nota, para comparar con la siguiente (sin abrir nada)."""
    return {
        _url_normal(m.group(2), m.group(1))
        for m in (
            re.match(r"^(?:\d+\.\s*)?(?:\[[^\]]+\]\s*)?(.+?)\s+—\s+(https?://\S+)\s+—", l) for l in texto.splitlines()
        )
        if m
    }


def comprobar_linea(linea, hoy=None, abrir_url=None, desde=None, urls_previas=None):
    """Resultado (dict) de una línea de ítem de la revista, o None si la línea no es un ítem.
    #483 (22-oct): con la nota anterior (`desde` = su fecha, `urls_previas` = sus URL), un ítem publicado antes que
    ella o con una URL que ya salió en ella es no_hecho sin abrir nada. En arXiv cuenta la fecha del listado."""
    hoy = hoy or date.today()
    abrir_url = abrir_url or abrir
    m = re.match(r"^(?:\d+\.\s*)?(?:\[[^\]]+\]\s*)?(.+?)\s+—\s+(https?://\S+)\s+—", linea)
    if not m:
        return None
    titulo, url = m.group(1).strip(), m.group(2).strip()
    r = {"titulo": titulo, "url": url}
    resto = linea[m.end() :]
    semanal = re.match(r"(?i)\s*señal:", resto)
    # #835: «2026-10-27 (listado del 28 oct) —», la forma que pide el prompt de la revista, también es fecha
    # #921: y con «enviado» o «enviado el» delante (run 67, 2-nov, tiró un paper listado dentro de la ventana)
    fm = re.match(r"(?i)\s*(?:enviado\s+(?:el\s+)?)?(\d{4})-(\d{2})-(\d{2})(?:\s*\([^)]*\))?\s+—", resto)
    fecha_item = None
    if fm:
        try:
            fecha_item = date(int(fm.group(1)), int(fm.group(2)), int(fm.group(3)))
        except ValueError:
            fecha_item = None
    texto = re.split(r"(?i)qu[eé] cambia para nosotros:|por qu[eé] importa:", resto, maxsplit=1)
    texto = texto[1] if len(texto) == 2 else ""
    if semanal:
        texto = re.split(r"(?i)fuente que nos falta:", resto[semanal.end() :], maxsplit=1)[0]
        # un código HTTP citado en la señal («devolvió 404», «HTTP 403») no es una cifra de la página
        texto = re.sub(r"(?i)\b(?:HTTP|error|devolvi[óo]|dio|da)\s+[45]\d\d\b", "", texto)
    # Prima, 24-nov: las URL citadas (las secundarias, con o sin esquema) no ponen cifras: el id de arXiv, un
    # /issues/123 o el /2026/11/23/ de un periódico no son datos de la página principal
    texto = _URL_EN_TEXTO.sub(" ", texto)
    if fecha_item is None and not semanal:
        r.update(resultado="no_hecho", motivo="el ítem no lleva fecha de publicación AAAA-MM-DD")
    elif fecha_item is not None and fecha_item > hoy:
        r.update(resultado="no_hecho", motivo=f"la fecha del ítem ({fecha_item}) es futura")
    elif desde is not None and fecha_item is not None and (fecha_de_listado(resto, fecha_item) or fecha_item) < desde:
        cuenta = fecha_de_listado(resto, fecha_item) or fecha_item
        r.update(
            resultado="no_hecho",
            fuera_de_ventana=True,
            motivo=f"publicado el {cuenta}, antes de la nota anterior ({desde})",
        )
    elif urls_previas and _url_normal(url, titulo) in urls_previas:
        r.update(resultado="no_hecho", motivo="la URL ya salió en la nota anterior")
    elif es_portada(url):
        r.update(resultado="no_hecho", motivo="la URL es una portada o un índice, no la página del ítem")
    elif forma_valida(url):
        r.update(resultado="no_hecho", motivo="URL no válida: " + forma_valida(url))
    else:
        try:
            final, t, cuerpo = abrir_url(url)
            r.update(url_final=final, title=t)
            version = re.search(r"\bv?(\d+\.\d+\.\d+)\b", titulo)
            if es_portada(final):
                r.update(resultado="no_hecho", motivo="la URL redirige a la portada (¿slug inventado?)")
            elif not t and version:
                # ficheros de texto sin <title> (changelog en raw): la versión del ítem tiene que estar en el texto
                ok = re.search(r"(?m)^#+\s*v?" + re.escape(version.group(1)) + r"\b", cuerpo)
                r.update(
                    resultado="hecho" if ok else "no_hecho",
                    motivo=None if ok else f"la versión {version.group(1)} no aparece como entrada del fichero",
                )
            else:
                fp = fecha_de_pagina(cuerpo)
                visible = texto_visible(cuerpo)
                faltan = sorted(n for n in cifras(texto) if not cifra_en(n, visible))
                if fp is not None:
                    r.update(fecha_pagina=str(fp))
                # el ítem puede ir hasta 3 días por DETRÁS de la página (arXiv: enviado el 7, listado el 9), nunca antes
                if (
                    fp is not None
                    and fecha_item is not None
                    and not (fp - timedelta(days=1) <= fecha_item <= fp + timedelta(days=3))
                ):
                    r.update(
                        resultado="no_hecho",
                        motivo=f"la fecha del ítem ({fecha_item}) no coincide con la de la página ({fp})",
                    )
                elif faltan:
                    r.update(
                        resultado="no_hecho", motivo="cifras del ítem que no están en la página: " + ", ".join(faltan)
                    )
                elif not (palabras(titulo) & palabras(t)):
                    r.update(
                        resultado="incierto",
                        motivo="ninguna palabra del título del ítem está en el <title> de la página",
                    )
                else:
                    r.update(resultado="hecho")
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                r.update(resultado="no_hecho", motivo=f"la URL no existe (HTTP {e.code})")
            else:
                r.update(resultado="incierto", motivo=f"no se pudo abrir: HTTP {e.code}")
        except ValueError as e:
            r.update(resultado="no_hecho", motivo=str(e)[:200])
        except Exception as e:
            r.update(resultado="incierto", motivo=f"no se pudo abrir: {type(e).__name__}: {e}"[:200])
    return r


_URL_EN_TEXTO = re.compile(r"https?://[^\s)\]»,;]+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}/[^\s)\]»,;]*", re.I)


def urls_secundarias(linea):
    """Las URL de una línea de ítem además de la principal, con o sin esquema, sin repetir y en orden. Sin red."""
    m = re.match(r"^(?:\d+\.\s*)?(?:\[[^\]]+\]\s*)?(.+?)\s+—\s+(https?://\S+)\s+—", linea)
    if not m:
        return []
    principal = m.group(2).strip().rstrip("/").lower()
    vistas, out = {principal}, []
    for u in _URL_EN_TEXTO.findall(linea[m.end() :]):
        u = u.rstrip(".:'\"")
        if not u.lower().startswith(("http://", "https://")):
            u = "https://" + u
        clave = u.rstrip("/").lower()
        if clave not in vistas:
            vistas.add(clave)
            out.append(u)
    return out


def comprobar_secundaria(url, padre, titulo, abrir_url=None):
    """Fila hija de una URL secundaria: solo que exista (abre → hecho; 404/410 → no_hecho; otro fallo → incierto)."""
    r = {"titulo": "↳ " + titulo[:46], "url": url, "padre": padre, "secundaria": True}
    motivo = forma_valida(url)
    if motivo:
        r.update(resultado="no_hecho", motivo="URL secundaria no válida: " + motivo)
        return r
    abrir_url = abrir_url or (lambda u: abrir(u, timeout=TIMEOUT_SECUNDARIA))
    try:
        final, _t, _c = abrir_url(url)
        r.update(url_final=final, resultado="hecho")
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            r.update(resultado="no_hecho", motivo=f"la URL secundaria no existe (HTTP {e.code})")
        else:
            r.update(resultado="incierto", motivo=f"no se pudo abrir la secundaria: HTTP {e.code}")
    except ValueError as e:
        r.update(resultado="no_hecho", motivo=str(e)[:200])
    except Exception as e:
        r.update(resultado="incierto", motivo=f"no se pudo abrir la secundaria: {type(e).__name__}: {e}"[:200])
    return r


PARALELAS = 6  # #1207: líneas a la vez (pocas, para no castigar a ningún servidor)
PRESUPUESTO_S = 240  # #1207: tope de toda la nota; el 13-nov, 60 URL en serie con la red lenta pasaron de 600 s
A_TIEMPO = "no se pudo abrir a tiempo"


def _sin_tiempo(_url):
    raise TimeoutError(A_TIEMPO)


def _una_linea(l, hoy, abrir_url, desde, urls_previas, abrir_secundaria):
    r = comprobar_linea(l, hoy, abrir_url, desde, urls_previas)
    if r is None:
        return []
    return [r] + [
        comprobar_secundaria(u, r["url"], r["titulo"], abrir_secundaria or abrir_url) for u in urls_secundarias(l)
    ]


def comprobar_nota(
    texto,
    hoy=None,
    abrir_url=None,
    desde=None,
    urls_previas=None,
    abrir_secundaria=None,
    paralelas=None,
    presupuesto_s=None,
):
    """Lista de resultados: una por línea de ítem de la nota y, detrás de cada una, una fila hija por URL secundaria.
    #1207: las líneas se comprueban `paralelas` a la vez y la nota entera tiene `presupuesto_s`. Una línea que no ha
    acabado a tiempo se vuelve a mirar SIN red: lo que se decide sin abrir nada (fecha, portada, repetida) sale igual,
    y lo que necesitaba abrir la URL sale «incierto: no se pudo abrir a tiempo», nunca hecho ni no_hecho.
    El orden de la salida es el de la nota."""
    from concurrent.futures import ThreadPoolExecutor, wait

    paralelas = PARALELAS if paralelas is None else paralelas  # se leen al llamar, no al definir
    presupuesto_s = PRESUPUESTO_S if presupuesto_s is None else presupuesto_s
    lineas = texto.splitlines()
    pool = ThreadPoolExecutor(max_workers=max(1, paralelas), thread_name_prefix="urls")
    try:
        futuros = [pool.submit(_una_linea, l, hoy, abrir_url, desde, urls_previas, abrir_secundaria) for l in lineas]
        wait(futuros, timeout=presupuesto_s)
        out = []
        for l, f in zip(lineas, futuros):
            if f.done() and not f.cancelled():
                out.extend(f.result())  # un fallo de código sube, como cuando iba en serie
            else:
                out.extend(_una_linea(l, hoy, _sin_tiempo, desde, urls_previas, _sin_tiempo))
        return out
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # lo que siga abriendo acaba solo (su timeout es de 25 s)


def recuento(resultados):
    return {k: sum(1 for r in resultados if r["resultado"] == k) for k in ("hecho", "no_hecho", "incierto")}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    breve = "--breve" in argv
    desde = urls_previas = None
    if "--anterior" in argv:
        # #483: la nota anterior se llama AAAA-MM-DD.md; de ella salen la fecha de la ventana y las URL ya publicadas
        i = argv.index("--anterior")
        previa = Path(argv[i + 1])
        argv = argv[:i] + argv[i + 2 :]
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", previa.name)
        desde = date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
        urls_previas = urls_de_nota(previa.read_text(encoding="utf-8"))
    nota = Path([a for a in argv if a != "--breve"][0]).read_text(encoding="utf-8")
    out = comprobar_nota(nota, desde=desde, urls_previas=urls_previas)
    for r in out:
        if breve:
            r = {
                "titulo": r["titulo"][:50],
                "url": r["url"],
                "resultado": r["resultado"],
                **({"motivo": r["motivo"][:80]} if r.get("motivo") else {}),
            }
        print(json.dumps(r, ensure_ascii=False))
    print(json.dumps(recuento(out)))


if __name__ == "__main__":
    main()
