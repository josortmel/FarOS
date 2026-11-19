"""Biblioteca (#850, Spec v2 §2 y §5): lo que FarOS y sus agentes producen, registrado y buscable.

Una entrada es un fichero (o una carpeta `artifacts\\run_<id>`) que cae dentro de una RAÍZ de
`biblioteca\\raices.json`. Todo pasa por `permitida`: lista negra, `..` y enlaces simbólicos.

`import faros.library` no abre la casa ni la red: la casa solo se toca en `entorno_de_la_casa()`,
y las funciones reciben el `Entorno` (raíces, carpeta de publicados...) para poder probarlas con
un directorio temporal.
"""

from __future__ import annotations

import fnmatch
import html as _html
import json
import logging
import os
import re
import shutil
import queue
import sqlite3
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger("faros.library")

CLASES = ("revista", "lectura", "faro", "resultado", "documentacion", "informe")   # informe: #1279, esquema 14
PUBLICABLES = ("faro", "informe")   # sus HTML interactivos se publican para abrirlos aparte (#1279: los informes también)
EXTRACTO_MAX = 16 * 1024
LEER_MAX = 8 * 1024 * 1024

# B5: sin distinguir mayúsculas, sobre CADA componente de la ruta real.
LISTA_NEGRA = ("archivo_*", "*_textos", "*_archivo", "*privado*", "chat",
               ".env", "credentials", "*.key", "*.bat", "*.bat.txt", "*.ps1", "*.cmd")


# ------------------------------------------------------------------ entorno y raíces

@dataclass
class Entorno:
    raices: list[dict]
    publicado_dir: Path
    html_revista_dir: Path | None = None
    token: str | None = None          # None = el de la casa, leído al publicar
    abrir_base: str | None = None     # prefijo público de los publicados (config.json); None = sin `abrir`
    artifacts_dir: Path | None = None
    rechazadas: list[dict] = field(default_factory=list)
    raices_ilegibles: bool = False    # raices.json ausente / roto / no es una lista: NO es lo mismo que una lista vacía


def _norm(ruta) -> str:
    return os.path.normcase(os.path.realpath(str(ruta)))


def en_lista_negra(ruta) -> bool:
    """True si algún componente de la ruta cae en la lista negra o empieza por punto."""
    partes = [p for p in re.split(r"[\\/]+", str(ruta)) if p]
    if partes and re.fullmatch(r"[A-Za-z]:", partes[0]):
        partes = partes[1:]
    for p in partes:
        p = unicodedata.normalize("NFKC", p).lower()   # `．env` de ancho completo = `.env`
        if p.startswith("."):
            return True
        if any(fnmatch.fnmatchcase(p, pat) for pat in LISTA_NEGRA):
            return True
    return False


def cargar_raices(ruta_json, rechazadas: list | None = None) -> list[dict]:
    """Como `leer_raices`, pero sin distinguir «no pude leer» de «lista vacía» (siempre una lista)."""
    return leer_raices(ruta_json, rechazadas) or []


