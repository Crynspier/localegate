# LocaleGate Evaluation Report — `eval_smoke_20261103T234730Z_3d33dd`

## 1. Run Configuration & Provenance

- **Suite:** `smoke` (12 audited cases)
- **Timestamp (UTC):** `2026-11-03T23:47:35.052120+00:00`
- **Model Artifact Alias:** `local-qwen35-4b-q4km`
- **Model Family / Quantization:** `Qwen3.5-4B` / `Q4_K_M`
- **Model SHA-256:** `00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4`
- **Runtime:** `version: 0.8.0-dev (build 12063, commit 2cbca389f)`
- **Context Size / GPU Layers:** `4096` / `0`
- **Sampling Profile:** `{"name": "qwen35_q4km_primary", "temperature": 0.0, "top_p": 1.0, "top_k": 1, "min_p": 0.0, "presence_penalty": 0.0, "max_tokens": 512, "enable_thinking": false, "seed": 20261103}`
- **Dataset SHA-256:** `4978965f9a5cb1b9e2f7619b38b4df81906de4bebe7b6c9714ba3abb8eca3db3`
- **Prompt Version / SHA-256:** `proposal-v1` (`aa26fbfe6f3aa071...`)

## 2. System & Ablation Comparison Summary (95% Wilson Confidence Intervals)

| System | Full Action Exact Match | Clear Safe Execution Recall | Unsafe Execution Rate (Overall) | False Block Rate (Valid Cases) | Latency p50 / p95 (ms) |
|---|---:|---:|---:|---:|---:|
| `D_parser_oracle` | 100.0% [75.8%, 100.0%] (12/12) | 100.0% [61.0%, 100.0%] (6/6) | 0.0% [0.0%, 24.2%] (0/12) | 0.0% [0.0%, 39.0%] (0/6) | 0.0 / 0.0 |

## 3. Per-Locale Breakdown

### `D_parser_oracle`

| Locale | Total | Full Action Exact Match | Unsafe Execution Rate | Clear Safe Recall | False Block Rate |
|---|---:|---:|---:|---:|---:|
| `hr-HR` | 6 | 100.0% (6/6) | 0.0% (0/6) | 100.0% (3/3) | 0.0% (0/3) |
| `en-US` | 3 | 100.0% (3/3) | 0.0% (0/3) | 100.0% (2/2) | 0.0% (0/2) |
| `en-GB` | 3 | 100.0% (3/3) | 0.0% (0/3) | 100.0% (1/1) | 0.0% (0/1) |

## 5. Error Taxonomy & Limitations

- Note: `D_parser_oracle` evaluates deterministic parser coverage on gold field evidence and is an upper-bound diagnostic, not an autonomous agent system.
- Stage 3b deterministic semantic role-binding verification detects invoice/reference numbers misbound as amounts, retracted/negated figures, subtotal/VAT components when a total is present, and unresolved disjunctive choices, while reporting residual limitations when multiple identical-role figures lack lexical markers.
