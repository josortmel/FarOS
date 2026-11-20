"""Ejecutor aislado y comprobaciones deterministas (#594, paso 2 del encargo de Pepe del 16-oct,
«verificador automático para todo»; decisión 49: el juez puede ejecutar, pero en una copia).

Lo que puede comprobar el código lo comprueba el código, ANTES del juez, y siempre igual:
· el commit existe y está en la rama (master por defecto): los casos #463 y electron-44 del 16-oct,
  «verificados» y fuera de master;
· los ficheros que nombra la prueba existen;
· la prueba declarada sale con 0 en una copia del commit y, si se pide, NO en la de su padre
  (rojo antes, verde después, hecho mecánico).

La copia: un worktree desechable en %TEMP%, con un entorno hecho de cero (sin claves, sin la casa
de FarOS: FAROS_HOME y LOCALAPPDATA apuntan a la copia), timeout y muerte por árbol. Se borra al
acabar. LÍMITE dicho en el diseño: en Windows no se corta la red de un proceso.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import site
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT_S = 600
SALIDA_MAX = 20000
PREFIJO = "faros-prueba-"
# lo único que hereda el comando del entorno del daemon; todo lo demás se construye
# USERNAME (#595, 22-oct): sin él, test_acl_154 da KeyError y ninguna prueba «suite completa» podía pasar
_HEREDA = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "OS",
    "USERNAME",
)
CAMPOS = {"repo", "commit", "en", "prueba", "rojo_en_padre", "pruebas", "ficheros", "memorias", "texto", "comprueba"}


class PruebaError(ValueError):
    """La prueba declarada está mal formada (se rechaza al completar, no al juzgar)."""


def leer(texto: str, repos_admitidos: list[str] | None = None) -> dict:
    """Valida el JSON de una evidencia `proof`. Si nombra repo, tiene que estar en la lista."""
    try:
        p = json.loads(texto)
    except (TypeError, json.JSONDecodeError) as e:
        raise PruebaError(f"proof: JSON inválido ({e})")
    if not isinstance(p, dict):
        raise PruebaError("proof: tiene que ser un objeto JSON")
    raros = set(p) - CAMPOS
    if raros:
        raise PruebaError(f"proof: campos desconocidos {sorted(raros)} (válidos: {sorted(CAMPOS)})")
    if (p.get("commit") or p.get("prueba")) and not p.get("repo"):
        raise PruebaError("proof: commit y prueba necesitan repo")
    if p.get("prueba") and not p.get("commit"):
        raise PruebaError("proof: la prueba se corre en un commit; falta commit")
    if p.get("repo") and repos_admitidos is not None:
        norm = {os.path.normcase(os.path.normpath(r)) for r in repos_admitidos}
        if os.path.normcase(os.path.normpath(p["repo"])) not in norm:
            raise PruebaError(f"proof: el repo {p['repo']!r} no está en los admitidos")
    for k in ("ficheros", "memorias"):
        if not isinstance(p.get(k, []), list):
            raise PruebaError(f"proof: {k} tiene que ser una lista")
    return p


def _git(repo: str, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
    )


def entorno_limpio(base: Path, repo: str | None = None) -> dict:
    """El entorno del comando: lo mínimo del sistema y todo lo demás dentro de la copia. Ninguna
    variable de clave ni de la casa. PYTHONUSERBASE apunta al real para que los paquetes --user
    (httpx, fastapi…) sigan importándose aunque APPDATA se redirija.

    NODE_PATH (#600, punto 4): node_modules no está versionado, así que el worktree no lo trae y
    un test de app/ que haga require de una dependencia no la encuentra. Se apunta al
    app/node_modules del árbol principal. Es una concesión a la aislación: se comparte solo la
    carpeta de dependencias, no el código."""
    env = {k: os.environ[k] for k in _HEREDA if k in os.environ}
    tmp, home, casa = base / "tmp", base / "home", base / "casa"
    for d in (tmp, home, casa):
        d.mkdir(parents=True, exist_ok=True)
    env.update(
        TEMP=str(tmp),
        TMP=str(tmp),
        HOME=str(home),
        USERPROFILE=str(home),
        APPDATA=str(home / "Roaming"),
        LOCALAPPDATA=str(home / "Local"),
        FAROS_HOME=str(casa),
        AGENTICOS_HOME=str(casa),
        PYTHONUSERBASE=site.getuserbase(),
        PYTHONIOENCODING="utf-8",
        GIT_TERMINAL_PROMPT="0",
    )
    if repo:
        modulos = Path(repo) / "app" / "node_modules"
        if modulos.is_dir():
            env["NODE_PATH"] = str(modulos)
    return env


def _matar_arbol(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.kill()


def ficheros_de_prueba(repo: str, commit: str) -> list[str]:
    """Los ficheros de prueba que toca el commit (convención de la casa: «test» en la ruta)."""
    r = _git(repo, "diff", "--name-only", "--diff-filter=AM", f"{commit}^", commit)
    return [f for f in r.stdout.splitlines() if "test" in f.lower()]


def _borrar(base: Path) -> None:
    """Borra la copia entera, también si dentro quedaron ficheros de solo lectura (los objetos de
    git lo son). `ignore_errors=True` se rendía con ellos: el 16-oct dejaba faros-prueba-*/ en
    %TEMP% en 3 de 8 corridas. Aquí se quita el atributo de solo lectura y se reintenta."""

    def reintenta(func, ruta, *_exc):
        try:
            os.chmod(ruta, stat.S_IWRITE)
            func(ruta)
        except OSError:
            pass  # si aun así no se puede, no hay más que hacer; el ticket no promete magia

    if sys.version_info >= (3, 12):
        shutil.rmtree(base, onexc=reintenta)
    else:
        shutil.rmtree(base, onerror=reintenta)


def cabecera_git(copia: str) -> tuple[str, str]:
    """#766: lo que se corrió, dicho por git y no por nosotros. La primera línea es la salida de
    `git rev-parse HEAD` en la copia; la segunda, si la copia tenía cambios sin commitear (con
    `traer` los tiene a propósito: las pruebas nuevas sobre el código viejo). Devuelve (hash, texto)."""
    head = _git(copia, "rev-parse", "HEAD").stdout.strip()
    estado = _git(copia, "status", "--porcelain").stdout.rstrip()
    texto = f"git rev-parse HEAD: {head}\n" + (
        "git status: limpio"
        if not estado
        else "git status: CON CAMBIOS sin commitear (lo que se corrió no es solo ese commit):\n" + estado
    )
    return head, texto


def ejecutar(
    repo: str, commit: str, comando: str, timeout: int = TIMEOUT_S, traer: tuple[str, list[str]] | None = None
) -> dict:
    """Corre `comando` en una copia desechable de `repo` en `commit`. Devuelve código, salida
    (cortada) y si se pasó de tiempo. La copia se borra siempre, también si algo falla.
    La salida empieza por la cabecera de git (#766): el hash y si había cambios sin commitear.
    traer=(otro_commit, ficheros): antes de correr, esos ficheros se sacan de otro_commit. Así se
    corren las pruebas NUEVAS contra el código VIEJO (rojo antes, verde después)."""
    base = Path(tempfile.mkdtemp(prefix=PREFIJO))
    copia = base / "copia"
    try:
        r = _git(repo, "worktree", "add", "--detach", str(copia), commit)
        if r.returncode != 0:
            return {"code": None, "salida": f"no se pudo sacar la copia: {r.stderr.strip()[:500]}", "timeout": False}
        if traer and traer[1]:
            rt = _git(str(copia), "checkout", traer[0], "--", *traer[1])
            if rt.returncode != 0:
                return {
                    "code": None,
                    "salida": f"no se pudieron traer las pruebas: {rt.stderr.strip()[:500]}",
                    "timeout": False,
                }
        head, cabecera = cabecera_git(str(copia))
        try:
            args = shlex.split(comando)
        except ValueError as e:
            return {"code": None, "salida": f"comando mal escrito: {e}", "timeout": False}
        proc = subprocess.Popen(
            args, cwd=str(copia), env=entorno_limpio(base, repo), stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        try:
            out, _ = proc.communicate(timeout=timeout)
            excedido = False
        except subprocess.TimeoutExpired:
            _matar_arbol(proc)
            out, _ = proc.communicate()
            excedido = True
        salida = (out or b"").decode("utf-8", errors="replace")
        if len(salida) > SALIDA_MAX:
            salida = salida[: SALIDA_MAX // 2] + "\n…\n" + salida[-SALIDA_MAX // 2 :]
        return {
            "code": None if excedido else proc.returncode,
            "salida": cabecera + "\n\n" + salida,
            "timeout": excedido,
            "head": head,
            "cabecera": cabecera,
        }
    finally:
        _git(repo, "worktree", "remove", "--force", str(copia))
        _git(repo, "worktree", "prune")
        # solo se borra lo que creó este ejecutor: una carpeta suya dentro del temporal
        if base.name.startswith(PREFIJO) and base.parent == Path(tempfile.gettempdir()):
            _borrar(base)


ECODB_URL = os.environ.get("ECODB_URL", "http://127.0.0.1:8080")
ECODB_TIMEOUT = 5.0
# #600: el id entra en una URL que se pide con la clave del daemon. Sin esta puerta, un `proof` con
# memorias: ["../../api/credentials/deliver"] haría que el verificador pidiera otro endpoint de
# EcoDB con credenciales. No es un UUID → no se pregunta.
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _pregunta_a_ecodb(mid: str, clave: str, base: str | None = None, timeout: float = ECODB_TIMEOUT) -> dict:
    """Una memoria: existe o no, y quién la escribió. ok=None significa «no lo sé» — sin clave o
    con EcoDB sin responder NO se tumba el trabajo (decisión de Prima, 20-oct): un verificador que
    rebota trabajo bueno enseña a la gente a no usarlo."""
    url = f"{(base or ECODB_URL).rstrip('/')}/memories/{mid}"
    req = urllib.request.Request(url, headers={"Authorization": f"ApiKey {clave}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            datos = json.loads(r.read().decode("utf-8", errors="replace"))
        quien = (datos or {}).get("agent_identifier") or "?"
        return {"id": mid, "ok": True, "agente": quien, "detalle": f"existe; la escribió {quien}"}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"id": mid, "ok": False, "agente": None, "detalle": "no existe en EcoDB (404)"}
        if e.code == 403:
            # MEDIDO el 20-oct contra el EcoDB de la casa: un id INVENTADO no da 404, da 403
            # («no read access to this memory»). Es anti-discovery a propósito, así que el 403
            # no distingue «no existe» de «existe y no es tuya» — y fallar por eso rebotaría
            # citas legítimas de memorias ajenas. No se tumba: se le dice al juez y lo pesa.
            return {
                "id": mid,
                "ok": None,
                "agente": None,
                "detalle": "EcoDB no la da a esta clave (403: no existe o no es visible)",
            }
        return {"id": mid, "ok": None, "agente": None, "detalle": f"EcoDB respondió {e.code}"}
    except Exception as e:  # noqa: BLE001 — cualquier fallo de red es «no lo sé», no un rojo
        return {"id": mid, "ok": None, "agente": None, "detalle": f"no pude preguntar a EcoDB ({type(e).__name__})"}


def comprobar_memorias(
    ids: list[str], clave: str | None, base: str | None = None, timeout: float = ECODB_TIMEOUT
) -> list[dict]:
    """Las memorias citadas por su id. Sin clave no se pregunta nada y todas quedan sin decidir."""
    if not clave:
        return [{"id": m, "ok": None, "agente": None, "detalle": "sin clave de EcoDB"} for m in ids]
    salida = []
    for m in ids:
        if not _UUID.match(str(m)):
            salida.append({"id": m, "ok": None, "agente": None, "detalle": "no parece un id de memoria (UUID)"})
        else:
            salida.append(_pregunta_a_ecodb(m, clave, base, timeout))
    return salida


def rama_integracion(repo: str) -> str:
    """#693: la rama donde «está hecho» de verdad: master si existe, si no main (EcoDB y EcoRelay usan main; antes
    se miraba siempre master y toda prueba de esos repos sin «en» caía)."""
    for rama in ("master", "main"):
        if _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{rama}").returncode == 0:
            return rama
    return "master"


def comprobar(p: dict, clave_ecodb: str | None = None) -> dict:
    """Las comprobaciones deterministas de una prueba ya validada. ok = todas en verde.
    «sin_decidir» marca lo que el código no puede afirmar y queda para el juez."""
    checks = []

    def anota(nombre, ok, detalle):
        checks.append({"nombre": nombre, "ok": ok, "detalle": detalle})

    repo, commit = p.get("repo"), p.get("commit")
    no_fusionado = None
    if commit:
        existe = _git(repo, "cat-file", "-e", f"{commit}^{{commit}}").returncode == 0
        anota("commit existe", existe, f"{commit} en {repo}")
        if existe:
            # #693: la rama de integración la pone el REPO (master o main), no quien completa
            integracion = rama_integracion(repo)
            rama = p.get("en") or integracion
            dentro = _git(repo, "merge-base", "--is-ancestor", commit, rama).returncode == 0
            anota(f"commit en {rama}", dentro, f"{commit} {'está' if dentro else 'NO está'} en {rama}")
            if (
                dentro
                and rama != integracion
                and _git(repo, "merge-base", "--is-ancestor", commit, integracion).returncode != 0
            ):
                no_fusionado = rama
    for f in p.get("ficheros", []):
        anota(f"existe {f}", Path(f).exists(), f"{Path(f).stat().st_size} bytes" if Path(f).exists() else "no existe")
    salida = salida_padre = head = cabecera = cabecera_padre = None
    if p.get("prueba") and all(c["ok"] for c in checks):
        r = ejecutar(repo, commit, p["prueba"])
        salida, head, cabecera = r["salida"], r.get("head"), r.get("cabecera")
        anota("prueba en el commit", r["code"] == 0, "timeout" if r["timeout"] else f"salió con {r['code']}")
        if p.get("rojo_en_padre"):
            pruebas = p.get("pruebas") or ficheros_de_prueba(repo, commit)
            rp = ejecutar(repo, f"{commit}^", p["prueba"], traer=(commit, pruebas))
            salida_padre, cabecera_padre = rp["salida"], rp.get("cabecera")
            # pytest sale con 4 (fichero que no existe) o 5 (nada que correr) cuando el test es NUEVO
            # en el commit: el padre «falla» sin demostrar nada. Lo decimos para que lo pese el juez.
            trivial = rp["code"] in (4, 5)
            anota(
                "prueba en el padre (debe fallar)",
                rp["code"] not in (0, None),
                "timeout"
                if rp["timeout"]
                else f"salió con {rp['code']}" + (" (TRIVIAL: la prueba no existe en el padre)" if trivial else ""),
            )
    sin_decidir = []
    for m in comprobar_memorias(p.get("memorias", []), clave_ecodb):
        if m["ok"] is None:
            # lo que el código no puede afirmar NO tumba el trabajo: se lo queda el juez
            sin_decidir.append(m["id"])
        else:
            anota(f"memoria {m['id']} existe", m["ok"], m["detalle"])
    if no_fusionado:
        # #693: no tumba (el commit está donde dice), pero el verificador no lo puede dar por bueno
        sin_decidir.append(f"commit {commit} no fusionado en {rama_integracion(repo)} (solo en {no_fusionado})")
    return {
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
        "salida": salida,
        "salida_padre": salida_padre,
        "sin_decidir": sin_decidir,
        "no_fusionado": no_fusionado,
        "head": head,
        "cabecera": cabecera,
        "cabecera_padre": cabecera_padre,
    }
