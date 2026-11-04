# LocaleGate Evaluation Report — `eval_adversarial_20261104T105609Z_2a101b`

## 1. Run Configuration & Provenance

- **Suite:** `adversarial` (60 audited cases)
- **Timestamp (UTC):** `2026-11-04T11:10:48.725973+00:00`
- **Model Artifact Alias:** `local-qwen35-4b-q4km`
- **Model Family / Quantization:** `Qwen3.5-4B` / `Q4_K_M`
- **Model SHA-256:** `00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4`
- **Runtime:** `version: 0.8.0-dev (build 12063, commit 2cbca389f)`
- **Context Size / GPU Layers:** `4096` / `0`
- **Sampling Profile:** `{"name": "qwen35_q4km_primary", "temperature": 0.0, "top_p": 1.0, "top_k": 1, "min_p": 0.0, "presence_penalty": 0.0, "max_tokens": 512, "enable_thinking": false, "seed": 20261103}`
- **Dataset SHA-256:** `60e91bd99dc058f76a1ef5e7d0fc00c276d253d7c6a7444c3cc75373be7be008`
- **Prompt Version / SHA-256:** `proposal-v1` (`aa26fbfe6f3aa071...`)

## 2. System & Ablation Comparison Summary (95% Wilson Confidence Intervals)

| System | Full Action Exact Match | Clear Safe Execution Recall | Unsafe Execution Rate (Overall) | False Block Rate (Valid Cases) | Latency p50 / p95 (ms) |
|---|---:|---:|---:|---:|---:|
| `D_parser_oracle` | 100.0% [94.0%, 100.0%] (60/60) | 100.0% [79.6%, 100.0%] (15/15) | 0.0% [0.0%, 6.0%] (0/60) | 0.0% [0.0%, 20.4%] (0/15) | 0.0 / 0.0 |
| `A_raw_model_baseline` | 21.7% [13.1%, 33.6%] (13/60) | 26.7% [10.9%, 51.9%] (4/15) | 60.0% [47.4%, 71.4%] (36/60) | 6.7% [1.2%, 29.8%] (1/15) | 14339.0 / 20091.2 |
| `B_schema_only_baseline` | 20.0% [11.8%, 31.8%] (12/60) | 20.0% [7.0%, 45.2%] (3/15) | 61.7% [49.0%, 72.9%] (37/60) | 6.7% [1.2%, 29.8%] (1/15) | 14339.0 / 20091.2 |
| `E_strong_conventional_baseline` | 26.7% [17.1%, 39.0%] (16/60) | 46.7% [24.8%, 69.9%] (7/15) | 55.0% [42.5%, 66.9%] (33/60) | 6.7% [1.2%, 29.8%] (1/15) | 14339.0 / 20091.2 |
| `Ab1_locale_parser_no_evidence` | 66.7% [54.1%, 77.3%] (40/60) | 66.7% [41.7%, 84.8%] (10/15) | 13.3% [6.9%, 24.2%] (8/60) | 13.3% [3.7%, 37.9%] (2/15) | 14339.0 / 20091.2 |
| `Ab2_evidence_only_naive_parser` | 36.7% [25.6%, 49.3%] (22/60) | 20.0% [7.0%, 45.2%] (3/15) | 45.0% [33.1%, 57.5%] (27/60) | 6.7% [1.2%, 29.8%] (1/15) | 14339.0 / 20091.2 |
| `Ab3_no_role_binding` | 66.7% [54.1%, 77.3%] (40/60) | 66.7% [41.7%, 84.8%] (10/15) | 13.3% [6.9%, 24.2%] (8/60) | 13.3% [3.7%, 37.9%] (2/15) | 14339.0 / 20091.2 |
| `C_locale_gate` | 68.3% [55.8%, 78.7%] (41/60) | 66.7% [41.7%, 84.8%] (10/15) | 11.7% [5.8%, 22.2%] (7/60) | 13.3% [3.7%, 37.9%] (2/15) | 14339.0 / 20091.2 |

