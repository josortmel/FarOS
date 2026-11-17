"""#1109 — la clave de DeepSeek no entra en el entorno del agente.

Antes, el harness claude-cli-deepseek ponía la clave en ANTHROPIC_AUTH_TOKEN del `claude -p` del run, y
todo lo que el agente ejecutara (un `env`, un script, un MCP) podía leerla. Ahora el run recibe un token
de un solo uso (vale mientras dure ESE run) y ANTHROPIC_BASE_URL apunta a este reenvío, dentro del daemon
(127.0.0.1). El daemon cambia el token por la clave de Ajustes › Claves al reenviar a DeepSeek. Lo que el
agente puede leer es un token que muere al terminar su run; la clave no sale del daemon.

Límite, dicho: durante el run, el agente puede usar su token para llamar a DeepSeek a cargo de la cuenta,
igual que ya podía con la clave. Lo que se quita es que se la lleve.
"""
from __future__ import annotations

import secrets
import threading

import httpx

UPSTREAM = "https://api.deepseek.com/anthropic"
RUTA = "/api/deepseek-proxy"
PREFIJO_TOKEN = "faros-run-"
# Lo que no se reenvía: lo que lleva la credencial del run y lo que httpx recalcula.
_FUERA = {"authorization", "x-api-key", "host", "content-length", "connection", "accept-encoding"}

PUERTO = 8756                  # lo fija el daemon al arrancar (daemon.main)
_TRANSPORTE = None              # los tests ponen aquí un httpx.MockTransport (la suite no sale a la red)
_lock = threading.Lock()
_tokens: dict[str, str] = {}   # token → para qué (run #N, juez externo)


def emitir(para: str) -> str:
    token = PREFIJO_TOKEN + secrets.token_urlsafe(32)
    with _lock:
        _tokens[token] = para
    return token


def revocar(token: str | None) -> None:
    if token:
        with _lock:
            _tokens.pop(token, None)


def vigente(token: str | None) -> bool:
    with _lock:
        return bool(token) and token in _tokens


def url_local() -> str:
    """La base que ve el agente: este mismo daemon, en 127.0.0.1 y su puerto."""
    return f"http://127.0.0.1:{PUERTO}{RUTA}"


def token_de(headers) -> str | None:
    auth = headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return headers.get("x-api-key")


def cabeceras_hacia_fuera(headers, clave: str) -> dict:
    out = {k: v for k, v in headers.items() if k.lower() not in _FUERA}
    out["Authorization"] = f"Bearer {clave}"
    return out


def _error(status: int, mensaje: str):
    from fastapi.responses import JSONResponse
    # La forma de error de la API de Anthropic: es la que entiende el `claude -p` del run.
    return JSONResponse({"type": "error", "error": {"type": "authentication_error", "message": mensaje}},
                        status_code=status)


async def reenviar(request, path: str, clave: str):
    """Reenvía la petición del run a DeepSeek con la clave, y devuelve la respuesta en streaming (el
    `claude -p` pide los mensajes con stream=true). Sin token vigente o sin clave, 401 sin salir de casa."""
    from starlette.background import BackgroundTask
    from starlette.responses import StreamingResponse
    if not vigente(token_de(request.headers)):
        return _error(401, "token de run de FarOS no válido o caducado (#1109)")
    if not clave:
        return _error(401, "falta la clave de DeepSeek en Ajustes › Claves")
    url = f"{UPSTREAM}/{path}"
    if request.url.query:
        url += f"?{request.url.query}"
    cliente = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=20.0), transport=_TRANSPORTE)
    try:
        peticion = cliente.build_request(request.method, url, content=await request.body(),
                                         headers=cabeceras_hacia_fuera(request.headers, clave))
        respuesta = await cliente.send(peticion, stream=True)
    except httpx.HTTPError as e:
        await cliente.aclose()
        return _error(502, f"no se pudo hablar con DeepSeek: {type(e).__name__}")

    async def cerrar():
        await respuesta.aclose()
        await cliente.aclose()

    cabeceras = {k: v for k, v in respuesta.headers.items()
                 if k.lower() not in {"content-length", "content-encoding", "transfer-encoding", "connection"}}
    return StreamingResponse(respuesta.aiter_bytes(), status_code=respuesta.status_code, headers=cabeceras,
                             background=BackgroundTask(cerrar))
