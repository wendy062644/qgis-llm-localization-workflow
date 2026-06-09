#!/usr/bin/env python3
"""Reproduce the publication-facing paper tables only.

This script intentionally writes only compact tables whose columns match the paper.
It does not emit detailed diagnostic CSVs, merged MQM rows, structure pivots, or logs.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTDIR = ROOT / "artifacts" / "paper_tables"

TABLES = {
    "table1_model_backends.csv": {
        "title": "Model backends",
        "columns": ["Backend", "Access", "Model ID", "Role"],
        "rows": [
            {"Backend": "Gemini", "Access": "API", "Model ID": "gemini-3.1-flash-lite", "Role": "Translation backend"},
            {"Backend": "Grok", "Access": "API", "Model ID": "grok-4.3", "Role": "Translation backend and MQM judge"},
            {"Backend": "TAIDE", "Access": "Local", "Model ID": "taide/Gemma-3-TAIDE-12b-Chat-2602", "Role": "Local translation backend"},
        ],
    },
    "table2_ablation_conditions.csv": {
        "title": "Ablation conditions",
        "columns": ["Condition", "Short name", "Masking", "ODS glossary", "Candidates", "Purpose"],
        "rows": [
            {"Condition": "C0", "Short name": "Direct baseline", "Masking": "no", "ODS glossary": "no", "Candidates": "1", "Purpose": "Direct no-mask translation"},
            {"Condition": "C1", "Short name": "Full workflow", "Masking": "yes", "ODS glossary": "yes", "Candidates": "3", "Purpose": "Production condition"},
            {"Condition": "C2", "Short name": "No-mask + ODS", "Masking": "no", "ODS glossary": "yes", "Candidates": "3", "Purpose": "Tests glossary/candidates without masking"},
            {"Condition": "C3", "Short name": "No-glossary", "Masking": "yes", "ODS glossary": "no", "Candidates": "3", "Purpose": "Tests masking without glossary hints"},
            {"Condition": "C4", "Short name": "Single-candidate", "Masking": "yes", "ODS glossary": "yes", "Candidates": "1", "Purpose": "Tests the value of multi-candidate selection"},
        ],
    },
    "table3_ablation.csv": {
        "title": "Compact ablation summary on the 3000-segment subset",
        "columns": ["Backend", "Cond.", "Short name", "Structure failed % ↓", "Det. QA ↑", "MQM-ER ↓"],
        "rows": [
            {"Backend": "Grok 4.3", "Cond.": "C0", "Short name": "Direct baseline", "Structure failed % ↓": "5.67", "Det. QA ↑": "93.60", "MQM-ER ↓": "6.289 ± 0.368"},
            {"Backend": "Grok 4.3", "Cond.": "C1", "Short name": "Full workflow", "Structure failed % ↓": "0.00", "Det. QA ↑": "92.57", "MQM-ER ↓": "9.005 ± 2.807"},
            {"Backend": "Grok 4.3", "Cond.": "C2", "Short name": "No-mask + ODS", "Structure failed % ↓": "5.13", "Det. QA ↑": "94.54", "MQM-ER ↓": "3.630 ± 1.488"},
            {"Backend": "Grok 4.3", "Cond.": "C4", "Short name": "Single-candidate", "Structure failed % ↓": "0.00", "Det. QA ↑": "92.40", "MQM-ER ↓": "11.016 ± 2.372"},
            {"Backend": "Gemini 3.1 Flash-Lite", "Cond.": "C0", "Short name": "Direct baseline", "Structure failed % ↓": "8.70", "Det. QA ↑": "92.72", "MQM-ER ↓": "7.194 ± 1.702"},
            {"Backend": "Gemini 3.1 Flash-Lite", "Cond.": "C1", "Short name": "Full workflow", "Structure failed % ↓": "0.00", "Det. QA ↑": "92.97", "MQM-ER ↓": "11.109 ± 2.481"},
            {"Backend": "Gemini 3.1 Flash-Lite", "Cond.": "C2", "Short name": "No-mask + ODS", "Structure failed % ↓": "9.30", "Det. QA ↑": "92.81", "MQM-ER ↓": "4.904 ± 1.945"},
            {"Backend": "Gemini 3.1 Flash-Lite", "Cond.": "C4", "Short name": "Single-candidate", "Structure failed % ↓": "0.00", "Det. QA ↑": "92.82", "MQM-ER ↓": "12.482 ± 3.276"},
            {"Backend": "TAIDE 12B", "Cond.": "C0", "Short name": "Direct baseline", "Structure failed % ↓": "30.10", "Det. QA ↑": "76.11", "MQM-ER ↓": "37.339 ± 10.414"},
            {"Backend": "TAIDE 12B", "Cond.": "C1", "Short name": "Full workflow", "Structure failed % ↓": "0.00", "Det. QA ↑": "84.52", "MQM-ER ↓": "40.736 ± 6.212"},
            {"Backend": "TAIDE 12B", "Cond.": "C2", "Short name": "No-mask + ODS", "Structure failed % ↓": "25.17", "Det. QA ↑": "79.63", "MQM-ER ↓": "31.564 ± 5.943"},
            {"Backend": "TAIDE 12B", "Cond.": "C4", "Short name": "Single-candidate", "Structure failed % ↓": "0.00", "Det. QA ↑": "80.72", "MQM-ER ↓": "41.402 ± 6.335"},
        ],
    },
    "table4_full_corpus.csv": {
        "title": "Full-corpus C1 production-condition comparison",
        "columns": ["Backend", "Messages checked", "Structure failed % ↓", "Structure score ↑", "Det. QA ↑", "Avg. valid candidates", "Possibly untranslated"],
        "rows": [
            {"Backend": "Grok 4.3", "Messages checked": "28,924", "Structure failed % ↓": "0.00", "Structure score ↑": "100.000", "Det. QA ↑": "92.11", "Avg. valid candidates": "2.938", "Possibly untranslated": "1,394 (4.82%)"},
            {"Backend": "Gemini 3.1 Flash-Lite", "Messages checked": "28,924", "Structure failed % ↓": "0.00", "Structure score ↑": "100.000", "Det. QA ↑": "90.09", "Avg. valid candidates": "2.935", "Possibly untranslated": "1,825 (6.31%)"},
            {"Backend": "TAIDE 12B", "Messages checked": "28,924", "Structure failed % ↓": "0.00", "Structure score ↑": "100.000", "Det. QA ↑": "65.13", "Avg. valid candidates": "2.052", "Possibly untranslated": "6,674 (23.07%)"},
        ],
    },
    "artifact_map.csv": {
        "title": "Artifact map",
        "columns": ["Artifact", "Location"],
        "rows": [
            {"Artifact": "Code and scripts", "Location": "scripts/"},
            {"Artifact": "Selected subset metadata", "Location": "experiments/*/subset/subset_summary.json"},
            {"Artifact": "Archived translated .ts outputs", "Location": "experiments/*/outputs_ts/"},
            {"Artifact": "Paper tables", "Location": "artifacts/paper_tables/"},
            {"Artifact": "Table 3 compact ablation", "Location": "artifacts/paper_tables/table3_ablation.csv"},
            {"Artifact": "Table 4 full-corpus C1", "Location": "artifacts/paper_tables/table4_full_corpus.csv"},
            {"Artifact": "Optional full pipeline scripts", "Location": "scripts/full_pipeline/"},
        ],
    },
}


def write_csv(path: Path, columns: Sequence[str], rows: Sequence[Mapping[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def markdown_table(columns: Sequence[str], rows: Sequence[Mapping[str, str]]) -> str:
    lines = ["| " + " | ".join(columns) + " |"]
    left_cols = {"Backend", "Cond.", "Short name", "Artifact", "Location", "Access", "Model ID", "Role", "Condition", "Masking", "ODS glossary", "Purpose"}
    align = ["---" if col in left_cols else "---:" for col in columns]
    lines.append("|" + "|".join(align) + "|")
    for row in rows:
        values = [str(row.get(col, "")).replace("|", "\\|") for col in columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def write_md(path: Path, title: str, columns: Sequence[str], rows: Sequence[Mapping[str, str]]) -> None:
    path.write_text(f"# {title}\n\n" + markdown_table(columns, rows) + "\n", encoding="utf-8")


def reproduce(outdir: Path, *, print_tables: bool = True) -> list[Path]:
    written: list[Path] = []
    for filename, spec in TABLES.items():
        csv_path = outdir / filename
        md_path = outdir / filename.replace(".csv", ".md")
        write_csv(csv_path, spec["columns"], spec["rows"])
        write_md(md_path, spec["title"], spec["columns"], spec["rows"])
        written.extend([csv_path, md_path])
    if print_tables:
        for filename in ["table3_ablation.csv", "table4_full_corpus.csv"]:
            spec = TABLES[filename]
            print(f"\n## {spec['title']}\n")
            print(markdown_table(spec["columns"], spec["rows"]))
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Write only the publication-facing QGIS paper tables.")
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR, help="Output directory for paper table CSV/Markdown files.")
    parser.add_argument("--quiet", action="store_true", help="Write files without printing the tables.")
    args = parser.parse_args()
    reproduce(args.outdir.resolve(), print_tables=not args.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
