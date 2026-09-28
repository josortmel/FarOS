# Changelog

Notable changes to FarOS. Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

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
