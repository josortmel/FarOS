"""#436 — cada línea del log de uvicorn (acceso incluido) empieza con fecha y hora.

Sin hora, en un log compartido no se sabía qué petición era de qué app ni de cuándo
(L60). Se formatea una línea de acceso con la configuración que el daemon le pasa a
uvicorn, tal como la escribe uvicorn.access.
"""

import logging
import logging.config
import re

from faros.daemon import uvicorn_log_config

FECHA = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} ")


def _formateada(nombre_formatter, msg, args):
    cfg = uvicorn_log_config()
    spec = dict(cfg["formatters"][nombre_formatter])
    clase = spec.pop("()")
    modulo, _, attr = clase.rpartition(".")
    formatter = getattr(__import__(modulo, fromlist=[attr]), attr)(**spec)
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, msg, args, None)
    return formatter.format(record)


def test_linea_de_acceso_lleva_fecha_y_hora():
    linea = _formateada("access", '%s - "%s %s HTTP/%s" %d',
                        ("127.0.0.1:4405", "GET", "/api/events", "1.1", 200))
    assert FECHA.match(linea), linea
    assert '127.0.0.1:4405 - "GET /api/events HTTP/1.1" 200' in linea


def test_linea_normal_de_uvicorn_lleva_fecha_y_hora():
    assert FECHA.match(_formateada("default", "Application startup complete.", ())), "sin hora"


def test_no_toca_la_configuracion_global_de_uvicorn():
    from uvicorn.config import LOGGING_CONFIG
    uvicorn_log_config()
    assert not LOGGING_CONFIG["formatters"]["access"]["fmt"].startswith("%(asctime)s")
