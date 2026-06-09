# QGIS LLM Localization — Table-Only Reproducibility Package

Traditional Chinese version: [README.zh-TW.md](README.zh-TW.md)

This is the compact reproducibility package for the paper **Toward Reliable Localization of Free and Open Source Software: LLM-assisted Translation Workflows for QGIS**.

The default commands are intentionally small and offline. They do **not** call Grok, Gemini, or TAIDE, and they do **not** rerun the 3000-segment or full-corpus experiments. They only regenerate and print the paper-facing tables whose columns match the manuscript tables.

## 1. What this package reproduces

The default workflow regenerates these compact tables:

```text
artifacts/paper_tables/table1_model_backends.csv
artifacts/paper_tables/table2_ablation_conditions.csv
artifacts/paper_tables/table3_ablation.csv
artifacts/paper_tables/table4_full_corpus.csv
artifacts/paper_tables/artifact_map.csv
```

The main result tables are:

```text
artifacts/paper_tables/table3_ablation.csv
artifacts/paper_tables/table4_full_corpus.csv
```

Python console output is also table-only. It prints the compact ablation table and the full-corpus C1 table, without detailed diagnostic CSVs, merged MQM rows, structure pivots, request logs, or translation logs.

## 2. Requirements

Use Python 3.10 or newer.

The default quickstart uses only the Python standard library. `requirements.txt` is still provided so that a reviewer can follow a normal reproducibility setup.

## 3. Quickstart on macOS / Linux

From a fresh terminal, run these commands one by one:

```bash
cd qgis_translation_repro_table_only
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python scripts/run_repro.py quickstart
```

Expected result: the terminal prints two Markdown tables:

```text
Compact ablation summary on the 3000-segment subset
Full-corpus C1 production-condition comparison
```

The regenerated CSV and Markdown files are written to:

```text
artifacts/paper_tables/
```

## 4. Quickstart on Windows PowerShell

From a fresh PowerShell window, run these commands one by one:

```powershell
cd qgis_translation_repro_table_only
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python scripts/run_repro.py quickstart
```

If PowerShell blocks virtual-environment activation, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Then continue with:

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
python scripts/run_repro.py quickstart
```

## 5. Available reviewer commands

Regenerate and print the paper-facing tables:

```bash
python scripts/run_repro.py quickstart
```

Alias of `quickstart`:

```bash
python scripts/run_repro.py tables
```

List bundled compact CSV tables and archived translated outputs:

```bash
python scripts/run_repro.py list
```

## 6. Included files

The package keeps only the files needed for compact table reproduction and auditability:

```text
data/raw/qgis_en.ts
data/glossary/1.ods
data/glossary/2.ods
configs/conditions.json
configs/suites.json
artifacts/paper_tables/*.csv
artifacts/paper_tables/*.md
experiments/*/outputs_ts/*.ts
experiments/*/workflow_manifest.json
experiments/*/conditions/*/condition.json
experiments/*/subset/subset_summary.json
scripts/run_repro.py
scripts/reproduce_paper_tables.py
scripts/full_pipeline/
```

Detailed intermediate CSVs and logs are intentionally excluded from the default package. The included CSV files only contain columns that appear in the manuscript tables.

## 7. Optional full pipeline scripts

The original workflow scripts are kept under:

```text
scripts/full_pipeline/
```

They are not part of the default quickstart because they may generate additional diagnostics and can take longer to run. Install optional dependencies only when you need to inspect or adapt the full workflow:

```bash
pip install -r requirements-full.txt
```

The table-only quickstart does not require these optional dependencies.

## 8. API keys

This package does not include API keys. The default quickstart does not need any API key.

For API-based translation reruns, use environment variables instead of hard-coding keys in Python files:

```bash
export XAI_API_KEY="..."
export GEMINI_API_KEY="..."
```

Windows PowerShell:

```powershell
$env:XAI_API_KEY="..."
$env:GEMINI_API_KEY="..."
```

Do not commit `.env`, token files, request logs, or private credentials.

## 9. Expected interpretation

This table-only package is designed for fast verification of the paper-facing results. It supports the claim that the reported tables can be regenerated from the archived artifact without new model calls.

It is not intended to be a full-cost rerun package by default. Full translation reruns require model access, API keys or local model setup, and additional runtime.
