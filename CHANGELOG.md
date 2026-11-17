# Changelog

Notable changes to FarOS. Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

### Added
- **The verifier can say *uncertain*.** The cheap judge runs without tools, and its answer used to be forced to *pass* or *fail*: when a point of the criterion could only be checked outside the evidence (opening a URL, reading a file that isn't shown, checking a fact), it had to pick one, and sometimes picked *pass* while saying it couldn't check — closing the task. Now it answers *uncertain* and names the point it can't check. An uncertain run does not complete its linked task, is not judged again on its own and sends no Telegram alert; a person settles it later with *pass* or *fail*. An automatically verified task the judge is uncertain about stays done and moves to peer verification, with the judge's reason in its history. Schema 7 (the run verdict now accepts `uncertain`; the runs table is rebuilt on first start, with a backup first).
- **Keys, in Settings.** Settings › Keys is an open list of keys for outside services: as many as you want of each type (DeepSeek, Anthropic, Telegram bots, or *other*), each with its name and, if you like, where it comes from. A key is **tested before it is saved** — a free call to the service (DeepSeek and Anthropic list their models, Telegram answers `getMe` with the bot's name) — and is **not saved** if the service rejects it. A DeepSeek key on an account with no balance is still saved (the key is valid) but marked *no balance*, because the model list answers even when every call would fail. Keys of type *other* are saved untested. Of each type, FarOS uses the one marked *in use*; the rest stay stored. Values are never shown again, never travel in a backup, and never appear in an error. `PATCH /api/settings` with an API key writes into the key in use (and is tested the same way), so scripts that set keys keep working. New API `/api/credentials` and MCP tools `credentials`, `create_credential`, `update_credential`, `test_credential`, `delete_credential`. Schema 6: on first start, the keys already in your settings join the list, marked in use.
- **Edit your agents from the app.** Settings › Agents lets you add, rename, suspend and reactivate agents without restarting anything. Renaming carries the agent's tasks and workflow coordination with it. Suspending asks who takes over its open tasks; closed tasks keep the old name, because they are history. Only a name with no tasks at all can be deleted. The owner can't be renamed, suspended or deleted. If the roster is fixed by an environment variable, the app says so instead of pretending to save.

### Changed
- **Nothing is scheduled for you.** A task created without a date used to be dropped on the next working day at 14:00 (and a task sent back without a date was set due today), which read like a commitment nobody had made. Now it is born *to place*: it shows up to its owner in a "To place" lane on the board and in their own board, and the owner decides when it happens. Whoever creates a task is never forced to pick a date.
- **Whoever does the work can close it.** On a task verified by a peer, the agent who carried it out can start and complete it with its evidence, even if the task belongs to someone else. The seal still comes from a third party: the one who completed it cannot verify it.
- **A weekly task keeps its day.** A weekly task takes its day of the week from the date its owner gave it; moving one occurrence moves only that one — the following ones stay on the usual day.
- **Every error carries a stable code.** Specific codes where they exist (`auth.invalid_token`, `ticket.not_found`, `agent.unknown`, `transition.illegal`, `roster.*`…); otherwise `<field>.invalid` when the offending field is known, `request.invalid` when it isn't, and `transition.rejected` for a refused transition. Errors that come from the web framework rather than from FarOS have the same flat shape and their own codes: `route.not_found` (404), `method.not_allowed` (405), `request.malformed_json` (422, a body that isn't valid JSON) and `<field>.invalid` (422, a parameter of the wrong type). The `reason` stays as the human explanation; branch on `code`.
- **Your own board fits on one screen.** `my_board` returns what you need to decide what's next plus a 200-character summary of each task; the full text is one call away (`get_ticket`, or `full=true`).

### Fixed
- **`update_job` over MCP edits jobs again.** Its fields are now real parameters of the tool, so a client can change a job's prompt, model, schedule or any other editable field. Before, the tool exposed a single `fields` argument that the daemon refused. A call that carries no editable field now gets an error instead of an empty update.
- **Uninstalling one copy of FarOS leaves the others alone.** The uninstaller only removes the scheduled tasks and stops the daemon that belong to its own install folder.

### Security
- **Opening documents can no longer run programs.** The app only opens an existing local file with a document extension (`.md`, `.json`, `.txt`, `.html`, `.pdf`). Executables, network (UNC) paths, relative paths and paths with a null byte are refused, and a link is judged by what it points to. The daemon lists only document files for a workflow, including its spec and plan paths.
- **Content-Security-Policy.** The interface refuses inline scripts, inline event handlers, `eval` and any network other than the local daemon.

## 3.3.1 — 2026-09-28

A fix release: the English interface is now complete, and a few screens that could strand you no longer do.

### Fixed
- **English interface, all the way through.** Toasts that carry a name, the task panel, the ticket states, the calendar's labels and dialogs (including the accessibility labels you only meet by tabbing), and the tray menu now follow the language you picked in the app. An end-to-end check fails if any panel shows an untranslated catalog key.
- **Empty board.** A fresh install shows the board's five columns, empty, instead of a blank screen.
- **New job dialog.** It closes the three ways you would expect — Escape, a click on the backdrop, and Cancel — and its buttons stay put when an error appears.

### Known issues — fixed in the next release
We found these in a dependency and security audit on the day of this release. We chose to ship the fixes above now and to say so plainly rather than hold them back:
- **Opening workflow documents is too permissive.** The desktop app's "open document" action hands any path to the operating system without checking the file type, so a workflow could point it at an executable. The documents come from your own agents through your own local daemon, but the check belongs in the app. The renderer also has no Content-Security-Policy yet.
- **The desktop app runs on Electron 33**, which is past its support window. It will move to a supported Electron.
- **Bundled Python dependencies are behind** (Starlette 0.48 and others with published advisories). The daemon only listens on `127.0.0.1` and does not use the affected features, but they will be updated.

## 3.3.0 — 2026-09-16

The release that took FarOS public.

### Added
- **Renamed to FarOS.** The package, the CLI (`python -m faros.daemon`), the database (`faros.db`), the data directory (`%LOCALAPPDATA%\FarOS`), the installer, the env vars (`FAROS_*`) and the auth header (`X-FarOS-Token`) all move to FarOS. Existing installs keep working untouched: the daemon falls back to the previous `agenticos.db` and `%LOCALAPPDATA%\AgenticOS`, and the old `AGENTICOS_*` variables and header are still accepted. The MCP server keeps its original name `agenticos` (its tools stay `mcp__agenticos__*`) — renaming a running server would change every tool's prefix and break connected clients, so that one name is deferred on purpose.
- **Bilingual UI (ES/EN).** A language toggle in the top bar switches the whole interface without a reload — dates and weekday names included — and the app starts in the system language.
- **Configurable roster.** The owner and the agents move out of the code into configuration (`roster.json` / env), so shipping data carries neutral example names instead of a private household.
- **`pyproject.toml`** with measured dependencies (fastapi, uvicorn, httpx, mcp). `pip install -e .` and `pytest` now work from a clean clone.
- **LICENSE:** PolyForm Noncommercial 1.0.0.

### Changed
- **Stable error codes for auth and lookup.** Authentication and not-found errors carry stable codes (`auth.invalid_token`, `ticket.not_found`) rather than localized sentences, so a client can branch on the code and the UI owns the wording. Validation errors still return the offending field plus a message; extending stable codes to those paths is next.
- **Test suite:** 240 tests, green in under ten seconds and free of model calls. The eight end-to-end tests that invoke a real LLM judge are deselected by default and opt-in via `pytest -m judge`.

### Privacy
- Synthetic fixtures generated from a single roster, with neutral example names. The public repository is built from a hand-picked whitelist rather than by scrubbing history, so private material is excluded by construction rather than deleted after the fact.

### Not yet
- Chaining agents inside the daemon — one agent's verified output feeding the next, with roles and adversarial review orchestrated by the daemon — is designed and not built. Today that chaining runs through the agents' own sessions and the MCP.
