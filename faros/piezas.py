"""#1346: la regla de las piezas. Un criterio de verificación se escribe en piezas con su etiqueta
(«C1: …», «C2 [fichero]: …») y un «pass» tiene que responder a cada una en un campo estructurado,
no en la nota libre. Diseño de Prima e Hilo (20-nov); casos de Prima en casos_1346.md; dudas
acordadas el 23-nov.

Este módulo es puro (ni base ni red): parte el criterio, calcula su huella y valida lo que manda
quien verifica. service.py lo llama en verify/complete y traduce PiezasError a TicketError.

Lo que comprueba es FORMA, nunca contenido: que una [salida] no esté vacía, que la ruta de un
[fichero] exista, que una [persona: X] la cierre X. Si la salida pegada es verdad, no lo sabe.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass

ESTADOS = ("cumple", "no_cumple", "no_comprobable", "incierto")
VEREDICTOS = ("pass", "fail", "incierto")

# «C1: texto», «C11a [salida]: texto», «C12 [persona: Prima]: texto». La etiqueta se lee del texto,
# nunca de la posición (C4-d: un salto en la numeración vale).
_ETIQUETA = re.compile(r"^\s*(?P<etiqueta>C\d+[a-z]?)\s*(?:\[(?P<marca>[^\]]*)\])?\s*:\s*(?P<texto>.*)$")
_PERSONA = re.compile(r"^persona\s*:\s*(?P<quien>.+)$", re.I)
# #1346 (25-nov, Prima): una etiqueta también abre pieza en mitad de línea, pero solo detrás de «. » o
# «; » (#1397 tenía C1-C4 en una línea). «(C1:, C2:…)» o «el caso C14 queda» no parten.
_FORMA_ETIQUETA = r"C\d+[a-z]?\s*(?:\[[^\]]*\])?\s*:"
_EN_LINEA = re.compile(r"(?<=[.;])[ \t]+(?=" + _FORMA_ETIQUETA + ")")
# Para el aviso: lo que tiene forma de etiqueta y se ha quedado dentro de una pieza.
_ESCONDIDA = re.compile(r"(?<![\w#])" + _FORMA_ETIQUETA)
# Una referencia que se puede abrir (propuesta de Prima, paper 2611.18471): de momento solo avisa.
_REFERENCIA = re.compile(
    r"(?:https?://\S+|#\d+|\b[0-9a-f]{7,40}\b|\b[0-9a-f]{8}-[0-9a-f]{4}-|[A-Za-z]:[\\/]\S+|(?:^|\s)[~.]?/\S+|\S+\.\w{1,5}\b)",
    re.I,
)


class PiezasError(ValueError):
    def __init__(self, code: str, mensaje: str, field: str = "piezas"):
        super().__init__(mensaje)
        self.code = code
        self.field = field


@dataclass(frozen=True)
class Pieza:
    etiqueta: str
    marca: str  # "" | "salida" | "fichero" | "persona"
    persona: str  # el X de [persona: X], o ""
    texto: str  # normalizado
    partido_por_faros: bool = False


def normalizar(texto: str | None) -> str:
    """Lo que cuenta como cambio (duda 1, lectura b): CRLF → LF, sin espacios al final de línea y sin
    líneas vacías en los extremos. Un CRLF o un espacio de más no es un cambio de criterio (#1263)."""
    lineas = [ln.rstrip() for ln in (texto or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lineas and not lineas[0]:
        lineas.pop(0)
    while lineas and not lineas[-1]:
        lineas.pop()
    return "\n".join(lineas)


def _leer_marca(marca: str | None) -> tuple[str, str]:
    m = (marca or "").strip()
    if not m:
        return "", ""
    p = _PERSONA.match(m)
    if p:
        return "persona", p.group("quien").strip()
    return m.lower(), ""


def partir(criterio: str | None) -> list[Pieza]:
    """Las piezas del criterio. Con etiquetas: cada línea etiquetada abre una pieza y las líneas
    siguientes sin etiqueta son parte de su texto. Sin ninguna etiqueta (criterio viejo, C6): una
    pieza por línea no vacía, C1, C2…, marcadas «partido por FarOS»; siempre la misma partición.
    Etiquetas repetidas → criteria.duplicate_label (C4-b/c). Sin criterio → [] (C13).
    Una etiqueta detrás de «. » o «; » cuenta como si empezara línea (25-nov)."""
    return _partir_lineas(normalizar(_EN_LINEA.sub("\n", normalizar(criterio))))


def _partir_lineas(texto: str) -> list[Pieza]:
    if not texto.strip():
        return []
    lineas = texto.split("\n")
    if not any(_ETIQUETA.match(ln) for ln in lineas):
        utiles = [ln.strip() for ln in lineas if ln.strip()]
        return [Pieza(f"C{i}", "", "", ln, partido_por_faros=True) for i, ln in enumerate(utiles, 1)]

    piezas: list[dict] = []
    sueltas: list[str] = []
    for ln in lineas:
        m = _ETIQUETA.match(ln)
        if m:
            marca, persona = _leer_marca(m.group("marca"))
            piezas.append(
                {"etiqueta": m.group("etiqueta"), "marca": marca, "persona": persona, "lineas": [m.group("texto")]}
            )
        elif piezas:
            piezas[-1]["lineas"].append(ln)
        elif ln.strip():
            sueltas.append(ln)
    vistas: dict[str, int] = {}
    for p in piezas:
        vistas[p["etiqueta"]] = vistas.get(p["etiqueta"], 0) + 1
    repetidas = sorted(e for e, n in vistas.items() if n > 1)
    if repetidas:
        raise PiezasError(
            "criteria.duplicate_label",
            f"el criterio repite la etiqueta {', '.join(repetidas)}: cada pieza lleva la suya",
            field="verify_criteria",
        )
    out = [Pieza(p["etiqueta"], p["marca"], p["persona"], normalizar("\n".join(p["lineas"]))) for p in piezas]
    if sueltas:
        # El texto antes de la primera etiqueta no es de ninguna pieza: se guarda como una pieza
        # más para que nadie pueda dejarlo sin responder.
        out.insert(0, Pieza("C0", "", "", normalizar("\n".join(sueltas)), partido_por_faros=True))
    return out


def huella(criterio: str | None) -> dict[str, str]:
    """sha256 por pieza, con su etiqueta y su marca dentro: cambiar el texto, la marca o la etiqueta
    (renombrar, C5-e) cambia la huella."""
    return _huella_de(partir(criterio))


def huella_v18(criterio: str | None) -> dict[str, str]:
    """La huella como la tomaba la 3.3.54 (etiquetas solo a principio de línea). Solo para la
    migración v19: saber si el texto de una tarea en curso sigue siendo el que era al empezar."""
    return _huella_de(_partir_lineas(normalizar(criterio)))


def _huella_de(ps: list[Pieza]) -> dict[str, str]:
    return {
        p.etiqueta: hashlib.sha256(f"{p.etiqueta}\n{p.marca}:{p.persona}\n{p.texto}".encode()).hexdigest() for p in ps
    }


def cambios_desde(huella_vieja: dict[str, str] | None, criterio: str | None) -> dict[str, str]:
    """{etiqueta: "cambiada" | "borrada" | "nueva"} entre la huella de in_progress y el criterio de
    ahora. Sin huella (tarea anterior a la regla y no tomada al instalar) → {}."""
    if not huella_vieja:
        return {}
    nueva = huella(criterio)
    out = {}
    for e, h in huella_vieja.items():
        if e not in nueva:
            out[e] = "borrada"
        elif nueva[e] != h:
            out[e] = "cambiada"
    for e in nueva:
        if e not in huella_vieja:
            out[e] = "nueva"
    return out


def _orden(etiquetas) -> list[str]:
    def clave(e):
        m = re.match(r"C(\d+)([a-z]?)", e)
        return (int(m.group(1)), m.group(2)) if m else (10**9, e)

    return sorted(etiquetas, key=clave)


def validar(
    criterio: str | None,
    verdict: str,
    piezas: dict | None,
    agent: str,
    house_owner: str,
    huella_vieja: dict[str, str] | None = None,
    porque: dict | None = None,
    existe=os.path.exists,
    sistema: frozenset | set = frozenset(),
) -> dict:
    """Valida un verify (o el complete que sella una tarea self) contra las piezas del criterio.
    Lanza PiezasError con el código y un mensaje que nombra las piezas. Devuelve
    {"piezas": [...], "historial": str|None, "avisos": [str]} para el historial y la respuesta."""
    if verdict not in VEREDICTOS:
        raise PiezasError("verify.verdict_invalid", f"verdict inválido: {verdict!r} (pass|fail|incierto)", "verdict")
    definidas = partir(criterio)
    if not definidas:  # C13: sin criterio, como hoy
        if piezas:
            raise PiezasError("verify.piezas_unknown", "la tarea no tiene criterio: no hay piezas que responder")
        if verdict == "incierto":
            raise PiezasError(
                "verify.incierto_sin_pieza", "un «incierto» tiene que decir qué pieza no se pudo comprobar"
            )
        return {"piezas": [], "historial": None, "avisos": []}
    por_etiqueta = {p.etiqueta: p for p in definidas}
    avisos: list[str] = []

    # C15 (Prima, 23-nov, provisional): los agentes de sistema (verifier-bot, runner-bot) verifican
    # sin piezas hasta que el juez responda pieza a pieza (CI/CD de Fiel, #1379). Quién es de sistema
    # lo dice quien llama (service.SYSTEM_AGENTS), nunca el que verifica: Fiel no es de sistema.
    if piezas is None and agent in sistema:
        return {
            "piezas": [],
            "historial": f"verificada por {agent} sin piezas (exención de los bots, provisional: #1379)",
            "avisos": [],
        }

    # C14 (decisión 136, «No me la apliquéis»): el dueño de la casa verifica sin piezas.
    if piezas is None and agent == house_owner:
        sin_su_persona = [p for p in definidas if p.marca == "persona" and p.persona.lower() != agent.lower()]
        historial = f"verificada por {house_owner} sin piezas"
        if sin_su_persona:
            lista = ", ".join(f"{p.etiqueta} [persona: {p.persona}]" for p in sin_su_persona)
            historial += f"; quedan sin su persona: {lista}"
            avisos.append(
                f"AVISO: {lista} {'era' if len(sin_su_persona) == 1 else 'eran'} de otra persona y se cierra sin ella"
            )
        return {"piezas": [], "historial": historial, "avisos": avisos}

    piezas = piezas or {}
    if not isinstance(piezas, dict):
        raise PiezasError("verify.piezas_invalid", "«piezas» es un objeto {C1: {estado, prueba}, …}")
    desconocidas = [e for e in piezas if e not in por_etiqueta]
    if desconocidas:
        partido = (
            " (el criterio no tiene etiquetas: FarOS lo partió por líneas y la partición no se cambia)"
            if any(p.partido_por_faros for p in definidas)
            else ""
        )
        raise PiezasError(
            "verify.piezas_unknown",
            f"piezas que no están en el criterio: {', '.join(_orden(desconocidas))}{partido}",
        )
    faltan = [p.etiqueta for p in definidas if p.etiqueta not in piezas]
    if verdict == "pass" and faltan:
        raise PiezasError(
            "verify.piezas_missing",
            f"un pass responde a cada pieza del criterio; faltan: {', '.join(faltan)}",
        )

    leidas, ajenas_del_dueno = [], []
    for e in _orden(piezas):
        p, entrada = por_etiqueta[e], piezas[e]
        if not isinstance(entrada, dict):
            raise PiezasError("verify.piezas_invalid", f"{e}: cada pieza es {{estado, prueba}}")
        estado = entrada.get("estado")
        prueba = str(entrada.get("prueba") or "")
        if estado not in ESTADOS:
            raise PiezasError("verify.pieza_estado_invalid", f"{e}: estado {estado!r} (válidos: {', '.join(ESTADOS)})")
        if p.marca == "persona" and p.persona.lower() != agent.lower():
            # Prima, 24-nov: para el dueño de la casa es aviso también con piezas, como sin ellas (C14).
            if agent != house_owner:
                raise PiezasError(
                    "verify.persona_only", f"{e} es [persona: {p.persona}]: solo la cierra {p.persona}, no {agent}"
                )
            ajenas_del_dueno.append(f"{e} [persona: {p.persona}]")
        if verdict == "pass" and estado in ("no_cumple", "no_comprobable"):
            raise PiezasError("verify.pieza_no_cumple", f"{e} está «{estado}»: con eso no hay pass")
        if verdict == "pass" and estado == "incierto":
            raise PiezasError("verify.pieza_incierta", f"{e} está «incierto»: un incierto no entra en un pass")
        if estado == "cumple":
            if p.marca == "salida" and not prueba.strip():
                raise PiezasError(
                    "verify.prueba_forma", f"{e} es [salida]: la prueba es la salida pegada, y está vacía"
                )
            if p.marca == "fichero" and not (prueba.strip() and existe(prueba.strip())):
                raise PiezasError("verify.prueba_forma", f"{e} es [fichero]: la ruta {prueba.strip()!r} no existe")
            if not p.marca and not _REFERENCIA.search(prueba):
                avisos.append(f"AVISO: {e} no lleva una referencia que se pueda abrir (ruta, #tarea, id, URL o sha)")
        leidas.append({"etiqueta": e, "estado": estado, "prueba": prueba})

    if verdict == "incierto" and not any(x["estado"] == "incierto" for x in leidas):
        raise PiezasError("verify.incierto_sin_pieza", "un «incierto» tiene que decir qué pieza no se pudo comprobar")

    historial = "; ".join(f"{x['etiqueta']} {x['estado']}: {x['prueba']}" for x in leidas) or None
    if ajenas_del_dueno:
        lista = ", ".join(ajenas_del_dueno)
        historial = (historial + "; " if historial else "") + f"quedan sin su persona: {lista}"
        avisos.append(
            f"AVISO: {lista} {'era' if len(ajenas_del_dueno) == 1 else 'eran'} de otra persona y se cierra sin ella"
        )
    cambios = cambios_desde(huella_vieja, criterio)
    if cambios and verdict == "pass":
        porque = porque or {}
        sin = [e for e in _orden(cambios) if not str(porque.get(e) or "").strip()]
        if sin:
            raise PiezasError(
                "verify.piezas_cambiadas",
                "el criterio cambió desde que empezó la tarea ("
                + ", ".join(f"{e} {cambios[e]}" for e in sin)
                + "): el pass dice por qué cambió cada una",
                field="cambios",
            )
        historial = (
            (historial + " — " if historial else "")
            + "cambios del criterio: "
            + "; ".join(f"{e} {cambios[e]}: {porque[e]}" for e in _orden(cambios))
        )
    return {"piezas": leidas, "historial": historial, "avisos": avisos}


def aviso_sin_piezas(criterio: str | None) -> str | None:
    """C10: para criterion_warnings. Avisa (no bloquea) si el criterio no está escrito en piezas."""
    piezas = partir(criterio)
    if piezas and all(p.partido_por_faros for p in piezas):
        return "el criterio no está en piezas (C1:, C2:…): FarOS lo partirá por líneas y cada línea será una pieza"
    return None


def aviso_escondidas(criterio: str | None) -> str | None:
    """Para criterion_warnings (Prima, 25-nov): algo con forma de etiqueta que, después de partir, se
    ha quedado dentro de otra pieza. Avisa, no bloquea: puede ser una mención («(C1:, C2:…)»)."""
    try:
        ps = partir(criterio)
    except PiezasError:
        return None  # la etiqueta repetida ya da su propio error
    dentro = [f"«{m.group(0)}» en {p.etiqueta}" for p in ps for m in _ESCONDIDA.finditer(p.texto)]
    if dentro:
        return (
            f"hay {'algo' if len(dentro) == 1 else 'cosas'} con forma de etiqueta dentro de otra pieza ("
            + ", ".join(dentro)
            + "): si es una pieza, empieza su línea o va detrás de «. » o «; »; si es una mención, ignora este aviso"
        )
    return None
