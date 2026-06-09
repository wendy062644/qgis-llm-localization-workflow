#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Merge and compare QGIS C0-C4 ablation/full-corpus experiment outputs from
Grok, Gemini, and local LLM workdirs.

Expected input workdirs are produced by run_qgis.py or run_qgis_local_llm.py and
scored by score_qgis.py.  Each workdir should contain:

  statistics/condition_summary.csv
  statistics/structure_items_long.csv
  statistics/deterministic_issue_counts_long.csv

Optional MQM files are also merged when present:

  mqm_eval/all_ts_mqm_scores.csv
  evaluation/all_ts_mqm_scores.csv

Example:
  python compare_qgis_experiments.py \
    --experiments grok=paper_ablation_grok_3000 gemini=paper_ablation_gemini_3000 taide=paper_ablation_taide_3000 \
    --outdir paper_compare_ablation

If a workdir has no statistics yet, add --score-missing:
  python compare_qgis_experiments.py \
    --experiments grok=paper_ablation_grok_3000 gemini=paper_ablation_gemini_3000 taide=paper_ablation_taide_3000 \
    --outdir paper_compare_ablation --score-missing --score-script score_qgis.py --eval-script evaluate_all.py
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


PREFERRED_COLUMNS = [
    "model",
    "experiment_label",
    "condition_id",
    "condition_slug",
    "mask",
    "ods",
    "num_candidates",
    "messages_checked",
    "message_count",
    "structure_average_score_0_100",
    "structure_failed_unique_segments",
    "structure_failed_rate_pct",
    "structure_failed_rate_per_1000",
    "qt_placeholder_affected_segments",
    "qt_placeholder_affected_rate_pct",
    "brace_placeholder_affected_segments",
    "printf_placeholder_affected_segments",
    "html_xml_entity_affected_segments",
    "html_xml_tag_affected_segments",
    "number_affected_segments",
    "newline_affected_segments",
    "newline_affected_rate_pct",
    "accelerator_affected_segments",
    "accelerator_affected_rate_pct",
    "deterministic_score_0_100",
    "deterministic_error_points_per_1000_source_chars",
    "safe_fallback_count",
    "format_safe_accept_count",
    "avg_valid_candidate_count",
    "rows_no_valid_candidates",
    "rows_no_valid_candidates_rate_pct",
    "candidate_structure_issue_count",
    "candidate_structure_issue_rate_pct",
    "possibly_untranslated_count",
    "possibly_untranslated_unique_segments",
    "high_english_residue_count",
    "glossary_target_missing_count",
    "forbidden_term_count",
]

ISSUE_TYPES_FOR_PAPER = [
    "possibly_untranslated",
    "bilingual_residue",
    "high_english_residue",
    "glossary_target_missing",
    "forbidden_term",
    "unfinished_translation",
    "empty_translation",
    "missing_qt_placeholder",
    "extra_qt_placeholder",
    "newline_count_mismatch",
    "accelerator_count_mismatch",
]

