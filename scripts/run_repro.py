#!/usr/bin/env python3
"""Reviewer-friendly entry point.

Default behavior is intentionally small/offline: it recreates and prints only the
publication-facing tables, without API calls and without detailed diagnostic CSVs.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PAPER_TABLES = ROOT / "artifacts" / "paper_tables"


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, cwd=str(ROOT), check=True)


def cmd_quickstart(args: argparse.Namespace) -> None:
    run([sys.executable, str(SCRIPTS / "reproduce_paper_tables.py"), "--outdir", str(args.outdir)])


def cmd_tables(args: argparse.Namespace) -> None:
    cmd_quickstart(args)


def cmd_list(_: argparse.Namespace) -> None:
    print("Publication-facing CSV tables included in this package:")
    for p in sorted(PAPER_TABLES.glob("*.csv")):
        print("-", p.relative_to(ROOT))
    print("\nArchived translated outputs are under experiments/*/outputs_ts/.")
    print("Optional full workflow scripts are under scripts/full_pipeline/.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="QGIS table-only reproducibility commands")
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("quickstart", help="Recreate and print only the paper-facing tables. No API calls.")
    q.add_argument("--outdir", type=Path, default=PAPER_TABLES)
    q.set_defaults(func=cmd_quickstart)

    t = sub.add_parser("tables", help="Alias of quickstart.")
    t.add_argument("--outdir", type=Path, default=PAPER_TABLES)
    t.set_defaults(func=cmd_tables)

    l = sub.add_parser("list", help="List bundled compact tables and archived outputs.")
    l.set_defaults(func=cmd_list)
    return p


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