def leer_raices(ruta_json, rechazadas: list | None = None) -> list[dict] | None:
    """Lee `raices.json`. None = NO se pudo leer (no existe, JSON roto o a medio escribir, no es una
    lista): quien llama no debe sacar conclusiones. Una lista válida (aunque sea `[]`) es deliberada.
    Una raíz con algo de la lista negra (N8), de clase o tipo inválidos se rechaza (log + `rechazadas`)."""
    try:
        crudo = json.loads(Path(ruta_json).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning("raices.json ilegible: %s", e)
        return None
    if not isinstance(crudo, list):
        log.warning("raices.json no es una lista")
        return None
    out = []
    for r in crudo:
        motivo = None
        if not isinstance(r, dict) or not isinstance(r.get("ruta"), str) or not r["ruta"]:
            motivo = "sin ruta"
        elif r.get("clase") not in CLASES:
            motivo = f"clase {r.get('clase')!r} no válida"
        elif r.get("tipo") not in ("carpeta", "fichero"):
            motivo = f"tipo {r.get('tipo')!r} no válido"
        elif en_lista_negra(r["ruta"]) or en_lista_negra(_norm(r["ruta"])):
            motivo = "contiene algo de la lista negra"
        if motivo:
            log.warning("raíz rechazada (%s): %s", motivo, r)
            if rechazadas is not None:
                rechazadas.append({"raiz": r, "motivo": motivo})
            continue
        pats = [str(p).lower() for p in (r.get("patrones") or [])]
        proyecto = r.get("proyecto") if isinstance(r.get("proyecto"), str) and r["proyecto"].strip() else None
        out.append({"clase": r["clase"], "ruta": r["ruta"], "tipo": r["tipo"], "patrones": pats,
                    "recursivo": bool(r.get("recursivo")), "_real": _norm(r["ruta"]), "proyecto": proyecto})
    return out


def entorno_de_la_casa() -> Entorno:
    from . import env as _env
    home = _env.data_dir()
    rech: list[dict] = []
    cfg = _config(home / "biblioteca" / "config.json")
    leidas = leer_raices(home / "biblioteca" / "raices.json", rech)
    return Entorno(raices=leidas or [], raices_ilegibles=leidas is None,
                   publicado_dir=home / "biblioteca" / "publicado",
                   html_revista_dir=cfg.get("html_revista_dir"), abrir_base=cfg.get("abrir_base"),
                   artifacts_dir=home / "artifacts", rechazadas=rech)


def _config(ruta) -> dict:
    """`biblioteca/config.json` junto a raices.json: {"html_revista_dir": "<carpeta>", "abrir_base": "https://<host>/biblioteca/"}. Nada de la
    casa va en el código: sin fichero (o roto) no hay carpeta de revistas maquetadas."""
    try:
        c = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(c, dict):
        return {}
    out = {}
    d = c.get("html_revista_dir")
    if isinstance(d, str) and d:
        out["html_revista_dir"] = Path(d)
    b = c.get("abrir_base")
    if isinstance(b, str) and re.fullmatch(r"https://[^\s]+", b):
        out["abrir_base"] = b
    return out


_RESERVADOS = re.compile(r"(?i)^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$")


def _ruta_sospechosa(raw) -> bool:
    """Formas que Windows resuelve a otra cosa o que no son una ruta local absoluta normal: UNC y
    rutas de dispositivo, relativas o `C:foo`, flujos alternativos (`x.md::$DATA`), punto o espacio
    final, nombres reservados (CON, NUL...), nombres cortos 8.3 (`PRIMA_~1`) y `..`."""
    if not isinstance(raw, (str, os.PathLike)):
        return True
    raw = os.fspath(raw)
    if not isinstance(raw, str) or not raw or len(raw) > 1024 or "\x00" in raw:
        return True
    if raw.startswith(("\\\\", "//")):
        return True
    if os.name == "nt":
        if not re.match(r"^[A-Za-z]:[\\/]", raw) or ":" in raw[2:]:
            return True
    elif not raw.startswith("/"):
        return True
    for c in (x for x in re.split(r"[\\/]+", raw) if x):
        if (c == ".." or c.endswith((".", " ")) or _RESERVADOS.match(c) or re.search(r"~\d", c)):
            return True
    return False


def _relativa(real: str, base: str) -> str | None:
    """Parte de `real` bajo `base` ('' si es la misma), o None si no está dentro."""
    try:
        if os.path.commonpath([real, base]) != base:
            return None
    except ValueError:
        return None
    return real[len(base):].lstrip("\\/")


def _casa(nombre: str, pats: list[str]) -> bool:
    n = nombre.lower()
    return any(fnmatch.fnmatchcase(n, p) for p in pats)


def raiz_de(ruta, raices: list[dict]) -> dict | None:
    """La raíz que permite `ruta`, o None. Rechaza `..`, lista negra (en la ruta tal cual y en la
    real) y todo lo que un enlace simbólico saque de las raíces. En raíces no recursivas el primer
    nivel bajo la raíz tiene que casar con un patrón; en las recursivas, el nombre del fichero."""
    try:
        if _ruta_sospechosa(ruta):
            return None
        raw = os.fspath(ruta)
        if en_lista_negra(raw):
            return None
        real = _norm(raw)
        if en_lista_negra(real) or _ruta_sospechosa(real):
            return None
    except (OSError, ValueError):
        return None
    for r in raices:
        if r["tipo"] == "fichero":
            if real == r["_real"] and not os.path.isdir(real):
                return r
            continue
        rel = _relativa(real, r["_real"])
        if not rel:
            continue
        partes = rel.split(os.sep)
        es_dir = os.path.isdir(real)
        if r["recursivo"]:
            if es_dir or (r["patrones"] and not _casa(partes[-1], r["patrones"])):
                continue            # una carpeta no es una entrada bajo una raíz recursiva
        else:
            if not r["patrones"] or not _casa(partes[0], r["patrones"]):
                continue
            # Carpetas y profundidad > 1 solo en `resultado` (artifacts/run_N/...): una carpeta
            # llamada x.md en la carpeta de revistas no abre nada.
            if (es_dir or len(partes) > 1) and r["clase"] != "resultado":
                continue
            if es_dir and len(partes) > 1:
                continue
        return r
    return None


def permitida(ruta, raices: list[dict]) -> bool:
    return raiz_de(ruta, raices) is not None


# ------------------------------------------------------------------ extracción de texto

_INTERACTIVO = re.compile(
    r"<script|@import|url\(\s*[\"']?https?:"
    r"|<(?:script|link|img|iframe)\b[^<>]{0,4096}?\b(?:src|href)\s*=\s*[\"']?https://", re.I | re.S)
_SECRETOS = re.compile(
    r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}|X-FarOS-Token|api[_-]?key[\"']?\s*[:=]"
    r"|(?<![A-Za-z0-9])ghp_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY"
    r"|(?<![0-9])[0-9]{8,10}:[A-Za-z0-9_-]{30,}", re.I)


def _leer(path, tope=None) -> str:
    with open(path, "rb") as f:
        return f.read(tope or LEER_MAX).decode("utf-8", errors="replace")


def _sin_script_ni_style(h: str) -> str:
    """Quita <script>/<style> con su contenido; lineal (sin `.*?` que reintenta en cada apertura)."""
    out, i, bajo = [], 0, h.lower()
    for m in re.finditer(r"<(script|style)\b", bajo):
        if m.start() < i:
            continue
        out.append(h[i:m.start()])
        fin = bajo.find("</" + m.group(1), m.end())
        if fin == -1:
            return " ".join(out)          # sin cerrar: el resto es código
        cierre = bajo.find(">", fin)
        i = len(h) if cierre == -1 else cierre + 1
        out.append(" ")
    out.append(h[i:])
    return "".join(out)


def _texto_de_html(h: str) -> str:
    h = re.sub(r"<[^<>]{0,4096}>", " ", _sin_script_ni_style(h))
    return re.sub(r"\s+", " ", _html.unescape(h)).strip()


def _titulo(texto: str, es_html: bool, nombre: str) -> str:
    texto = texto[:65536]                  # el título está al principio; acotado para no pagar regex cuadráticas
    if es_html:
        m = (re.search(r"(?is)<title[^<>]{0,200}>(.{0,1000}?)</title>", texto)
             or re.search(r"(?is)<h1[^<>]{0,200}>(.{0,1000}?)</h1>", texto))
        if m:
            t = _texto_de_html(m.group(1))
            if t:
                return t[:200]
    else:
        m = re.search(r"(?m)^title:\s*[\"']?(.+?)[\"']?\s*$", texto[:600]) if texto.startswith("---") else None
        m = m or re.search(r"(?m)^#{1,6}\s+(.+?)\s*#*\s*$", texto)
        if m:
            return m.group(1).strip()[:200]
    return Path(nombre).stem[:200] or nombre


def _fecha(nombre: str, mtime: float) -> str:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", nombre)
    if m:
        return m.group(1)
    m = re.search(r"semana-(\d{4})-(\d{2})", nombre)
    if m:
        try:
            # La semanal sale el VIERNES (job 17); con el lunes quedaba debajo de las diarias (Pepe, 16-nov).
            return date.fromisocalendar(int(m.group(1)), int(m.group(2)), 5).isoformat()
        except ValueError:
            pass
    return datetime.fromtimestamp(mtime).date().isoformat()


def _slug(titulo: str) -> str:
    s = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60].strip("-") or "sin-titulo"


