// #1217: el botón «Copiar» de Ajustes › Claves (deseo de Pepe, 16-nov: «yo no puedo sacar contraseñas de FarOS»).
// Módulo aparte (sin require("electron")) para probarlo con `node --test`, como ipc_guard.js.
//
// SEGURIDAD: el valor NUNCA llega al renderer. Lo pide el proceso principal por la entrega que ya existe (#499:
// solo claves «entregables», con su fila en Entregas), lo escribe en el portapapeles y al renderer solo le devuelve
// {ok, nombre, se_borra_en}. No hay endpoint nuevo: la app tiene el mismo token de casa que los agentes, así que
// uno «solo para el dueño» lo sería para cualquiera. A los `segundos` se limpia el portapapeles, pero SOLO si
// todavía tiene esa clave (si Pepe ha copiado otra cosa, no se toca).

const SE_BORRA_EN = 300;   // Pepe, 17-nov: «no me vale que desaparezcan a los 30 segundos, dale al menos 5 min»
const PARA_QUE = "copiar al portapapeles desde la app";

function crearCopiador({ baseUrl, token, fetch, clipboard, setTimeout, segundos = SE_BORRA_EN }) {
  let dueno = null;

  async function pedir(ruta, opciones = {}) {
    const res = await fetch(`${baseUrl}${ruta}`, {
      ...opciones,
      headers: { "X-FarOS-Token": token(), "Content-Type": "application/json" },
      signal: AbortSignal.timeout(10000),
    });
    let cuerpo = null;
    try { cuerpo = await res.json(); } catch { cuerpo = null; }
    return { res, cuerpo };
  }

  return async function copiar(nombre) {
    if (typeof nombre !== "string" || !nombre.trim() || nombre.length > 120) {
      return { ok: false, motivo: "nombre_invalido", mensaje: "No sé qué clave copiar." };
    }
    let res, cuerpo;
    try {
      if (!dueno) {
        const r = await pedir("/api/roster");
        dueno = (r.cuerpo && r.cuerpo.owner) || "el dueño";
      }
      ({ res, cuerpo } = await pedir("/api/credentials/deliver", {
        method: "POST",
        body: JSON.stringify({ name: nombre.trim(), requested_by: dueno, purpose: PARA_QUE }),
      }));
    } catch {
      return { ok: false, motivo: "sin_daemon", mensaje: "FarOS no contesta: no se ha copiado nada." };
    }
    if (!res.ok || !cuerpo || typeof cuerpo.value !== "string") {
      const code = cuerpo && cuerpo.code;
      if (code === "credential.not_deliverable") return { ok: false, motivo: "no_entregable" };
      return { ok: false, motivo: code || `http_${res.status}`,
               mensaje: (cuerpo && cuerpo.reason) || "No se ha podido copiar." };
    }
    const valor = cuerpo.value;
    clipboard.writeText(valor);
    setTimeout(() => {
      if (clipboard.readText() === valor) clipboard.clear();
    }, segundos * 1000);
    return { ok: true, nombre: cuerpo.name, se_borra_en: segundos };
  };
}

module.exports = { crearCopiador, SE_BORRA_EN, PARA_QUE };
