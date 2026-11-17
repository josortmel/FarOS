"""Timbre de franjas (#1184): a la hora de inicio de cada tarea del calendario de un agente, FarOS le manda
un aviso por el relay de la casa. Es un timbre, no una orden: dice la hora y lo que estaba planeado, y
recuerda la regla del dueño (cerrar lo que se está haciendo con su siguiente paso y empezar esto, salvo
prioridad acordada). Lo que toca de verdad lo lee cada uno en su tablero.

- Lee el calendario EN CADA VUELTA: una tarea movida a media mañana suena a su hora nueva.
- Solo se manda a quien tiene sesión en el relay; si no está, no se manda ni se reintenta.
- Si el daemon estuvo parado, solo suena lo que empezó en los últimos VENTANA_S segundos.
- Al dueño no se le manda por el relay: lo suyo va por el bot de Telegram (#1189, del_dueno).
- FAROS_TIMBRE=0 lo apaga (tests y copias de prueba de la casa)."""
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from . import agenda, roster

log = logging.getLogger("faros.timbre")

NOMBRE = "FarOS"
VENTANA_S = 300
CERRADAS = {"done", "verified", "rejected"}
PROTOCOLO_RELAY = "5"


def activo(conn=None) -> bool:
    """FAROS_TIMBRE=0 lo apaga siempre; si no, manda el ajuste «timbre_activo» (el interruptor de la app)."""
    if os.environ.get("FAROS_TIMBRE", "1") == "0":
        return False
    if conn is None:
        return True
    from . import settings
    return bool(settings.get(conn, "timbre_activo"))


def _hasta(valor, ahora: datetime) -> str | None:
    """«1h», «3h», «mañana» (hasta las 00:00 de mañana), «siempre», una fecha ISO, o vacío para quitarlo."""
    v = (str(valor).strip().lower() if valor is not None else "")
    if v in ("", "nada", "quitar", "no"):
        return None
    if v == "siempre":
        return "siempre"
    if v in ("mañana", "manana"):
        return datetime.combine(ahora.date() + timedelta(days=1), datetime.min.time()).isoformat(timespec="seconds")
    m = re.fullmatch(r"(\d{1,2})\s*h", v)
    if m:
        return (ahora + timedelta(hours=int(m.group(1)))).isoformat(timespec="seconds")
    return datetime.fromisoformat(str(valor).strip()).isoformat(timespec="seconds")   # ValueError si no se entiende


def silencios(conn, ahora: datetime | None = None) -> dict[str, str]:
    """Los silencios que siguen vigentes ahora."""
    from . import settings
    ahora = ahora or datetime.now()
    out = {}
    for agente, hasta in (settings.get(conn, "timbre_silencio") or {}).items():
        if hasta == "siempre":
            out[agente] = hasta
        else:
            try:
                if datetime.fromisoformat(hasta) > ahora:
                    out[agente] = hasta
            except (TypeError, ValueError):
                continue
    return out


def silenciar(conn, quien: str, agente: str, hasta, ahora: datetime | None = None) -> dict:
    """Cada uno silencia el suyo; el dueño de la casa puede quitar o poner el de cualquiera (desde Ajustes)."""
    from . import settings
    from .service import TicketError
    if agente not in roster.AGENTS:
        raise TicketError(f"agente desconocido: {agente!r}", field="agente")
    if quien != agente and quien != roster.HOUSE_OWNER:
        raise TicketError("cada uno silencia solo su timbre (o el dueño de la casa desde Ajustes)",
                          field="agente", code="timbre.solo_el_suyo")
    ahora = ahora or datetime.now()
    try:
        valor = _hasta(hasta, ahora)
    except ValueError:
        raise TicketError("«hasta» no se entiende: «1h», «mañana», «siempre», una fecha ISO o vacío para quitarlo",
                          field="hasta", code="timbre.hasta_invalido")
    actual = silencios(conn, ahora)
    if valor is None:
        actual.pop(agente, None)
    else:
        actual[agente] = valor
    settings.patch(conn, quien, {"timbre_silencio": actual})
    return {"agente": agente, "silenciado_hasta": valor, "silencios": actual}


