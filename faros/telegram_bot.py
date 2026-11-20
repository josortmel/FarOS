"""Bot de Telegram de FarOS (#1189). Pepe, 12-nov: «darle un token y hacer un bot de Telegram para FarOS y así
dejar de usar el chat de Eco». Lo que hace:

- Le manda al dueño de la casa sus timbres (el mismo cálculo que el timbre de franjas, #1184).
- Le manda cada decisión pendiente con sus opciones como botones. Al pulsar uno, la decisión queda decidida en
  FarOS y se avisa por el relay a quien la pidió. Si en vez de pulsar contesta al mensaje con texto, ese texto es
  su decisión (el dueño puede decidir fuera de las opciones).
- Solo habla con el chat del dueño (`telegram_chat_id`). Cualquier otro chat no recibe respuesta y queda apuntado
  en telegram_registro.jsonl.

Usa la clave de Telegram marcada «en uso» en Ajustes › Claves (su espejo `telegram_token`). Debe ser un bot
PROPIO de FarOS: Telegram solo deja un lector (getUpdates) por bot, y si fuera el del canal de Eco se quitarían
los mensajes el uno al otro. El token no se escribe nunca en el log: los errores de red se apuntan por su tipo.
FAROS_TELEGRAM=0 lo apaga (tests y copias de prueba de la casa).

La base se toca solo en el hilo del bucle; la red, en otro (asyncio.to_thread), como el resto del daemon (#1222)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import httpx

from . import roster

log = logging.getLogger("faros.telegram")
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx a INFO escribiría la URL, que lleva el token

ESPERA_S = 8  # long polling: corto, para que apagar el daemon no espere al hilo
LIMITE = 4096  # caracteres por mensaje en Telegram
SUPERFICIE = "telegram"


class BotError(Exception):
    """Fallo hablando con Telegram. El texto nunca lleva la URL (lleva el token)."""


def activo() -> bool:
    return os.environ.get("FAROS_TELEGRAM", "1") != "0"


def datos(conn) -> tuple[str, str] | None:
    """(token, chat del dueño), o None si falta alguno."""
    from . import settings

    token, chat = settings.get(conn, "telegram_token"), settings.get(conn, "telegram_chat_id")
    if not token or not chat:
        return None
    return str(token), str(chat).strip()


def llamar(token: str, metodo: str, cuerpo: dict | None = None, timeout: float = 15) -> object:
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/{metodo}", json=cuerpo or {}, timeout=timeout)
        res = r.json()
    except Exception as e:  # el mensaje de una excepción de httpx puede llevar la URL: solo el tipo
        raise BotError(f"{metodo}: {type(e).__name__}") from None
    if not res.get("ok"):
        raise BotError(f"{metodo}: HTTP {r.status_code} {str(res.get('description', ''))[:200]}")
    return res["result"]


def recortar(texto: str) -> str:
    return texto if len(texto) <= LIMITE else texto[: LIMITE - 1] + "…"


def mandar(token: str, chat: str, texto: str, botones: list | None = None) -> int:
    """Manda un mensaje y devuelve su message_id. botones: [[{text, callback_data}], ...]."""
    cuerpo = {"chat_id": chat, "text": recortar(texto)}
    if botones:
        cuerpo["reply_markup"] = {"inline_keyboard": botones}
    return llamar(token, "sendMessage", cuerpo)["message_id"]


# ---------------------------------------------------------------- textos


def texto_timbre(items: list[dict], ahora: datetime) -> str:
    """Para el dueño, sin números de tarea (no se los sabe) y sin la regla de cerrar lo que estaba haciendo."""
    hora = ahora.strftime("%H:%M")
    lineas = [f"⏰ Son las {hora}."]
    for i in sorted(items, key=lambda i: i["start"]):
        lineas.append(f"• {i['title']}" + ("" if i["start"] == hora else f" (era a las {i['start']})"))
    return "\n".join(lineas)


def texto_decision(d: dict) -> tuple[str, list]:
    cabeza = f"🗳 {d['asked_by']} te pide una decisión:\n{d['title']}"
    opciones = "\n".join(
        f"• {o['label']}: {o['text']}" if o["text"] != o["label"] else f"• {o['label']}" for o in d["options"]
    )
    pie = "Pulsa una opción, o contesta a este mensaje con tu decisión en texto."
    contexto = (d.get("context") or "").strip()
    sitio = LIMITE - len(cabeza) - len(opciones) - len(pie) - 40
    if contexto and sitio > 200:
        if len(contexto) > sitio:
            contexto = contexto[: sitio - 1] + "…"
        texto = f"{cabeza}\n\n{contexto}\n\nOpciones:\n{opciones}\n\n{pie}"
    else:
        texto = f"{cabeza}\n\nOpciones:\n{opciones}\n\n{pie}"
    botones = [[{"text": o["label"][:60], "callback_data": f"d:{d['id']}:{n}"}] for n, o in enumerate(d["options"])]
    return recortar(texto), botones


AYUDA = (
    "Soy el bot de FarOS. Aquí te llegan tus timbres y las decisiones que te pide la familia. "
    "Para contestar una decisión, pulsa uno de sus botones o contesta a su mensaje con texto."
)


# ---------------------------------------------------------------- el bot


class Bot:
    """Estado en telegram_estado.json junto a la base: el offset de getUpdates (para no atender dos veces lo
    mismo tras un reinicio) y qué decisión va en qué mensaje (para no mandarla dos veces y para leer las
    respuestas en texto)."""

    def __init__(self, estado: Path | None = None, registro: Path | None = None, emit=None, avisar=None):
        self.estado, self.registro, self.emit = estado, registro, emit
        self.avisar = avisar  # (agente, texto) -> None; por defecto, el relay del timbre
        self.offset = 0
        self.decisiones: dict[int, int] = {}  # decision_id -> message_id
        self.cerradas: set[int] = set()  # las que ya no tienen botones en su mensaje
        try:
            if estado is not None and estado.exists():
                e = json.loads(estado.read_text(encoding="utf-8"))
                self.offset = int(e.get("offset", 0))
                self.decisiones = {int(k): int(v) for k, v in e.get("decisiones", {}).items()}
                self.cerradas = {int(x) for x in e.get("cerradas", [])}
        except (OSError, ValueError):
            log.warning("telegram: no pude leer %s; empiezo de cero", estado)

    @staticmethod
    def ficheros(conn) -> tuple[Path | None, Path | None]:
        fichero = conn.execute("PRAGMA database_list").fetchone()[2]
        if not fichero:
            return None, None
        casa = Path(fichero).parent
        return casa / "telegram_estado.json", casa / "telegram_registro.jsonl"

    def _guardar(self) -> None:
        if self.estado is not None:
            try:
                self.estado.write_text(
                    json.dumps(
                        {"offset": self.offset, "decisiones": self.decisiones, "cerradas": sorted(self.cerradas)}
                    ),
                    encoding="utf-8",
                )
            except OSError:
                log.warning("telegram: no pude guardar %s", self.estado)

    def apuntar(self, **campos) -> None:
        if self.registro is None:
            return
        try:
            with self.registro.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps({"hora": datetime.now().isoformat(timespec="seconds"), **campos}, ensure_ascii=False)
                    + "\n"
                )
        except OSError:
            log.warning("telegram: no pude apuntar en %s", self.registro)

    async def _avisar(self, agente: str, texto: str) -> None:
        if not agente or agente == roster.HOUSE_OWNER or agente not in roster.AGENTS:
            return
        try:
            if self.avisar is not None:
                await asyncio.to_thread(self.avisar, agente, texto)
            else:
                from . import timbre

                await asyncio.to_thread(timbre.enviar, {agente: texto})
        except Exception as e:
            log.warning("telegram: no pude avisar a %s por el relay (%s)", agente, type(e).__name__)

    # -------------------------------------------------- decisiones salientes

    async def mandar_decisiones(self, conn, token: str, chat: str) -> list[int]:
        from . import decisions

        mandadas = []
        for d in decisions.list_decisions(conn, status="pending"):
            if d["id"] in self.decisiones:
                continue
            d = decisions.get_decision(conn, d["id"])
            texto, botones = texto_decision(d)
            mid = await asyncio.to_thread(mandar, token, chat, texto, botones)
            self.decisiones[d["id"]] = mid
            self._guardar()
            self.apuntar(tipo="decision_mandada", decision=d["id"], mensaje=mid)
            mandadas.append(d["id"])
        return mandadas

    async def cerrar_decididas_fuera(self, conn, token: str, chat: str) -> list[int]:
        """Una decisión que se decide en la app (o por MCP) no puede dejar en Telegram unos botones que ya no
        valen: su mensaje pasa a decir qué se decidió y quién."""
        from . import decisions

        abiertas = [did for did in self.decisiones if did not in self.cerradas]
        if not abiertas:
            return []
        marca = ",".join("?" * len(abiertas))
        cerradas = []
        for r in conn.execute(f"SELECT id FROM decisions WHERE status='decided' AND id IN ({marca})", abiertas):
            d = decisions.get_decision(conn, r["id"] if hasattr(r, "keys") else r[0])
            texto, _ = texto_decision(d)
            hecho = f"✅ Decidido en FarOS por {d['decided_by']}: {d['decision']}"
            try:
                await asyncio.to_thread(
                    llamar,
                    token,
                    "editMessageText",
                    {
                        "chat_id": chat,
                        "message_id": self.decisiones[d["id"]],
                        "text": recortar(texto.rsplit("\n\n", 1)[0] + "\n\n" + hecho),
                    },
                )
            except BotError as e:  # p. ej. un mensaje de hace más de 48 h: no se insiste
                log.warning("telegram: no pude cerrar el mensaje de la decisión %s (%s)", d["id"], e)
            self.cerradas.add(d["id"])
            self._guardar()
            cerradas.append(d["id"])
        return cerradas

    # -------------------------------------------------- lo que llega

    async def atender(self, conn, token: str, chat: str, u: dict) -> str:
        """Devuelve qué se hizo (para el registro y los tests)."""
        if "callback_query" in u:
            return await self._boton(conn, token, chat, u["callback_query"])
        m = u.get("message") or u.get("edited_message")
        if not m:
            return "ignorado"
        quien = m.get("from") or {}
        if str((m.get("chat") or {}).get("id")) != chat or str(quien.get("id")) != chat:
            self.apuntar(
                tipo="ajeno",
                chat=(m.get("chat") or {}).get("id"),
                de=quien.get("id"),
                usuario=quien.get("username"),
                texto=(m.get("text") or "")[:200],
            )
            return "ajeno"
        texto = (m.get("text") or "").strip()
        respondido = (m.get("reply_to_message") or {}).get("message_id")
        did = next((d for d, mid in self.decisiones.items() if mid == respondido), None) if respondido else None
        if did is not None and texto:
            return await self._decidir(
                conn, token, chat, did, decision=texto, rationale=texto, mensaje=respondido, en_texto=True
            )
        await asyncio.to_thread(mandar, token, chat, AYUDA)
        return "ayuda"

    async def _boton(self, conn, token: str, chat: str, cq: dict) -> str:
        quien = cq.get("from") or {}
        if str(quien.get("id")) != chat:
            self.apuntar(tipo="ajeno", de=quien.get("id"), usuario=quien.get("username"), boton=cq.get("data"))
            return "ajeno"
        partes = str(cq.get("data", "")).split(":")
        mensaje = (cq.get("message") or {}).get("message_id")
        if len(partes) != 3 or partes[0] != "d" or not partes[1].isdigit() or not partes[2].isdigit():
            await asyncio.to_thread(
                llamar,
                token,
                "answerCallbackQuery",
                {"callback_query_id": cq["id"], "text": "Este botón no lo entiendo."},
            )
            return "boton_raro"
        return await self._decidir(
            conn, token, chat, int(partes[1]), indice=int(partes[2]), mensaje=mensaje, callback_id=cq["id"]
        )

    async def _decidir(
        self,
        conn,
        token,
        chat,
        did,
        indice=None,
        decision=None,
        rationale=None,
        mensaje=None,
        callback_id=None,
        en_texto=False,
    ) -> str:
        from . import decisions
        from .service import TicketError

        dueno = roster.HOUSE_OWNER
        try:
            d = decisions.get_decision(conn, did)
            option = None
            if indice is not None:
                if indice >= len(d["options"]):
                    raise TicketError("esa opción ya no existe")
                option = d["options"][indice]["label"]
            d = decisions.decide(
                conn,
                dueno,
                did,
                option=option,
                decision=decision,
                rationale=rationale or "Elegida con su botón en Telegram.",
                surface=SUPERFICIE,
                decidido_por={"tipo": "humano", "quien": dueno},
            )
        except TicketError as e:
            aviso = str(e)[:190]
            if callback_id:
                await asyncio.to_thread(
                    llamar,
                    token,
                    "answerCallbackQuery",
                    {"callback_query_id": callback_id, "text": aviso, "show_alert": True},
                )
            else:
                await asyncio.to_thread(mandar, token, chat, f"No he podido apuntarla: {aviso}")
            self.apuntar(tipo="decision_fallida", decision=did, motivo=aviso)
            return "fallida"
        self.cerradas.add(did)
        self._guardar()
        if self.emit:
            self.emit("decision.changed", {"decision_id": did, "status": d["status"]})
        hecho = f"✅ Decidido: {d['decision']}"
        if callback_id:
            await asyncio.to_thread(
                llamar, token, "answerCallbackQuery", {"callback_query_id": callback_id, "text": hecho[:190]}
            )
        if mensaje:  # quita los botones y deja escrito lo que se decidió
            texto, _ = texto_decision(d)
            try:
                await asyncio.to_thread(
                    llamar,
                    token,
                    "editMessageText",
                    {
                        "chat_id": chat,
                        "message_id": mensaje,
                        "text": recortar(texto.rsplit("\n\n", 1)[0] + "\n\n" + hecho),
                    },
                )
            except BotError as e:
                log.warning("telegram: no pude editar el mensaje de la decisión %s (%s)", did, e)
        if en_texto:
            await asyncio.to_thread(mandar, token, chat, hecho)
        self.apuntar(tipo="decidida", decision=did, eleccion=d["decision"], en_texto=en_texto)
        await self._avisar(
            d["asked_by"], f"{dueno} ha decidido «{d['title']}»: {d['decision']} (desde Telegram). — FarOS"
        )
        return "decidida"

    # -------------------------------------------------- una vuelta

    async def vuelta(self, conn, espera: int = ESPERA_S) -> bool:
        """Manda lo pendiente y atiende lo que haya llegado. False si no está configurado."""
        cfg = datos(conn)
        if cfg is None:
            return False
        token, chat = cfg
        await self.mandar_decisiones(conn, token, chat)
        await self.cerrar_decididas_fuera(conn, token, chat)
        updates = await asyncio.to_thread(
            llamar,
            token,
            "getUpdates",
            {"offset": self.offset, "timeout": espera, "allowed_updates": ["message", "callback_query"]},
            espera + 10,
        )
        for u in updates:
            self.offset = u["update_id"] + 1
            self._guardar()  # antes de atender: un fallo no hace que se atienda dos veces
            try:
                await self.atender(conn, token, chat, u)
            except Exception as e:
                log.warning("telegram: fallo atendiendo el update %s (%s)", u.get("update_id"), type(e).__name__)
        return True


async def bucle(conn, stop_event: asyncio.Event, emit=None) -> None:
    if not activo():
        return
    estado, registro = Bot.ficheros(conn)
    bot = Bot(estado=estado, registro=registro, emit=emit)
    log.info("bot de Telegram arrancado")
    while not stop_event.is_set():
        pausa = 0
        try:
            if not await bot.vuelta(conn):
                pausa = 60  # sin token o sin chat: se mira otra vez en un minuto
        except BotError as e:
            log.warning("telegram: %s", e)
            pausa = 15
        except Exception as e:
            log.warning("telegram: vuelta fallida (%s)", type(e).__name__)
            pausa = 15
        if pausa:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=pausa)
            except asyncio.TimeoutError:
                pass