def _es_enlace(p: str) -> bool:
    try:
        if os.path.islink(p):
            return True
        if hasattr(os.path, "isjunction"):  # Python 3.12+
            return os.path.isjunction(p)
        # #1354: before 3.12 there is no isjunction, and this check returned False in
        # silence, so the Library followed junctions out of its roots on Python 3.11
        # (pyproject promises >=3.11; the first CI caught it). A junction is a reparse
        # point; other reparse points (OneDrive placeholders) are not followed either:
        # when in doubt, don't go in.
        attrs = getattr(os.lstat(p), "st_file_attributes", 0)
        return bool(attrs & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except OSError:
        return True


def _listado_carpeta(real: str) -> tuple[list[str], float, int]:
    """(ficheros relativos, mtime mayor, nº de ficheros) de una carpeta de run; sin lista negra."""
    lista, mt = [], os.path.getmtime(real)
    for dp, dns, fns in os.walk(real):
        dns[:] = [d for d in dns if not en_lista_negra(d) and not _es_enlace(os.path.join(dp, d))]
        for fn in fns:
            if en_lista_negra(fn):
                continue
            rel = os.path.relpath(os.path.join(dp, fn), real)
            lista.append(rel.replace("\\", "/"))
            try:
                mt = max(mt, os.path.getmtime(os.path.join(dp, fn)))
            except OSError:
                pass
    return sorted(lista), mt, len(lista)


def leer_contenido(ruta, env: Entorno) -> str | None:
    """Texto de una entrada (para /content y library_get). None si no está permitida o no existe.
    Una carpeta devuelve su lista de ficheros (N23)."""
    if not permitida(ruta, env.raices):
        return None
    real = os.path.realpath(str(ruta))
    if not permitida(real, env.raices):         # la ruta RESUELTA también (junction, enlaces)
        return None
    try:
        if os.path.isdir(real):
            texto = "\n".join(_listado_carpeta(real)[0])
        else:
            texto = _leer(real)
        # TOCTOU: si la ruta apunta ahora a otro sitio (junction cambiado a mitad), no se entrega.
        if os.path.realpath(str(ruta)) != real or not permitida(ruta, env.raices):
            return None
        return texto
    except OSError:
        return None


# ------------------------------------------------------------------ publicación (§5)

def _token_de_la_casa(env: Entorno) -> str:
    if env.token is not None:
        return env.token
    try:
        from . import env as _env
        return (_env.data_dir() / "token").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def hay_secretos(texto: str, env: Entorno) -> bool:
    if _SECRETOS.search(texto):
        return True
    tok = _token_de_la_casa(env)
    return bool(tok) and len(tok) >= 8 and tok in texto


PUBLICADO_RE = re.compile(r"[0-9]+-[a-z0-9-]{1,60}[.]html")


def _borrar_publicado(env: Entorno, nombre: str | None) -> None:
    """Borra una copia de publicado/. Solo nombres con la forma exacta que genera _publicar: una fila
    manipulada (`../x`) no puede hacer borrar nada fuera de la carpeta."""
    if nombre and PUBLICADO_RE.fullmatch(nombre):
        try:
            (env.publicado_dir / nombre).unlink()
        except OSError:
            pass


def _publicar(env: Entorno, item_id: int, titulo: str, real: str, texto: str, previo: str | None) -> str:
    """Copia el HTML interactivo a publicado/<id>-<slug>.html. Devuelve '' (retenido: no se publica,
    y no se vuelve a escanear hasta que cambie) si lleva secretos o pasa de LEER_MAX. Se publican
    exactamente los bytes que se escanearon."""
    with open(real, "rb") as f:
        datos = f.read(LEER_MAX + 1)
    if len(datos) > LEER_MAX:
        log.warning("biblioteca: %s NO se publica (pasa del límite de %d bytes que el escaneo puede leer)", real, LEER_MAX)
        return ""
    if hay_secretos(datos.decode("utf-8", errors="replace"), env):
        log.warning("biblioteca: %s NO se publica (coincide con el escaneo de secretos)", real)
        return ""
    nombre = f"{item_id}-{_slug(titulo)}.html"
    env.publicado_dir.mkdir(parents=True, exist_ok=True)
    tmp = env.publicado_dir / (nombre + ".tmp")
    tmp.write_bytes(datos)
    os.replace(tmp, env.publicado_dir / nombre)
    return nombre


# ------------------------------------------------------------------ registrar y recorrer

def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


# El .md de una revista con maquetación: la diaria (AAAA-MM-DD) y la semanal (semana-AAAA-SS).
REVISTA_MD = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2}|semana-[0-9]{4}-[0-9]{2})\.md", re.I)


def _html_de_revista(env: Entorno, nombre: str) -> str | None:
    m = REVISTA_MD.fullmatch(nombre)
    if not m or env.html_revista_dir is None:
        return None
    p = env.html_revista_dir / f"revista-{m.group(1)}.html"
    return str(p) if p.is_file() and not en_lista_negra(p) else None


class _Bloqueada(Exception):
    """«database is locked» al subir de lectura a escritura: reintentable si no hay transacción ajena."""


def registrar(conn, ruta, clase: str | None = None, origen: str = "manual", *, ticket_id=None,
              job_id=None, run_id=None, proyecto=None, env: Entorno | None = None, pasada: str | None = None) -> int | None:
    """Registra (o actualiza) una entrada y sus enlaces. Devuelve su id, o None si la ruta no está
    permitida, no existe o algo falla. NUNCA lanza. Va en un SAVEPOINT: no deja filas a medias.
    Si otro escritor confirma entre la lectura y la escritura (database is locked al instante) y no hay
    transacción ajena abierta, se repite hasta 4 veces."""
    try:
        reintentable = not conn.in_transaction
    except Exception:
        reintentable = False
    for intento in range(4 if reintentable else 1):
        try:
            return _registrar(conn, ruta, clase, origen, ticket_id, job_id, run_id, proyecto, env, pasada, reintentable)
        except _Bloqueada:
            time.sleep(0.05 * (intento + 1))
    log.warning("biblioteca: registrar(%s) sigue bloqueada tras reintentar", ruta)
    return None


def _registrar(conn, ruta, clase, origen, ticket_id, job_id, run_id, proyecto, env, pasada, reintentable):
    sp = "lib_" + uuid.uuid4().hex[:8]
    abierto = False
    try:
        env = env or entorno_de_la_casa()
        raiz = raiz_de(ruta, env.raices)
        if raiz is None or (clase and clase != raiz["clase"]):
            return None
        real = os.path.realpath(str(ruta))
        clave = os.path.normcase(real)
        conn.execute(f"SAVEPOINT {sp}")
        abierto = True
        borrar: list[str] = []          # copias viejas: se borran SOLO cuando la fila ya es firme
        item_id = _registrar_dentro(conn, env, raiz, real, clave, origen, proyecto, borrar)
        if item_id is not None and pasada is not None:
            conn.execute("UPDATE library_items SET ultima_pasada=? WHERE id=?", (pasada, item_id))
        if item_id is not None and (os.path.normcase(os.path.realpath(str(ruta))) != clave
                                    or raiz_de(ruta, env.raices) is not raiz):
            raise RuntimeError("la ruta cambió mientras se registraba")   # TOCTOU: ROLLBACK
        if item_id is not None and (ticket_id or job_id or run_id):
            if ticket_id and not job_id and not run_id and conn.execute(
                    "SELECT 1 FROM library_links WHERE item_id=? AND ticket_id=?", (item_id, ticket_id)).fetchone():
                pass            # ya enlazado a ese ticket (p. ej. por finish_run, con su job y su run)
            else:
                conn.execute("INSERT OR IGNORE INTO library_links (item_id, ticket_id, job_id, run_id)"
                             " VALUES (?, ?, ?, ?)", (item_id, ticket_id, job_id, run_id))
        conn.execute(f"RELEASE {sp}")
        for nombre in borrar:
            _borrar_publicado(env, nombre)
        return item_id
    except Exception as e:
        bloqueada = reintentable and isinstance(e, sqlite3.OperationalError) and "locked" in str(e)
        if not bloqueada:
            log.exception("biblioteca: registrar(%s) falló", ruta)
        if abierto:
            try:
                conn.execute(f"ROLLBACK TO {sp}")
                conn.execute(f"RELEASE {sp}")
            except Exception:
                pass
        if bloqueada:
            raise _Bloqueada() from e
        return None


def _proyecto_de(raiz: dict, real: str) -> str | None:
    """#1076 (criterio 11): de qué proyecto es una entrada de documentación, si nadie lo ha dicho. La raíz
    manda («proyecto» en raices.json, que es de cada casa); si no, la carpeta que contiene el fichero
    (<repo>/CLAUDE.md da <repo>). Las otras clases no llevan proyecto."""
    if raiz.get("proyecto"):
        return raiz["proyecto"]
    if raiz.get("clase") != "documentacion":
        return None
    partes = [x for x in real.replace("\\", "/").split("/") if x]
    return partes[-2] if len(partes) >= 2 else None


