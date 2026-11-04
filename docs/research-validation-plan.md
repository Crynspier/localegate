# LocaleGate: Research Validation 1.0 Milestone Roadmap

**Target Version:** `0.1.0` (Research Release Candidate)  
**Objective:** Consolidate experimental rigor, evaluation reproducibility, empirical audit trails, and open-science verification artifacts prior to public open-source release.

---

## 1. Core Architecture Status (100% Complete)

All foundational architectural pillars have been implemented, tested, and empirically benchmarked:

- [x] **Deterministic Locale-Aware Parsing:** Exact `Decimal` arithmetic, ISO 4217 minor-unit bounds, 4-digit year enforcement, and zoneinfo DST fold/gap boundary detection (`hr-HR`, `en-US`, `en-GB`).
- [x] **Evidence Quotation Verification:** Strict Unicode NFC codepoint matching against source text; forbidden character offset generation by untrusted LLMs.
- [x] **Closest-Governing-Cue Semantic Role Binding:** Disambiguates candidate numbers and dates (tax components, reversed temporal clauses, disjunction alternatives).
- [x] **Two-Phase Confirmation Capabilities:** Single-use HMAC-authenticated capability tokens bound to deterministic SHA-256 action digests.
- [x] **Simultaneous Barrier Concurrency Protection:** Cross-process `multiprocessing.Barrier` synchronization with `BEGIN IMMEDIATE` SQLite transaction isolation, preventing duplicate mutations under load.
- [x] **Dual-Model Adversarial Evaluation:** Tested against local GGUF models (`Qwen3.5-4B` and `Gemma-4-E2B-It`) running strictly locally on CPU without reasoning artifacts.
- [x] **Separate Proposal and Execution Metrics:** 6-metric taxonomy clearly distinguishing harmless model omissions from genuinely unsafe proposals and unauthorized database commits.
- [x] **Local Reproducible Development Workflow:** Zero cloud dependencies, loopback-only binding (`127.0.0.1`), unified CLI (`locale-gate`), and 100% offline test execution.

---

## 2. Research Validation 1.0 Objectives Matrix

| Validation Objective | Artifact / Code Location | Verification Evidence | Status |
| :--- | :--- | :--- | :---: |
| **1. Repeated Concurrency Stress Experiment** | `scripts/run_repeated_concurrency_stress.py`<br>`docs/concurrency-stress-report.md` | 100 repetitions across 4, 8, and 16 concurrent processes (300 rounds, 2,800 attempts) verifying 0 duplicate mutations. | **Complete** |
| **2. Consolidated Model & Dataset Provenance Manifests** | `artifacts/evaluations/evaluation_provenance_manifest.json`<br>`artifacts/evaluations/*/reproducibility_manifest.json` | Machine-readable JSON specifications containing Model SHA-256, Prompt SHA-256, Dataset SHA-256, `llama.cpp` build/commit, seed, temperature, and environment tags. | **Complete** |
| **3. Full Case-Level Failure Analysis & Named Regression Fixtures** | `artifacts/analysis/adversarial-v1-case-reconciliation.csv`<br>`tests/regression/test_adversarial_model_regressions.py` | Named, reproducible regression fixtures for all 4 Qwen and 9 Gemma identified unsafe proposals, guaranteeing they fail closed. | **Complete** |
| **4. Per-Locale and Per-Attack-Family Statistical Reports** | `artifacts/evaluations/*/metrics.json`<br>`docs/implementation-report.md` | Granular metrics breakdown by locale (`hr-HR`, `en-US`, `en-GB`) and attack family (homoglyphs, zero-width spaces, currency confusion, DST fold/gap, prompt injection). | **Complete** |
| **5. Independent Reproduction Instructions** | `docs/reproducibility.md`<br>`README.md` (Sections 5 & 12) | Step-by-step terminal commands for reproducing tests, oracle baselines, smoke runs, frontend builds, and stress experiments. | **Complete** |
| **6. External Technical Evaluation Package** | `scripts/package_evaluation_release.py`<br>`artifacts/evaluations/` | Self-contained evaluation archive containing test cases, run logs, confusion matrices, metrics, and reconciliation tables. | **Ready for Release** |

---

## 3. Scientific Defense & Scope Boundaries

1. **Deterministic Parser Oracle vs. Live Model Agents:**
   The 600-case deterministic parser oracle (`full_parser_oracle_v1`) achieves 100% exact match and 0.0% unsafe execution. As documented across the repository, this benchmark serves as a diagnostic upper bound establishing complete locale grammar coverage on ground truth spans. Live model evaluations are conducted separately on the 60-case adversarial corpus (`adversarial-v1`).
2. **Conditional Containment vs. Universal Guarantees:**
   While LocaleGate intercepted 100% of genuinely unsafe proposals (4/4 for Qwen, 9/9 for Gemma), these sample observations carry wide binomial confidence intervals ([51.0%, 100%] and [70.1%, 100%]). The research claims that the deterministic gate successfully intercepted all observed unsafe model proposals in the audited adversarial suite, rather than guaranteeing zero edge-case risk across arbitrary unseen distributions.
3. **Simultaneous Barrier-Released Contention:**
   Multi-process concurrency testing employs `multiprocessing.Barrier` to synchronize process arrival, releasing all processes to contend simultaneously under OS kernel scheduling. SQLite transaction isolation (`BEGIN IMMEDIATE`) reliably enforces mutual exclusion with zero duplicate mutations across thousands of attempts.