def marcar_libre(conn, quien: str, ticket_id: int, libre: bool = True) -> dict:
    """Cada uno decide qué tareas SUYAS son tiempo libre (suenan como invitación). Así quien quiera un segundo
    aviso a mitad de su hora libre parte su tarea en dos y marca las dos (Pepe, 13-nov). El dueño, las de todos."""
    from . import service, settings
    from .service import TicketError
    t = service._get(conn, int(ticket_id))
    if quien != t["owner"] and quien != roster.HOUSE_OWNER:
        raise TicketError("cada uno marca solo sus tareas como tiempo libre", field="ticket_id",
                          code="timbre.solo_las_suyas")
    actual = dict(settings.get(conn, "timbre_tiempo_libre") or {})
    ids = [int(x) for x in actual.get("tareas", []) if int(x) != int(ticket_id)]
    if libre:
        ids.append(int(ticket_id))
    actual["tareas"] = sorted(ids)
    actual.setdefault("textos", {})
    settings.patch(conn, quien, {"timbre_tiempo_libre": actual})
    return {"ticket_id": int(ticket_id), "libre": libre, "tareas": actual["tareas"]}


def ficheros(conn) -> tuple[Path | None, Path | None]:
    """(estado, registro) junto a la base de la casa; (None, None) con una base en memoria."""
    fichero = conn.execute("PRAGMA database_list").fetchone()[2]
    if not fichero:
        return None, None
    casa = Path(fichero).parent
    return casa / "timbre_ultimo.txt", casa / "timbre_registro.jsonl"


def estado(conn, fecha: str | None = None) -> dict:
    """Lo que enseña la app: si está encendido, cuándo dio la última vuelta y qué ha sonado ese día y a quién."""
    fecha = fecha or datetime.now().date().isoformat()
    datetime.strptime(fecha, "%Y-%m-%d")   # valida (ValueError → 400 en la API)
    est, reg = ficheros(conn)
    ultima = None
    if est is not None and est.exists():
        ultima = est.read_text(encoding="utf-8").strip() or None
    avisos = []
    if reg is not None and reg.exists():
        for linea in reg.read_text(encoding="utf-8").splitlines():
            try:
                a = json.loads(linea)
            except ValueError:
                continue
            if a.get("hora", "").startswith(fecha):
                avisos.append(a)
    return {"activo": activo(conn), "apagado_por_entorno": os.environ.get("FAROS_TIMBRE", "1") == "0",
            "ultima_vuelta": ultima, "fecha": fecha, "silencios": silencios(conn), "avisos": avisos}


def franjas(conn, desde: datetime, hasta: datetime) -> dict[str, list[dict]]:
    """{agente: [ocurrencias]} de HOY con hora de inicio en (desde, hasta], sin cerradas ni repetidas."""
    dia = hasta.date().isoformat()
    out: dict[str, list[dict]] = {}
    vistos = set()
    for it in agenda.occurrences(conn, dia, dia)["items"]:
        if it.get("all_day") or not it.get("start") or it.get("date") != dia or it.get("status") in CERRADAS:
            continue
        inicio = datetime.fromisoformat(f"{dia}T{it['start']}")
        if not (desde < inicio <= hasta):
            continue
        clave = (it["owner"], it["ticket_id"], dia, it["start"])
        if clave in vistos:
            continue
        vistos.add(clave)
        out.setdefault(it["owner"], []).append(it)
    return out


def texto_libre(ruta: str | None) -> str | None:
    """La invitación de cada uno: el primer bloque ``` de su .md, sin la línea «/loop …» (eso era del cron)."""
    if not ruta:
        return None
    try:
        crudo = Path(ruta).read_text(encoding="utf-8")
    except OSError:
        return None
    partes = crudo.split("```")
    cuerpo = partes[1] if len(partes) >= 3 else crudo
    lineas = [l for l in cuerpo.splitlines() if not l.strip().startswith("/loop")]
    return "\n".join(lineas).strip() or None


def mensaje_libre(items: list[dict], ahora: datetime, texto: str | None) -> str:
    hora = ahora.strftime("%H:%M")
    franja = "; ".join(f"#{i['ticket_id']} «{i['title']}»" for i in items)
    cuerpo = texto or "Lo que puedes hacer está en tu tarea de descanso."
    return f"Son las {hora}: empieza tu rato libre ({franja}).\n\n{cuerpo}\n\n— FarOS (timbre de franjas)"