def _registrar_dentro(conn, env, raiz, real, clave, origen, proyecto, borrar):
    proyecto = proyecto or _proyecto_de(raiz, real)
    nombre = os.path.basename(real.rstrip("\\/"))
    es_dir = os.path.isdir(real)
    if not es_dir and not os.path.isfile(real):
        return None
    interactivo, html_pub, texto = 0, None, ""
    if es_dir:
        lista, mtime, tamano = _listado_carpeta(real)
        extracto, titulo = "\n".join(lista)[:EXTRACTO_MAX], nombre
    else:
        st = os.stat(real)
        mtime, tamano = st.st_mtime, st.st_size
        texto = _leer(real)
        es_html = real.lower().endswith((".html", ".htm"))
        titulo = _titulo(texto, es_html, nombre)
        extracto = (_texto_de_html(texto) if es_html else texto)[:EXTRACTO_MAX]
        interactivo = int(es_html and bool(_INTERACTIVO.search(texto)))
        if raiz["clase"] == "revista":
            html_pub = _html_de_revista(env, nombre)
    fecha = _fecha(nombre, mtime)
    ahora = _ahora()
    fila = conn.execute("SELECT id, mtime, publicado FROM library_items WHERE ruta = ?", (clave,)).fetchone()
    if fila is None:
        cur = conn.execute(
            "INSERT INTO library_items (clase, ruta, titulo, fecha, origen, proyecto, tamano, mtime, interactivo,"
            " html_publicado, creado, actualizado) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (raiz["clase"], clave, titulo, fecha, origen, proyecto, tamano, mtime, interactivo, html_pub, ahora, ahora))
        item_id, previo = cur.lastrowid, None
    else:
        item_id, previo = fila["id"], fila["publicado"]
        conn.execute(
            "UPDATE library_items SET clase=?, titulo=?, fecha=?, proyecto=COALESCE(?, proyecto), tamano=?, mtime=?,"
            " interactivo=?, html_publicado=?, estado='presente', faltas=0, actualizado=? WHERE id=?",
            (raiz["clase"], titulo, fecha, proyecto, tamano, mtime, interactivo, html_pub, ahora, item_id))
    publicado = previo
    if raiz["clase"] in PUBLICABLES and interactivo and not es_dir:
        # '' = retenido (secretos / tamaño): sigue retenido mientras no cambie el fichero.
        vigente = (fila is not None and fila["mtime"] == mtime and previo is not None
                   and (previo == "" or (env.publicado_dir / previo).is_file()))
        if not vigente:
            publicado = _publicar(env, item_id, titulo, real, texto, previo)
            if previo and previo != publicado:
                borrar.append(previo)
    else:
        if previo:
            borrar.append(previo)
        publicado = None
    conn.execute("UPDATE library_items SET publicado=? WHERE id=?", (publicado, item_id))
    conn.execute("INSERT OR REPLACE INTO library_fts (rowid, titulo, extracto) VALUES (?, ?, ?)",
                 (item_id, titulo, extracto))
    return item_id


def _candidatas(raiz) -> list[str] | None:
    """Rutas candidatas de una raíz, o None si no se puede abrir (se salta entera)."""
    base = raiz["ruta"]
    try:
        if raiz["tipo"] == "fichero":
            return [base] if os.path.isfile(base) else []
        if not os.path.isdir(base):
            return None
        pats, out = raiz["patrones"], []
        if raiz["recursivo"]:
            for dp, dns, fns in os.walk(base):
                dns[:] = [d for d in dns if not en_lista_negra(d) and not _es_enlace(os.path.join(dp, d))]
                out += [os.path.join(dp, f) for f in fns if (not pats or _casa(f, pats)) and not en_lista_negra(f)]
        else:
            out = [os.path.join(base, n) for n in os.listdir(base)
                   if (not pats or _casa(n, pats)) and not en_lista_negra(n)]
        return out
    except OSError:
        return None


def _falta_su_copia(env: Entorno, raiz: dict, fila) -> bool:
    """Un interactivo de `faro` sin copia publicada válida: NULL = nunca se publicó (o volvió de
    ausente), nombre = la copia ya no está. '' (retenido por secretos/tamaño) no cuenta: no se
    reescanea mientras el fichero no cambie."""
    if raiz["clase"] not in PUBLICABLES or not fila["interactivo"]:
        return False
    if fila["publicado"] is None:
        return True
    return fila["publicado"] != "" and not (env.publicado_dir / fila["publicado"]).is_file()


CANDADO_MAX_S = 0.05  # como mucho ~50 ms con una transacción de escritura abierta (y nunca mientras se lee un fichero)
CEDER_S_DEFECTO = 0.1
CEDER_S = CEDER_S_DEFECTO   # pausa tras cada commit, >= el mayor sueño del busy handler de SQLite (100 ms): así la conexión del daemon entra seguro


def _dentro_de_alguna(ruta_norm: str, raices: list[dict]) -> bool:
    return any((ruta_norm == r["_real"]) if r["tipo"] == "fichero" else bool(_relativa(ruta_norm, r["_real"]))
               for r in raices)


def rellenar_proyectos(conn, env: Entorno) -> int:
    """#1076: las entradas de documentación que entraron sin proyecto lo reciben de su ruta (o de su raíz).
    El recorrido no vuelve a registrar lo que no ha cambiado, así que sin esto se quedarían a NULL.
    #1124: si su raíz lleva «proyecto» en raices.json, ese manda también sobre el que ya tengan (el de la
    carpeta de una pasada anterior): añadir o cambiar el proyecto de una raíz llega en la pasada siguiente."""
    por_ruta = {_norm(r["ruta"]): r for r in env.raices if r.get("clase") == "documentacion" and r.get("ruta")}
    n = 0
    for fila in conn.execute("SELECT id, ruta, proyecto FROM library_items WHERE clase = 'documentacion'").fetchall():
        raiz = por_ruta.get(_norm(fila["ruta"]), {"clase": "documentacion"})
        if fila["proyecto"] is not None and not raiz.get("proyecto"):
            continue
        p = _proyecto_de(raiz, fila["ruta"])
        if p and p != fila["proyecto"]:
            conn.execute("UPDATE library_items SET proyecto = ? WHERE id = ?", (p, fila["id"]))
            n += 1
    if n:
        conn.commit()
    return n


_RUN_DIR = re.compile(r"run_(\d+)$")


def enlazar_runs(conn) -> int:
    """#1076 (criterio 12): una carpeta `run_<id>` que entró sin enlace (la encontró el recorrido, o es de
    antes del gancho de al_terminar_run) se enlaza con su job y su ticket, que la tabla runs ya sabe."""
    n = 0
    filas = conn.execute(
        "SELECT i.id, i.ruta FROM library_items i WHERE i.clase = 'resultado'"
        " AND NOT EXISTS (SELECT 1 FROM library_links l WHERE l.item_id = i.id AND l.run_id IS NOT NULL)").fetchall()
    for fila in filas:
        m = _RUN_DIR.search(str(fila["ruta"]).rstrip("\\/"))
        run = m and conn.execute("SELECT id, job_id, ticket_id FROM runs WHERE id = ?", (int(m.group(1)),)).fetchone()
        if run:
            conn.execute("INSERT OR IGNORE INTO library_links (item_id, ticket_id, job_id, run_id) VALUES (?, ?, ?, ?)",
                         (fila["id"], run["ticket_id"], run["job_id"], run["id"]))
            n += 1
    if n:
        conn.commit()
    return n


def recorrer(conn, env: Entorno | None = None, detener=None) -> dict:
    """Una pasada sobre todas las raíces (R7): registra lo nuevo o cambiado, cuenta como mucho una
    falta por entrada y pasada, y con 2 faltas pasa a `ausente` (y se despublica). Una raíz que no
    se puede abrir se salta entera, sin faltas. Una entrada cuya ruta YA no pasa `raiz_de` (raíz
    quitada o rechazada) pasa a ausente enseguida, salvo que cuelgue de una raíz ilegible. Al final
    se borran de publicado/ los ficheros que ninguna fila cita. Nunca lanza."""
    env = env or entorno_de_la_casa()
    pasada = _ahora() + "#" + uuid.uuid4().hex[:6]
    res = {"vistas": 0, "registradas": 0, "ausentes": 0, "raices_saltadas": 0}
    if env.raices_ilegibles:  # raices.json ausente/roto/a medias: no es «todo ha desaparecido»
        log.warning("biblioteca: raices.json ilegible; esta pasada no toca nada")
        return res
    rellenar_proyectos(conn, env)
    enlazar_runs(conn)
    leidas, saltadas, vistos = [], [], set()   # vistos: claves de lo que EXISTE (aunque no se pudiera leer o registrar)
    abierta = [None]          # cuándo se abrió la transacción de escritura en curso

    def _suelta_el_candado(forzar: bool = False):
        if conn.in_transaction:
            if abierta[0] is None:
                abierta[0] = time.monotonic()
            if forzar or time.monotonic() - abierta[0] > CANDADO_MAX_S:
                conn.commit()
                abierta[0] = None
                time.sleep(CEDER_S)
        else:
            abierta[0] = None

    for raiz in env.raices:
        cand = _candidatas(raiz)
        if cand is None:
            log.warning("biblioteca: raíz %s no se puede abrir; se salta", raiz["ruta"])
            res["raices_saltadas"] += 1
            saltadas.append(raiz)
            continue
        leidas.append(raiz)
        for ruta in cand:
            if detener is not None and detener():
                conn.commit()
                return res          # parada a medias: sin faltas ni ausentes (la pasada no vio todo)
            try:
                if raiz_de(ruta, env.raices) is not raiz:
                    continue
                real = os.path.realpath(ruta)
                clave = os.path.normcase(real)
                st = os.stat(real)
                vistos.add(clave)       # existe: pase lo que pase después, no es una falta (un rollback no lo borra)
                fila = conn.execute("SELECT id, mtime, tamano, clase, html_publicado, estado, publicado,"
                                    " interactivo, fecha FROM library_items WHERE ruta=?", (clave,)).fetchone()
                if (fila and fila["clase"] == raiz["clase"] and not os.path.isdir(real)
                        and fila["estado"] == "presente" and not _falta_su_copia(env, raiz, fila)
                        and fila["mtime"] == st.st_mtime and fila["tamano"] == st.st_size
                        and not (raiz["clase"] == "revista" and fila["html_publicado"] is None
                                 and _html_de_revista(env, os.path.basename(real)))):
                    # Sin cambios no se relee, pero la fecha sale del nombre: si la regla cambió, se corrige.
                    conn.execute("UPDATE library_items SET estado='presente', faltas=0, ultima_pasada=?, fecha=?"
                                 " WHERE id=?", (pasada, _fecha(os.path.basename(real), st.st_mtime), fila["id"]))
                    res["vistas"] += 1
                elif (os.path.isdir(real) and fila and fila["estado"] == "presente" and fila["clase"] == raiz["clase"]
                      and fila["mtime"] == _listado_carpeta(real)[1]):
                    conn.execute("UPDATE library_items SET estado='presente', faltas=0, ultima_pasada=? WHERE id=?",
                                 (pasada, fila["id"]))
                    res["vistas"] += 1
                else:
                    # Leer y extraer el fichero NO puede hacerse con el candado cogido: se suelta antes.
                    _suelta_el_candado(forzar=True)
                    if registrar(conn, ruta, raiz["clase"], "scan", env=env, pasada=pasada):
                        res["registradas"] += 1
                    time.sleep(CEDER_S)       # registrar confirma por su cuenta: se cede el candado
                _suelta_el_candado()
            except Exception as e:
                log.exception("biblioteca: recorrer falló con %s", ruta)
                if not isinstance(e, FileNotFoundError):                    # un fichero bloqueado (antivirus, ACL) no ha desaparecido
                    vistos.add(os.path.normcase(os.path.realpath(ruta)))
    try:
        for fila in conn.execute("SELECT id, ruta, clase, faltas, ultima_pasada, publicado FROM library_items"
                                 " WHERE estado='presente'").fetchall():
            if fila["ultima_pasada"] == pasada or fila["ruta"] in vistos:
                continue
            if raiz_de(fila["ruta"], env.raices) is None:
                # Ya no está permitida (raíz quitada/rechazada, patrón, lista negra): ausente YA, sin
                # esperar faltas, y fuera del índice y de publicado/. Una raíz ilegible no cuenta.
                if _dentro_de_alguna(fila["ruta"], saltadas):
                    continue
                conn.execute("UPDATE library_items SET estado='ausente', faltas=2, ultima_pasada=?, publicado=NULL"
                             " WHERE id=?", (pasada, fila["id"]))
                conn.execute("DELETE FROM library_fts WHERE rowid=?", (fila["id"],))
                _borrar_publicado(env, fila["publicado"])
                res["ausentes"] += 1
                continue
            if not any(r["clase"] == fila["clase"] and (
                    fila["ruta"] == r["_real"] if r["tipo"] == "fichero" else _relativa(fila["ruta"], r["_real"]))
                    for r in leidas):
                continue
            faltas = fila["faltas"] + 1
            if faltas >= 2:
                conn.execute("UPDATE library_items SET faltas=?, estado='ausente', ultima_pasada=?, publicado=NULL"
                             " WHERE id=?", (faltas, pasada, fila["id"]))
                _borrar_publicado(env, fila["publicado"])
                res["ausentes"] += 1
            else:
                conn.execute("UPDATE library_items SET faltas=?, ultima_pasada=? WHERE id=?",
                             (faltas, pasada, fila["id"]))
        conn.commit()
        _barrer_huerfanos(conn, env)
    except Exception:
        log.exception("biblioteca: recorrer falló al cerrar la pasada")
    return res


def _barrer_huerfanos(conn, env: Entorno) -> None:
    """Borra de publicado/ lo que ninguna fila cita (restos de un registro deshecho, de una copia
    antigua o un .tmp). Una fila que cita un fichero que falta se rehace sola en la pasada siguiente."""
    if not env.publicado_dir.is_dir():
        return
    citados = {r[0] for r in conn.execute("SELECT publicado FROM library_items WHERE publicado IS NOT NULL")}
    for f in env.publicado_dir.iterdir():
        if f.is_file() and f.suffix in (".html", ".tmp") and f.name not in citados:
            try:
                f.unlink()
            except OSError:
                pass


# ------------------------------------------------------------------ ganchos (Spec §3)

_cache_entorno: dict = {"clave": None, "env": None}


def entorno_cacheado() -> Entorno:
    """El Entorno de la casa, releído solo cuando cambian raices.json o config.json (los ganchos
    corren en cada cierre: no se reparsean 30 raíces cada vez)."""
    from . import env as _env
    home = _env.data_dir()
    clave = []
    for n in ("raices.json", "config.json"):
        try:
            st = os.stat(home / "biblioteca" / n)
            clave.append((n, st.st_mtime_ns, st.st_size))
        except OSError:
            clave.append((n, None, None))
    clave = (str(home), tuple(clave))
    if _cache_entorno["clave"] != clave:
        nuevo = entorno_de_la_casa()
        previo = _cache_entorno["env"]
        if nuevo.raices_ilegibles and previo is not None and not previo.raices_ilegibles:
            log.warning("biblioteca: raices.json ilegible; se sigue con las últimas raíces buenas")
            nuevo = previo
        _cache_entorno["env"], _cache_entorno["clave"] = nuevo, clave
    return _cache_entorno["env"]


EN_LINEA_HASTA = 5          # más rutas que esto se registran en un hilo: 50 rutas son ~400 ms dentro de la petición
_cola: queue.Queue = queue.Queue()
_worker_lock = threading.Lock()
_worker_hilo: list = [None]


def _ruta_de_la_base(conn) -> str | None:
    try:
        f = conn.execute("PRAGMA database_list").fetchone()
        return f["file"] if f and f["file"] else None
    except Exception:
        return None


def _worker() -> None:
    """UN hilo con UNA conexión por base, para los cierres con muchas rutas. Se para con None."""
    conexiones: dict = {}
    try:
        while True:
            trabajo = _cola.get()
            try:
                if trabajo is None:
                    return
                db_path, ticket_id, rutas, env = trabajo
                try:
                    if db_path not in conexiones:
                        from . import db
                        conexiones[db_path] = db.connect(db_path)
                    for ruta in rutas:
                        registrar(conexiones[db_path], ruta, None, "cierre", ticket_id=ticket_id, env=env)
                except Exception:
                    log.exception("biblioteca: el registro en segundo plano de #%s falló", ticket_id)
            finally:
                _cola.task_done()
    finally:
        for c in conexiones.values():
            try:
                c.close()
            except Exception:
                pass
        with _worker_lock:        # llegaron cierres detrás de la señal de parada: otro worker los atiende
            with _cola.mutex:
                hay_trabajo = any(t is not None for t in _cola.queue)
            if hay_trabajo and _worker_hilo[0] is threading.current_thread():
                h = threading.Thread(target=_worker, name="biblioteca-cierre", daemon=True)
                _worker_hilo[0] = h
                h.start()


def _encolar(db_path: str, ticket_id: int, rutas: list[str], env: Entorno) -> None:
    with _worker_lock:
        h = _worker_hilo[0]
        if h is None or not h.is_alive():
            h = threading.Thread(target=_worker, name="biblioteca-cierre", daemon=True)
            _worker_hilo[0] = h
            h.start()
    _cola.put((db_path, ticket_id, rutas, env))


def esperar_pendientes(timeout: float = 15.0) -> None:
    """Espera a que el worker vacíe la cola (apagado del daemon, tests)."""
    fin = time.monotonic() + timeout
    while _cola.unfinished_tasks and time.monotonic() < fin:
        time.sleep(0.01)


def parar_worker(timeout: float = 10.0) -> None:
    """Vacía la cola (hasta `timeout`, UNA vez), para el worker y le hace cerrar su conexión. Si queda
    trabajo se dice cuánto y de qué tickets; un hilo que sigue vivo NO se olvida."""
    fin = time.monotonic() + timeout
    esperar_pendientes(timeout)
    with _worker_lock:
        h = _worker_hilo[0]
    if h is None:
        return
    with _cola.mutex:
        quedan = [t[1] for t in list(_cola.queue) if t is not None]
        ya_hay_senal = any(t is None for t in _cola.queue)
    if quedan:
        log.warning("biblioteca: al parar quedan %d cierres sin registrar (tickets %s)", len(quedan), quedan)
    if h.is_alive():
        if not ya_hay_senal:
            _cola.put(None)
        h.join(max(0.0, fin - time.monotonic()))     # sin el candado del worker: su salida también lo toma
    with _worker_lock:
        if not h.is_alive() and _worker_hilo[0] is h:
            _worker_hilo[0] = None


CARPETA_MAX = 200           # ficheros enlazados como mucho por carpeta de evidencia (#1154)
CARPETA_VISTOS_MAX = 5000   # entradas que se miran como mucho al recorrerla: el cierre no se cuelga en una carpeta enorme


def _expandir_carpetas(rutas: list[str], env: Entorno) -> list[str]:
    """#1154: bajo una raíz recursiva una carpeta no es una entrada (raiz_de), así que una evidencia que es una
    CARPETA se enlaza por los ficheros de dentro que la Biblioteca sí admite. Las rutas que ya son entradas
    (ficheros, artifacts/run_N) pasan tal cual. Lo de la lista negra no se recorre."""
    out: list[str] = []
    for r in rutas:
        if not os.path.isdir(r) or raiz_de(r, env.raices) is not None:
            if r not in out:
                out.append(r)
            continue
        enlazados = vistos = 0
        for base, dirs, ficheros in os.walk(r):
            dirs[:] = sorted(d for d in dirs if not en_lista_negra(os.path.join(base, d)))
            for f in sorted(ficheros):
                vistos += 1
                p = os.path.join(base, f)
                if p not in out and raiz_de(p, env.raices) is not None:
                    out.append(p)
                    enlazados += 1
                if enlazados >= CARPETA_MAX or vistos >= CARPETA_VISTOS_MAX:
                    break
            if enlazados >= CARPETA_MAX or vistos >= CARPETA_VISTOS_MAX:
                break
    return out


def al_cerrar(conn, ticket_id: int, tipo: str | None, evidencia: str | None, env: Entorno | None = None) -> int:
    """Tras cerrar un ticket: registra las rutas de su evidencia (regla por tipo, D4b) y las enlaza al
    ticket. Con pocas rutas, en línea (devuelve cuántas); con más de EN_LINEA_HASTA, en un hilo con SU
    conexión (devuelve 0: la petición no espera). NUNCA lanza: cerrar un ticket no puede fallar por esto."""
    try:
        env = env or entorno_cacheado()
        if not env.raices:
            return 0
        rutas = _expandir_carpetas(rutas_de_evidencia(tipo, evidencia, env), env)
        if len(rutas) > EN_LINEA_HASTA:
            db_path = _ruta_de_la_base(conn)
            if db_path:
                _encolar(db_path, ticket_id, rutas, env)
                return 0
        n = 0
        for ruta in rutas:
            if registrar(conn, ruta, None, "cierre", ticket_id=ticket_id, env=env):
                n += 1
        return n
    except Exception:
        log.exception("biblioteca: al_cerrar(#%s) falló", ticket_id)
        return 0


def al_terminar_run(conn, run_id: int, job_id: int | None, ticket_id: int | None, env: Entorno | None = None) -> int | None:
    """Tras terminar un run: registra `artifacts/run_<id>` enlazada a su job y a su ticket. Nunca lanza."""
    try:
        env = env or entorno_cacheado()
        if not env.raices or env.artifacts_dir is None:
            return None
        carpeta = Path(env.artifacts_dir) / f"run_{int(run_id)}"
        if not carpeta.is_dir():
            return None
        return registrar(conn, carpeta, "resultado", "run", run_id=run_id, job_id=job_id, ticket_id=ticket_id, env=env)
    except Exception:
        log.exception("biblioteca: al_terminar_run(#%s) falló", run_id)
        return None


class Barredor:
    """El recorrido al arrancar y cada hora, en SU hilo y con SU conexión (la del daemon no se comparte
    entre hilos ni se bloquea el bucle de eventos). `stop()` lo para sin esperar a que acabe la hora; un
    fallo se registra y el hilo sigue: no tumba el daemon."""

    def __init__(self, db_path, cada_s: float = 3600.0, entorno=entorno_de_la_casa):
        self.db_path, self.cada_s, self.entorno = db_path, cada_s, entorno
        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None
        self.pasadas = 0

    def start(self) -> None:
        if self._hilo is None:
            self._hilo = threading.Thread(target=self._bucle, name="biblioteca-recorrido", daemon=True)
            self._hilo.start()

    def stop(self, timeout: float = 15.0) -> None:
        self._parar.set()
        if self._hilo is not None:
            self._hilo.join(timeout)

    def _bucle(self) -> None:
        while not self._parar.is_set():
            self.una_pasada()
            self._parar.wait(self.cada_s)

    def una_pasada(self) -> dict | None:
        conn = None
        try:
            from . import db
            conn = db.connect(self.db_path)
            res = recorrer(conn, self.entorno(), detener=self._parar.is_set)
            self.pasadas += 1
            log.info("biblioteca: recorrido %s", res)
            return res
        except Exception:
            log.exception("biblioteca: el recorrido falló (se reintenta a la hora)")
            return None
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


# ------------------------------------------------------------------ rutas en prosa y evidencias

_RUTA_WIN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:(?!\W[A-Za-z]:[\\/])[^\n\r\"'<>|*?`])*")


def _unidad_local(ruta: str) -> bool:
    """False si la letra es una unidad de red: hacer stat de ahí puede filtrar credenciales NTLM."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        return ctypes.windll.kernel32.GetDriveTypeW(ruta[:3]) not in (0, 1, 4)   # desconocida, sin raíz, remota
    except Exception:
        return False


def rutas_de_texto(texto: str, max_rutas: int = 50) -> list[str]:
    """Rutas absolutas de Windows que aparecen en prosa. Si la ruta lleva espacios, gana el
    prefijo más largo que exista en disco; si ninguno existe, la primera palabra. Antes de tocar el
    disco se descarta lo sospechoso (`_ruta_sospechosa`: UNC, flujos, `..`...) y las unidades de red."""
    out: list[str] = []
    for m in _RUTA_WIN.finditer((texto or "")[:200_000]):
        if len(out) >= max_rutas:
            break
        trozos = m.group(0).rstrip().split(" ")[:20]
        candidatos = [" ".join(trozos[:i]).rstrip(".,;:)]}»") for i in range(len(trozos), 0, -1)]
        candidatos = [c for c in candidatos if c and not _ruta_sospechosa(c)]
        if not candidatos or not _unidad_local(candidatos[0]):
            continue
        # Gana el prefijo más largo que sea un FICHERO; una carpeta solo si es el texto entero. Si no,
        # el texto entero (que no existe y nadie registrará): nunca «...\Faro» por «...\Faro Notes\x.md».
        elegido = next((c for c in candidatos if os.path.isfile(c)), None)
        if elegido is None:
            # el más largo que termine en «.ext» (el resto del texto es prosa); si no, el más largo
            elegido = next((c for c in candidatos if re.search(r"[.][A-Za-z0-9]{1,8}$", c)), candidatos[0])
        if elegido not in out:
            out.append(elegido)
    return out


def rutas_de_evidencia(tipo: str | None, texto: str | None, env: Entorno) -> list[str]:
    """Regla D4b: qué rutas se registran según el tipo de evidencia."""
    if tipo in ("file_path", "command_output"):
        return rutas_de_texto(texto or "")
    if tipo == "run_output":
        out = []
        m = re.match(r"\s*([0-9]{1,12})(?![0-9])", texto or "")
        if m and env.artifacts_dir is not None:
            carpeta = env.artifacts_dir / f"run_{int(m.group(1))}"
            if carpeta.is_dir():
                out.append(str(carpeta))
        return out + [r for r in rutas_de_texto(texto or "") if r not in out]
    return []   # memory_id, peer_attestation, proof y lo demás: nada


# ------------------------------------------------------------------ Markdown propio (B7)

def _url_ok(u: str) -> bool:
    return bool(re.match(r"(?i)(https?://|mailto:)", u.strip()))


def _enlace(u: str, texto_html: str) -> str:
    return (f'<a href="{_html.escape(u, quote=True)}" target="_blank" rel="noopener noreferrer">'
            f'{texto_html}</a>')


def _enfasis(s: str) -> str:
    s = re.sub(r"\*\*(?=\S)(.{1,500}?)(?<=\S)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w_])__(?=\S)(.{1,500}?)(?<=\S)__(?![\w_])", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*(?=[^\s*])(.{1,500}?)(?<=[^\s*])\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"(?<![\w_])_(?=[^\s_])(.{1,500}?)(?<=[^\s_])_(?![\w_])", r"<em>\1</em>", s)
    return s


LINEA_MAX = 10_000


def _inline(s: str) -> str:
    if len(s) > LINEA_MAX:              # sin regex: las líneas enormes son texto plano
        return _html.escape(s.replace("\x00", ""), quote=False)
    guardado: list[str] = []

    def guarda(h: str) -> str:
        guardado.append(h)
        return f"\x00{len(guardado) - 1}\x00"

    s = re.sub(r"`([^`\n]+)`", lambda m: guarda(f"<code>{_html.escape(m.group(1), quote=False)}</code>"), s)
    s = _html.escape(s, quote=False)

    def link(m):
        u = _html.unescape(m.group(2))
        return guarda(_enlace(u, _enfasis(m.group(1)))) if _url_ok(u) else m.group(0)

    s = re.sub(r"\[([^\]\n]{1,500})\]\(([^)\s]{1,1000})\)", link, s)

    def pelado(m):
        u = _html.unescape(m.group(1))
        cola = ""
        while u and u[-1] in ".,;:!?":
            cola, u = u[-1] + cola, u[:-1]
        return guarda(_enlace(u, _html.escape(u, quote=False))) + cola

    s = re.sub(r"(?<![\w\"'=/])(https?://[^\s<>\"'\x00]+)", pelado, s)
    s = _enfasis(s)
    while "\x00" in s:
        nuevo = re.sub(r"\x00(\d+)\x00", lambda m: guardado[int(m.group(1))], s)
        if nuevo == s:
            break
        s = nuevo
    return s


_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")


def md_a_html(texto: str, _nivel: int = 0) -> str:
    """Markdown → fragmento HTML: títulos, párrafos, listas (anidadas), enlaces, negrita, cursiva,
    código, citas, tablas simples y reglas. El HTML crudo del texto se escapa; solo http(s) y mailto
    son enlaces. Sin dependencias."""
    texto = (texto or "").replace("\x00", "")
    try:
        return _md_a_html(texto, _nivel)
    except Exception:                    # nunca lanza: lo que no se pueda convertir se muestra como texto
        log.exception("biblioteca: md_a_html falló")
        return "<pre>" + _html.escape(texto[:LEER_MAX], quote=False) + "</pre>"


def _md_a_html(texto: str, _nivel: int) -> str:
    lineas = texto.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lineas and lineas[0].strip() == "---":
        for i in range(1, len(lineas)):
            if lineas[i].strip() == "---":
                lineas = lineas[i + 1:]
                break
    out: list[str] = []
    par: list[str] = []
    pila: list[tuple[int, str]] = []

    def cierra_par():
        if par:
            out.append("<p>" + _inline(" ".join(x.strip() for x in par)) + "</p>")
            par.clear()

    def cierra_listas():
        while pila:
            out.append(f"</li></{pila.pop()[1]}>")

    i = 0
    while i < len(lineas):
        ln = lineas[i]
        if ln.strip().startswith("```"):
            cierra_par(); cierra_listas()
            j, buf = i + 1, []
            while j < len(lineas) and not lineas[j].strip().startswith("```"):
                buf.append(lineas[j]); j += 1
            out.append("<pre><code>" + _html.escape("\n".join(buf), quote=False) + "</code></pre>")
            i = j + 1
            continue
        if not ln.strip():
            cierra_par(); cierra_listas(); i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", ln)
        if m:
            cierra_par(); cierra_listas()
            n = len(m.group(1))
            out.append(f"<h{n}>{_inline(m.group(2))}</h{n}>"); i += 1
            continue
        if re.match(r"^\s*([-*_])(\s*\1){2,}\s*$", ln):
            cierra_par(); cierra_listas(); out.append("<hr>"); i += 1
            continue
        if ln.lstrip().startswith(">"):
            cierra_par(); cierra_listas()
            buf = []
            while i < len(lineas) and lineas[i].lstrip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lineas[i])); i += 1
            if _nivel >= 8:             # más anidamiento = texto plano (">"*3000 no desborda la pila)
                out.append("<blockquote><p>" + _html.escape(" ".join(buf), quote=False) + "</p></blockquote>")
            else:
                out.append("<blockquote>" + md_a_html("\n".join(buf), _nivel + 1) + "</blockquote>")
            continue
        if ln.lstrip().startswith("|") and i + 1 < len(lineas) and re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", lineas[i + 1]):
            cierra_par(); cierra_listas()
            celdas = lambda x: [c.strip() for c in x.strip().strip("|").split("|")]
            filas = [celdas(ln)]
            i += 2
            while i < len(lineas) and lineas[i].lstrip().startswith("|"):
                filas.append(celdas(lineas[i])); i += 1
            t = "<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in filas[0]) + "</tr></thead><tbody>"
            for f in filas[1:]:
                t += "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in f) + "</tr>"
            out.append(t + "</tbody></table>")
            continue
        m = _ITEM.match(ln)
        if m:
            cierra_par()
            ind, tag = len(m.group(1).replace("\t", "    ")), ("ol" if m.group(2)[0].isdigit() else "ul")
            while pila and ind < pila[-1][0]:
                out.append(f"</li></{pila.pop()[1]}>")
            if pila and ind == pila[-1][0] and tag != pila[-1][1]:
                out.append(f"</li></{pila.pop()[1]}>")
            if pila and ind == pila[-1][0]:
                out.append("</li>")
            else:
                out.append(f"<{tag}>"); pila.append((ind, tag))
            out.append("<li>" + _inline(m.group(3)))
            i += 1
            continue
        cierra_listas()
        par.append(ln); i += 1
    cierra_par(); cierra_listas()
    return "\n".join(out)


# ------------------------------------------------------------------ búsqueda

def _terminos(q: str, union: str = " OR ") -> str:
    # Las comillas y los NUL de dentro de una palabra romperían el FTS5 (`"a"b"*`): se quitan.
    limpias = (w.replace('"', "").replace("\x00", "") for w in (q or "").split())
    palabras = [w for w in limpias if len(w) > 1]
    if not palabras:
        raise ValueError("búsqueda vacía (mínimo una palabra de 2+ letras)")
    return union.join(f'"{w}"*' for w in palabras[:8])


def buscar(conn, q: str | None = None, *, clase=None, desde=None, hasta=None, limite: int = 50,
           offset: int = 0, filtro=None) -> list[dict]:
    """Solo entradas presentes. Con `q`: FTS5 saneado (como search.py) ordenado por bm25(3,1).
    Sin `q`: la lista por fecha, la misma vista que pinta la pestaña (criterio 2)."""
    filtros, args = ["i.estado = 'presente'"], []
    if clase:
        filtros.append("i.clase = ?"); args.append(clase)
    if desde:
        filtros.append("i.fecha >= ?"); args.append(desde)
    if hasta:
        filtros.append("i.fecha <= ?"); args.append(hasta)
    limite = max(1, min(int(limite), 500))
    offset = max(0, int(offset))
    donde = " AND ".join(filtros)
    if q and q.strip():
        # #1076 (criterio 4): primero las que tienen TODAS las palabras y luego las que tienen alguna, cada
        # tramo por bm25. Con solo OR, un documento que repetía muchas de las palabras le ganaba al que las
        # tenía todas («KnowTwin — Execution Plan v4» salía 2.º detrás del orchestration.md de su sesión).
        # Y delante de todo, la entrada cuyo título ES la consulta (criterio 4: el orchestration.md de la
        # sesión también tiene todas las palabras del título del plan).
        todas, alguna = _terminos(q, " AND "), _terminos(q)
        base = ("SELECT i.*, {tramo} AS tramo, bm25(library_fts, 3, 1) AS score FROM library_fts JOIN library_items i"
                f" ON i.id = library_fts.rowid WHERE library_fts MATCH ? AND {donde}")
        sql = ("SELECT * FROM (" + base.format(tramo=1) + " UNION ALL " + base.format(tramo=2)
               + " AND i.id NOT IN (SELECT rowid FROM library_fts WHERE library_fts MATCH ?))"
               " ORDER BY CASE WHEN lower(trim(titulo)) = lower(trim(?)) THEN 0"
               "  WHEN lower(coalesce(proyecto, '')) = lower(trim(?)) THEN 1 ELSE tramo + 1 END,"
               # dentro de la documentación del proyecto, primero su CLAUDE.md y su fichero de estado
               " CASE WHEN lower(coalesce(proyecto, '')) = lower(trim(?))"
               "  AND (lower(ruta) LIKE '%claude.md' OR lower(ruta) LIKE '%estado%') THEN 0 ELSE 1 END,"
               " score, id")
        # (criterio 11) Si la consulta es el nombre de un proyecto, su documentación va justo detrás.
        args = [todas] + args + [alguna] + args + [todas, q, q, q]
    else:
        sql = f"SELECT i.* FROM library_items i WHERE {donde} ORDER BY i.fecha DESC, i.id DESC"
    if filtro is None:
        return [dict(r) for r in conn.execute(sql + " LIMIT ? OFFSET ?", args + [limite, offset])]
    # Con filtro (permitida) ANTES de limite y offset: se recorre el cursor hasta llenar la página, así una
    # página llena puede tener más y una corta es el final, aunque haya filas viejas en medio.
    out, saltadas = [], 0
    for r in conn.execute(sql, args):
        if not filtro(r):
            continue
        if saltadas < offset:
            saltadas += 1
            continue
        out.append(dict(r))
        if len(out) >= limite:
            break
    return out
