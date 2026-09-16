#!/usr/bin/env python3
"""Importa un Plan (CONTRATO_PLAN.md) a FarOS vía API.

Cliente del daemon — NO abre la DB (un solo escritor). Uso:
    python tools/import_plan.py <fichero.plan.json> [--agent <nombre>] [--url http://127.0.0.1:8756]

El agente por defecto sale del roster configurado (roster.py), no de un
nombre clavado aquí: esta herramienta se publica y no debe traer dentro el
nombre de nadie.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from faros.roster import HOUSE_OWNER  # noqa: E402  (tras ajustar sys.path)

DEFAULT_URL = os.environ.get("AGENTICOS_URL", "http://127.0.0.1:8756")
TOKEN_FILE = Path(os.environ.get("AGENTICOS_HOME")
                  or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AgenticOS") / "token"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan_file")
    ap.add_argument("--agent", default=HOUSE_OWNER)
    ap.add_argument("--url", default=DEFAULT_URL)
    args = ap.parse_args()

    plan_file = Path(args.plan_file).resolve()
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    # v3.1 (T4.2): raíz del repo = el ancestro de plan_file donde plan_path relativo existe.
    repo_root = None
    rel = (plan.get("workflow") or {}).get("plan_path")
    if rel and not Path(rel).is_absolute():
        for cand in [plan_file.parent, *plan_file.parents]:
            if (cand / rel).resolve() == plan_file:
                repo_root = str(cand)
                break
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    r = httpx.post(f"{args.url}/api/workflows/import",
                   headers={"X-AgenticOS-Token": token},
                   json={"agent": args.agent, "plan": plan, "repo_root": repo_root}, timeout=60)
    body = r.json()
    if r.status_code >= 400:
        print(f"ERROR {r.status_code}: {body.get('reason', body)}", file=sys.stderr)
        sys.exit(1)
    print(f"Workflow '{body['name']}' (#{body['id']}) importado:")
    for ps in body["phases_summary"]:
        print(f"  fase {ps['phase']}: {ps['total']} tareas ({ps['ready']} listas)")


if __name__ == "__main__":
    main()
