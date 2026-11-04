# LocaleGate Evaluation Report — `eval_adversarial_20261104T111528Z_4e4e91`

## 1. Run Configuration & Provenance

- **Suite:** `adversarial` (60 audited cases)
- **Timestamp (UTC):** `2026-11-04T11:25:24.338255+00:00`
- **Model Artifact Alias:** `local-gemma4-e2b-it-q4km`
- **Model Family / Quantization:** `Gemma-4-E2B-It` / `Q4_K_M`
- **Model SHA-256:** `740185b21d22ceb83a11c3aa62ad5842ef32c70f6096d756bbee85a1e4ec34b8`
- **Runtime:** `version: 0.8.0-dev (build 12063, commit 2cbca389f)`
- **Context Size / GPU Layers:** `4096` / `0`
- **Sampling Profile:** `{"name": "gemma4_e2b_q4km_primary", "temperature": 0.0, "top_p": 1.0, "top_k": 1, "min_p": 0.0, "presence_penalty": 0.0, "max_tokens": 512, "enable_thinking": false, "seed": 20261103}`
- **Dataset SHA-256:** `60e91bd99dc058f76a1ef5e7d0fc00c276d253d7c6a7444c3cc75373be7be008`
- **Prompt Version / SHA-256:** `proposal-v1` (`aa26fbfe6f3aa071...`)

## 2. System & Ablation Comparison Summary (95% Wilson Confidence Intervals)

| System | Full Action Exact Match | Clear Safe Execution Recall | Unsafe Execution Rate (Overall) | False Block Rate (Valid Cases) | Latency p50 / p95 (ms) |
|---|---:|---:|---:|---:|---:|
| `D_parser_oracle` | 100.0% [94.0%, 100.0%] (60/60) | 100.0% [79.6%, 100.0%] (15/15) | 0.0% [0.0%, 6.0%] (0/60) | 0.0% [0.0%, 20.4%] (0/15) | 0.0 / 0.0 |
| `A_raw_model_baseline` | 10.0% [4.7%, 20.2%] (6/60) | 13.3% [3.7%, 37.9%] (2/15) | 66.7% [54.1%, 77.3%] (40/60) | 26.7% [10.9%, 51.9%] (4/15) | 9874.7 / 14131.8 |
| `B_schema_only_baseline` | 10.0% [4.7%, 20.2%] (6/60) | 13.3% [3.7%, 37.9%] (2/15) | 66.7% [54.1%, 77.3%] (40/60) | 26.7% [10.9%, 51.9%] (4/15) | 9874.7 / 14131.8 |
| `E_strong_conventional_baseline` | 13.3% [6.9%, 24.2%] (8/60) | 26.7% [10.9%, 51.9%] (4/15) | 63.3% [50.7%, 74.4%] (38/60) | 26.7% [10.9%, 51.9%] (4/15) | 9874.7 / 14131.8 |
| `Ab1_locale_parser_no_evidence` | 48.3% [36.2%, 60.7%] (29/60) | 40.0% [19.8%, 64.2%] (6/15) | 23.3% [14.4%, 35.4%] (14/60) | 26.7% [10.9%, 51.9%] (4/15) | 9874.7 / 14131.8 |
| `Ab2_evidence_only_naive_parser` | 25.0% [15.8%, 37.2%] (15/60) | 13.3% [3.7%, 37.9%] (2/15) | 51.7% [39.3%, 63.8%] (31/60) | 26.7% [10.9%, 51.9%] (4/15) | 9874.7 / 14131.8 |
| `Ab3_no_role_binding` | 48.3% [36.2%, 60.7%] (29/60) | 40.0% [19.8%, 64.2%] (6/15) | 23.3% [14.4%, 35.4%] (14/60) | 26.7% [10.9%, 51.9%] (4/15) | 9874.7 / 14131.8 |
| `C_locale_gate` | 50.0% [37.7%, 62.3%] (30/60) | 40.0% [19.8%, 64.2%] (6/15) | 20.0% [11.8%, 31.8%] (12/60) | 33.3% [15.2%, 58.3%] (5/15) | 9874.7 / 14131.8 |

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
| `hr-HR` | 30 | 6.7% (2/30) | 93.3% (28/30) | 0.0% (0/8) | 0.0% (0/8) |
| `en-US` | 15 | 13.3% (2/15) | 33.3% (5/15) | 50.0% (2/4) | 25.0% (1/4) |
| `en-GB` | 15 | 13.3% (2/15) | 46.7% (7/15) | 0.0% (0/3) | 100.0% (3/3) |

### `B_schema_only_baseline`

| Locale | Total | Full Action Exact Match | Unsafe Execution Rate | Clear Safe Recall | False Block Rate |
|---|---:|---:|---:|---:|---:|
| `hr-HR` | 30 | 6.7% (2/30) | 93.3% (28/30) | 0.0% (0/8) | 0.0% (0/8) |
| `en-US` | 15 | 13.3% (2/15) | 33.3% (5/15) | 50.0% (2/4) | 25.0% (1/4) |
| `en-GB` | 15 | 13.3% (2/15) | 46.7% (7/15) | 0.0% (0/3) | 100.0% (3/3) |

### `E_strong_conventional_baseline`

