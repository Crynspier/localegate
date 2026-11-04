# Reproducibility Guide & Verification Matrix

This guide provides step-by-step instructions to reproduce all evaluation metrics, benchmarks, tests, and artifacts in LocaleGate.

---

## 1. Environment & Hardware Specifications

- **OS:** Windows 11 (64-bit)
- **Python:** 3.12+ (managed with `uv`)
- **Package Manager:** `uv` 0.5+
- **Local Inference Engine:** `llama.cpp` / `llama-server` (`version: 0.8.0-dev (build 12063, commit 2cbca389f)`)
- **Inference Hardware:** CPU execution (`--n-gpu-layers 0`, context size `4096`, strictly bound to `127.0.0.1:8080`)
- **Node / Web:** Node 20+, npm 10+, Vite 5, React 19

---

## 2. Model & Dataset Provenance & Cryptographic Hashes

All benchmark runs and live tests are strictly pinned by SHA-256 digests:

| Component | File / Path | Size (Bytes) | SHA-256 Digest |
|---|---|---:|---|
| **Primary Model** | `Qwen3.5-4B-Q4_K_M.gguf` | 2,740,937,888 | `00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4` |
| **Secondary Model** | `gemma-4-E2B-it-Q4_K_M.gguf` | 3,106,738,272 | `740185b21d22ceb83a11c3aa62ad5842ef32c70f6096d756bbee85a1e4ec34b8` |
| **Benchmark Dataset** | `data/benchmark-v1/cases.jsonl` | 593,156 | `b535072ca516099cab49256f33892e3f46e9d50dd1d1e73668e011dfc511d734` |
| **Adversarial Dataset** | `data/adversarial-v1/cases.jsonl` | 57,341 | `22bb60d717bb883d37c4c7c72c087442a81a2f0a45256c1ce8f532876543c9e4` |
| **Prompt Template** | `prompts/proposal-v1.txt` | 3,498 | `e523762c989aed6fc34c989b40f9f9676a8c35c8ccbb4326c71fbeb278c81527` |

---

## 3. Reproduction Commands

### 3.1 Python Unit & Security Suite (144 Tests)
```powershell
uv sync --locked --dev
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
```

### 3.2 Web Frontend Suite & Build
```powershell
cd web
npm test -- --run
npm run typecheck
npm run lint
npm run build
cd ..
```

### 3.3 Dataset Validation & Zero-Leakage Audit
```powershell
uv run locale-gate eval validate-dataset
```

### 3.4 Parser Oracle Baseline (Full 600-case Benchmark)
```powershell
uv run locale-gate eval run --model oracle-only --suite full --run-id full_parser_oracle_v1
```

### 3.5 Live Model Preflight Smoke Test
```powershell
# Terminal 1: launch model server
uv run locale-gate model serve --model qwen35-q4km --port 8080

# Terminal 2: run 5-case preflight test
uv run locale-gate model smoke-test
```

### 3.6 Evaluation Suite Runs (Adversarial Suite: 60 Cases)
```powershell
# Qwen3.5-4B
uv run locale-gate eval run --model qwen35-q4km --suite adversarial --run-id eval_adversarial_qwen35

# Gemma-4-E2B-It
uv run locale-gate model serve --model gemma4-e2b-q4km --port 8080
uv run locale-gate eval run --model gemma4-e2b-q4km --suite adversarial --run-id eval_adversarial_gemma4
```

### 3.7 Model Adversarial Regression Suite (4 Qwen + 9 Gemma Named Fixtures)
```powershell
uv run pytest tests/regression/test_adversarial_model_regressions.py -v
```

### 3.8 Repeated Multi-Process Barrier Concurrency Stress Experiment (100 Repetitions @ 4, 8, 16 Workers)
```powershell
uv run python scripts/run_repeated_concurrency_stress.py --workers 4,8,16 --repetitions 100
```

---

## 4. Sampling Profiles & Seed Control

Sampling parameters are deterministically controlled across all evaluation runs:
- `seed`: `42` / `20261103`
- `temperature`: `0.0` (greedy deterministic decoding)
- `top_p`: `1.0`
- `top_k`: `1`
- `max_tokens`: `512`
- `reasoning_mode`: `--reasoning off` / `{"reasoning": "off"}` (thinking tokens suppressed)
- `jinja`: `true` (native embedded chat template)

---

## 5. Machine-Readable Reproducibility Manifests

All provenance metadata, cryptographic digests, runtime versions, and hyperparameters are committed as machine-readable JSON manifests:
- Consolidated Manifest: [`artifacts/evaluations/evaluation_provenance_manifest.json`](../artifacts/evaluations/evaluation_provenance_manifest.json)
- Qwen3.5-4B Run Manifest: [`artifacts/evaluations/eval_adversarial_20261104T105609Z_2a101b/reproducibility_manifest.json`](../artifacts/evaluations/eval_adversarial_20261104T105609Z_2a101b/reproducibility_manifest.json)
- Gemma-4-E2B-It Run Manifest: [`artifacts/evaluations/eval_adversarial_20261104T111528Z_4e4e91/reproducibility_manifest.json`](../artifacts/evaluations/eval_adversarial_20261104T111528Z_4e4e91/reproducibility_manifest.json)
- Repeated Concurrency Stress Report: [`artifacts/analysis/concurrency_stress_report.json`](../artifacts/analysis/concurrency_stress_report.json)

