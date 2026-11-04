# LocaleGate Evaluation Report — `smoke_qwen35_q4km_nonthinking`

## 1. Run Configuration & Provenance

- **Suite:** `smoke` (12 audited cases)
- **Timestamp (UTC):** `2026-11-03T22:30:20.113503+00:00`
- **Model Artifact Alias:** `local-qwen35-4b-q4km`
- **Model Family / Quantization:** `Qwen3.5-4B` / `Q4_K_M`
- **Model SHA-256:** `skipped`
- **Runtime:** `version: 0.8.0-dev (build 12063, commit 2cbca389f)`
- **Context Size / GPU Layers:** `4096` / `0`
- **Sampling Profile:** `{"name": "qwen35_q4km_recommended_nonthinking", "temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5, "max_tokens": 512, "enable_thinking": false, "seed": 20261103}`
- **Dataset SHA-256:** `6b7611b6f5ed0954c012280e1b074e4faaa7850377c90d5db0dac888209be8da`
- **Prompt Version / SHA-256:** `proposal-v1` (`aa26fbfe6f3aa071...`)

## 2. System Comparison Summary (95% Wilson Confidence Intervals)

| System | Full Action Exact Match | Clear Safe Execution Recall | Unsafe Execution Rate (Overall) | False Block Rate (Valid Cases) | Latency p50 / p95 (ms) |
|---|---:|---:|---:|---:|---:|
| `D_parser_oracle` | 100.0% [75.8%, 100.0%] (12/12) | 100.0% [61.0%, 100.0%] (6/6) | 0.0% [0.0%, 24.2%] (0/12) | 0.0% [0.0%, 39.0%] (0/6) | 0.0 / 0.0 |
| `A_raw_model_baseline` | 16.7% [4.7%, 44.8%] (2/12) | 16.7% [3.0%, 56.4%] (1/6) | 83.3% [55.2%, 95.3%] (10/12) | 0.0% [0.0%, 39.0%] (0/6) | 15735.3 / 19636.5 |
| `B_schema_only_baseline` | 16.7% [4.7%, 44.8%] (2/12) | 16.7% [3.0%, 56.4%] (1/6) | 83.3% [55.2%, 95.3%] (10/12) | 0.0% [0.0%, 39.0%] (0/6) | 15735.3 / 19636.5 |
| `C_locale_gate` | 100.0% [75.8%, 100.0%] (12/12) | 100.0% [61.0%, 100.0%] (6/6) | 0.0% [0.0%, 24.2%] (0/12) | 0.0% [0.0%, 39.0%] (0/6) | 15735.3 / 19636.5 |

## 3. Per-Locale Breakdown

### `D_parser_oracle`

| Locale | Total | Full Action Exact Match | Unsafe Execution Rate | Clear Safe Recall | False Block Rate |
|---|---:|---:|---:|---:|---:|
| `hr-HR` | 6 | 100.0% (6/6) | 0.0% (0/6) | 100.0% (3/3) | 0.0% (0/3) |
| `en-US` | 3 | 100.0% (3/3) | 0.0% (0/3) | 100.0% (2/2) | 0.0% (0/2) |
| `en-GB` | 3 | 100.0% (3/3) | 0.0% (0/3) | 100.0% (1/1) | 0.0% (0/1) |

### `A_raw_model_baseline`

| Locale | Total | Full Action Exact Match | Unsafe Execution Rate | Clear Safe Recall | False Block Rate |
|---|---:|---:|---:|---:|---:|
| `hr-HR` | 6 | 16.7% (1/6) | 83.3% (5/6) | 0.0% (0/3) | 0.0% (0/3) |
| `en-US` | 3 | 33.3% (1/3) | 66.7% (2/3) | 50.0% (1/2) | 0.0% (0/2) |
| `en-GB` | 3 | 0.0% (0/3) | 100.0% (3/3) | 0.0% (0/1) | 0.0% (0/1) |

### `B_schema_only_baseline`

| Locale | Total | Full Action Exact Match | Unsafe Execution Rate | Clear Safe Recall | False Block Rate |
|---|---:|---:|---:|---:|---:|
| `hr-HR` | 6 | 16.7% (1/6) | 83.3% (5/6) | 0.0% (0/3) | 0.0% (0/3) |
| `en-US` | 3 | 33.3% (1/3) | 66.7% (2/3) | 50.0% (1/2) | 0.0% (0/2) |
| `en-GB` | 3 | 0.0% (0/3) | 100.0% (3/3) | 0.0% (0/1) | 0.0% (0/1) |

### `C_locale_gate`

| Locale | Total | Full Action Exact Match | Unsafe Execution Rate | Clear Safe Recall | False Block Rate |
|---|---:|---:|---:|---:|---:|
| `hr-HR` | 6 | 100.0% (6/6) | 0.0% (0/6) | 100.0% (3/3) | 0.0% (0/3) |
| `en-US` | 3 | 100.0% (3/3) | 0.0% (0/3) | 100.0% (2/2) | 0.0% (0/2) |
| `en-GB` | 3 | 100.0% (3/3) | 0.0% (0/3) | 100.0% (1/1) | 0.0% (0/1) |

## 4. Paired Cluster Bootstrap Comparisons (2,000 Resamples)

- **`C_vs_A` (`full_action_exact_match` Δ):** `+83.33%` (95% CI: `[+58.33%, +100.00%]`, 12 paraphrase groups)
- **`C_vs_A` (`unsafe_execution_rate` Δ):** `-83.33%` (95% CI: `[-100.00%, -58.33%]`, 12 paraphrase groups)
- **`C_vs_B` (`full_action_exact_match` Δ):** `+83.33%` (95% CI: `[+58.33%, +100.00%]`, 12 paraphrase groups)
- **`C_vs_B` (`unsafe_execution_rate` Δ):** `-83.33%` (95% CI: `[-100.00%, -58.33%]`, 12 paraphrase groups)

## 5. Error Taxonomy & Limitations

-Note: `D_parser_oracle` evaluates deterministic parser coverage on gold field evidence and is an upper-bound diagnostic, not an autonomous agent system.
- Evidence verification guarantees verbatim source provenance and deterministic parsing under the configured locale profile, but cannot independently verify whether the model bound a valid substring to the user's intended semantic field role.