| Locale | Total | Full Action Exact Match | Unsafe Execution Rate | Clear Safe Recall | False Block Rate |
|---|---:|---:|---:|---:|---:|
| `hr-HR` | 30 | 13.3% (4/30) | 86.7% (26/30) | 25.0% (2/8) | 0.0% (0/8) |
| `en-US` | 15 | 13.3% (2/15) | 33.3% (5/15) | 50.0% (2/4) | 25.0% (1/4) |
| `en-GB` | 15 | 13.3% (2/15) | 46.7% (7/15) | 0.0% (0/3) | 100.0% (3/3) |

### `Ab1_locale_parser_no_evidence`

| Locale | Total | Full Action Exact Match | Unsafe Execution Rate | Clear Safe Recall | False Block Rate |
|---|---:|---:|---:|---:|---:|
| `hr-HR` | 30 | 56.7% (17/30) | 36.7% (11/30) | 50.0% (4/8) | 0.0% (0/8) |
| `en-US` | 15 | 33.3% (5/15) | 13.3% (2/15) | 50.0% (2/4) | 25.0% (1/4) |
| `en-GB` | 15 | 46.7% (7/15) | 6.7% (1/15) | 0.0% (0/3) | 100.0% (3/3) |

### `Ab2_evidence_only_naive_parser`

| Locale | Total | Full Action Exact Match | Unsafe Execution Rate | Clear Safe Recall | False Block Rate |
|---|---:|---:|---:|---:|---:|
| `hr-HR` | 30 | 20.0% (6/30) | 80.0% (24/30) | 0.0% (0/8) | 0.0% (0/8) |
| `en-US` | 15 | 33.3% (5/15) | 13.3% (2/15) | 50.0% (2/4) | 25.0% (1/4) |
| `en-GB` | 15 | 26.7% (4/15) | 33.3% (5/15) | 0.0% (0/3) | 100.0% (3/3) |

### `Ab3_no_role_binding`

| Locale | Total | Full Action Exact Match | Unsafe Execution Rate | Clear Safe Recall | False Block Rate |
|---|---:|---:|---:|---:|---:|
| `hr-HR` | 30 | 56.7% (17/30) | 36.7% (11/30) | 50.0% (4/8) | 0.0% (0/8) |
| `en-US` | 15 | 33.3% (5/15) | 13.3% (2/15) | 50.0% (2/4) | 25.0% (1/4) |
| `en-GB` | 15 | 46.7% (7/15) | 6.7% (1/15) | 0.0% (0/3) | 100.0% (3/3) |

### `C_locale_gate`

| Locale | Total | Full Action Exact Match | Unsafe Execution Rate | Clear Safe Recall | False Block Rate |
|---|---:|---:|---:|---:|---:|
| `hr-HR` | 30 | 60.0% (18/30) | 30.0% (9/30) | 50.0% (4/8) | 12.5% (1/8) |
| `en-US` | 15 | 33.3% (5/15) | 13.3% (2/15) | 50.0% (2/4) | 25.0% (1/4) |
| `en-GB` | 15 | 46.7% (7/15) | 6.7% (1/15) | 0.0% (0/3) | 100.0% (3/3) |

## 4. Paired Cluster Bootstrap Comparisons (2,000 Resamples)

- **`C_vs_A` (`full_action_exact_match` Δ):** `+40.00%` (95% CI: `[+28.33%, +51.67%]`, 60 paraphrase groups)
- **`C_vs_A` (`unsafe_execution_rate` Δ):** `-46.67%` (95% CI: `[-58.33%, -33.33%]`, 60 paraphrase groups)
- **`C_vs_B` (`full_action_exact_match` Δ):** `+40.00%` (95% CI: `[+28.33%, +51.67%]`, 60 paraphrase groups)
- **`C_vs_B` (`unsafe_execution_rate` Δ):** `-46.67%` (95% CI: `[-58.33%, -33.33%]`, 60 paraphrase groups)
- **`C_vs_E` (`full_action_exact_match` Δ):** `+36.67%` (95% CI: `[+25.00%, +48.33%]`, 60 paraphrase groups)
- **`C_vs_E` (`unsafe_execution_rate` Δ):** `-43.33%` (95% CI: `[-56.67%, -31.67%]`, 60 paraphrase groups)
- **`C_vs_Ab1` (`full_action_exact_match` Δ):** `+1.67%` (95% CI: `[+0.00%, +5.00%]`, 60 paraphrase groups)
- **`C_vs_Ab1` (`unsafe_execution_rate` Δ):** `-3.33%` (95% CI: `[-8.33%, +0.00%]`, 60 paraphrase groups)
- **`C_vs_Ab2` (`full_action_exact_match` Δ):** `+25.00%` (95% CI: `[+15.00%, +36.67%]`, 60 paraphrase groups)
- **`C_vs_Ab2` (`unsafe_execution_rate` Δ):** `-31.67%` (95% CI: `[-43.33%, -20.00%]`, 60 paraphrase groups)
- **`C_vs_Ab3` (`full_action_exact_match` Δ):** `+1.67%` (95% CI: `[+0.00%, +5.00%]`, 60 paraphrase groups)
- **`C_vs_Ab3` (`unsafe_execution_rate` Δ):** `-3.33%` (95% CI: `[-8.33%, +0.00%]`, 60 paraphrase groups)

## 5. Error Taxonomy & Limitations

- Note: `D_parser_oracle` evaluates deterministic parser coverage on gold field evidence and is an upper-bound diagnostic, not an autonomous agent system.
- Stage 3b deterministic semantic role-binding verification detects invoice/reference numbers misbound as amounts, retracted/negated figures, subtotal/VAT components when a total is present, and unresolved disjunctive choices, while reporting residual limitations when multiple identical-role figures lack lexical markers.
