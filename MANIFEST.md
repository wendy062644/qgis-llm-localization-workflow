# Manifest

This is a compact mini full reproduction package.

## Main files

- `README.md`: English instructions.
- `README.zh-TW.md`: Traditional Chinese instructions.
- `scripts/run_repro.py`: Unified entry point.
- `scripts/run_qgis.py`: API translation workflow for Grok/Gemini smoke runs.
- `scripts/evaluate_all.py`: Deterministic evaluator.
- `scripts/score_qgis.py`: Condition-level scorer.
- `scripts/compare_qgis.py`: Cross-condition comparison.
- `scripts/mqm_qgis_evaluator.py`: MQM-style judge request builder / optional runner.
- `data/raw/qgis_en.ts`: Source TS file.
- `data/glossary/1.ods`, `data/glossary/2.ods`: Glossary files.
- `experiments/demo_ablation_grok_100/`: Archived 100-segment C0-C4 demo outputs.
