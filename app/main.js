// FarOS — Electron main process (SPEC §2, §9).
// #261 (16-sep): marca visible = FarOS. Las TAREAS PROGRAMADAS se llaman
// FarOS-daemon / FarOS-watchdog desde el #288; ensureLogonTask aun lee la vieja
// AgenticOS-daemon para migrar las maquinas que la tienen.
// Responsabilidades: asegurar daemon vivo, ventana, bandeja, pasar token al renderer.
// El daemon NO muere con la ventana: las tareas programadas siguen (SPEC §2).

const { app, BrowserWindow, ipcMain, shell, Tray, Menu, nativeImage, dialog, clipboard } = require("electron");
const { rejectReason } = require("./open_path_guard");
const { puedeNavegar, abrirFuera, peticionLocalPermitida } = require("./window_policy");
const { senderEsLaVentana, permisoPermitido } = require("./ipc_guard");
const { pathToFileURL } = require("url");

// T3.6: la UI pide abrir documentos del workflow (rutas que devuelve el daemon).
// #384: solo documentos locales existentes (open_path_guard.js); nunca ejecutables ni UNC.
ipcMain.handle("agenticos:open-path", async (event, target) => {
  // #476: solo nuestra ventana, desde su marco principal (antes atendía a cualquier remitente).
  if (!senderEsLaVentana(event, win)) return "origen no permitido";
  const why = rejectReason(target);
  if (why) return why;
  return shell.openPath(target);
});
const { spawn, execFile } = require("child_process");
const fs = require("fs");
const path = require("path");

/* #302: FAROS_PORT primero, como en faros/env.py. Faltaba: main.js ya leia
   FAROS_HOME para los datos pero el puerto solo por el nombre viejo, asi que
   quien aislara una instalacion de prueba con el nombre NUEVO se quedaba en
   8756 — el de la casa. */
const PORT = process.env.FAROS_PORT || process.env.AGENTICOS_PORT || 8756;
const BASE_URL = `http://127.0.0.1:${PORT}`;
// Misma resolucion que faros/db.py: variable nueva, luego la vieja, y en
// %LOCALAPPDATA% se prefiere FarOS cayendo a AgenticOS si la nueva no existe.
// Sin este fallback, una instalacion anterior al rename abre la app y no
// encuentra su token ni su base: tablero vacio con los datos intactos al lado.
const DATA_DIR = (() => {
  const env = process.env.FAROS_HOME || process.env.AGENTICOS_HOME;
  if (env) return env;
  const base = process.env.LOCALAPPDATA || app.getPath("home");
  const nueva = path.join(base, "FarOS");
  const vieja = path.join(base, "AgenticOS");
  if (fs.existsSync(nueva)) return nueva;
  if (fs.existsSync(vieja)) return vieja;
  return nueva;
})();
const TOKEN_FILE = path.join(DATA_DIR, "token");
const REPO_ROOT = path.resolve(__dirname, "..");
// T4.2/T4.3: empaquetada, el daemon es el onedir de PyInstaller en resources/daemon.
const PACKAGED_DAEMON = path.join(process.resourcesPath || "", "daemon", "faros-daemon.exe");
const ICON_FILE = path.join(__dirname, "assets", "icon.ico");

// Finding #21 (3-sep): en este PC Chromium daba la ventana por OCLUIDA (detección
// nativa de Windows) → document.visibilityState "hidden" → ni un frame → ventana negra
// con el DOM entero dentro. Medido con CDP: con este switch, "visible" y pinta.
app.commandLine.appendSwitch("disable-features", "CalculateNativeWinOcclusion");

// T4.3: instancia única — la segunda enfoca la primera (decisión del dueño: bandeja).
// #390 (5-oct): app.quit() no corta el script. La perdedora seguía hasta whenReady →
// createWindow → ensureDaemon y, con otro FAROS_HOME, dejaba un daemon huérfano y
// ninguna ventana (medido: daemon en 8798 + base creada). Por eso whenReady mira PRIMARY.
const PRIMARY = app.requestSingleInstanceLock();
if (!PRIMARY) {
  console.error(`FarOS ya está abierto con este perfil (${app.getPath("userData")}); ` +
                "esta instancia sale. Para aislar una prueba: --user-data-dir=<carpeta>.");
  app.quit();
}