def mensaje(items: list[dict], ahora: datetime | None = None) -> str:
    """La hora es la de ahora; si una tarea empezaba antes (llega tarde, p. ej. tras un reinicio), se dice cuándo."""
    hora = (ahora or datetime.now()).strftime("%H:%M")
    plan = "; ".join(f"#{i['ticket_id']} «{i['title']}»" + ("" if i["start"] == hora else f" (a las {i['start']})")
                     for i in sorted(items, key=lambda i: i["start"]))
    return (f"Son las {hora}. Tenías planeado: {plan}. Cierra lo que estés haciendo con su siguiente paso "
            f"en FarOS y empieza con esto, salvo que tengas una prioridad acordada con {roster.HOUSE_OWNER or 'el dueño'}. "
            f"— FarOS (timbre de franjas)")


# ---------------------------------------------------------------- relay (WebSocket del hub)

def _token() -> str:
    t = os.environ.get("ECORELAY_WS_TOKEN")
    if t:
        return t.strip()
    return (Path.home() / ".eco-relay" / "hub-ws-token").read_text(encoding="utf-8").strip()


def _esperar(ws, quiero) -> dict:
    """Lee mensajes hasta el que cumpla `quiero`; contesta a los ping del hub por el camino."""
    while True:
        msg = json.loads(ws.recv(timeout=10))
        if msg.get("type") == "ping":
            ws.send(json.dumps({"type": "pong", "req_id": msg.get("req_id")}))
            continue
        if quiero(msg):
            return msg


def enviar(avisos: dict[str, str], url: str | None = None, conectar=None) -> dict[str, str]:
    """avisos {agente: texto} → {agente: 'enviado' | 'sin_sesion' | 'error: …'}. Lanza si no hay hub."""
    if conectar is None:
        from websockets.sync.client import connect as conectar
    url = url or os.environ.get("ECORELAY_WS_URL", "ws://127.0.0.1:19736")
    res: dict[str, str] = {}
    with conectar(url, open_timeout=5, close_timeout=2) as ws:
        token, nombre = _token(), NOMBRE
        for intento in range(5):
            ws.send(json.dumps({"auth": token}))
            ws.send(json.dumps({"type": "register", "name": nombre, "cwd": str(Path.home()),
                                "git_branch": "", "protocol_version": PROTOCOLO_RELAY}))
            r = _esperar(ws, lambda m: m.get("type") in ("ack", "err"))
            if r.get("type") == "ack":
                break
            if r.get("code") != "name_taken":
                raise RuntimeError(f"el relay rechazó el registro: {r.get('code')}")
            nombre = f"{NOMBRE}-{intento + 2}"
        else:
            raise RuntimeError("el relay no dejó registrar el nombre")
        ws.send(json.dumps({"type": "list_peers", "req_id": "timbre-peers"}))
        r = _esperar(ws, lambda m: m.get("req_id") == "timbre-peers")
        conectados = {p.get("name") for p in (r.get("peers") or [])}
        for n, (agente, texto) in enumerate(sorted(avisos.items())):
            if agente not in conectados:
                res[agente] = "sin_sesion"
                continue
            rid = f"timbre-{n}"
            ws.send(json.dumps({"type": "send", "to": agente, "text": texto, "req_id": rid}))
            r = _esperar(ws, lambda m, rid=rid: m.get("req_id") == rid)
            res[agente] = "enviado" if r.get("type") == "send_ack" else f"error: {r.get('code')}"
    return res


# ---------------------------------------------------------------- estado entre vueltas

