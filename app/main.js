// FarOS — Electron main process (SPEC §2, §9).
// #261 (16-sep): marca visible = FarOS. Los nombres de TAREA PROGRAMADA
// ("AgenticOS-daemon", "AgenticOS-watchdog") NO se tocan: son tareas reales
// registradas en la maquina, y renombrarlas aqui dejaria de encontrarlas.
// Responsabilidades: asegurar daemon vivo, ventana, bandeja, pasar token al renderer.
// El daemon NO muere con la ventana: las tareas programadas siguen (SPEC §2).

const { app, BrowserWindow, ipcMain, shell, Tray, Menu, nativeImage } = require("electron");

// T3.6: la UI pide abrir documentos del workflow (rutas que devuelve el daemon).
ipcMain.handle("agenticos:open-path", async (_event, target) => {
  if (typeof target !== "string" || !target) return "ruta vacía";
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
if (!app.requestSingleInstanceLock()) {
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

async function ensureDaemon() {
  if (await daemonAlive()) return true;
  spawnDaemon();
  for (let i = 0; i < 40; i++) {
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
  // Se pregunta a la propia tarea (no a un marcador) a qué exe apunta: si ya es
  // este, nada; si es otro (desarrollo, versión anterior), se re-registra.
  // Vigilante independiente (finding #5, 12:55): tarea con trigger de TIEMPO cada 5 min
  // que hace health y relanza la tarea del daemon. La repeticion de la propia tarea del
  // daemon NO cubre instancias arrancadas a mano (medido en produccion).
  // v3.1 (T1.2, el dueño, queja 2ª): el vigilante es el propio exe del daemon en modo --watchdog
  // (sin ventana). Si la tarea no existe O todavia apunta a powershell.exe / a otro exe,
  // se re-registra. Se pregunta a la tarea (XML), no a un marcador.
  const watchdog = path.join(process.resourcesPath, "tools", "install_watchdog.ps1");
  execFile("schtasks.exe", ["/Query", "/TN", "AgenticOS-watchdog", "/XML"], { windowsHide: true }, (wErr, wXml) => {
    if (!fs.existsSync(watchdog)) return;
    const ok = !wErr && typeof wXml === "string" && wXml.includes(PACKAGED_DAEMON) && wXml.includes("--watchdog");
    if (ok) return;
    execFile("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", watchdog,
      "-Exe", PACKAGED_DAEMON, "-Port", String(PORT)],
      { windowsHide: true }, (error, stdout, stderr) => {
        if (error) fs.appendFileSync(path.join(DATA_DIR, "daemon_stderr.log"), `[app] install_watchdog.ps1 fallo: ${error.message}
${stderr}
`);
      });
  });
  execFile("schtasks.exe", ["/Query", "/TN", "AgenticOS-daemon", "/XML"],
    { windowsHide: true }, (qErr, xml) => {
      if (!qErr && typeof xml === "string" && xml.includes(PACKAGED_DAEMON)) return;
      execFile("powershell.exe",
        ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script,
         "-Exe", PACKAGED_DAEMON, "-Port", String(PORT)],
        { windowsHide: true }, (error, stdout, stderr) => {
          if (error) {
            fs.appendFileSync(path.join(DATA_DIR, "daemon_stderr.log"),
              `[app] install_service.ps1 falló: ${error.message}\n${stderr}\n`);
          }
        });
    });
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

ipcMain.on("agenticos:set-lang", (_event, l) => {
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

async function createWindow() {
  const ok = await ensureDaemon();
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
        `--agenticos-token=${ok ? readToken() || "" : ""}`,
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
  await win.loadFile(path.join(__dirname, "renderer", "index.html"));
  if (!ok) {
    win.webContents.executeJavaScript(
      `document.title = "FarOS — daemon NO disponible";`);
  }
}

app.on("second-instance", () => {
  if (win) showWindow(); else createWindow();
});

app.whenReady().then(() => {
  createTray();
  createWindow();
});
app.on("before-quit", () => { quitting = true; });
// Con bandeja la app no sale al cerrar la última ventana; el daemon sigue vivo aparte.
app.on("window-all-closed", () => { if (quitting) app.quit(); });