let win = null;
let tray = null;
let quitting = false;

async function daemonAlive() {
  try {
    const res = await fetch(`${BASE_URL}/api/health`, { signal: AbortSignal.timeout(1500) });
    return res.ok;
  } catch {
    return false;
  }
}

function spawnDaemon() {
  // OJO: NO usar pythonw — uvicorn escribe a stdout y con pythonw (stdout=None)
  // el daemon arranca y muere en silencio (medido 1-sep). python + windowsHide
  // + stdio a fichero: invisible, con log, y sobrevive al cierre de la app.
  const usePackaged = !process.env.AGENTICOS_PYTHON && fs.existsSync(PACKAGED_DAEMON);
  const py = process.env.AGENTICOS_PYTHON || "python";
  fs.mkdirSync(DATA_DIR, { recursive: true });
  const out = fs.openSync(path.join(DATA_DIR, "daemon_stdout.log"), "a");
  const err = fs.openSync(path.join(DATA_DIR, "daemon_stderr.log"), "a");
  const cmd = usePackaged ? PACKAGED_DAEMON : py;
  const args = usePackaged ? ["--port", String(PORT)]
                           : ["-m", "agenticos.daemon", "--port", String(PORT)];
  const child = spawn(cmd, args, {
    cwd: usePackaged ? path.dirname(PACKAGED_DAEMON) : REPO_ROOT,
    detached: true,
    stdio: ["ignore", out, err],
    windowsHide: true,
  });
  child.unref();
}

// #1134: ¿hay alguien escuchando en el puerto? Un daemon lento no contesta a /api/health en 1,5 s,
// pero sigue teniendo el puerto: entonces se le espera, no se arranca otro.
function portBusy() {
  return new Promise((resolve) => {
    const sock = require("net").connect({ host: "127.0.0.1", port: Number(PORT) });
    const fin = (busy) => { sock.destroy(); resolve(busy); };
    sock.setTimeout(1500, () => fin(true));   // acepta pero no contesta: ocupado y lento
    sock.once("connect", () => fin(true));
    sock.once("error", () => fin(false));
  });
}

async function ensureDaemon() {
  if (await daemonAlive()) return true;
  if (!(await portBusy())) spawnDaemon();
  for (let i = 0; i < 60; i++) {
    await new Promise((r) => setTimeout(r, 500));
    if (await daemonAlive()) return true;
  }
  return false;
}

