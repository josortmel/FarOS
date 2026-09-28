<p align="center">
  <img src="docs/images/banner.png" alt="FarOS: un sistema operativo para una familia de agentes de IA" width="100%">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-3.3.1-f37513" alt="Version">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-f37513" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.11+-3776ab" alt="Python">
  <img src="https://img.shields.io/badge/MCP-67%20tools-f37513" alt="MCP Tools">
  <img src="https://img.shields.io/badge/desktop-Electron-f37513" alt="Desktop">
  <img src="https://img.shields.io/badge/windows-installer-0078d4" alt="Windows">
</p>

<p align="center"><a href="README.md">English</a> · <strong>Español</strong></p>

**Un sistema operativo para una familia de agentes de IA.**

Los frameworks de agentes te dan un bucle: prompt, herramientas, resultado. FarOS le da a una **casa de agentes persistentes** lo que una casa necesita para funcionar durante meses: un tablero compartido con calendario, workflows con fases y decisiones que esperan a un humano, jobs programados que corren sin vigilancia, y un verificador que juzga cada ejecución autónoma — y una app de escritorio donde el humano lo ve todo sin abrir una terminal.

Se construyó para una casa real — y por ella: un humano y cuatro agentes de Claude Code con identidad persistente, trabajando de lunes a viernes desde agosto de 2026. Los agentes propusieron, despacharon, revisaron y verificaron el trabajo sobre un tablero común de FarOS, coordinándose en tiempo real por EcoRelay y llevando la memoria entre sesiones en EcoDB — así que FarOS lo construyó el mismo tipo de sistema que describe. Cada función existe porque esa casa la necesitó.

**En producción desde septiembre de 2026.**

<p align="center">
  <img src="docs/images/board.png" alt="El tablero de FarOS: columnas propuesta, aceptada, en curso, hecha y verificada, con una ejecución de agente en vivo en una tarjeta y otra tarjeta bloqueada — esperando una exportación externa, con el motivo y la fecha en que se pidió escritos en la propia tarjeta" width="100%">
</p>

## Arquitectura

<p align="center">
  <img src="docs/images/architecture.svg" alt="Una puerta, un escritor: la app de escritorio y los agentes llaman ambos a la misma API HTTP; el daemon es el único proceso que escribe la base de datos" width="100%">
</p>

Un proceso, un fichero. El daemon guarda el estado; la app y los agentes son dos ventanas a la misma base de datos. La app no puede hacer nada que una sesión de agente no pueda hacer por el MCP, y al revés: los dos hablan con la misma base. Backups diarios con retención de 14 días, y las claves se vacían de la copia.

## El bucle que la hace distinta

Un gestor de tareas registra el trabajo. Un cron lo ejecuta. Ninguno de los dos **cierra el bucle** — la parte donde una acción autónoma se propone, se ejecuta y luego se *juzga*, con el fallo yendo a algún sitio en vez de perderse en un chat. En FarOS ese bucle es la columna vertebral, y cada etapa es un objeto de primera clase compartido entre humanos y agentes.

<p align="center">
  <img src="docs/images/cycle.svg" alt="El ciclo de verificación: propuesta, aceptada, en curso, hecha, y verificada por un agente distinto; un veredicto fallido devuelve la tarea a en curso con el motivo" width="100%">
</p>

La evidencia que produce una ejecución entra al verificador como entrada *no confiable*: al juez se le manda evaluarla contra los criterios, no confiar en ella porque una ejecución lo diga.

- **Por defecto el veredicto lo da un juez distinto del que hizo el trabajo.** Los jobs programados los juzga un modelo barato (`haiku` por defecto) leyendo el artefacto contra el prompt del job; las ejecuciones de un workflow puede verificarlas un agente *distinto* de la casa. (La auto-verificación está disponible cuando un job la elige — la idea es que un veredicto separado sea el valor por defecto, no una ocurrencia tardía.)
- **Un fallo tiene destino.** Un veredicto fallido se convierte en una tarea FIX en el tablero que se cierra sola cuando el arreglo pasa — las revisiones no se evaporan en un log de chat.
- **El humano es un objeto, no una interrupción.** "Bloqueado hasta que alguien decida X" es una cosa real en el tablero — una decisión con opciones enteras, un motivo obligatorio, y un registro de quién eligió qué, cuándo y por qué. Los agentes la esperan; no adivinan a su alrededor.
- **La recurrencia sabe que quien llega tarde es una máquina.** Las tareas recurren por días de la semana y horas; "vencida" significa algo cuando quien va con retraso es un agente programado, y las fechas de fin de semana saltan al lunes.

<p align="center">
  <img src="docs/images/verification-queue.png" alt="La vista de resultados de agentes: la última ejecución de cada job con su veredicto — un fallo con su motivo, dos pases, una ejecución en curso, y botones de veredicto manual" width="100%">
</p>

Esta es la parte que nadie tenía que construir para una casa de un humano y cuatro agentes, así que la construimos.

## Qué hay dentro

**Tablero.** Kanban con Hoy / Semana / Futuras / Todas, más un calendario con Día (un carril por agente, horas de 7 a 22), Semana, Mes y Año. Tareas recurrentes ancladas a días y horas. Las tareas cerradas se quedan en el día en que se cerraron, en gris: el trabajo hecho se ve, no se borra.

<p align="center">
  <img src="docs/images/calendar-week.png" alt="El calendario semanal: la semana laboral en columnas y las horas en el lateral, con los jobs programados y las tareas recurrentes colocados en su franja" width="100%">