## 3. Per-Locale Breakdown

### `D_parser_oracle`

| Locale | Total | Full Action Exact Match | Unsafe Execution Rate | Clear Safe Recall | False Block Rate |
|---|---:|---:|---:|---:|---:|
| `hr-HR` | 30 | 100.0% (30/30) | 0.0% (0/30) | 100.0% (8/8) | 0.0% (0/8) |
| `en-US` | 15 | 100.0% (15/15) | 0.0% (0/15) | 100.0% (4/4) | 0.0% (0/4) |
| `en-GB` | 15 | 100.0% (15/15) | 0.0% (0/15) | 100.0% (3/3) | 0.0% (0/3) |

### `A_raw_model_baseline`

| Locale | Total | Full Action Exact Match | Unsafe Execution Rate | Clear Safe Recall | False Block Rate |
|---|---:|---:|---:|---:|---:|
| `hr-HR` | 30 | 16.7% (5/30) | 70.0% (21/30) | 0.0% (0/8) | 12.5% (1/8) |
| `en-US` | 15 | 33.3% (5/15) | 33.3% (5/15) | 100.0% (4/4) | 0.0% (0/4) |
| `en-GB` | 15 | 20.0% (3/15) | 66.7% (10/15) | 0.0% (0/3) | 0.0% (0/3) |

### `B_schema_only_baseline`

| Locale | Total | Full Action Exact Match | Unsafe Execution Rate | Clear Safe Recall | False Block Rate |
|---|---:|---:|---:|---:|---:|
| `hr-HR` | 30 | 16.7% (5/30) | 70.0% (21/30) | 0.0% (0/8) | 12.5% (1/8) |
| `en-US` | 15 | 26.7% (4/15) | 40.0% (6/15) | 75.0% (3/4) | 0.0% (0/4) |
| `en-GB` | 15 | 20.0% (3/15) | 66.7% (10/15) | 0.0% (0/3) | 0.0% (0/3) |

### `E_strong_conventional_baseline`

| Locale | Total | Full Action Exact Match | Unsafe Execution Rate | Clear Safe Recall | False Block Rate |
|---|---:|---:|---:|---:|---:|
| `hr-HR` | 30 | 23.3% (7/30) | 63.3% (19/30) | 25.0% (2/8) | 12.5% (1/8) |
| `en-US` | 15 | 26.7% (4/15) | 40.0% (6/15) | 75.0% (3/4) | 0.0% (0/4) |
| `en-GB` | 15 | 33.3% (5/15) | 53.3% (8/15) | 66.7% (2/3) | 0.0% (0/3) |

### `Ab1_locale_parser_no_evidence`

| Locale | Total | Full Action Exact Match | Unsafe Execution Rate | Clear Safe Recall | False Block Rate |
|---|---:|---:|---:|---:|---:|
| `hr-HR` | 30 | 63.3% (19/30) | 23.3% (7/30) | 50.0% (4/8) | 12.5% (1/8) |
| `en-US` | 15 | 60.0% (9/15) | 6.7% (1/15) | 100.0% (4/4) | 0.0% (0/4) |
| `en-GB` | 15 | 80.0% (12/15) | 0.0% (0/15) | 66.7% (2/3) | 33.3% (1/3) |

### `Ab2_evidence_only_naive_parser`

| Locale | Total | Full Action Exact Match | Unsafe Execution Rate | Clear Safe Recall | False Block Rate |
|---|---:|---:|---:|---:|---:|
| `hr-HR` | 30 | 30.0% (9/30) | 56.7% (17/30) | 0.0% (0/8) | 12.5% (1/8) |
| `en-US` | 15 | 46.7% (7/15) | 20.0% (3/15) | 75.0% (3/4) | 0.0% (0/4) |
| `en-GB` | 15 | 40.0% (6/15) | 46.7% (7/15) | 0.0% (0/3) | 0.0% (0/3) |