// T4.3: la app instalada registra (idempotente) la tarea de logon apuntando al
// daemon empaquetado, con vigilante cada 5 min (T4.1 + finding #5). Solo en
// Windows y solo empaquetada; en desarrollo la tarea la pone tools/install_service.ps1.
function ensureLogonTask() {
  if (process.platform !== "win32" || !app.isPackaged) return;
  // Casa o puerto alternativos = instalacion de prueba: no tocar la tarea real.
  // Los CUATRO nombres. Con solo los dos viejos, quien aislara con FAROS_HOME
  // —el nombre que documentamos— pasaba este guardia y la instalacion de
  // prueba REEMPLAZABA la tarea programada de la casa. Un aislamiento que no
  // aisla es peor que no aislar, porque crees que estas a salvo.
  if (process.env.FAROS_HOME || process.env.FAROS_PORT ||
      process.env.AGENTICOS_HOME || process.env.AGENTICOS_PORT) return;
  const script = path.join(process.resourcesPath, "tools", "install_service.ps1");
  if (!fs.existsSync(script) || !fs.existsSync(PACKAGED_DAEMON)) return;
  // #288: las tareas se llaman FarOS-daemon / FarOS-watchdog. Los scripts quitan las
  // AgenticOS-* viejas al registrar las nuevas (dos daemons se pelearian por el puerto).
  // #317 (C): si las tareas ya apuntan a OTRA instalacion que sigue existiendo, se
  // PREGUNTA antes de reapuntarlas. El 16-sep un instalador con «Ejecutar FarOS» marcado
  // reapunto en silencio las tareas de produccion: un cambio en el arranque de la
  // maquina de alguien no ocurre sin que se vea. Si apuntan a un exe que ya no existe,
  // se reparan sin preguntar: no hay nada que proteger.
  const watchdog = path.join(process.resourcesPath, "tools", "install_watchdog.ps1");
  const xmlDe = (tn) => new Promise((res) => execFile("schtasks.exe", ["/Query", "/TN", tn, "/XML"],
    { windowsHide: true }, (err, out) => res(!err && typeof out === "string" ? out : null)));
  const exeDe = (xml) => ((xml || "").match(/<Command>([^<]+)<\/Command>/) || [])[1] || null;
  const correr = (ps1, nombre) => execFile("powershell.exe",
    ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1, "-Exe", PACKAGED_DAEMON, "-Port", String(PORT)],
    { windowsHide: true }, (error, stdout, stderr) => {
      if (error) fs.appendFileSync(path.join(DATA_DIR, "daemon_stderr.log"),
        `[app] ${nombre} fallo: ${error.message}
${stderr}
`);
    });
  (async () => {
    const dXml = (await xmlDe("FarOS-daemon")) || (await xmlDe("AgenticOS-daemon"));
    const wXml = await xmlDe("FarOS-watchdog");
    const daemonOk = !!dXml && dXml.includes(PACKAGED_DAEMON) && !!(await xmlDe("FarOS-daemon"));
    const watchOk = !!wXml && wXml.includes(PACKAGED_DAEMON) && wXml.includes("--watchdog");
    if (daemonOk && watchOk) return;
    const otro = exeDe(dXml);
    const ajeno = otro && path.resolve(otro).toLowerCase() !== path.resolve(PACKAGED_DAEMON).toLowerCase()
      && fs.existsSync(otro);
    if (ajeno) {
      const r = dialog.showMessageBoxSync({
        type: "question", buttons: ["Usar esta instalación", "Dejarlo como está"], defaultId: 1, cancelId: 1,
        title: "FarOS", message: "El arranque automático de FarOS apunta a otra instalación.",
        detail: `Ahora arranca: ${otro}
Esta app: ${PACKAGED_DAEMON}

¿Quieres que al encender el equipo arranque esta?`,
      });
      if (r !== 0) {
        fs.appendFileSync(path.join(DATA_DIR, "daemon_stderr.log"),
          `[app] tareas de arranque NO reapuntadas: el usuario lo dejo como estaba (${otro})
`);
        return;
      }
    }
    if (!daemonOk && fs.existsSync(script)) correr(script, "install_service.ps1");
    if (!watchOk && fs.existsSync(watchdog)) correr(watchdog, "install_watchdog.ps1");
  })();
}

function readToken() {
  try {
    return fs.readFileSync(TOKEN_FILE, "utf-8").trim();
  } catch {
    return null;
  }
}

function showWindow() {
  if (!win) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
}

/* #314 (absorbe el #335): el menu de la bandeja vive en el PROCESO PRINCIPAL, que no
   tiene catalogo ni idioma, y se pintaba en espaniol siempre. El idioma de arranque sale
   del sistema con la misma regla que delSistema() del renderer (en -> en, lo demas -> es);
   el renderer manda el suyo al cargar y en cada cambio, y el menu se RECONSTRUYE. */
const TRAY_LABELS = {
  es: { open: "Abrir FarOS", quit: "Salir" },
  en: { open: "Open FarOS", quit: "Quit" },
};
let trayLang = null;   // se fija en createTray: getLocale() no es fiable antes de ready

function buildTrayMenu() {
  if (!tray) return;
  const l = TRAY_LABELS[trayLang] ?? TRAY_LABELS.es;
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: l.open, click: showWindow },
    { type: "separator" },
    { label: l.quit, click: () => { quitting = true; app.quit(); } },
  ]));
}

ipcMain.on("agenticos:set-lang", (event, l) => {
  if (!senderEsLaVentana(event, win)) return;   // #476
  if (!TRAY_LABELS[l] || l === trayLang) return;
  trayLang = l;
  buildTrayMenu();
});

function createTray() {
  const icon = fs.existsSync(ICON_FILE) ? nativeImage.createFromPath(ICON_FILE) : nativeImage.createEmpty();
  tray = new Tray(icon);
  tray.setToolTip("FarOS");
  if (!trayLang) trayLang = (app.getLocale() || "es").toLowerCase().startsWith("en") ? "en" : "es";
  buildTrayMenu();
  tray.on("click", showWindow);
  tray.on("double-click", showWindow);
}

