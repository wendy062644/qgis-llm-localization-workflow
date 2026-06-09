# QGIS LLM Localization — 表格精簡重現套件

English version: [README.md](README.md)

這是論文 **Toward Reliable Localization of Free and Open Source Software: LLM-assisted Translation Workflows for QGIS** 的精簡可重現套件。

預設指令刻意設計成小型、離線、快速。它們**不會**呼叫 Grok、Gemini 或 TAIDE，也**不會**重新跑 3000 筆或 full-corpus 實驗。預設只會重新產生並印出論文中會出現的表格，而且 CSV 欄位會對齊論文表格欄位。

## 1. 這個套件會重現什麼

預設 workflow 會重新產生這些精簡表格：

```text
artifacts/paper_tables/table1_model_backends.csv
artifacts/paper_tables/table2_ablation_conditions.csv
artifacts/paper_tables/table3_ablation.csv
artifacts/paper_tables/table4_full_corpus.csv
artifacts/paper_tables/artifact_map.csv
```

最重要的結果表格是：

```text
artifacts/paper_tables/table3_ablation.csv
artifacts/paper_tables/table4_full_corpus.csv
```

Python console 輸出也只保留表格內容。它只會印出 compact ablation table 和 full-corpus C1 table，不會輸出詳細 diagnostics CSV、MQM merged rows、structure pivots、request logs 或 translation logs。

## 2. 環境需求

建議使用 Python 3.10 或更新版本。

預設 quickstart 只需要 Python standard library。仍然保留 `requirements.txt`，讓 reviewer 可以按照一般 reproducibility 流程建立環境。

## 3. macOS / Linux 快速執行步驟

在新的 terminal 中，一行一行執行：

```bash
cd qgis_translation_repro_table_only
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python scripts/run_repro.py quickstart
```

預期結果：terminal 會印出兩個 Markdown 表格：

```text
Compact ablation summary on the 3000-segment subset
Full-corpus C1 production-condition comparison
```

重新產生的 CSV 和 Markdown 表格會寫到：

```text
artifacts/paper_tables/
```

## 4. Windows PowerShell 快速執行步驟

在新的 PowerShell 視窗中，一行一行執行：

```powershell
cd qgis_translation_repro_table_only
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python scripts/run_repro.py quickstart
```

如果 PowerShell 阻擋 virtual environment 啟動，先執行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

再接著執行：

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
python scripts/run_repro.py quickstart
```

## 5. Reviewer 可用指令

重新產生並印出論文表格：

```bash
python scripts/run_repro.py quickstart
```

`quickstart` 的別名：

```bash
python scripts/run_repro.py tables
```

列出套件內保留的精簡 CSV 表格與 archived translated outputs：

```bash
python scripts/run_repro.py list
```

## 6. 套件內保留的內容

這個套件只保留精簡表格重現與審查需要的檔案：

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

詳細中間 CSV 與 logs 預設不放入此套件。保留下來的 CSV 只包含論文表格中會出現的欄位。

## 7. 選用的完整 pipeline 程式

原始 workflow 程式碼保留在：

```text
scripts/full_pipeline/
```

這些程式不屬於預設 quickstart，因為它們可能會產生額外 diagnostics，而且執行時間較長。只有在需要檢查或改寫完整 workflow 時，才安裝選用相依套件：

```bash
pip install -r requirements-full.txt
```

table-only quickstart 不需要這些選用相依套件。

## 8. API key

這個套件不包含 API key。預設 quickstart 不需要任何 API key。

如果要重新呼叫 API 翻譯，請使用環境變數，不要把 key 寫死在 Python 檔案：

```bash
export XAI_API_KEY="..."
export GEMINI_API_KEY="..."
```

Windows PowerShell：

```powershell
$env:XAI_API_KEY="..."
$env:GEMINI_API_KEY="..."
```

請不要 commit `.env`、token 檔、request logs 或 private credentials。

## 9. 結果解讀方式

這個 table-only 套件的用途是讓 reviewer 快速確認論文表格可以從 archived artifact 重新產生，而且不需要新的模型呼叫。

它預設不是完整高成本重跑套件。完整翻譯重跑需要模型存取權、API keys 或 local model 設定，也需要更長的執行時間。
