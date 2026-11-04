# LocaleGate Evaluation Report — `full_parser_oracle_v1`

## 1. Run Configuration & Provenance

- **Suite:** `full` (600 audited cases)
- **Timestamp (UTC):** `2026-11-03T22:22:28.038654+00:00`
- **Model Artifact Alias:** `local-qwen35-4b-q4km`
- **Model Family / Quantization:** `Qwen3.5-4B` / `Q4_K_M`
- **Model SHA-256:** `00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4`
- **Runtime:** `version: 0.8.0-dev (build 12063, commit 2cbca389f)`
- **Context Size / GPU Layers:** `4096` / `0`
- **Sampling Profile:** `{"name": "qwen35_q4km_primary", "temperature": 0.0, "top_p": 1.0, "top_k": 1, "min_p": 0.0, "presence_penalty": 0.0, "max_tokens": 512, "enable_thinking": false, "seed": 20261103}`
- **Dataset SHA-256:** `6b7611b6f5ed0954c012280e1b074e4faaa7850377c90d5db0dac888209be8da`
- **Prompt Version / SHA-256:** `proposal-v1` (`aa26fbfe6f3aa071...`)

## 2. System Comparison Summary (95% Wilson Confidence Intervals)

| System | Full Action Exact Match | Clear Safe Execution Recall | Unsafe Execution Rate (Overall) | False Block Rate (Valid Cases) | Latency p50 / p95 (ms) |
|---|---:|---:|---:|---:|---:|
| `D_parser_oracle` | 100.0% [99.4%, 100.0%] (600/600) | 100.0% [98.8%, 100.0%] (327/327) | 0.0% [0.0%, 0.6%] (0/600) | 0.0% [0.0%, 1.2%] (0/327) | 0.0 / 0.0 |

## 3. Per-Locale Breakdown

### `D_parser_oracle`

| Locale | Total | Full Action Exact Match | Unsafe Execution Rate | Clear Safe Recall | False Block Rate |
|---|---:|---:|---:|---:|---:|
| `hr-HR` | 300 | 100.0% (300/300) | 0.0% (0/300) | 100.0% (165/165) | 0.0% (0/165) |
| `en-US` | 150 | 100.0% (150/150) | 0.0% (0/150) | 100.0% (81/81) | 0.0% (0/81) |
| `en-GB` | 150 | 100.0% (150/150) | 0.0% (0/150) | 100.0% (81/81) | 0.0% (0/81) |

## 5. Error Taxonomy & Limitations

-Note: `D_parser_oracle` evaluates deterministic parser coverage on gold field evidence and is an upper-bound diagnostic, not an autonomous agent system.
- Evidence verification guarantees verbatim source provenance and deterministic parsing under the configured locale profile, but cannot independently verify whether the model bound a valid substring to the user's intended semantic field role.
