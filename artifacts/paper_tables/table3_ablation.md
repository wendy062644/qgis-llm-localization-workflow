# Compact ablation summary on the 3000-segment subset

| Backend | Cond. | Short name | Structure failed % ↓ | Det. QA ↑ | MQM-ER ↓ |
|---|---|---|---:|---:|---:|
| Grok 4.3 | C0 | Direct baseline | 5.67 | 93.60 | 6.289 ± 0.368 |
| Grok 4.3 | C1 | Full workflow | 0.00 | 92.57 | 9.005 ± 2.807 |
| Grok 4.3 | C2 | No-mask + ODS | 5.13 | 94.54 | 3.630 ± 1.488 |
| Grok 4.3 | C4 | Single-candidate | 0.00 | 92.40 | 11.016 ± 2.372 |
| Gemini 3.1 Flash-Lite | C0 | Direct baseline | 8.70 | 92.72 | 7.194 ± 1.702 |
| Gemini 3.1 Flash-Lite | C1 | Full workflow | 0.00 | 92.97 | 11.109 ± 2.481 |
| Gemini 3.1 Flash-Lite | C2 | No-mask + ODS | 9.30 | 92.81 | 4.904 ± 1.945 |
| Gemini 3.1 Flash-Lite | C4 | Single-candidate | 0.00 | 92.82 | 12.482 ± 3.276 |
| TAIDE 12B | C0 | Direct baseline | 30.10 | 76.11 | 37.339 ± 10.414 |
| TAIDE 12B | C1 | Full workflow | 0.00 | 84.52 | 40.736 ± 6.212 |
| TAIDE 12B | C2 | No-mask + ODS | 25.17 | 79.63 | 31.564 ± 5.943 |
| TAIDE 12B | C4 | Single-candidate | 0.00 | 80.72 | 41.402 ± 6.335 |
