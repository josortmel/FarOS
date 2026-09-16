"""Adapter opencode (T1.4). Cubre deepseek via API — billing='api', presupuesto real.

Medido 2026-09-02 por Prima. opencode 1.15.13.
- `opencode models` devuelve provider/model (e.g. deepseek/deepseek-v4-pro)
- `opencode run -m provider/model --format json` stream JSON events por stdout
- Events: step_start, text (con el contenido), tool_call, tool_result, step_finish
- Coste: la session de opencode guarda cost/tokens en su DB SQLite
  (~/.local/share/opencode/opencode.db, tabla session)
- Prompt: positional args o stdin
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess

from ..procutil import run_hidden
from pathlib import Path

from .base import HarnessAdapter, Capabilities, ModelInfo, RunResult

OPENCODE_DB = Path(
    os.environ.get("LOCALAPPDATA", ""),
    "..", ".local", "share", "opencode", "opencode.db"
) if os.name == "nt" else Path.home() / ".local" / "share" / "opencode" / "opencode.db"

if os.name == "nt":
    OPENCODE_DB = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".local" / "share" / "opencode" / "opencode.db"


class OpenCodeAdapter(HarnessAdapter):
    name = "opencode"
    billing = "api"
    executable = "opencode"
    required_settings = ["deepseek_api_key"]
    # T5.8 (v3.1) MEDIDO 4-sep y DESACTIVADO: opencode (Bun) arranca TODOS los servidores MCP de
    # la config global en cada run, ignora enabled:false por OPENCODE_CONFIG, y cada servidor
    # abre una ventana de Windows Terminal (~30 por run). Hasta medir OPENCODE_CONFIG_DIR con
    # una config aislada + envoltorio sin consola (v3.2), MCP en opencode = off.
    capabilities = Capabilities(
        mcp=False,
        allowed_tools=False,
        permission_mode=False,
        json_schema=False,
        effort=False,
        fallback_model=False,
        system_prompt=False,
        add_dir=False,
        budget=True,
        inherit_mcp=False,
    )

    def __init__(self):
        super().__init__()
        self.exe = self._find_exe()

    @staticmethod
    def _find_exe() -> str | None:
        return shutil.which("opencode")

    def find_exe(self) -> str | None:
        return self.exe

    def env(self, settings: dict) -> dict:
        return {"DEEPSEEK_API_KEY": settings.get("deepseek_api_key") or ""}

    def fetch_models(self, settings=None) -> tuple[list[ModelInfo], str]:
        exe = self.find_exe()
        if not exe:
            return [], "none"
        try:
            out = run_hidden(
                [exe, "models"],
                capture_output=True, text=True, timeout=30,
            )
            lines = [l.strip() for l in (out.stdout or "").splitlines() if l.strip()]
            models = []
            for line in lines:
                parts = line.split("/", 1)
                provider = parts[0] if len(parts) == 2 else "unknown"
                label = line
                tier = None
                if "free" in line:
                    tier = "free"
                elif "pro" in line or "reasoner" in line:
                    tier = "high"
                elif "flash" in line:
                    tier = "mid"
                models.append(ModelInfo(line, label, "cli", tier))
            return models, "cli"
        except (subprocess.SubprocessError, OSError):
            return [], "none"

    def validate_model(self, model: str) -> bool:
        return any(m.id == model for m in self.list_models())

    def build_command(self, job: dict) -> list[str]:
        exe = self.find_exe()
        if not exe:
            raise RuntimeError("opencode CLI no encontrado en PATH")
        cmd = [exe, "run", "-m", job["model"], "--format", "json"]
        if job.get("cwd"):
            cmd += ["--dir", job["cwd"]]
        if job.get("permission_mode") == "dontAsk":
            cmd += ["--dangerously-skip-permissions"]
        return cmd

    def stdin_payload(self, job: dict, prompt: str) -> bytes | None:
        return prompt.encode("utf-8")

    def parse_output(self, stdout: str, exit_code: int) -> RunResult:
        events = _parse_events(stdout)
        text_parts = []
        session_id = None
        for e in events:
            etype = e.get("type", "")
            if not session_id:
                session_id = e.get("sessionID")
            if etype == "text":
                part = e.get("part", {})
                text_parts.append(part.get("text", ""))
            elif etype == "error":
                err = e.get("error", {})
                err_data = err.get("data", {})
                text_parts.append(f"ERROR: {err_data.get('message', err.get('name', 'unknown'))}")

        output_text = "".join(text_parts)
        cost_usd = None

        if session_id:
            cost_usd = _read_session_cost(session_id)
            if not output_text.strip():
                # Medido 4-sep (T5.8): con tool calls el stream JSON no trae el texto final;
                # la respuesta del asistente sí queda en la DB de opencode (tabla part).
                output_text = _read_session_text(session_id)

        is_error = exit_code != 0 or any(e.get("type") == "error" for e in events)
        return RunResult(
            status="error" if is_error else "ok",
            exit_code=exit_code,
            cost_usd=cost_usd,
            session_id=session_id,
            output_text=output_text[:4000],
            raw={"events_count": len(events), "session_id": session_id},
        )

    def read_cost(self, result: RunResult) -> tuple[float | None, str]:
        if result.cost_usd is not None:
            return result.cost_usd, "billed"
        return None, "none"


def _parse_events(stdout: str) -> list[dict]:
    events = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return events


def _read_session_text(session_id: str) -> str:
    """Texto del asistente (parts type=text, sin el prompt) de una sesión de opencode."""
    db_path = OPENCODE_DB
    if not db_path.exists():
        return ""
    try:
        conn = sqlite3.connect(str(db_path), timeout=2)
        rows = conn.execute(
            "SELECT p.data FROM part p JOIN message m ON m.id = p.message_id"
            " WHERE p.session_id=? ORDER BY p.time_created", (session_id,)).fetchall()
        conn.close()
    except sqlite3.Error:
        return ""
    texts = []
    for (data,) in rows:
        try:
            d = json.loads(data)
        except (TypeError, json.JSONDecodeError):
            continue
        if d.get("type") == "text" and d.get("text"):
            texts.append(d["text"])
    # el primer text suele ser el prompt (preámbulo + trabajo); lo que importa es el último
    return texts[-1].strip() if len(texts) > 1 else ""


def _read_session_cost(session_id: str) -> float | None:
    """Read cost from opencode's internal DB after a run completes."""
    db_path = OPENCODE_DB
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path), timeout=2)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT cost, tokens_input, tokens_output FROM session WHERE id=?",
            (session_id,),
        ).fetchone()
        conn.close()
        if row and row["cost"] is not None and row["cost"] > 0:
            return float(row["cost"])
        return 0.0
    except Exception:
        return None
