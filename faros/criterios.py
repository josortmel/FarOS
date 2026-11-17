"""#1081 — revisar el criterio de verificación AL CREAR la tarea, no al cerrarla.

Paso de la decisión 75 («Primero los criterios», 26-oct). En el banco del 22-oct, 7 de los 8 «buenos» que el
juez tumbó tenían el fallo en la letra de su criterio, y eso solo se veía al cerrar. Aquí se mira al escribirlo,
contra la regla de criterios (docs del verificador: por la letra, comprobable, la prueba ejecuta lo que pide, el
comando del repo, rojo en el padre). Solo lo determinista y NUNCA bloquea: devuelve avisos que nombran el punto
incumplido; quien escribe decide. Si un día hace falta un juez, irá aparte y tampoco bloqueará.
"""
from __future__ import annotations

import re

# Un comando concreto: algo entre comillas invertidas o un ejecutable conocido seguido de algo.
_COMANDO = re.compile(r"`[^`]+`|\b(?:python|pytest|npm|npx|node|uv|make|bash|sh|cargo|go|dotnet|gradle|mvn|"
                      r"docker|ruff|mypy|pwsh|powershell)\b\s+\S", re.I)
_SUITE = re.compile(r"\b(?:la\s+suite|suite\s+(?:completa|entera)|(?:los\s+)?tests?\s+(?:pasan|en\s+verde)|"
                    r"todo\s+en\s+verde|en\s+verde|the\s+suite|tests?\s+pass|all\s+green)\b", re.I)
_ARREGLO = re.compile(r"\b(?:arreglad[oa]s?|corregid[oa]s?|ya\s+no\s+(?:falla|pasa|ocurre)|fixed|no\s+longer)\b", re.I)
_TEST = re.compile(r"\btests?\b", re.I)
_ROJO = re.compile(r"\b(?:rojo|padre|antes\s+del?\s+(?:arreglo|cambio)|red|parent)\b", re.I)
_FUERA_DEL_JUEZ = re.compile(r"\b(?:en\s+producci[oó]n|que\s+lo\s+vea\s+\w+|lo\s+confirma\s+\w+|"
                             r"en\s+el\s+m[oó]vil|in\s+production)\b", re.I)

NIVELES_CON_JUEZ = ("auto", "external")


def revisar(criterio: str | None, nivel: str | None = "self") -> list[dict]:
    """Avisos sobre un criterio de verificación: [{"punto": n, "aviso": texto}]. Vacío = nada que decir."""
    texto = (criterio or "").strip()
    nivel = (nivel or "self").strip()
    if not texto:
        if nivel != "self":
            return [{"punto": 2, "aviso": f"sin criterio y con verificación «{nivel}»: quien verifique no tiene "
                                          "contra qué medir. Escribe qué se comprueba y con qué."}]
        return []
    avisos = []
    if _SUITE.search(texto) and not _COMANDO.search(texto):
        avisos.append({"punto": 4, "aviso": "habla de «la suite» o de tests en verde sin el comando exacto; pon el "
                                             "comando que corre el repo (y qué excluye, si excluye algo)."})
    if _ARREGLO.search(texto) and _TEST.search(texto) and not _ROJO.search(texto):
        avisos.append({"punto": 6, "aviso": "dice que algo queda arreglado pero no pide el rojo: el test tiene que "
                                             "fallar por una aserción en el commit padre (o con solo el test "
                                             "aplicado al padre) y pasar en el commit."})
    if nivel in NIVELES_CON_JUEZ and _FUERA_DEL_JUEZ.search(texto):
        avisos.append({"punto": 2, "aviso": "pide algo que un juez automático no puede tocar (producción, que lo "
                                             "vea una persona); saldrá «incierto» y subirá a una persona. Si es lo "
                                             "que quieres, ponle verificación «peer»."})
    return avisos
