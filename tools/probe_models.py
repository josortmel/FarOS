#!/usr/bin/env python3
"""Probe which model IDs claude -p and opencode accept.

Phase 1: $0 probes — send "hi" with --max-budget-usd 0.001 to every candidate.
          If the CLI rejects the model ID before calling the API, cost = $0.
          If it reaches the API, cost ≈ $0.0001 (one input token, budget kills it).
Phase 2: real inference — on accepted models, --max-budget-usd 0.01 with a real prompt.

Usage:
    python tools/probe_models.py [--phase 1] [--json out.json]
    python tools/probe_models.py --phase 2 --json out.json   # reads phase-1 results

Output: docs/mediciones/modelos_concretos.md + JSON with structured results.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_CANDIDATES = [
    "opus", "sonnet", "haiku", "fable",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-6",
    "claude-opus-4-6[1m]",
    "claude-sonnet-5",
    "claude-sonnet-4-5-20250514",
    "claude-sonnet-4-20250514",
    "claude-haiku-4-5-20251001",
    "claude-fable-5",
    "claude-fable-5-1",
    "us.anthropic.claude-opus-4-6-v1:0",
    "us.anthropic.claude-sonnet-4-5-20250514-v1:0",
]

OPENCODE_CMD = "opencode models"

PROBE_PROMPT = "Say only: ok"
REAL_PROMPT = "Reply with exactly one word: the model family you belong to (opus/sonnet/haiku/fable)."


def run_claude_probe(model_id: str, budget: float = 0.001, prompt: str = PROBE_PROMPT, timeout: int = 30) -> dict:
    cmd = ["claude", "-p", prompt, "--model", model_id, "--max-budget-usd", str(budget)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=True)
        stderr = r.stderr.strip()
        stdout = r.stdout.strip()

        combined = stdout + " " + stderr
        if "unrecognized_model" in combined:
            return {"model": model_id, "status": "rejected", "reason": "unrecognized_model", "exit": r.returncode}
        if "not exist or you may not have access" in combined:
            return {"model": model_id, "status": "rejected", "reason": "no_access", "exit": r.returncode}
        if "Exceeded USD budget" in combined:
            return {"model": model_id, "status": "accepted", "reason": "budget_hit_model_valid", "exit": r.returncode}
        if r.returncode == 0:
            return {"model": model_id, "status": "accepted", "output": stdout[:200], "exit": 0}
        return {"model": model_id, "status": "error", "stderr": stderr[:300], "stdout": stdout[:200], "exit": r.returncode}
    except subprocess.TimeoutExpired:
        return {"model": model_id, "status": "timeout", "exit": -1}
    except Exception as e:
        return {"model": model_id, "status": "exception", "error": str(e), "exit": -1}


def get_opencode_models() -> list[str]:
    try:
        r = subprocess.run(["opencode", "models"], capture_output=True, text=True, timeout=15, shell=True)
        return [line.strip() for line in r.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def phase1(out_path: Path | None) -> list[dict]:
    results = []
    print(f"[probe] Phase 1: probing {len(CLAUDE_CANDIDATES)} claude model IDs ($0.001 budget)")

    for model_id in CLAUDE_CANDIDATES:
        print(f"  {model_id} ... ", end="", flush=True)
        r = run_claude_probe(model_id, budget=0.001)
        print(r["status"])
        results.append(r)

    opencode = get_opencode_models()
    print(f"\n[probe] opencode models: {len(opencode)}")
    for m in opencode:
        print(f"  {m}")

    data = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "phase": 1,
        "claude_probes": results,
        "opencode_models": opencode,
        "accepted": [r["model"] for r in results if r["status"] == "accepted"],
        "rejected": [r["model"] for r in results if r["status"] == "rejected"],
    }

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n[probe] wrote {out_path}")

    print(f"\n[probe] accepted: {data['accepted']}")
    print(f"[probe] rejected: {data['rejected']}")
    return results


def phase2(json_path: Path):
    if not json_path.exists():
        print(f"[probe] phase-1 results not found at {json_path}. Run --phase 1 first.")
        sys.exit(1)

    data = json.loads(json_path.read_text(encoding="utf-8"))
    accepted = data.get("accepted", [])
    print(f"[probe] Phase 2: real inference on {len(accepted)} accepted models ($0.01 budget)")

    results = []
    for model_id in accepted:
        print(f"  {model_id} ... ", end="", flush=True)
        r = run_claude_probe(model_id, budget=0.01, prompt=REAL_PROMPT, timeout=60)
        print(f"{r['status']}: {r.get('output', r.get('stderr', ''))[:80]}")
        results.append(r)

    data["phase2"] = results
    data["phase"] = 2
    data["phase2_ts"] = datetime.now(timezone.utc).isoformat()
    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[probe] updated {json_path}")


def main():
    parser = argparse.ArgumentParser(description="Probe model IDs for claude -p and opencode")
    parser.add_argument("--phase", type=int, default=1, choices=[1, 2])
    parser.add_argument("--json", type=str, default="docs/mediciones/modelos_concretos.json")
    args = parser.parse_args()

    out = Path(args.json)
    if args.phase == 1:
        phase1(out)
    else:
        phase2(out)


if __name__ == "__main__":
    main()