STRUCTURE_ITEMS = [
    "qt_placeholder",
    "brace_placeholder",
    "printf_placeholder",
    "html_xml_entity",
    "html_xml_tag",
    "number",
    "newline",
    "accelerator",
]


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv_rows(path: Path, rows: Sequence[Dict[str, Any]], fieldnames: Optional[Sequence[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        keys: List[str] = []
        seen = set()
        for row in rows:
            for k in row.keys():
                if k not in seen:
                    seen.add(k)
                    keys.append(k)
        fieldnames = keys
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: csv_value(row.get(k, "")) for k in fieldnames})


def csv_value(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    return v


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def to_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or v == "":
            return default
        return int(float(v))
    except Exception:
        return default


def parse_experiment_arg(arg: str) -> Tuple[str, Path]:
    if "=" not in arg:
        path = Path(arg)
        return path.name, path
    label, path = arg.split("=", 1)
    label = label.strip()
    if not label:
        raise ValueError(f"Invalid experiment label in {arg!r}")
    return label, Path(path.strip())


def run_score_if_needed(label: str, workdir: Path, args: argparse.Namespace) -> None:
    stats_csv = workdir / "statistics" / "condition_summary.csv"
    if stats_csv.exists() and not args.force_score:
        return
    if not args.score_missing and not args.force_score:
        return
    if not args.score_script:
        raise FileNotFoundError(f"{label}: statistics missing and --score-script was not provided")
    cmd = [sys.executable, str(args.score_script), "--workdir", str(workdir), "--eval-script", str(args.eval_script or "evaluate_all.py")]
    if args.force_score:
        cmd.append("--force-eval")
    print("[score]", label, " ".join(cmd), file=sys.stderr)
    proc = subprocess.run(cmd, text=True, capture_output=True)
    score_log_dir = args.outdir / "score_logs"
    score_log_dir.mkdir(parents=True, exist_ok=True)
    (score_log_dir / f"{label}_stdout.txt").write_text(proc.stdout or "", encoding="utf-8")
    (score_log_dir / f"{label}_stderr.txt").write_text(proc.stderr or "", encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"score_qgis failed for {label} with exit code {proc.returncode}. See {score_log_dir}")


def load_condition_summary(label: str, workdir: Path) -> List[Dict[str, Any]]:
    path = workdir / "statistics" / "condition_summary.csv"
    rows = read_csv_rows(path)
    out: List[Dict[str, Any]] = []
    for row in rows:
        r: Dict[str, Any] = {"model": label, "experiment_label": label, "workdir": str(workdir)}
        r.update(row)
        out.append(r)
    return out


def load_structure_items(label: str, workdir: Path) -> List[Dict[str, Any]]:
    path = workdir / "statistics" / "structure_items_long.csv"
    rows = read_csv_rows(path)
    out: List[Dict[str, Any]] = []
    for row in rows:
        r: Dict[str, Any] = {"model": label, "experiment_label": label, "workdir": str(workdir)}
        r.update(row)
        out.append(r)
    return out


def load_issue_counts(label: str, workdir: Path) -> List[Dict[str, Any]]:
    path = workdir / "statistics" / "deterministic_issue_counts_long.csv"
    rows = read_csv_rows(path)
    out: List[Dict[str, Any]] = []
    for row in rows:
        r: Dict[str, Any] = {"model": label, "experiment_label": label, "workdir": str(workdir)}
        r.update(row)
        out.append(r)
    return out


def load_mqm_scores(label: str, workdir: Path) -> List[Dict[str, Any]]:
    candidates = [
        workdir / "mqm_eval" / "all_ts_mqm_scores.csv",
        workdir / "evaluation" / "all_ts_mqm_scores.csv",
    ]
    rows: List[Dict[str, Any]] = []
    for path in candidates:
        if not path.exists():
            continue
        for row in read_csv_rows(path):
            r: Dict[str, Any] = {"model": label, "experiment_label": label, "workdir": str(workdir), "mqm_source_csv": str(path)}
            r.update(row)
            # Infer condition from filename when possible.
            # The source CSV may contain Windows-style absolute paths even on Linux/macOS.
            ts_file = str(row.get("ts_file", ""))
            filename = PureWindowsPath(ts_file).name if "\\" in ts_file else Path(ts_file).name
            name = Path(filename).stem
            m = re.match(r"^(C\d+)_", name)
            if m:
                r["condition_id"] = m.group(1)
                r["condition_slug"] = name
            rows.append(r)
        break
    return rows


def ordered_fields(rows: Sequence[Dict[str, Any]], preferred: Sequence[str]) -> List[str]:
    fields: List[str] = []
    seen = set()
    for k in preferred:
        if any(k in r for r in rows):
            fields.append(k)
            seen.add(k)
    for r in rows:
        for k in r.keys():
            if k not in seen:
                fields.append(k)
                seen.add(k)
    return fields


def make_ablation_table(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({k: r.get(k, "") for k in PREFERRED_COLUMNS if k in r})
    return out


def make_c1_table(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    c1 = [r for r in rows if str(r.get("condition_id", "")) == "C1"]
    fields = [
        "model", "messages_checked", "message_count", "structure_average_score_0_100",
        "structure_failed_unique_segments", "structure_failed_rate_pct", "deterministic_score_0_100",
        "safe_fallback_count", "format_safe_accept_count", "avg_valid_candidate_count", "rows_no_valid_candidates",
        "possibly_untranslated_count", "glossary_target_missing_count", "forbidden_term_count",
        "workdir",
    ]
    return [{k: r.get(k, "") for k in fields if k in r} for r in c1]


def make_structure_pivot(item_rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in item_rows:
        key = (str(r.get("model", "")), str(r.get("condition_id", "")))
        row = grouped.setdefault(key, {"model": key[0], "condition_id": key[1], "condition_slug": r.get("condition_slug", ""), "messages_checked": r.get("checked_messages", "")})
        item = str(r.get("item", ""))
        if item:
            row[f"{item}_affected_segments"] = r.get("affected_segment_count", "")
            row[f"{item}_affected_rate_pct"] = r.get("affected_rate_pct", "")
            row[f"{item}_score_0_100"] = r.get("score_0_100", "")
    return [grouped[k] for k in sorted(grouped.keys(), key=lambda x: (x[0], x[1]))]


def make_issue_pivot(issue_rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in issue_rows:
        key = (str(r.get("model", "")), str(r.get("condition_id", "")))
        row = grouped.setdefault(key, {"model": key[0], "condition_id": key[1], "condition_slug": r.get("condition_slug", ""), "checked_messages": r.get("checked_messages", "")})
        it = str(r.get("issue_type", ""))
        if it in ISSUE_TYPES_FOR_PAPER:
            row[f"{it}_issue_count"] = r.get("issue_count", "")
            row[f"{it}_unique_segment_count"] = r.get("unique_segment_count", "")
            row[f"{it}_unique_segment_rate_pct"] = r.get("unique_segment_rate_pct", "")
    return [grouped[k] for k in sorted(grouped.keys(), key=lambda x: (x[0], x[1]))]


def make_mqm_table(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    fields = [
        "model", "condition_id", "condition_slug", "score_status", "message_count",
        "mqm_average_primary_score_0_100", "mqm_primary_score_stddev", "mqm_primary_score_95ci_half_width",
        "mqm_average_error_rate_per_1000_source_chars", "mqm_run_count_scored", "mqm_run_count_requested",
        "ts_file", "workdir",
    ]
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({k: r.get(k, "") for k in fields if k in r})
    return out


def markdown_table(rows: Sequence[Dict[str, Any]], columns: Sequence[str], max_rows: int = 30) -> str:
    rows = list(rows)[:max_rows]
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join(["---"] + ["---:"] * (len(columns) - 1)) + "|"]
    for r in rows:
        vals = []
        for c in columns:
            v = r.get(c, "")
            vals.append(str(v).replace("|", "\\|"))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def write_report(path: Path, ablation_rows: Sequence[Dict[str, Any]], c1_rows: Sequence[Dict[str, Any]], mqm_rows: Sequence[Dict[str, Any]], out_files: Dict[str, str]) -> None:
    lines: List[str] = []
    lines.append("# QGIS model comparison report")
    lines.append("")
    lines.append("## C1 full-system model comparison")
    lines.append("")
    c1_cols = ["model", "messages_checked", "structure_average_score_0_100", "structure_failed_rate_pct", "deterministic_score_0_100", "safe_fallback_count", "avg_valid_candidate_count", "glossary_target_missing_count"]
    lines.append(markdown_table(c1_rows, [c for c in c1_cols if any(c in r for r in c1_rows)], max_rows=50))
    lines.append("")
    lines.append("## Ablation summary")
    lines.append("")
    abl_cols = ["model", "condition_id", "mask", "ods", "num_candidates", "structure_average_score_0_100", "structure_failed_rate_pct", "deterministic_score_0_100", "safe_fallback_count", "rows_no_valid_candidates"]
    lines.append(markdown_table(ablation_rows, [c for c in abl_cols if any(c in r for r in ablation_rows)], max_rows=80))
    if mqm_rows:
        lines.append("")
        lines.append("## MQM / LLM-judge summary")
        lines.append("")
        mqm_cols = ["model", "condition_id", "mqm_average_primary_score_0_100", "mqm_primary_score_95ci_half_width", "mqm_average_error_rate_per_1000_source_chars", "mqm_run_count_scored"]
        lines.append(markdown_table(mqm_rows, [c for c in mqm_cols if any(c in r for r in mqm_rows)], max_rows=80))
    lines.append("")
    lines.append("## Output files")
    lines.append("")
    for label, f in out_files.items():
        lines.append(f"- `{label}`: `{f}`")
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)

    experiments = [parse_experiment_arg(x) for x in args.experiments]
    all_condition_rows: List[Dict[str, Any]] = []
    all_item_rows: List[Dict[str, Any]] = []
    all_issue_rows: List[Dict[str, Any]] = []
    all_mqm_rows: List[Dict[str, Any]] = []

    for label, workdir in experiments:
        workdir = workdir.resolve()
        if not workdir.exists():
            raise FileNotFoundError(f"Experiment workdir not found for {label}: {workdir}")
        run_score_if_needed(label, workdir, args)
        summary_path = workdir / "statistics" / "condition_summary.csv"
        if not summary_path.exists():
            raise FileNotFoundError(f"Missing statistics/condition_summary.csv for {label}: {summary_path}")
        all_condition_rows.extend(load_condition_summary(label, workdir))
        all_item_rows.extend(load_structure_items(label, workdir))
        all_issue_rows.extend(load_issue_counts(label, workdir))
        all_mqm_rows.extend(load_mqm_scores(label, workdir))

    ablation_rows = make_ablation_table(all_condition_rows)
    c1_rows = make_c1_table(all_condition_rows)
    structure_pivot = make_structure_pivot(all_item_rows)
    issue_pivot = make_issue_pivot(all_issue_rows)
    mqm_rows = make_mqm_table(all_mqm_rows)

    files = {
        "model_condition_summary.csv": str(args.outdir / "model_condition_summary.csv"),
        "paper_table_ablation.csv": str(args.outdir / "paper_table_ablation.csv"),
        "paper_table_c1_model_comparison.csv": str(args.outdir / "paper_table_c1_model_comparison.csv"),
        "paper_table_structure_items.csv": str(args.outdir / "paper_table_structure_items.csv"),
        "paper_table_deterministic_issues.csv": str(args.outdir / "paper_table_deterministic_issues.csv"),
        "mqm_scores_merged.csv": str(args.outdir / "mqm_scores_merged.csv"),
        "paper_table_mqm.csv": str(args.outdir / "paper_table_mqm.csv"),
        "model_comparison_report.md": str(args.outdir / "model_comparison_report.md"),
        "comparison_summary.json": str(args.outdir / "comparison_summary.json"),
    }

    write_csv_rows(Path(files["model_condition_summary.csv"]), all_condition_rows, ordered_fields(all_condition_rows, PREFERRED_COLUMNS + ["workdir"]))
    write_csv_rows(Path(files["paper_table_ablation.csv"]), ablation_rows, ordered_fields(ablation_rows, PREFERRED_COLUMNS))
    write_csv_rows(Path(files["paper_table_c1_model_comparison.csv"]), c1_rows)
    write_csv_rows(Path(files["paper_table_structure_items.csv"]), structure_pivot)
    write_csv_rows(Path(files["paper_table_deterministic_issues.csv"]), issue_pivot)
    write_csv_rows(Path(files["mqm_scores_merged.csv"]), all_mqm_rows)
    write_csv_rows(Path(files["paper_table_mqm.csv"]), mqm_rows)

    summary = {
        "experiments": [{"label": label, "workdir": str(path.resolve())} for label, path in experiments],
        "condition_row_count": len(all_condition_rows),
        "structure_item_row_count": len(all_item_rows),
        "issue_row_count": len(all_issue_rows),
        "mqm_row_count": len(all_mqm_rows),
        "output_files": files,
        "notes": [
            "paper_table_ablation.csv is suitable for C0-C4 ablation comparison.",
            "paper_table_c1_model_comparison.csv compares the full system condition across models.",
            "paper_table_structure_items.csv breaks down format failures by structure item.",
            "paper_table_deterministic_issues.csv pivots selected deterministic content/terminology issues.",
            "paper_table_mqm.csv is populated only when mqm_eval/all_ts_mqm_scores.csv or evaluation/all_ts_mqm_scores.csv exists.",
        ],
    }
    write_json(Path(files["comparison_summary.json"]), summary)
    write_report(Path(files["model_comparison_report.md"]), ablation_rows, c1_rows, mqm_rows, files)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare QGIS Grok/Gemini/local LLM experiment statistics.")
    p.add_argument("--experiments", nargs="+", required=True, help="List of label=workdir entries, e.g. grok=paper_ablation_grok_3000 gemini=paper_ablation_gemini_3000 taide=paper_ablation_taide_3000")
    p.add_argument("--outdir", type=Path, default=Path("paper_compare_models"))
    p.add_argument("--score-missing", action="store_true", help="Run score_qgis.py for workdirs whose statistics are missing.")
    p.add_argument("--force-score", action="store_true", help="Force rerun score_qgis.py for every workdir before comparing.")
    p.add_argument("--score-script", type=Path, default=None, help="Path to score_qgis.py when using --score-missing/--force-score.")
    p.add_argument("--eval-script", type=Path, default=None, help="Path to evaluate_all.py passed to score_qgis.py.")
    return p.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
