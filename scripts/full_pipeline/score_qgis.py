#!/usr/bin/env python3
"""
Score and summarize QGIS/Qt .ts C0-C4 ablation workflow outputs.

Run this AFTER run_qgis_ablation_workflow.py finishes.  It will:

1. Locate the ablation workdir, e.g. ablation_grok_3000/.
2. Locate outputs_ts/*.ts generated for C0-C4.
3. Run evaluate_all_structure100.py in batch mode, unless evaluation already exists.
4. Parse evaluator outputs and condition translation logs.
5. Write CSV/JSON/Markdown summaries, including:
   - structure score per condition
   - structure-failed segment counts and rates
   - structure item affected counts and rates
   - deterministic issue counts
   - candidate-selection / fallback / repair statistics
   - C1-vs-other-condition comparison table

Typical usage:

  python score_qgis_ablation_results.py ^
    --workdir ablation_grok_3000 ^
    --eval-script evaluate_all_structure100.py

Force re-evaluation:

  python score_qgis_ablation_results.py ^
    --workdir ablation_grok_3000 ^
    --eval-script evaluate_all_structure100.py ^
    --force-eval

Skip evaluator and only summarize existing evaluation files:

  python score_qgis_ablation_results.py ^
    --workdir ablation_grok_3000 ^
    --skip-eval
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


STRUCTURE_GROUPS: Dict[str, set[str]] = {
    "qt_placeholder": {"missing_qt_placeholder", "extra_qt_placeholder"},
    "brace_placeholder": {"missing_brace_placeholder", "extra_brace_placeholder"},
    "printf_placeholder": {"missing_printf_placeholder", "extra_printf_placeholder"},
    "html_xml_entity": {"missing_html_xml_entity", "extra_html_xml_entity"},
    "html_xml_tag": {"missing_html_xml_tag", "extra_html_xml_tag"},
    "number": {"missing_number", "extra_number"},
    "newline": {"newline_count_mismatch"},
    "accelerator": {"accelerator_count_mismatch"},
}

STRUCTURE_GROUP_ORDER = [
    "qt_placeholder",
    "brace_placeholder",
    "printf_placeholder",
    "html_xml_entity",
    "html_xml_tag",
    "number",
    "newline",
    "accelerator",
]

COMPLETION_STATE_ISSUES = {
    "unfinished_translation",
    "empty_translation",
    "missing_translation_element",
}

CONTENT_OR_TERMINOLOGY_ISSUES = {
    "possibly_untranslated",
    "bilingual_residue",
    "high_english_residue",
    "script_variant_risk",
    "glossary_target_missing",
    "forbidden_term",
    "ts_language_mismatch",
}

IMPORTANT_CONTENT_ISSUES = [
    "possibly_untranslated",
    "bilingual_residue",
    "high_english_residue",
    "glossary_target_missing",
    "forbidden_term",
    "unfinished_translation",
    "empty_translation",
    "missing_translation_element",
]


# -----------------------------------------------------------------------------
# Generic file utilities
# -----------------------------------------------------------------------------
def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


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
            for key in row.keys():
                if key not in seen:
                    seen.add(key)
                    keys.append(key)
        fieldnames = keys
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _csv_value(row.get(k, "")) for k in fieldnames})


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return []
    def _iter() -> Iterable[Dict[str, Any]]:
        with path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        yield obj
                except Exception:
                    yield {"_json_parse_error": f"line {line_no}"}
    return _iter()


def to_float(value: Any, default: float = float("nan")) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    return to_int(value, default)


def safe_rate(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def pct(value: float) -> float:
    if value != value:  # NaN
        return value
    return round(value * 100.0, 4)


def wilson_ci(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4.0 * n)) / n) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


# -----------------------------------------------------------------------------
# Experiment discovery
# -----------------------------------------------------------------------------
def resolve_eval_script(requested: Optional[Path], workdir: Path) -> Optional[Path]:
    candidates: List[Path] = []
    if requested:
        candidates.append(requested)
    script_dir = Path(__file__).resolve().parent
    candidates.extend([
        Path.cwd() / "evaluate_all.py",
        Path.cwd() / "evaluate_all_structure100.py",
        Path.cwd() / "evaluate_all_with_structure_failures.py",
        workdir / "evaluate_all.py",
        workdir / "evaluate_all_structure100.py",
        workdir.parent / "evaluate_all.py",
        workdir.parent / "evaluate_all_structure100.py",
        script_dir / "evaluate_all.py",
        script_dir / "evaluate_all_structure100.py",
        Path("/mnt/data/evaluate_all.py"),
        Path("/mnt/data/evaluate_all_structure100.py"),
    ])
    for p in candidates:
        if p and p.exists():
            return p.resolve()
    return None


def condition_id_sort_key(condition_id: str) -> Tuple[int, str]:
    m = re.search(r"C(\d+)", condition_id or "")
    if m:
        return int(m.group(1)), condition_id
    return 9999, condition_id


def load_workflow_context(workdir: Path) -> Dict[str, Any]:
    manifest = read_json(workdir / "workflow_manifest.json", default={}) or {}
    partial = read_json(workdir / "condition_summaries_partial.json", default=[]) or []
    subset_summary = read_json(workdir / "subset" / "subset_summary.json", default={}) or {}
    if not subset_summary and isinstance(manifest, dict):
        subset_summary = manifest.get("subset", {}) or {}

    conditions: Dict[str, Dict[str, Any]] = {}
    for cond in (manifest.get("conditions", []) if isinstance(manifest, dict) else []):
        if isinstance(cond, dict) and cond.get("slug"):
            conditions[str(cond["slug"])] = cond

    condition_summaries = manifest.get("condition_summaries") if isinstance(manifest, dict) else None
    if not condition_summaries:
        condition_summaries = partial
    condition_summaries = condition_summaries or []
    for entry in condition_summaries:
        if not isinstance(entry, dict):
            continue
        cond = entry.get("condition") or {}
        if isinstance(cond, dict) and cond.get("slug"):
            conditions[str(cond["slug"])] = cond

    outputs_dir = Path(manifest.get("outputs_dir") or workdir / "outputs_ts") if isinstance(manifest, dict) else workdir / "outputs_ts"
    if not outputs_dir.is_absolute():
        outputs_dir = (workdir / outputs_dir).resolve() if not outputs_dir.exists() else outputs_dir.resolve()

    return {
        "manifest": manifest,
        "condition_summaries": condition_summaries,
        "conditions": conditions,
        "subset_summary": subset_summary,
        "outputs_dir": outputs_dir,
    }


def selected_message_count(workdir: Path, context: Dict[str, Any]) -> int:
    subset_summary = context.get("subset_summary", {}) or {}
    for key in ["selected_messages", "message_count", "sampling_frame_size"]:
        if subset_summary.get(key) not in (None, ""):
            n = to_int(subset_summary.get(key), 0)
            if n > 0:
                return n
    selected_csv = workdir / "subset" / "selected_segments.csv"
    if selected_csv.exists():
        rows = read_csv_rows(selected_csv)
        if rows:
            return len(rows)
    outputs_dir = context.get("outputs_dir", workdir / "outputs_ts")
    ts_files = sorted(Path(outputs_dir).glob("*.ts"))
    if ts_files:
        try:
            import xml.etree.ElementTree as ET
            root = ET.parse(ts_files[0]).getroot()
            return len(root.findall(".//message"))
        except Exception:
            pass
    return 10000


def discover_ts_outputs(outputs_dir: Path) -> List[Path]:
    return sorted(p for p in outputs_dir.glob("*.ts") if p.is_file())


# -----------------------------------------------------------------------------
# Evaluator runner
# -----------------------------------------------------------------------------
def evaluation_csv_candidates(eval_outdir: Path, batch_summary_name: str) -> List[Path]:
    return [
        eval_outdir / f"{batch_summary_name}.csv",
        eval_outdir / "all_ts_mqm_scores.csv",
    ] + sorted(eval_outdir.glob("*_mqm_scores.csv"))


def find_existing_evaluation_csv(eval_outdir: Path, batch_summary_name: str) -> Optional[Path]:
    for path in evaluation_csv_candidates(eval_outdir, batch_summary_name):
        if path.exists():
            return path
    return None


def run_evaluator(
    python_exe: str,
    eval_script: Path,
    outputs_dir: Path,
    eval_outdir: Path,
    structure_score_limit: int,
    batch_summary_name: str,
    script_check: str,
    force: bool,
    skip_eval: bool,
) -> Dict[str, Any]:
    eval_outdir.mkdir(parents=True, exist_ok=True)
    existing_csv = find_existing_evaluation_csv(eval_outdir, batch_summary_name)
    if skip_eval:
        return {
            "ran": False,
            "skipped": True,
            "reason": "--skip-eval",
            "csv": str(existing_csv) if existing_csv else "",
        }
    if existing_csv and not force:
        return {
            "ran": False,
            "skipped": True,
            "reason": "existing evaluation CSV found; use --force-eval to rerun",
            "csv": str(existing_csv),
        }

    if not eval_script.exists():
        raise FileNotFoundError(f"Evaluator script not found: {eval_script}")
    if not outputs_dir.exists():
        raise FileNotFoundError(f"outputs_ts directory not found: {outputs_dir}")

    cmd = [
        python_exe,
        str(eval_script),
        "--all-ts",
        "--ts-dir", str(outputs_dir),
        "--outdir", str(eval_outdir),
        "--script-check", script_check,
        "--sample-size", "0",
        "--repeats", "1",
        "--structure-score-limit", str(structure_score_limit),
        "--batch-summary-name", batch_summary_name,
    ]
    cmd_path = eval_outdir / "score_qgis_ablation_eval_command.txt"
    cmd_path.write_text(" ".join(cmd), encoding="utf-8")
    start = time.time()
    proc = subprocess.run(cmd, text=True, capture_output=True)
    (eval_outdir / "score_qgis_ablation_eval_stdout.txt").write_text(proc.stdout or "", encoding="utf-8")
    (eval_outdir / "score_qgis_ablation_eval_stderr.txt").write_text(proc.stderr or "", encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(
            f"Evaluator failed with exit code {proc.returncode}.\n"
            f"Command: {' '.join(cmd)}\n"
            f"See: {eval_outdir / 'score_qgis_ablation_eval_stderr.txt'}"
        )
    csv_path = find_existing_evaluation_csv(eval_outdir, batch_summary_name)
    return {
        "ran": True,
        "skipped": False,
        "seconds": round(time.time() - start, 3),
        "csv": str(csv_path) if csv_path else "",
        "command": cmd,
    }


# -----------------------------------------------------------------------------
# Parsing evaluator and translation outputs
# -----------------------------------------------------------------------------


def issue_in_structure_window(issue: Dict[str, str], checked_messages: int) -> bool:
    """Return True if an issue belongs to the first-N structure-score window."""
    idx = safe_int(issue.get("index"), 0)
    # File-level synthetic issues use index <= 0 and should not count as segment structure failures.
    return idx > 0 and idx <= max(1, int(checked_messages))

def issue_group(issue_type: str) -> str:
    for group, issue_types in STRUCTURE_GROUPS.items():
        if issue_type in issue_types:
            return group
    if issue_type in COMPLETION_STATE_ISSUES:
        return "completion_state"
    if issue_type in CONTENT_OR_TERMINOLOGY_ISSUES:
        return "content_or_terminology"
    if issue_type.startswith("missing_") or issue_type.startswith("extra_") or issue_type.endswith("_mismatch"):
        return "other_structure"
    return "other"


def has_structure_issue(issues: Sequence[Dict[str, Any]]) -> bool:
    for item in issues:
        it = str(item.get("issue_type", ""))
        if issue_group(it) in STRUCTURE_GROUPS or issue_group(it) == "other_structure":
            return True
    return False


def issues_by_type_and_unique_segments(rows: Sequence[Dict[str, str]]) -> Tuple[Counter, Counter, Dict[str, int]]:
    counts = Counter()
    unique_by_type: Dict[str, set[str]] = defaultdict(set)
    severity = Counter()
    for row in rows:
        it = row.get("issue_type", "")
        sid = row.get("segment_id", "")
        sev = row.get("severity", "")
        if it:
            counts[it] += 1
            if sid:
                unique_by_type[it].add(sid)
        if sev:
            severity[sev] += 1
    return counts, severity, {k: len(v) for k, v in unique_by_type.items()}


def parse_eval_rows(eval_outdir: Path, batch_summary_name: str) -> List[Dict[str, str]]:
    csv_path = find_existing_evaluation_csv(eval_outdir, batch_summary_name)
    if not csv_path:
        return []
    return read_csv_rows(csv_path)


def outdir_from_eval_row(row: Dict[str, str], eval_outdir: Path) -> Optional[Path]:
    raw = row.get("outdir", "")
    if not raw:
        return None
    p = Path(raw)
    if p.exists():
        return p
    # Try relative to evaluation root and workdir root.
    candidates = [eval_outdir / raw, eval_outdir.parent / raw]
    for c in candidates:
        if c.exists():
            return c
    return p


def item_rows_from_summary(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    det = summary.get("deterministic", {}) or {}
    struct = det.get("structure_scores", {}) or {}
    return list(struct.get("items", []) or [])


def item_map_from_summary(summary: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(row.get("item")): row for row in item_rows_from_summary(summary) if row.get("item")}


def structure_failed_unique_count(csv_rows: Sequence[Dict[str, str]]) -> int:
    return len({r.get("segment_id", "") for r in csv_rows if r.get("segment_id")})


def parse_translation_log(log_path: Path) -> Dict[str, Any]:
    status_counts = Counter()
    selected_candidate_counts = Counter()
    selected_index_counts = Counter()
    valid_candidate_counts = Counter()
    issue_counts = Counter()
    candidate_issue_counts = Counter()
    candidate_structure_issue_count = 0
    candidate_any_issue_count = 0
    candidate_total = 0
    log_rows = 0
    rows_with_candidates = 0
    rows_no_valid_candidates = 0
    rows_with_fallback_status = 0
    rows_with_structure_issue = 0
    api_call_count = 0
    selected_candidate_sum = 0
    selected_candidate_n = 0
    valid_candidate_sum = 0
    valid_candidate_n = 0

    for row in read_jsonl(log_path):
        log_rows += 1
        status = str(row.get("status") or "")
        meta = row.get("meta") or {}
        if not status and isinstance(meta, dict):
            status = str(meta.get("status") or "")
        if status:
            status_counts[status] += 1
            if "fallback" in status.lower():
                rows_with_fallback_status += 1
        if isinstance(meta, dict):
            api_call_count += to_int(meta.get("api_call_count"), 0)
            # Masked scripts use selected_index; raw no-mask uses selected_candidate_no.
            selected = meta.get("selected_candidate_no", meta.get("selected_index"))
            if selected is None and isinstance(meta.get("selection"), dict):
                selected = meta["selection"].get("selected")
            if selected is not None and selected != "":
                selected_candidate_counts[str(selected)] += 1
                selected_index_counts[str(selected)] += 1
                selected_candidate_sum += to_int(selected, 0)
                selected_candidate_n += 1
            selection = meta.get("selection") if isinstance(meta.get("selection"), dict) else {}
            if isinstance(selection, dict) and selection.get("valid_candidate_count") not in (None, ""):
                valid_count = to_int(selection.get("valid_candidate_count"), 0)
            else:
                valid_count = -1
            candidates = meta.get("candidates") if isinstance(meta.get("candidates"), list) else []
        else:
            candidates = []
            valid_count = -1

        row_issues = row.get("issues") if isinstance(row.get("issues"), list) else []
        if has_structure_issue(row_issues):
            rows_with_structure_issue += 1
        for issue in row_issues:
            issue_counts[str(issue.get("issue_type", "unknown"))] += 1

        if candidates:
            rows_with_candidates += 1
            candidate_total += len(candidates)
            computed_valid = 0
            for cand in candidates:
                if not isinstance(cand, dict):
                    continue
                c_issues = cand.get("issues") if isinstance(cand.get("issues"), list) else []
                if cand.get("blocking") is False or ("blocking" not in cand and not c_issues):
                    computed_valid += 1
                if c_issues:
                    candidate_any_issue_count += 1
                if has_structure_issue(c_issues):
                    candidate_structure_issue_count += 1
                for issue in c_issues:
                    candidate_issue_counts[str(issue.get("issue_type", "unknown"))] += 1
            if valid_count < 0:
                valid_count = computed_valid
            valid_candidate_counts[str(valid_count)] += 1
            valid_candidate_sum += valid_count
            valid_candidate_n += 1
            if valid_count == 0:
                rows_no_valid_candidates += 1

    return {
        "translation_log_rows": log_rows,
        "api_call_count_from_log": api_call_count,
        "status_counts_from_log": dict(status_counts),
        "selected_candidate_counts": dict(selected_candidate_counts),
        "valid_candidate_count_distribution": dict(valid_candidate_counts),
        "avg_selected_candidate_no": round(selected_candidate_sum / selected_candidate_n, 4) if selected_candidate_n else None,
        "rows_with_candidates": rows_with_candidates,
        "candidate_total": candidate_total,
        "avg_candidates_per_candidate_row": round(candidate_total / rows_with_candidates, 4) if rows_with_candidates else 0.0,
        "avg_valid_candidate_count": round(valid_candidate_sum / valid_candidate_n, 4) if valid_candidate_n else None,
        "rows_no_valid_candidates": rows_no_valid_candidates,
        "rows_no_valid_candidates_rate_pct": pct(safe_rate(rows_no_valid_candidates, rows_with_candidates)),
        "rows_with_fallback_status": rows_with_fallback_status,
        "rows_with_structure_issue_in_selected_output": rows_with_structure_issue,
        "selected_output_issue_counts_from_log": dict(issue_counts),
        "candidate_any_issue_count": candidate_any_issue_count,
        "candidate_structure_issue_count": candidate_structure_issue_count,
        "candidate_structure_issue_rate_pct": pct(safe_rate(candidate_structure_issue_count, candidate_total)),
        "candidate_issue_counts": dict(candidate_issue_counts),
    }


def load_condition_translation_summary(condition_dir: Path) -> Dict[str, Any]:
    candidates = [
        condition_dir / "translation_summary.json",
        condition_dir / "nomask_translation_summary.json",
    ] + sorted(condition_dir.glob("*translation_summary*.json"))
    for path in candidates:
        obj = read_json(path, default=None)
        if isinstance(obj, dict):
            obj = dict(obj)
            obj["_summary_path"] = str(path)
            return obj
    return {}


def condition_info_from_slug(slug: str, context: Dict[str, Any]) -> Dict[str, Any]:
    conditions = context.get("conditions", {}) or {}
    cond = dict(conditions.get(slug, {}) or {})
    if not cond:
        m = re.match(r"(C\d+)_", slug)
        cond = {"id": m.group(1) if m else slug, "slug": slug}
    cond.setdefault("id", slug.split("_", 1)[0])
    cond.setdefault("slug", slug)
    cond.setdefault("description", "")
    return cond


def condition_status_from_manifest(slug: str, context: Dict[str, Any]) -> Dict[str, Any]:
    for entry in context.get("condition_summaries", []) or []:
        if not isinstance(entry, dict):
            continue
        cond = entry.get("condition") or {}
        if isinstance(cond, dict) and cond.get("slug") == slug:
            return entry
    return {}


def collect_condition_result(
    row: Dict[str, str],
    eval_outdir: Path,
    workdir: Path,
    context: Dict[str, Any],
    checked_messages_default: int,
) -> Dict[str, Any]:
    ts_file = Path(row.get("ts_file", ""))
    slug = ts_file.stem
    cond = condition_info_from_slug(slug, context)
    condition_dir = workdir / "conditions" / slug
    manifest_status = condition_status_from_manifest(slug, context)
    evaluator_outdir = outdir_from_eval_row(row, eval_outdir)
    eval_summary = read_json((evaluator_outdir or Path("")) / "summary.json", default={}) or {}
    det = eval_summary.get("deterministic", {}) or {}
    struct = det.get("structure_scores", {}) or {}
    checked_messages = to_int(struct.get("checked_messages"), checked_messages_default)
    item_map = item_map_from_summary(eval_summary)

    failures_csv = None
    if eval_summary.get("structure_failures_csv"):
        failures_csv = Path(str(eval_summary.get("structure_failures_csv")))
    if not failures_csv or not failures_csv.exists():
        failures_csv = (evaluator_outdir or Path("")) / "structure_failed_sentences.csv"
    structure_failed_rows = read_csv_rows(failures_csv) if failures_csv and failures_csv.exists() else []
    structure_failed_unique = structure_failed_unique_count(structure_failed_rows)
    ci_low, ci_high = wilson_ci(structure_failed_unique, checked_messages)

    deterministic_issues_csv = (evaluator_outdir or Path("")) / "deterministic_issues.csv"
    deterministic_issue_rows = read_csv_rows(deterministic_issues_csv)
    det_issue_counts, det_severity_counts, det_unique_by_type = issues_by_type_and_unique_segments(deterministic_issue_rows)
    structure_issue_rows = [r for r in deterministic_issue_rows if issue_group(r.get("issue_type", "")) in STRUCTURE_GROUPS]
    other_structure_rows = [r for r in deterministic_issue_rows if issue_group(r.get("issue_type", "")) == "other_structure"]

    translation_summary = load_condition_translation_summary(condition_dir)
    log_stats = parse_translation_log(condition_dir / "translation_log.jsonl")

    # Main one-row statistics.
    result: Dict[str, Any] = {
        "condition_id": cond.get("id", ""),
        "condition_slug": slug,
        "description": cond.get("description", ""),
        "mask": cond.get("mask", ""),
        "ods": cond.get("ods", ""),
        "num_candidates": cond.get("num_candidates", ""),
        "workflow_status": manifest_status.get("status", ""),
        "workflow_error": manifest_status.get("error", ""),
        "ts_file": str(ts_file),
        "condition_dir": str(condition_dir),
        "evaluator_outdir": str(evaluator_outdir or ""),
        "messages_checked": checked_messages,
        "message_count": to_int(det.get("message_count", row.get("message_count", checked_messages)), checked_messages),
        "structure_average_score_0_100": to_float(det.get("structure_average_score_0_100", row.get("structure_average_score_0_100", ""))),
        "structure_total_score_sum": to_float(det.get("structure_total_score_sum", row.get("structure_total_score_sum", ""))),
        "structure_item_count": to_int(det.get("structure_score_item_count", 8), 8),
        "structure_failed_unique_segments": structure_failed_unique,
        "structure_failed_rows": len(structure_failed_rows),
        "structure_failed_rate": round(safe_rate(structure_failed_unique, checked_messages), 8),
        "structure_failed_rate_pct": pct(safe_rate(structure_failed_unique, checked_messages)),
        "structure_failed_rate_per_1000": round(safe_rate(structure_failed_unique, checked_messages) * 1000.0, 4),
        "structure_failed_rate_95ci_low_pct": pct(ci_low),
        "structure_failed_rate_95ci_high_pct": pct(ci_high),
        "counted_structure_issue_count": to_int(struct.get("counted_structure_issue_count", 0), 0),
        "structure_issue_row_count_in_deterministic_issues": len(structure_issue_rows),
        "other_structure_issue_row_count": len(other_structure_rows),
        "deterministic_score_0_100": to_float(det.get("deterministic_score_0_100", row.get("deterministic_score_0_100", ""))),
        "deterministic_error_points_per_1000_source_chars": to_float(det.get("deterministic_error_points_per_1000_source_chars", row.get("deterministic_error_points_per_1000_source_chars", ""))),
        "deterministic_issue_count": to_int(det.get("issue_count", 0), 0),
        "deterministic_affected_segment_count": to_int(det.get("affected_segment_count", 0), 0),
        "deterministic_affected_segment_rate_pct": pct(to_float(det.get("affected_segment_rate", 0.0), 0.0)),
        "blocking_issue_count": to_int(det.get("blocking_issue_count", 0), 0),
        "translation_summary_path": translation_summary.get("_summary_path", ""),
        "translation_api_call_count": to_int(translation_summary.get("api_call_count", log_stats.get("api_call_count_from_log", 0)), 0),
        "translation_issue_count": to_int(translation_summary.get("issue_count", 0), 0),
        "safe_fallback_count": to_int(translation_summary.get("safe_fallback_count", 0), 0),
        "format_safe_accept_count": to_int(translation_summary.get("format_safe_accept_count", 0), 0),
        "translated_ok": to_int(translation_summary.get("translated_ok", 0), 0),
        "validation_failed_exception_or_unfinished": to_int(translation_summary.get("validation_failed_exception_or_unfinished", 0), 0),
        "copied_language_neutral_or_no_text": to_int(translation_summary.get("copied_language_neutral_or_no_text", 0), 0),
        "repair_retranslate_count": to_int(translation_summary.get("repair_retranslate_count", 0), 0),
        "repair_kept_existing_count": to_int(translation_summary.get("repair_kept_existing_count", 0), 0),
        "raw_status_counts": translation_summary.get("status_counts", {}),
        "translation_issue_counts": translation_summary.get("issue_counts", {}),
        "translation_severity_counts": translation_summary.get("severity_counts", {}),
        **log_stats,
    }

    # Structure item details flattened into the one-row summary.
    for item in STRUCTURE_GROUP_ORDER:
        item_row = item_map.get(item, {})
        affected = to_int(item_row.get("affected_segment_count", 0), 0)
        issue_count = to_int(item_row.get("issue_count", 0), 0)
        score = to_float(item_row.get("score_0_100", ""))
        result[f"{item}_score"] = score
        result[f"{item}_affected_segments"] = affected
        result[f"{item}_affected_rate_pct"] = pct(safe_rate(affected, checked_messages))
        result[f"{item}_issue_count"] = issue_count
        result[f"{item}_issue_counts"] = item_row.get("issue_counts", {})

    for issue_type in IMPORTANT_CONTENT_ISSUES:
        count = det_issue_counts.get(issue_type, 0)
        unique = det_unique_by_type.get(issue_type, 0)
        result[f"{issue_type}_count"] = count
        result[f"{issue_type}_unique_segments"] = unique
        result[f"{issue_type}_unique_rate_pct"] = pct(safe_rate(unique, checked_messages))

    return {
        "summary_row": result,
        "structure_item_rows": build_structure_item_long_rows(result, item_map, checked_messages),
        "issue_count_rows": build_issue_count_rows(cond, slug, deterministic_issue_rows, checked_messages),
        "structure_failed_rows": add_condition_to_rows(cond, slug, structure_failed_rows),
        "translation_summary": translation_summary,
        "eval_summary": eval_summary,
    }


def build_structure_item_long_rows(summary_row: Dict[str, Any], item_map: Dict[str, Dict[str, Any]], checked_messages: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in STRUCTURE_GROUP_ORDER:
        item_row = item_map.get(item, {})
        affected = to_int(item_row.get("affected_segment_count", 0), 0)
        issue_count = to_int(item_row.get("issue_count", 0), 0)
        ci_low, ci_high = wilson_ci(affected, checked_messages)
        rows.append({
            "condition_id": summary_row.get("condition_id", ""),
            "condition_slug": summary_row.get("condition_slug", ""),
            "description": summary_row.get("description", ""),
            "item": item,
            "score_0_100": to_float(item_row.get("score_0_100", summary_row.get(f"{item}_score", ""))),
            "affected_segment_count": affected,
            "affected_rate": round(safe_rate(affected, checked_messages), 8),
            "affected_rate_pct": pct(safe_rate(affected, checked_messages)),
            "affected_rate_95ci_low_pct": pct(ci_low),
            "affected_rate_95ci_high_pct": pct(ci_high),
            "issue_count": issue_count,
            "issue_counts": item_row.get("issue_counts", {}),
            "severity_counts": item_row.get("severity_counts", {}),
            "checked_messages": checked_messages,
        })
    return rows


def build_issue_count_rows(cond: Dict[str, Any], slug: str, issue_rows: Sequence[Dict[str, str]], checked_messages: int) -> List[Dict[str, Any]]:
    by_type: Dict[str, Dict[str, Any]] = {}
    unique_segments: Dict[str, set[str]] = defaultdict(set)
    severity_by_type: Dict[str, Counter] = defaultdict(Counter)
    for row in issue_rows:
        issue_type = row.get("issue_type", "unknown") or "unknown"
        by_type.setdefault(issue_type, {"issue_count": 0})["issue_count"] += 1
        if row.get("segment_id"):
            unique_segments[issue_type].add(row["segment_id"])
        severity_by_type[issue_type][row.get("severity", "")] += 1
    out: List[Dict[str, Any]] = []
    for issue_type, values in sorted(by_type.items(), key=lambda kv: (-kv[1]["issue_count"], kv[0])):
        unique = len(unique_segments.get(issue_type, set()))
        out.append({
            "condition_id": cond.get("id", ""),
            "condition_slug": slug,
            "issue_group": issue_group(issue_type),
            "issue_type": issue_type,
            "issue_count": int(values["issue_count"]),
            "unique_segment_count": unique,
            "unique_segment_rate_pct": pct(safe_rate(unique, checked_messages)),
            "checked_messages": checked_messages,
            "severity_counts": dict(severity_by_type[issue_type]),
        })
    return out


def add_condition_to_rows(cond: Dict[str, Any], slug: str, rows: Sequence[Dict[str, str]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in rows:
        new = dict(row)
        new.update({
            "condition_id": cond.get("id", ""),
            "condition_slug": slug,
            "description": cond.get("description", ""),
            "mask": cond.get("mask", ""),
            "ods": cond.get("ods", ""),
            "num_candidates": cond.get("num_candidates", ""),
        })
        # Put condition columns first by constructing a new ordered dict-like object.
        ordered = {
            "condition_id": new.pop("condition_id"),
            "condition_slug": new.pop("condition_slug"),
            "description": new.pop("description"),
            "mask": new.pop("mask"),
            "ods": new.pop("ods"),
            "num_candidates": new.pop("num_candidates"),
        }
        ordered.update(new)
        out.append(ordered)
    return out


# -----------------------------------------------------------------------------
# Comparisons and reports
# -----------------------------------------------------------------------------
def build_comparison_rows(condition_rows: Sequence[Dict[str, Any]], reference_id: str = "C1") -> List[Dict[str, Any]]:
    by_id = {str(row.get("condition_id", "")): row for row in condition_rows}
    ref = by_id.get(reference_id)
    if not ref:
        return []
    rows: List[Dict[str, Any]] = []
    for cond_id, row in sorted(by_id.items(), key=lambda kv: condition_id_sort_key(kv[0])):
        if cond_id == reference_id:
            continue
        ref_fail = to_float(ref.get("structure_failed_rate", 0.0), 0.0)
        row_fail = to_float(row.get("structure_failed_rate", 0.0), 0.0)
        fail_reduction_abs = row_fail - ref_fail
        fail_reduction_rel = (fail_reduction_abs / row_fail) if row_fail > 0 else (1.0 if ref_fail == 0 else 0.0)
        comp = {
            "reference_condition_id": reference_id,
            "compared_condition_id": cond_id,
            "compared_condition_slug": row.get("condition_slug", ""),
            "reference_structure_average": ref.get("structure_average_score_0_100", ""),
            "compared_structure_average": row.get("structure_average_score_0_100", ""),
            "delta_structure_average_ref_minus_compared": round(to_float(ref.get("structure_average_score_0_100"), 0.0) - to_float(row.get("structure_average_score_0_100"), 0.0), 6),
            "reference_structure_failed_rate_pct": ref.get("structure_failed_rate_pct", ""),
            "compared_structure_failed_rate_pct": row.get("structure_failed_rate_pct", ""),
            "structure_failure_rate_reduction_abs_pct": pct(fail_reduction_abs),
            "structure_failure_rate_reduction_relative_pct": pct(fail_reduction_rel),
            "reference_safe_fallback_count": ref.get("safe_fallback_count", ""),
            "compared_safe_fallback_count": row.get("safe_fallback_count", ""),
            "delta_safe_fallback_ref_minus_compared": to_int(ref.get("safe_fallback_count"), 0) - to_int(row.get("safe_fallback_count"), 0),
            "reference_deterministic_score": ref.get("deterministic_score_0_100", ""),
            "compared_deterministic_score": row.get("deterministic_score_0_100", ""),
            "delta_deterministic_score_ref_minus_compared": round(to_float(ref.get("deterministic_score_0_100"), 0.0) - to_float(row.get("deterministic_score_0_100"), 0.0), 6),
        }
        for item in STRUCTURE_GROUP_ORDER:
            comp[f"{item}_affected_delta_ref_minus_compared"] = to_int(ref.get(f"{item}_affected_segments"), 0) - to_int(row.get(f"{item}_affected_segments"), 0)
            comp[f"{item}_score_delta_ref_minus_compared"] = round(to_float(ref.get(f"{item}_score"), 0.0) - to_float(row.get(f"{item}_score"), 0.0), 6)
        rows.append(comp)
    return rows


def best_and_worst_structure_items(item_rows: Sequence[Dict[str, Any]], max_items: int = 10) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    sorted_rows = sorted(item_rows, key=lambda r: (-to_int(r.get("affected_segment_count"), 0), str(r.get("condition_id", "")), str(r.get("item", ""))))
    worst = sorted_rows[:max_items]
    best = sorted(item_rows, key=lambda r: (to_int(r.get("affected_segment_count"), 0), str(r.get("condition_id", "")), str(r.get("item", ""))))[:max_items]
    return best, worst


def markdown_table(rows: Sequence[Dict[str, Any]], columns: Sequence[str], max_rows: Optional[int] = None) -> str:
    if max_rows is not None:
        rows = rows[:max_rows]
    lines = []
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("|" + "|".join(["---"] + ["---:"] * (len(columns) - 1)) + "|")
    for row in rows:
        vals = []
        for col in columns:
            value = row.get(col, "")
            if isinstance(value, float):
                if value != value:
                    value = ""
                else:
                    value = round(value, 4)
            vals.append(str(value).replace("|", "\\|"))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def write_report(
    path: Path,
    workdir: Path,
    eval_info: Dict[str, Any],
    condition_rows: Sequence[Dict[str, Any]],
    item_rows: Sequence[Dict[str, Any]],
    comparison_rows: Sequence[Dict[str, Any]],
    output_files: Dict[str, str],
) -> None:
    best, worst = best_and_worst_structure_items(item_rows, max_items=12)
    lines: List[str] = []
    lines.append("# QGIS C0-C4 ablation 統計報告")
    lines.append("")
    lines.append(f"- Workdir: `{workdir}`")
    if eval_info.get("ran"):
        lines.append(f"- Evaluator: 已重新執行，耗時 {eval_info.get('seconds')} 秒")
    else:
        lines.append(f"- Evaluator: 未重新執行，原因：{eval_info.get('reason', '')}")
    lines.append("")
    lines.append("## 條件總表")
    lines.append("")
    summary_cols = [
        "condition_id", "mask", "ods", "num_candidates", "messages_checked",
        "structure_average_score_0_100", "structure_failed_unique_segments", "structure_failed_rate_pct",
        "safe_fallback_count", "rows_no_valid_candidates", "deterministic_score_0_100",
    ]
    lines.append(markdown_table(sorted(condition_rows, key=lambda r: condition_id_sort_key(str(r.get("condition_id", "")))), summary_cols))
    lines.append("")
    lines.append("## 結構失敗最多的項目")
    lines.append("")
    lines.append(markdown_table(worst, ["condition_id", "item", "affected_segment_count", "affected_rate_pct", "score_0_100", "issue_count"], max_rows=12))
    lines.append("")
    if comparison_rows:
        lines.append("## 以 C1 full system 為基準的比較")
        lines.append("")
        comp_cols = [
            "compared_condition_id", "delta_structure_average_ref_minus_compared",
            "structure_failure_rate_reduction_abs_pct", "structure_failure_rate_reduction_relative_pct",
            "delta_safe_fallback_ref_minus_compared", "delta_deterministic_score_ref_minus_compared",
        ]
        lines.append(markdown_table(comparison_rows, comp_cols))
        lines.append("")
    lines.append("## 產生的檔案")
    lines.append("")
    for label, fpath in output_files.items():
        lines.append(f"- `{label}`: `{fpath}`")
    lines.append("")
    lines.append("## 解讀提醒")
    lines.append("")
    lines.append("- `structure_failed_unique_segments` 是至少有一種 structure item 沒過的句子數。")
    lines.append("- `structure_failed_rate_pct` = `structure_failed_unique_segments / messages_checked × 100%`。")
    lines.append("- 8 個 structure item 分別是 Qt placeholder、brace placeholder、printf placeholder、HTML/XML entity、HTML/XML tag、number、newline、accelerator。")
    lines.append("- `deterministic_score_0_100` 不是純結構分數，會受到未翻譯、英文殘留、詞庫缺失等內容問題影響。")
    lines.append("- C0/C2 若沒有開 hard-lock，格式錯誤會保留下來，這是為了讓 ablation 看得出 no-mask 的真實失敗率。")
    path.write_text("\n".join(lines), encoding="utf-8")


# -----------------------------------------------------------------------------
# Main orchestration
# -----------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    workdir = args.workdir.resolve()
    if not workdir.exists():
        raise FileNotFoundError(f"Ablation workdir not found: {workdir}")

    context = load_workflow_context(workdir)
    outputs_dir = Path(args.outputs_dir).resolve() if args.outputs_dir else Path(context["outputs_dir"]).resolve()
    if not outputs_dir.exists():
        raise FileNotFoundError(f"outputs_ts directory not found: {outputs_dir}")
    ts_files = discover_ts_outputs(outputs_dir)
    if not ts_files:
        raise FileNotFoundError(f"No .ts files found in {outputs_dir}")

    selected_n = selected_message_count(workdir, context)
    structure_score_limit = args.structure_score_limit if args.structure_score_limit > 0 else selected_n

    eval_outdir = Path(args.eval_outdir).resolve() if args.eval_outdir else (workdir / "evaluation").resolve()
    stats_outdir = Path(args.stats_outdir).resolve() if args.stats_outdir else (workdir / "statistics").resolve()
    stats_outdir.mkdir(parents=True, exist_ok=True)

    eval_script = resolve_eval_script(args.eval_script, workdir)
    eval_info: Dict[str, Any]
    if args.skip_eval:
        eval_info = run_evaluator(
            python_exe=args.python,
            eval_script=eval_script or Path("evaluate_all.py"),
            outputs_dir=outputs_dir,
            eval_outdir=eval_outdir,
            structure_score_limit=structure_score_limit,
            batch_summary_name=args.batch_summary_name,
            script_check=args.script_check,
            force=False,
            skip_eval=True,
        )
    else:
        if eval_script is None:
            raise FileNotFoundError(
                "Cannot find evaluate_all.py. Pass --eval-script path/to/evaluate_all.py"
            )
        eval_info = run_evaluator(
            python_exe=args.python,
            eval_script=eval_script,
            outputs_dir=outputs_dir,
            eval_outdir=eval_outdir,
            structure_score_limit=structure_score_limit,
            batch_summary_name=args.batch_summary_name,
            script_check=args.script_check,
            force=args.force_eval,
            skip_eval=False,
        )

    eval_rows = parse_eval_rows(eval_outdir, args.batch_summary_name)
    if not eval_rows:
        raise FileNotFoundError(
            f"No evaluator batch CSV found in {eval_outdir}. Run without --skip-eval or pass --force-eval."
        )

    condition_results = []
    condition_rows: List[Dict[str, Any]] = []
    item_rows: List[Dict[str, Any]] = []
    issue_count_rows: List[Dict[str, Any]] = []
    failed_sentence_rows: List[Dict[str, Any]] = []

    for row in eval_rows:
        result = collect_condition_result(
            row=row,
            eval_outdir=eval_outdir,
            workdir=workdir,
            context=context,
            checked_messages_default=structure_score_limit,
        )
        condition_results.append(result)
        condition_rows.append(result["summary_row"])
        item_rows.extend(result["structure_item_rows"])
        issue_count_rows.extend(result["issue_count_rows"])
        failed_sentence_rows.extend(result["structure_failed_rows"])

    condition_rows.sort(key=lambda r: condition_id_sort_key(str(r.get("condition_id", ""))))
    item_rows.sort(key=lambda r: (condition_id_sort_key(str(r.get("condition_id", ""))), STRUCTURE_GROUP_ORDER.index(str(r.get("item"))) if str(r.get("item")) in STRUCTURE_GROUP_ORDER else 999))
    issue_count_rows.sort(key=lambda r: (condition_id_sort_key(str(r.get("condition_id", ""))), str(r.get("issue_group", "")), -to_int(r.get("issue_count"), 0), str(r.get("issue_type", ""))))
    failed_sentence_rows.sort(key=lambda r: (condition_id_sort_key(str(r.get("condition_id", ""))), to_int(r.get("index"), 0), str(r.get("structure_item", ""))))

    comparison_rows = build_comparison_rows(condition_rows, reference_id=args.reference_condition)

    # Write reports.
    condition_summary_csv = stats_outdir / "condition_summary.csv"
    structure_items_csv = stats_outdir / "structure_items_long.csv"
    deterministic_issues_csv = stats_outdir / "deterministic_issue_counts_long.csv"
    failed_sentences_csv = stats_outdir / "structure_failed_sentences_merged.csv"
    comparison_csv = stats_outdir / "condition_comparisons_vs_C1.csv"
    report_md = stats_outdir / "ablation_statistics_report.md"
    summary_json = stats_outdir / "ablation_statistics_summary.json"

    condition_summary_fields = build_condition_summary_fields(condition_rows)
    write_csv_rows(condition_summary_csv, condition_rows, fieldnames=condition_summary_fields)
    write_csv_rows(structure_items_csv, item_rows)
    write_csv_rows(deterministic_issues_csv, issue_count_rows)
    write_csv_rows(failed_sentences_csv, failed_sentence_rows)
    write_csv_rows(comparison_csv, comparison_rows)

    output_files = {
        "condition_summary.csv": str(condition_summary_csv),
        "structure_items_long.csv": str(structure_items_csv),
        "deterministic_issue_counts_long.csv": str(deterministic_issues_csv),
        "structure_failed_sentences_merged.csv": str(failed_sentences_csv),
        "condition_comparisons_vs_C1.csv": str(comparison_csv),
        "ablation_statistics_report.md": str(report_md),
        "ablation_statistics_summary.json": str(summary_json),
    }

    write_report(
        path=report_md,
        workdir=workdir,
        eval_info=eval_info,
        condition_rows=condition_rows,
        item_rows=item_rows,
        comparison_rows=comparison_rows,
        output_files=output_files,
    )

    summary = {
        "workflow": "qgis_ablation_score_and_statistics",
        "workdir": str(workdir),
        "outputs_dir": str(outputs_dir),
        "eval_outdir": str(eval_outdir),
        "stats_outdir": str(stats_outdir),
        "ts_files": [str(p) for p in ts_files],
        "selected_messages_or_structure_score_limit": structure_score_limit,
        "evaluation": eval_info,
        "condition_count": len(condition_rows),
        "conditions": condition_rows,
        "structure_items": item_rows,
        "condition_comparisons": comparison_rows,
        "output_files": output_files,
        "notes": [
            "structure_failed_unique_segments counts unique messages with one or more scored structure failures.",
            "structure_failed_rate_pct is computed as unique failed messages divided by checked messages.",
            "deterministic_score_0_100 includes content and completion issues and should not be interpreted as a pure structure score.",
            "For ablation, no-mask conditions are usually intentionally not hard-locked so structure failures remain visible.",
        ],
    }
    write_json(summary_json, summary)

    print(json.dumps({
        "stats_outdir": str(stats_outdir),
        "condition_summary_csv": str(condition_summary_csv),
        "structure_items_csv": str(structure_items_csv),
        "failed_sentences_csv": str(failed_sentences_csv),
        "report_md": str(report_md),
        "summary_json": str(summary_json),
    }, ensure_ascii=False, indent=2))
    return 0


def build_condition_summary_fields(rows: Sequence[Dict[str, Any]]) -> List[str]:
    primary = [
        "condition_id", "condition_slug", "description", "mask", "ods", "num_candidates",
        "workflow_status", "workflow_error", "messages_checked", "message_count",
        "structure_average_score_0_100", "structure_total_score_sum",
        "structure_failed_unique_segments", "structure_failed_rate_pct", "structure_failed_rate_per_1000",
        "structure_failed_rate_95ci_low_pct", "structure_failed_rate_95ci_high_pct",
    ]
    item_fields: List[str] = []
    for item in STRUCTURE_GROUP_ORDER:
        item_fields.extend([
            f"{item}_score",
            f"{item}_affected_segments",
            f"{item}_affected_rate_pct",
            f"{item}_issue_count",
        ])
    translation_fields = [
        "deterministic_score_0_100", "deterministic_error_points_per_1000_source_chars",
        "deterministic_issue_count", "deterministic_affected_segment_count", "deterministic_affected_segment_rate_pct",
        "blocking_issue_count", "translation_api_call_count", "translation_issue_count",
        "safe_fallback_count", "format_safe_accept_count", "translated_ok",
        "validation_failed_exception_or_unfinished", "copied_language_neutral_or_no_text",
        "repair_retranslate_count", "repair_kept_existing_count",
        "translation_log_rows", "rows_with_candidates", "candidate_total", "avg_candidates_per_candidate_row",
        "avg_valid_candidate_count", "rows_no_valid_candidates", "rows_no_valid_candidates_rate_pct",
        "candidate_structure_issue_count", "candidate_structure_issue_rate_pct",
        "selected_candidate_counts", "valid_candidate_count_distribution",
    ]
    content_fields: List[str] = []
    for issue_type in IMPORTANT_CONTENT_ISSUES:
        content_fields.extend([
            f"{issue_type}_count",
            f"{issue_type}_unique_segments",
            f"{issue_type}_unique_rate_pct",
        ])
    tail = [
        "ts_file", "condition_dir", "evaluator_outdir", "translation_summary_path",
        "raw_status_counts", "translation_issue_counts", "translation_severity_counts",
    ]
    ordered = primary + item_fields + translation_fields + content_fields + tail
    # Add any extra fields not already included.
    seen = set(ordered)
    for row in rows:
        for key in row.keys():
            if key not in seen:
                ordered.append(key)
                seen.add(key)
    return ordered


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run evaluator and summarize C0-C4 QGIS ablation workflow results."
    )
    p.add_argument("--workdir", type=Path, required=True, help="Ablation workdir produced by run_qgis_ablation_workflow.py, e.g. ablation_grok_3000")
    p.add_argument("--outputs-dir", type=Path, default=None, help="Optional override for outputs_ts directory. Default: read from workflow_manifest or workdir/outputs_ts")
    p.add_argument("--eval-script", type=Path, default=None, help="Path to evaluate_all_structure100.py. If omitted, common locations are searched.")
    p.add_argument("--eval-outdir", type=Path, default=None, help="Evaluation output directory. Default: workdir/evaluation")
    p.add_argument("--stats-outdir", type=Path, default=None, help="Statistics output directory. Default: workdir/statistics")
    p.add_argument("--python", default=sys.executable, help="Python executable used to run the evaluator. Default: current interpreter")
    p.add_argument("--batch-summary-name", default="all_ts_mqm_scores", help="Evaluator batch summary base name. Default: all_ts_mqm_scores")
    p.add_argument("--structure-score-limit", type=int, default=0, help="Messages used as denominator for structure score. Default: selected subset size from subset_summary.json")
    p.add_argument("--script-check", choices=["none", "unihan", "opencc", "both"], default="none", help="Passed to evaluator. Default: none for deterministic structure/statistics runs")
    p.add_argument("--force-eval", action="store_true", help="Rerun evaluator even if evaluation CSV already exists")
    p.add_argument("--skip-eval", action="store_true", help="Do not run evaluator; only summarize existing evaluation files")
    p.add_argument("--reference-condition", default="C1", help="Reference condition for comparison table. Default: C1")
    return p.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
