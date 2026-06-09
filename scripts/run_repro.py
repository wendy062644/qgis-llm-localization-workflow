#!/usr/bin/env python3
"""Unified reviewer-friendly entry point.

The default quickstart is small and offline: 100 segments, deterministic scoring,
no model API calls.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
EXPERIMENTS = ROOT / "experiments"
RESULTS = ROOT / "results"
DATA_TS = ROOT / "data" / "raw" / "qgis_en.ts"
GLOSSARY = [ROOT / "data" / "glossary" / "1.ods", ROOT / "data" / "glossary" / "2.ods"]

SUITES: Dict[str, Mapping[str, str]] = {
    "demo": {"grok": "demo_ablation_grok_100"},
}
DEFAULT_MODELS = {"grok": "grok-4.3", "gemini": "gemini-3.1-flash-lite"}


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except Exception:
        return str(path)


def run(cmd: List[str]) -> None:
    print("\n$ " + " ".join(str(x) for x in cmd), flush=True)
    subprocess.run(cmd, cwd=str(ROOT), check=True)


def resolve_workdir(value: str | Path) -> Path:
    raw = Path(value)
    candidates = [raw, ROOT / raw, EXPERIMENTS / raw]
    for p in candidates:
        if p.exists():
            return p.resolve()
    known = ", ".join(sorted(p.name for p in EXPERIMENTS.iterdir() if p.is_dir()))
    raise SystemExit(f"Cannot find workdir/experiment: {value}\nKnown experiments: {known}")


def score_one(workdir_arg: str | Path, *, force_eval: bool, skip_eval: bool, script_check: str) -> Path:
    workdir = resolve_workdir(workdir_arg)
    cmd = [
        sys.executable, str(SCRIPTS / "score_qgis.py"),
        "--workdir", str(workdir),
        "--outputs-dir", str(workdir / "outputs_ts"),
        "--eval-script", str(SCRIPTS / "evaluate_all.py"),
        "--script-check", script_check,
    ]
    if force_eval:
        cmd.append("--force-eval")
    if skip_eval:
        cmd.append("--skip-eval")
    run(cmd)
    return workdir


def compare_suite(suite: str, outdir: Optional[Path] = None) -> Path:
    if suite not in SUITES:
        raise SystemExit("Unknown suite: " + suite)
    outdir = (outdir or (RESULTS / suite.replace("-", "_"))).resolve()
    exps = [f"{label}={resolve_workdir(name)}" for label, name in SUITES[suite].items()]
    run([sys.executable, str(SCRIPTS / "compare_qgis.py"), "--experiments", *exps, "--outdir", str(outdir)])
    return outdir


def preview_report(outdir: Path, max_lines: int = 100) -> None:
    report = outdir / "model_comparison_report.md"
    if report.exists():
        print("\n=== model_comparison_report.md ===\n")
        lines = report.read_text(encoding="utf-8").splitlines()
        print("\n".join(lines[:max_lines]))
        if len(lines) > max_lines:
            print("\n... console preview truncated; open the file for full report.")
    print("\nResult directory:", rel(outdir))


def cmd_quickstart(args: argparse.Namespace) -> None:
    print("Quickstart: rerun deterministic scoring on the bundled 100-segment demo. No API calls.")
    score_one("demo_ablation_grok_100", force_eval=True, skip_eval=False, script_check=args.script_check)
    outdir = compare_suite("demo", RESULTS / "demo_quickstart")
    preview_report(outdir)


def cmd_score(args: argparse.Namespace) -> None:
    workdir = score_one(args.experiment, force_eval=args.force_eval, skip_eval=args.use_existing_eval, script_check=args.script_check)
    print("\nStatistics directory:", rel(workdir / "statistics"))
    report = workdir / "statistics" / "ablation_statistics_report.md"
    if report.exists():
        print("\n=== ablation_statistics_report.md preview ===\n")
        print("\n".join(report.read_text(encoding="utf-8").splitlines()[:80]))


def cmd_compare(args: argparse.Namespace) -> None:
    outdir = compare_suite(args.suite, args.outdir)
    preview_report(outdir)


def cmd_translate(args: argparse.Namespace) -> None:
    provider = args.provider.lower()
    if provider == "grok" and not (args.api_key or os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY")):
        raise SystemExit("Missing Grok/xAI key. Set XAI_API_KEY or pass --api-key. Quickstart does not require keys.")
    if provider == "gemini" and not (args.api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        raise SystemExit("Missing Gemini key. Set GEMINI_API_KEY or GOOGLE_API_KEY, or pass --api-key. Quickstart does not require keys.")
    model_id = args.model_id or DEFAULT_MODELS[provider]
    workdir = args.workdir or (ROOT / "runs" / f"smoke_{provider}_{args.sample_size}_{args.conditions.replace(',', '-')}")
    cmd = [
        sys.executable, str(SCRIPTS / "run_qgis.py"),
        "--input", str(DATA_TS),
        "--provider", provider,
        "--model-id", model_id,
        "--glossary", *(str(p) for p in GLOSSARY),
        "--workdir", str(workdir),
        "--sample-size", str(args.sample_size),
        "--subset-mode", args.subset_mode,
        "--seed", str(args.seed),
        "--conditions", args.conditions,
        "--api-parallelism", str(args.api_parallelism),
        "--rpm-limit", str(args.rpm_limit),
    ]
    if args.api_key:
        cmd += ["--api-key", args.api_key]
    if args.run_eval:
        cmd += ["--run-eval", "--eval-script", str(SCRIPTS / "evaluate_all.py")]
    run(cmd)
    print("\nGenerated workdir:", rel(workdir))
    print("To score it:")
    print(f"  {sys.executable} scripts/run_repro.py score --experiment {rel(workdir)} --force-eval")



def cmd_mqm(args: argparse.Namespace) -> None:
    """Build or run a small MQM-style judge evaluation."""
    workdir = resolve_workdir(args.experiment)
    outdir = (args.outdir or (workdir / "mqm_quality_grokjudge_mini")).resolve()
    cmd = [
        sys.executable, str(SCRIPTS / "mqm_qgis_evaluator.py"),
        "--ts-dir", str(workdir / "outputs_ts"),
        "--outdir", str(outdir),
        "--glossary", *(str(p) for p in GLOSSARY),
        "--sample-size", str(args.sample_size),
        "--repeats", str(args.repeats),
        "--seed", str(args.seed),
        "--sampling-mode", args.sampling_mode,
        "--max-workers", str(args.max_workers),
        "--rpm-limit", str(args.rpm_limit),
    ]
    if args.total_request_budget:
        cmd += ["--total-request-budget", str(args.total_request_budget)]
    if args.run_grok:
        if not (args.api_key or os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY")):
            raise SystemExit("Missing Grok/xAI judge key. Set XAI_API_KEY or pass --api-key. Omit --run-grok to generate requests only.")
        cmd.append("--run-grok")
    if args.api_key:
        cmd += ["--api-key", args.api_key]
    run(cmd)
    print("\nMQM output directory:", rel(outdir))
    report = outdir / "mqm_report.md"
    if report.exists():
        print("\n=== mqm_report.md preview ===\n")
        print("\n".join(report.read_text(encoding="utf-8").splitlines()[:80]))
    else:
        print("Generated MQM request plan. Add --run-grok and XAI_API_KEY to call the judge model.")


def cmd_full_mini(args: argparse.Namespace) -> None:
    """Run the complete offline mini path: deterministic scoring, comparison, and MQM request generation."""
    print("Mini full reproduction: score the bundled 100-segment C0-C4 demo, compare conditions, and create an MQM judge request plan. No API calls by default.")
    score_one("demo_ablation_grok_100", force_eval=True, skip_eval=False, script_check=args.script_check)
    outdir = compare_suite("demo", RESULTS / "mini_full_compare")
    preview_report(outdir)
    # Generate a tiny MQM request plan without external API calls.
    ns = argparse.Namespace(
        experiment="demo_ablation_grok_100",
        outdir=RESULTS / "mini_mqm_plan",
        sample_size=args.mqm_sample_size,
        repeats=1,
        seed=42,
        sampling_mode="mixed",
        max_workers=1,
        rpm_limit=30,
        total_request_budget=args.mqm_budget,
        run_grok=False,
        api_key="",
    )
    cmd_mqm(ns)

def cmd_list(_: argparse.Namespace) -> None:
    print("Suites:")
    for suite, mapping in SUITES.items():
        print("  " + suite + ": " + ", ".join(f"{k}={v}" for k, v in mapping.items()))
    print("\nExperiments:")
    for p in sorted(EXPERIMENTS.iterdir()):
        if p.is_dir():
            outputs = len(list((p / "outputs_ts").glob("*.ts"))) if (p / "outputs_ts").exists() else 0
            stats = "yes" if (p / "statistics" / "condition_summary.csv").exists() else "no"
            print(f"  {p.name:30s} outputs_ts={outputs} statistics={stats}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="QGIS translation reproducibility workflow")
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("quickstart", help="Default small offline reproduction: score 100 segments and print analysis.")
    q.add_argument("--script-check", choices=["none", "unihan", "opencc", "both"], default="none")
    q.set_defaults(func=cmd_quickstart)

    s = sub.add_parser("score", help="Score one experiment/workdir.")
    s.add_argument("--experiment", default="demo_ablation_grok_100", help="Experiment name under experiments/ or a path such as runs/smoke_grok_20_C1.")
    s.add_argument("--force-eval", action="store_true", help="Rerun deterministic evaluator before summarizing.")
    s.add_argument("--use-existing-eval", action="store_true", help="Only summarize existing evaluation files; do not run evaluator.")
    s.add_argument("--script-check", choices=["none", "unihan", "opencc", "both"], default="none")
    s.set_defaults(func=cmd_score)

    c = sub.add_parser("compare", help="Compare demo or paper suites.")
    c.add_argument("--suite", choices=sorted(SUITES), default="demo")
    c.add_argument("--outdir", type=Path, default=None)
    c.set_defaults(func=cmd_compare)

    t = sub.add_parser("translate", help="Optional API translation smoke run. Defaults are intentionally tiny.")
    t.add_argument("--provider", choices=["grok", "gemini"], default="grok")
    t.add_argument("--model-id", default="")
    t.add_argument("--sample-size", type=int, default=20)
    t.add_argument("--conditions", default="C1")
    t.add_argument("--subset-mode", choices=["stratified", "random", "first"], default="stratified")
    t.add_argument("--seed", type=int, default=42)
    t.add_argument("--api-parallelism", type=int, default=1)
    t.add_argument("--rpm-limit", type=int, default=30)
    t.add_argument("--api-key", default="")
    t.add_argument("--workdir", type=Path, default=None)
    t.add_argument("--run-eval", action="store_true")
    t.set_defaults(func=cmd_translate)


    m = sub.add_parser("mqm", help="Build or optionally run a small MQM-style judge evaluation on an experiment.")
    m.add_argument("--experiment", default="demo_ablation_grok_100")
    m.add_argument("--outdir", type=Path, default=None)
    m.add_argument("--sample-size", type=int, default=1, help="Segments sampled per .ts file per repeat. Small by default.")
    m.add_argument("--total-request-budget", type=int, default=0, help="Optional total request budget across all files/repeats.")
    m.add_argument("--repeats", type=int, default=1)
    m.add_argument("--seed", type=int, default=42)
    m.add_argument("--sampling-mode", choices=["random", "issue_enriched", "mixed"], default="mixed")
    m.add_argument("--max-workers", type=int, default=1)
    m.add_argument("--rpm-limit", type=int, default=30)
    m.add_argument("--run-grok", action="store_true", help="Actually call the Grok judge. Without this, only requests are generated.")
    m.add_argument("--api-key", default="")
    m.set_defaults(func=cmd_mqm)

    fm = sub.add_parser("full-mini", help="Run the complete mini reproduction path: scoring + comparison + MQM request plan. No API calls.")
    fm.add_argument("--script-check", choices=["none", "unihan", "opencc", "both"], default="none")
    fm.add_argument("--mqm-sample-size", type=int, default=1)
    fm.add_argument("--mqm-budget", type=int, default=5)
    fm.set_defaults(func=cmd_full_mini)

    l = sub.add_parser("list", help="List included experiments and suites.")
    l.set_defaults(func=cmd_list)
    return p


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