class Timbre:
    """Se reparte en dos pasos para no usar la conexión de la base fuera de su hilo:
    preparar() lee el calendario (hilo del scheduler) y enviar() habla con el relay (otro hilo)."""

    def __init__(self, ahora: datetime | None = None, estado: Path | None = None, registro: Path | None = None):
        """`estado`: fichero donde se guarda hasta cuándo ya ha sonado todo. Sin él, un reinicio volvía a
        hacer sonar lo de los últimos VENTANA_S segundos (medido en la prueba real del 13-nov)."""
        ahora = ahora or datetime.now()
        self.estado = estado
        self.registro = registro
        self.ultimo = ahora - timedelta(seconds=VENTANA_S)
        try:
            if estado is not None and estado.exists():
                self.ultimo = max(self.ultimo, datetime.fromisoformat(estado.read_text(encoding="utf-8").strip()))
        except (OSError, ValueError):
            log.warning("timbre: no pude leer %s; empiezo con la ventana de %ss", estado, VENTANA_S)
        self.enviados: set = set()
        self.inicio = self.ultimo   # #1189: lo del dueño no mira más atrás que esto (no repite tras reiniciar)

    def del_dueno(self, conn, ahora: datetime | None = None) -> list[dict]:
        """#1189: lo del dueño de la casa, que va por el bot de Telegram. Lleva su propia cuenta: si Telegram
        falla, se reintenta en la vuelta siguiente mientras siga en la ventana (el relay avanza aparte)."""
        ahora = ahora or datetime.now()
        dueno = roster.HOUSE_OWNER
        if not dueno or dueno in silencios(conn, ahora):
            return []
        desde = max(self.inicio, ahora - timedelta(seconds=VENTANA_S))
        return [i for i in franjas(conn, desde, ahora).get(dueno, [])
                if (dueno, i["ticket_id"], i["date"], i["start"]) not in self.enviados]

    def marcar_dueno(self, items: list[dict], ahora: datetime, resultado: str) -> None:
        dueno = roster.HOUSE_OWNER
        if resultado == "enviado":
            self.enviados.update((dueno, i["ticket_id"], i["date"], i["start"]) for i in items)
        if self.registro is not None and items:
            try:
                with self.registro.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "hora": ahora.isoformat(timespec="seconds"), "agente": dueno, "resultado": resultado,
                        "por": "telegram",
                        "tareas": [{"id": i["ticket_id"], "titulo": i["title"], "inicio": i["start"]} for i in items],
                    }, ensure_ascii=False) + "\n")
            except OSError:
                log.warning("timbre: no pude apuntar en %s", self.registro)

    def preparar(self, conn, ahora: datetime | None = None) -> dict:
        ahora = ahora or datetime.now()
        desde = max(self.ultimo, ahora - timedelta(seconds=VENTANA_S))
        pendientes = {}
        callados = silencios(conn, ahora)   # lo de un silenciado no suena ni se guarda para después
        for agente, items in franjas(conn, desde, ahora).items():
            if agente == roster.HOUSE_OWNER or agente in callados:
                continue
            nuevos = [i for i in items if (agente, i["ticket_id"], i["date"], i["start"]) not in self.enviados]
            if nuevos:
                pendientes[agente] = nuevos
        # Tiempo libre: solo si TODO lo que suena a la vez es descanso (si coincide con trabajo, manda el aviso normal).
        from . import settings
        libre = settings.get(conn, "timbre_tiempo_libre") or {}
        ids_libres = {int(x) for x in libre.get("tareas", [])}
        textos = libre.get("textos", {})
        libres = {agente: texto_libre(textos.get(agente)) for agente, items in pendientes.items()
                  if ids_libres and all(i["ticket_id"] in ids_libres for i in items)}
        return {"ahora": ahora, "pendientes": pendientes, "libres": libres}

    def confirmar(self, plan: dict, res: dict | None) -> None:
        """Con respuesta del relay: lo enviado o sin sesión no vuelve a sonar. Sin respuesta (hub
        caído): no se avanza, y se reintenta en la vuelta siguiente mientras siga en la ventana."""
        if res is None:
            return
        self.ultimo = plan["ahora"]
        for agente, items in plan["pendientes"].items():
            if res.get(agente) in ("enviado", "sin_sesion"):
                self.enviados.update((agente, i["ticket_id"], i["date"], i["start"]) for i in items)
        if self.registro is not None and plan["pendientes"]:
            try:
                with self.registro.open("a", encoding="utf-8") as f:
                    for agente, items in sorted(plan["pendientes"].items()):
                        f.write(json.dumps({
                            "hora": plan["ahora"].isoformat(timespec="seconds"), "agente": agente,
                            "resultado": res.get(agente, "sin_respuesta"),
                            "tareas": [{"id": i["ticket_id"], "titulo": i["title"], "inicio": i["start"]} for i in items],
                        }, ensure_ascii=False) + "\n")
            except OSError:
                log.warning("timbre: no pude apuntar en %s", self.registro)
        self._guardar()

    def saltar(self, ahora: datetime | None = None) -> None:
        """Apagado desde la app: el tiempo pasa sin sonar, para que al encenderlo no suene lo atrasado."""
        self.ultimo = ahora or datetime.now()
        self._guardar()

    def _guardar(self) -> None:
        if self.estado is not None:
            try:
                self.estado.write_text(self.ultimo.isoformat(timespec="seconds"), encoding="utf-8")
            except OSError:
                log.warning("timbre: no pude guardar %s", self.estado)

    def avisos(self, plan: dict) -> dict[str, str]:
        libres = plan.get("libres", {})
        return {agente: (mensaje_libre(items, plan["ahora"], libres[agente]) if agente in libres
                         else mensaje(items, plan["ahora"]))
                for agente, items in plan["pendientes"].items()}