</p>

**Workflows (la Oficina).** Un proyecto como máquina de estados: tareas con fases y puertas de dependencia duras (nada se despacha hasta que sus dependencias están verificadas), hallazgos que se convierten en tareas FIX auto-cerrables, handoffs que llevan el "por dónde vamos" entre sesiones, y las decisiones de arriba.

**Agentes.** Jobs programados (día de la semana + hora) que lanzan un harness de agente (`claude-cli` hoy, `opencode` experimental), con los servidores MCP que eliges por job y las claves guardadas solo en la base local. Cada ejecución la juzga el verificador; los fallos van a Telegram; los resultados son la lectura de la mañana — última ejecución de cada job, pasa o falla.

<p align="center">
  <img src="docs/images/agents-jobs.png" alt="Un job de agente: su harness, modelo, cadencia y coste — los campos que hacen de un agente una entidad configurable y no un script clavado" width="100%">
</p>

**App de escritorio.** Electron, bandeja del sistema, modo oscuro, un instalador que no necesita Python ni terminal. El daemon corre como tarea programada de Windows con un watchdog; la app es una ventana hacia él. Si el daemon está caído, la app carga en un modo demo con datos de ejemplo y lo dice.

**MCP.** Sesenta y siete herramientas. Cualquier cosa que la app pueda hacer, un agente la puede hacer desde su propia terminal contra la misma base:

```
claude -p "¿qué tengo vencido hoy?" --allowedTools "mcp__agenticos__*"
```

El servidor MCP se registra todavía con su nombre original, `agenticos`, así que sus herramientas llevan el prefijo `mcp__agenticos__*` aunque el paquete ya sea `faros`. Renombrar un servidor MCP en marcha cambiaría el prefijo de todas las herramientas y rompería a cada cliente conectado, así que ese nombre se mantiene a propósito.

<p align="center">
  <img src="docs/images/mcp-registry.png" alt="El registro de MCP: qué servidores están conectados a cada agente, con las claves enmascaradas — así se enchufan las herramientas a un job" width="100%">
</p>

## Instalar (escritorio)

1. Descarga `FarOS-Setup-x.y.z.exe` de la última release y ejecútalo.
2. El instalador no está firmado, así que Windows SmartScreen avisará de que el editor es desconocido. Elige *Más información → Ejecutar de todas formas*. (No firmar es una decisión de coste deliberada, no un descuido — los certificados de firma de código son un gasto recurrente que este proyecto no ha asumido.)
3. Abre FarOS desde el acceso directo del escritorio. El daemon arranca solo.

Todo vive bajo `%LOCALAPPDATA%\FarOS\`: base de datos, backups, logs, artefactos de ejecuciones, y el token de autenticación local.

Desinstalar quita la app pero conserva esa carpeta a propósito — tu tablero y tu historia viven ahí, y una reinstalación las recupera. También deja dos tareas programadas en segundo plano. Se llaman `AgenticOS-daemon` y `AgenticOS-watchdog` —el nombre anterior del proyecto, que se conserva de momento porque renombrar una tarea registrada es un cambio aparte y delicado—. Para quitarlas, en PowerShell:

```powershell
Unregister-ScheduledTask -TaskName AgenticOS-daemon   -Confirm:$false
Unregister-ScheduledTask -TaskName AgenticOS-watchdog -Confirm:$false
```

Un instalador futuro lo hará por ti.

**Tu tablero empieza vacío — a propósito.** Una instalación nueva te da tu propia base de datos, no una demo: cinco columnas y cero tickets, porque son tuyos para llenarlos. Para ver el bucle de arriba girar de verdad — un agente proponiendo un ticket, una ejecución en la cola de verificación, un veredicto de un agente distinto — mira las capturas, o crea tu primer ticket y despáchalo. Una herramienta que shipea precargada con trabajo falso parecería más ocupada y significaría menos.

## Ejecutar desde el código

```
git clone https://github.com/josortmel/FarOS
cd FarOS
pip install -e .                 # instala el paquete `faros` (fastapi, uvicorn, httpx, mcp)
python -m faros.daemon           # http://127.0.0.1:8756
cd app && npm install && npm start
```

Tests — 240, en verde en menos de diez segundos, sin claves de API:

```
pip install -e ".[dev]"
pytest
```

Los ocho tests de extremo a extremo que llaman a un juez LLM real son opcionales (`pytest -m judge`) — clonar el repo y correr su suite no te cuesta nada.

## Lo que aún no es

FarOS propone, despacha, ejecuta, juzga y registra agentes. Todavía no los **encadena** dentro del daemon: el motor donde la salida verificada de un agente alimenta al siguiente, con roles y revisión adversarial orquestados por el propio daemon, está diseñado y no construido. Hoy ese encadenado corre a través de las propias sesiones de los agentes y el MCP — que es exactamente como se publicó este repositorio. Meterlo dentro del daemon es lo siguiente.

## Construido con

- [EcoDB](https://github.com/josortmel/EcoDB) — la memoria compartida que los agentes usan para recordar entre sesiones. FarOS no depende de ella; la casa sí.
- [EcoRelay](https://github.com/josortmel/EcoRelay) — el canal en tiempo real por el que los agentes se coordinan. FarOS tampoco depende de él; la casa sí.
- Claude Code como runtime de los agentes, y MCP como el contrato entre los agentes y el tablero.

## Licencia

PolyForm Noncommercial 1.0.0 (la misma que EcoDB).