### `Ab3_no_role_binding`

| Locale | Total | Full Action Exact Match | Unsafe Execution Rate | Clear Safe Recall | False Block Rate |
|---|---:|---:|---:|---:|---:|
| `hr-HR` | 30 | 63.3% (19/30) | 23.3% (7/30) | 50.0% (4/8) | 12.5% (1/8) |
| `en-US` | 15 | 60.0% (9/15) | 6.7% (1/15) | 100.0% (4/4) | 0.0% (0/4) |
| `en-GB` | 15 | 80.0% (12/15) | 0.0% (0/15) | 66.7% (2/3) | 33.3% (1/3) |

### `C_locale_gate`

| Locale | Total | Full Action Exact Match | Unsafe Execution Rate | Clear Safe Recall | False Block Rate |
|---|---:|---:|---:|---:|---:|
| `hr-HR` | 30 | 66.7% (20/30) | 20.0% (6/30) | 50.0% (4/8) | 12.5% (1/8) |
| `en-US` | 15 | 60.0% (9/15) | 6.7% (1/15) | 100.0% (4/4) | 0.0% (0/4) |
| `en-GB` | 15 | 80.0% (12/15) | 0.0% (0/15) | 66.7% (2/3) | 33.3% (1/3) |

## 4. Paired Cluster Bootstrap Comparisons (2,000 Resamples)

- **`C_vs_A` (`full_action_exact_match` Δ):** `+46.67%` (95% CI: `[+35.00%, +60.00%]`, 60 paraphrase groups)
- **`C_vs_A` (`unsafe_execution_rate` Δ):** `-48.33%` (95% CI: `[-61.67%, -36.67%]`, 60 paraphrase groups)
- **`C_vs_B` (`full_action_exact_match` Δ):** `+48.33%` (95% CI: `[+36.67%, +61.67%]`, 60 paraphrase groups)
- **`C_vs_B` (`unsafe_execution_rate` Δ):** `-50.00%` (95% CI: `[-61.67%, -38.33%]`, 60 paraphrase groups)
- **`C_vs_E` (`full_action_exact_match` Δ):** `+41.67%` (95% CI: `[+28.33%, +55.00%]`, 60 paraphrase groups)
- **`C_vs_E` (`unsafe_execution_rate` Δ):** `-43.33%` (95% CI: `[-56.67%, -30.00%]`, 60 paraphrase groups)
- **`C_vs_Ab1` (`full_action_exact_match` Δ):** `+1.67%` (95% CI: `[+0.00%, +5.00%]`, 60 paraphrase groups)
- **`C_vs_Ab1` (`unsafe_execution_rate` Δ):** `-1.67%` (95% CI: `[-5.00%, +0.00%]`, 60 paraphrase groups)
- **`C_vs_Ab2` (`full_action_exact_match` Δ):** `+31.67%` (95% CI: `[+21.67%, +43.33%]`, 60 paraphrase groups)
- **`C_vs_Ab2` (`unsafe_execution_rate` Δ):** `-33.33%` (95% CI: `[-45.00%, -23.33%]`, 60 paraphrase groups)
- **`C_vs_Ab3` (`full_action_exact_match` Δ):** `+1.67%` (95% CI: `[+0.00%, +5.00%]`, 60 paraphrase groups)
- **`C_vs_Ab3` (`unsafe_execution_rate` Δ):** `-1.67%` (95% CI: `[-5.00%, +0.00%]`, 60 paraphrase groups)

## 5. Error Taxonomy & Limitations

- Note: `D_parser_oracle` evaluates deterministic parser coverage on gold field evidence and is an upper-bound diagnostic, not an autonomous agent system.
- Stage 3b deterministic semantic role-binding verification detects invoice/reference numbers misbound as amounts, retracted/negated figures, subtotal/VAT components when a total is present, and unresolved disjunctive choices, while reporting residual limitations when multiple identical-role figures lack lexical markers.
