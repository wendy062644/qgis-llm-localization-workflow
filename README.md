# QGIS LLM Localization Mini Full Repro Package

This package is a **small end-to-end reproduction package** for the QGIS LLM localization workflow. It keeps the experiment structure of the paper, but the default experiment uses **100 segments instead of 3000** so reviewers can run it quickly.

The default path does **not** call any model APIs. It uses archived 100-segment translated `.ts` outputs, then reruns deterministic evaluation, condition comparison, and MQM request generation.

## What this package can reproduce

| Level | Command | Model calls? | Purpose |
|---|---|---:|---|
| Mini full reproduction | `python scripts/run_repro.py full-mini` | No | Rerun deterministic scoring on the bundled 100-segment C0-C4 outputs, compare conditions, and generate a tiny MQM request plan. |
| Deterministic scoring only | `python scripts/run_repro.py score --experiment demo_ablation_grok_100 --force-eval` | No | Recompute structure and deterministic QA metrics from archived `.ts` files. |
| MQM request planning | `python scripts/run_repro.py mqm --experiment demo_ablation_grok_100 --total-request-budget 5` | No | Build the exact MQM judge requests without sending them. |
| MQM judge run | `python scripts/run_repro.py mqm --experiment demo_ablation_grok_100 --total-request-budget 5 --run-grok` | Yes, Grok/xAI key required | Actually run a tiny MQM-style judge evaluation. |
| New translation smoke run | `python scripts/run_repro.py translate --provider grok --sample-size 20 --conditions C1 --run-eval` | Yes | Regenerate a small translation run with an API backend. |

## Install

### macOS / Linux

```bash
cd qgis_translation_mini_full_repro
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Windows PowerShell

```powershell
cd qgis_translation_mini_full_repro
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Run the default mini full reproduction

```bash
python scripts/run_repro.py full-mini
```

This will run:

1. deterministic evaluator on `experiments/demo_ablation_grok_100/outputs_ts/*.ts`;
2. scorer/comparison scripts for C0-C4;
3. a dry-run MQM request plan with 5 total requests and no API calls.

Main outputs:

```text
experiments/demo_ablation_grok_100/statistics/
results/mini_full_compare/
results/mini_mqm_plan/
```

## Run only deterministic scoring

```bash
python scripts/run_repro.py score --experiment demo_ablation_grok_100 --force-eval
```

Important outputs:

```text
experiments/demo_ablation_grok_100/statistics/condition_summary.csv
experiments/demo_ablation_grok_100/statistics/structure_items_long.csv
experiments/demo_ablation_grok_100/statistics/ablation_statistics_report.md
```

## Generate MQM judge requests without API calls

```bash
python scripts/run_repro.py mqm \
  --experiment demo_ablation_grok_100 \
  --total-request-budget 5
```

This writes:

```text
results/mini_mqm_plan/mqm_requests.jsonl
results/mini_mqm_plan/selected_mqm_segments.csv
results/mini_mqm_plan/mqm_report.md
```

## Actually run the MQM judge

This requires a Grok/xAI API key.

```bash
export XAI_API_KEY="your_key_here"
python scripts/run_repro.py mqm \
  --experiment demo_ablation_grok_100 \
  --total-request-budget 5 \
  --run-grok
```

On Windows PowerShell:

```powershell
$env:XAI_API_KEY="your_key_here"
python scripts/run_repro.py mqm --experiment demo_ablation_grok_100 --total-request-budget 5 --run-grok
```

The default budget is intentionally tiny. Increase `--total-request-budget` or `--sample-size` only if you want more judged segments.

## Optional: rerun a new small translation experiment

```bash
export XAI_API_KEY="your_key_here"
python scripts/run_repro.py translate \
  --provider grok \
  --sample-size 20 \
  --conditions C0,C1,C2,C3,C4 \
  --run-eval
```

For Gemini:

```bash
export GEMINI_API_KEY="your_key_here"
python scripts/run_repro.py translate \
  --provider gemini \
  --sample-size 20 \
  --conditions C1 \
  --run-eval
```

Generated runs are written under `runs/`.

## Included data

```text
data/raw/qgis_en.ts                         Original QGIS TS input
data/glossary/1.ods, data/glossary/2.ods    ODS glossary resources
experiments/demo_ablation_grok_100/          Archived 100-segment C0-C4 demo
scripts/                                     Workflow, scoring, comparison, MQM tools
configs/conditions.json                      C0-C4 condition definitions
```

## Important limitations

- The default package is a **mini reproduction**: 100 segments, not the full 3000-segment paper ablation.
- The default `full-mini` path does not regenerate translations; it recomputes metrics from archived `.ts` outputs.
- MQM judging requires an API key if `--run-grok` is used.
- This package supports API reruns for Grok and Gemini. TAIDE/local reruns require a separate local Hugging Face inference setup and are not bundled as a one-command path here.
- API model outputs may change over time; archived-output scoring is the stable no-API reproducibility path.
