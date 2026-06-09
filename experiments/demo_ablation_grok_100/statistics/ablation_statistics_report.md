# QGIS C0-C4 ablation 統計報告

- Workdir: `/mnt/data/qgis_mini_full_repro/qgis_translation_mini_full_repro/experiments/demo_ablation_grok_100`
- Evaluator: 已重新執行，耗時 1.409 秒

## 條件總表

| condition_id | mask | ods | num_candidates | messages_checked | structure_average_score_0_100 | structure_failed_unique_segments | structure_failed_rate_pct | safe_fallback_count | rows_no_valid_candidates | deterministic_score_0_100 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C0 | False | False | 1 | 100 | 99.375 | 5 | 5.0 | 0 | 0 | 94.22 |
| C1 | True | True | 3 | 100 | 100.0 | 0 | 0.0 | 7 | 0 | 93.48 |
| C2 | False | True | 3 | 100 | 99.375 | 5 | 5.0 | 0 | 0 | 94.84 |
| C3 | True | False | 3 | 100 | 100.0 | 0 | 0.0 | 7 | 0 | 93.3 |
| C4 | True | True | 1 | 100 | 100.0 | 0 | 0.0 | 8 | 0 | 93.12 |

## 結構失敗最多的項目

| condition_id | item | affected_segment_count | affected_rate_pct | score_0_100 | issue_count |
|---|---:|---:|---:|---:|---:|
| C0 | newline | 3 | 3.0 | 97.0 | 3 |
| C2 | newline | 3 | 3.0 | 97.0 | 3 |
| C0 | accelerator | 2 | 2.0 | 98.0 | 2 |
| C2 | accelerator | 2 | 2.0 | 98.0 | 2 |
| C0 | brace_placeholder | 0 | 0.0 | 100.0 | 0 |
| C0 | html_xml_entity | 0 | 0.0 | 100.0 | 0 |
| C0 | html_xml_tag | 0 | 0.0 | 100.0 | 0 |
| C0 | number | 0 | 0.0 | 100.0 | 0 |
| C0 | printf_placeholder | 0 | 0.0 | 100.0 | 0 |
| C0 | qt_placeholder | 0 | 0.0 | 100.0 | 0 |
| C1 | accelerator | 0 | 0.0 | 100.0 | 0 |
| C1 | brace_placeholder | 0 | 0.0 | 100.0 | 0 |

## 以 C1 full system 為基準的比較

| compared_condition_id | delta_structure_average_ref_minus_compared | structure_failure_rate_reduction_abs_pct | structure_failure_rate_reduction_relative_pct | delta_safe_fallback_ref_minus_compared | delta_deterministic_score_ref_minus_compared |
|---|---:|---:|---:|---:|---:|
| C0 | 0.625 | 5.0 | 100.0 | 7 | -0.74 |
| C2 | 0.625 | 5.0 | 100.0 | 7 | -1.36 |
| C3 | 0.0 | 0.0 | 100.0 | 0 | 0.18 |
| C4 | 0.0 | 0.0 | 100.0 | -1 | 0.36 |

## 產生的檔案

- `condition_summary.csv`: `/mnt/data/qgis_mini_full_repro/qgis_translation_mini_full_repro/experiments/demo_ablation_grok_100/statistics/condition_summary.csv`
- `structure_items_long.csv`: `/mnt/data/qgis_mini_full_repro/qgis_translation_mini_full_repro/experiments/demo_ablation_grok_100/statistics/structure_items_long.csv`
- `deterministic_issue_counts_long.csv`: `/mnt/data/qgis_mini_full_repro/qgis_translation_mini_full_repro/experiments/demo_ablation_grok_100/statistics/deterministic_issue_counts_long.csv`
- `structure_failed_sentences_merged.csv`: `/mnt/data/qgis_mini_full_repro/qgis_translation_mini_full_repro/experiments/demo_ablation_grok_100/statistics/structure_failed_sentences_merged.csv`
- `condition_comparisons_vs_C1.csv`: `/mnt/data/qgis_mini_full_repro/qgis_translation_mini_full_repro/experiments/demo_ablation_grok_100/statistics/condition_comparisons_vs_C1.csv`
- `ablation_statistics_report.md`: `/mnt/data/qgis_mini_full_repro/qgis_translation_mini_full_repro/experiments/demo_ablation_grok_100/statistics/ablation_statistics_report.md`
- `ablation_statistics_summary.json`: `/mnt/data/qgis_mini_full_repro/qgis_translation_mini_full_repro/experiments/demo_ablation_grok_100/statistics/ablation_statistics_summary.json`

## 解讀提醒

- `structure_failed_unique_segments` 是至少有一種 structure item 沒過的句子數。
- `structure_failed_rate_pct` = `structure_failed_unique_segments / messages_checked × 100%`。
- 8 個 structure item 分別是 Qt placeholder、brace placeholder、printf placeholder、HTML/XML entity、HTML/XML tag、number、newline、accelerator。
- `deterministic_score_0_100` 不是純結構分數，會受到未翻譯、英文殘留、詞庫缺失等內容問題影響。
- C0/C2 若沒有開 hard-lock，格式錯誤會保留下來，這是為了讓 ablation 看得出 no-mask 的真實失敗率。