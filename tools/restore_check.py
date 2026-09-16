#!/usr/bin/env python3
"""Restore drill: verify a backup against production (T0.6).

Usage:
    python tools/restore_check.py --check <backup_file> [--db <production_db>]

Runs integrity_check on the backup and compares ticket count to production.
Exit 0 = all good, exit 1 = mismatch or corruption.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from faros.db import connect, DEFAULT_DB
from faros.backup import verify_backup


def main():
    parser = argparse.ArgumentParser(description="Verify a FarOS backup.")
    parser.add_argument("--check", required=True, help="Path to backup .db file")
    parser.add_argument("--db", default=None,
                        help=f"Production DB (default: $AGENTICOS_DB or {DEFAULT_DB})")
    args = parser.parse_args()

    prod_conn = connect(args.db)
    result = verify_backup(args.check, prod_conn)
    prod_conn.close()

    if not result.get("ok"):
        print(f"FAIL: {result}")
        sys.exit(1)

    print(f"OK: integrity={result['integrity']}, "
          f"tickets={result['backup_tickets']} (production={result['production_tickets']})")
    sys.exit(0)


if __name__ == "__main__":
    main()
