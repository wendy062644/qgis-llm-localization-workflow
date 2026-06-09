# QGIS LLM 在地化 Mini Full Repro Package

這份 package 是 QGIS LLM localization workflow 的**縮小版完整實驗重現包**。它保留論文的實驗流程結構，但預設把 3000 筆 ablation 改成 **100 筆**，讓 reviewer 可以快速執行。

預設流程**不會呼叫模型 API**。它會使用已封存的 100 筆翻譯後 `.ts` 輸出，重新跑 deterministic evaluation、condition comparison，以及 MQM 評分請求產生流程。

## 這份 package 可以重現什麼

| 層級 | 指令 | 需要模型呼叫？ | 用途 |
|---|---|---:|---|
| Mini full reproduction | `python scripts/run_repro.py full-mini` | 不需要 | 對封存的 100 筆 C0-C4 輸出重跑 deterministic scoring、比較條件，並產生小型 MQM request plan。 |
| 只跑 deterministic scoring | `python scripts/run_repro.py score --experiment demo_ablation_grok_100 --force-eval` | 不需要 | 從封存 `.ts` 重新計算 structure 與 deterministic QA metrics。 |
| 產生 MQM 評分請求 | `python scripts/run_repro.py mqm --experiment demo_ablation_grok_100 --total-request-budget 5` | 不需要 | 建立 MQM judge requests，但不送出 API。 |
| 實際跑 MQM judge | `python scripts/run_repro.py mqm --experiment demo_ablation_grok_100 --total-request-budget 5 --run-grok` | 需要 Grok/xAI key | 實際執行小型 MQM-style judge evaluation。 |
| 重新翻譯小樣本 | `python scripts/run_repro.py translate --provider grok --sample-size 20 --conditions C1 --run-eval` | 需要 API key | 使用 API backend 重新產生小型翻譯實驗。 |

## 安裝

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

## 執行預設 mini full reproduction

```bash
python scripts/run_repro.py full-mini
```

這個指令會執行：

1. 對 `experiments/demo_ablation_grok_100/outputs_ts/*.ts` 重新跑 deterministic evaluator；
2. 對 C0-C4 重新跑 scorer / comparison；
3. 產生一個總共 5 筆 request 的 MQM dry-run plan，不呼叫 API。

主要輸出位置：

```text
experiments/demo_ablation_grok_100/statistics/
results/mini_full_compare/
results/mini_mqm_plan/
```

## 只跑 deterministic scoring

```bash
python scripts/run_repro.py score --experiment demo_ablation_grok_100 --force-eval
```

重要輸出：

```text
experiments/demo_ablation_grok_100/statistics/condition_summary.csv
experiments/demo_ablation_grok_100/statistics/structure_items_long.csv
experiments/demo_ablation_grok_100/statistics/ablation_statistics_report.md
```

## 不呼叫 API，只產生 MQM judge requests

```bash
python scripts/run_repro.py mqm \
  --experiment demo_ablation_grok_100 \
  --total-request-budget 5
```

這會產生：

```text
results/mini_mqm_plan/mqm_requests.jsonl
results/mini_mqm_plan/selected_mqm_segments.csv
results/mini_mqm_plan/mqm_report.md
```

## 實際呼叫 MQM judge

這需要 Grok/xAI API key。

```bash
export XAI_API_KEY="your_key_here"
python scripts/run_repro.py mqm \
  --experiment demo_ablation_grok_100 \
  --total-request-budget 5 \
  --run-grok
```

Windows PowerShell：

```powershell
$env:XAI_API_KEY="your_key_here"
python scripts/run_repro.py mqm --experiment demo_ablation_grok_100 --total-request-budget 5 --run-grok
```

預設 budget 很小，避免 reviewer 一開始就花太多時間或 API 成本。若需要更多 judge segments，再提高 `--total-request-budget` 或 `--sample-size`。

## 可選：重新跑一個小型翻譯實驗

```bash
export XAI_API_KEY="your_key_here"
python scripts/run_repro.py translate \
  --provider grok \
  --sample-size 20 \
  --conditions C0,C1,C2,C3,C4 \
  --run-eval
```

Gemini：

```bash
export GEMINI_API_KEY="your_key_here"
python scripts/run_repro.py translate \
  --provider gemini \
  --sample-size 20 \
  --conditions C1 \
  --run-eval
```

新產生的實驗會輸出到 `runs/`。

## 內含資料

```text
data/raw/qgis_en.ts                         原始 QGIS TS input
data/glossary/1.ods, data/glossary/2.ods    ODS glossary resources
experiments/demo_ablation_grok_100/          已封存的 100 筆 C0-C4 demo
scripts/                                     workflow、scoring、comparison、MQM tools
configs/conditions.json                      C0-C4 condition definitions
```

## 重要限制

- 這是**縮小版重現包**：預設 100 筆，不是論文完整 3000 筆 ablation。
- 預設 `full-mini` 不會重新翻譯，而是從封存 `.ts` 重新計算 metrics。
- 如果使用 `--run-grok`，MQM judge 需要 API key。
- 這份 package 支援 Grok 與 Gemini 的 API rerun。TAIDE/local rerun 需要額外的 Hugging Face 本地推論環境，這裡沒有包成一鍵指令。
- API 模型輸出可能隨時間改變；最穩定的 no-API 重現路徑是 archived-output scoring。