/* #281/#153 (F-SEG-07): el token iba en additionalArguments, o sea en la línea de comandos
   del proceso renderer. Ahora se queda aquí y el preload lo pide por IPC; solo se entrega a
   NUESTRA ventana (y #402 ya impide que esa ventana navegue fuera de su index.html). */
let tokenRenderer = "";
ipcMain.on("agenticos:get-token", (event) => {
  event.returnValue = senderEsLaVentana(event, win) ? tokenRenderer : "";   // #476: también el marco
});

// #1217: «Copiar» en Ajustes › Claves. El valor lo pide este proceso y va directo al portapapeles; al renderer
// solo le llega {ok, nombre, se_borra_en} (app/copiar_clave.js). Solo para nuestra ventana, como todo el IPC.
const copiarClave = require("./copiar_clave").crearCopiador({
  baseUrl: BASE_URL, token: () => readToken() || "", fetch, clipboard, setTimeout,
});
ipcMain.handle("agenticos:copiar-clave", async (event, nombre) => {
  if (!senderEsLaVentana(event, win)) return { ok: false, motivo: "origen_no_permitido" };
  return copiarClave(nombre);
});

async function createWindow() {
  const ok = await ensureDaemon();
  tokenRenderer = ok ? readToken() || "" : "";
  ensureLogonTask();
  win = new BrowserWindow({
    width: 1440,
    height: 900,
    // #280 (16-sep): la ventana no tenía mínimo y se podía arrastrar hasta 300px,
    // un estado que la barra superior no sabe pintar — «Nueva tarea» se salía 69px
    // fuera y el wordmark quedaba tapado por el botón de tema. Lienzo midió de 5 en 5
    // que con el CSS arreglado el ancho mínimo sano es 515; 560 deja margen para el
    // botón ES/EN que aún tiene que entrar ahí. El mínimo mata el caso de raíz:
    // la app deja de poder entrar en un estado que no sabe dibujar.
    minWidth: 560,
    title: "FarOS",
    icon: fs.existsSync(ICON_FILE) ? ICON_FILE : undefined,
    backgroundColor: "#111213",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: [
        `--agenticos-base-url=${BASE_URL}`,
        `--agenticos-app-version=${app.getVersion()}`,
      ],
    },
  });
  win.removeMenu();
  // Decisión #2 del dueño (3-sep): cerrar la ventana la esconde en la bandeja;
  // salir solo desde el icono de la bandeja.
  win.on("close", (event) => {
    if (quitting) return;
    event.preventDefault();
    win.hide();
  });
  win.on("closed", () => { win = null; });
  // #402: la ventana lleva el preload (token + openPath); no puede irse de su index.html,
  // ni abrir ventanas nuevas, ni hablar con otros puertos locales (window_policy.js).
  const indexFile = path.join(__dirname, "renderer", "index.html");
  const indexUrl = pathToFileURL(indexFile).href;
  win.webContents.on("will-navigate", (event, url) => {
    if (!puedeNavegar(url, indexUrl)) event.preventDefault();
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (abrirFuera(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  // #476: Electron concede por defecto cualquier permiso del navegador; aquí solo el de copiar.
  win.webContents.session.setPermissionRequestHandler((_wc, permiso, callback) => callback(permisoPermitido(permiso)));
  win.webContents.session.setPermissionCheckHandler((_wc, permiso) => permisoPermitido(permiso));
  win.webContents.session.webRequest.onBeforeRequest((details, callback) => {
    callback({ cancel: !peticionLocalPermitida(details.url, BASE_URL) });
  });
  await win.loadFile(indexFile);
  if (!ok) {
    win.webContents.executeJavaScript(
      `document.title = "FarOS — daemon NO disponible";`);
  }
}

app.on("second-instance", () => {
  if (win) showWindow(); else createWindow();
});

app.whenReady().then(() => {
  if (!PRIMARY) return;
  createTray();
  createWindow();
});
app.on("before-quit", () => { quitting = true; });
// Con bandeja la app no sale al cerrar la última ventana; el daemon sigue vivo aparte.
app.on("window-all-closed", () => { if (quitting) app.quit(); });
