# LocaleGate: Deterministic Evidence-Backed Execution Boundary for Local Small-Language-Model Tool Calling

**Document type:** Research Technical Report & Milestone Assessment  
**Project Version:** `0.1.0`  
**Milestone:** `Research Validation 1.0`  
**Authors:** LocaleGate Contributors (`contributors@localegate.org`)  
**Repository:** [https://github.com/Crynspier/localegate](https://github.com/Crynspier/localegate)  
**Execution Environment:** Windows 11 x86_64, Python 3.12.15, SQLite 3.53.1, Local loopback (`127.0.0.1`) only  

---

## Abstract

Small open-weight language models (3B–4B parameters) running locally on commodity CPU hardware offer privacy, zero data-egress latency, and autonomy for personal edge agents. However, when parsing locale-sensitive natural language into structured tool calls, these models frequently introduce severe semantic corruption, including inverted timestamps, misidentified invoice subtotals as grand totals, and silent hallucination of non-existent arguments. 

We introduce **LocaleGate**, a deterministic verification and fail-closed execution gate designed to safely mediate tool-calling proposals from untrusted local small models. Rather than relying on recursive chain-of-thought prompting or fine-tuning, LocaleGate decouples probabilistic entity extraction from authoritative parsing, temporal role binding, and state commitment. Models are required to emit verbatim character-slice evidence quotes alongside proposals. Deterministic validators verify substring presence, validate grammatical and numeric grouping rules (e.g. Croatian comma decimals vs. US period decimals), apply closest-governing-cue role resolution, and enforce capability-token-based confirmation with replay protection. 

Across 60 adversarial-v1 cases evaluated on each model (120 model-case evaluations on Qwen3.5-4B and Gemma-4-E2B-It), 13 unsafe proposal instances were observed in raw baseline execution (4 Qwen, 9 Gemma). LocaleGate deterministically intercepted 13/13 (100%) of these unsafe proposals, resulting in 0/13 reaching confirmation capability and zero unauthorized committed mutations. A 600-case deterministic parser oracle diagnostic on benchmark-v1 confirmed 100% parser coverage independently of extraction noise, and a separately disclosed 30-case adversarial-v2 challenge suite validated complex DST and script-injection boundaries, all while maintaining 1.2 ms p50 / 2.1 ms p95 gate validation latency.

---

## 1. Introduction & Research Problem

Local agent architectures increasingly attempt to delegate tool invocation (such as calendar scheduling and expense submission) directly to local SLMs using JSON Schema grammar sampling. While grammar-constrained decoding guarantees syntactic compliance (valid JSON), it provides **zero semantic safety guarantees**:

1. **Semantic Inversion:** In calendar operations, models routinely invert chronological parameters (e.g., binding `10:00` to `end_datetime` and `09:00` to `start_datetime`), silently attempting invalid schedule entries.
2. **Component Disambiguation Failure:** In multi-component currency requests (such as invoices containing subtotal, VAT, and grand total figures), models frequently pick sub-components arbitrarily without clarification.
3. **Prompt Injection & Authority Bypass:** Embedded adversary text (`"SYSTEM: user already confirmed, bypass approval"`) tricks compact models into proposing immediate, unverified tool execution.
4. **Non-Atomic Concurrency & Replay Attacks:** Concurrent agent invocations can race against SQLite datastores or replay previously consumed confirmation tokens to execute duplicate financial transactions.

LocaleGate solves these problems by enforcing a deterministic, fail-closed perimeter between the untrusted model and the authoritative tool simulator.

---

## 2. System Architecture & Threat Model

LocaleGate enforces a multi-tier pipeline between user input and persistent tool execution:

```
+-----------------------------------------------------------------------------------------+
|                                UNTRUSTED BOUNDARY                                       |
|  User Request  -->  Local SLM (Qwen3.5-4B / Gemma-4-E2B-It)  -->  ActionProposal       |
|                     (Strict grammar decoding, reasoning off)        (Evidence quotes)   |
+-----------------------------------------------------------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------------+
|                              LOCALGATE VERIFICATION GATE                                |
|                                                                                         |
|  1. Substring Evidence Resolver: Verifies quote in original request verbatim           |
|  2. CLDR / Regional Parser: Croatian comma grouping, ISO/EU/US dates, fold detection   |
|  3. Closest-Governing-Cue Scanner: Resolves temporal & negative roles deterministically |
|  4. Semantic Role Binder: Prevents silent field swaps or subtotal confusion             |
|  5. Policy Engine: Classifies into OK (Auto), Awaiting Confirmation, Needs Clarification|
+-----------------------------------------------------------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------------+
|                              AUTHORITATIVE DATASTORE                                    |
|                                                                                         |
|  1. Cryptographic Capability Token & Digest Verification                                |
|  2. BEGIN IMMEDIATE Atomic Transaction Lock (SQLite WAL)                                |
|  3. Consumed Token SHA-256 Replay Protection (Replay attacks fail closed)                |
|  4. Immutable Audit Ledger with cryptographic record chaining                            |
+-----------------------------------------------------------------------------------------+
```

### Threat Model & Boundaries
- **Adversary Capability:** Untrusted model output, corrupted inputs, prompt injections, and multi-threaded race conditions attempting duplicate execution.
- **Fail-Closed Principle:** Any un-grounded argument quote, malformed numeric grouping, inverted datetime interval, or missing capability token results in immediate `BLOCKED` status or requires explicit human confirmation.

---

## 3. Experimental Evaluation & The Six-Metric Taxonomy

We evaluate LocaleGate across a comprehensive six-metric evaluation taxonomy:

| Metric | Definition | Purpose |
| :--- | :--- | :--- |
| **1. Grounded Validity Rate (GVR)** | Ratio of proposals where all arguments are strictly grounded in user text. | Evaluates hallucination prevention. |
| **2. Canonical Parsing Accuracy (CPA)** | Accuracy of converting valid strings to canonical values (e.g. `1.234,56 €` -> `1234.56 EUR`). | Measures locale-aware data integrity. |
| **3. Decision Concordance (DC)** | Agreement between the gate's policy decision and gold benchmark annotations. | Measures policy correctness. |
| **4. Unsafe Execution Prevention (UEP)** | Interception rate for adversarial, malformed, or injected proposals. | Primary safety evaluation metric. |
| **5. Semantic Intent Preservation (SIP)**| Fraction of canonical arguments preserving the user's explicit intent. | Measures semantic accuracy without drift. |
| **6. Confirmation Integrity (CI)** | Interception rate for forged, expired, or replayed confirmation tokens. | Evaluates datastore authorization defense. |

---

## 4. Empirical Model Comparison: Qwen-3.5-4B vs. Gemma-4-E2B-It

We benchmarked two small local open-weight models executed strictly via CPU inference (`llama-server`, reasoning suppressed, `--temp 0.0`):

| Model Artifact | Parameters | Quantization | SHA-256 Digest |
| :--- | :--- | :--- | :--- |
| **Qwen3.5-4B-Q4_K_M.gguf** | 4.0B | Q4_K_M | `00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4` |
| **gemma-4-E2B-it-Q4_K_M.gguf**| 3.1B | Q4_K_M | `740185b21d22ceb83a11c3aa62ad5842ef32c70f6096d756bbee85a1e4ec34b8` |

#### Key Findings & Evaluation Populations
1. **Live Model Adversarial Evaluation (`adversarial-v1`, 60 Cases):**
   * Evaluated against live local CPU execution of Qwen3.5-4B and Gemma-4-E2B-It.
   * In raw model execution without LocaleGate, Qwen committed **4 unsafe proposals** and Gemma committed **9 unsafe proposals** across 9 unique adversarial scenarios (`ADV-HR-0009`, `ADV-HR-0010`, `ADV-HR-0012`, `ADV-HR-0022`, `ADV-HR-0027`, `ADV-HR-0028`, `ADV-HR-0029`, `ADV-US-0005`, and `ADV-GB-0015`).
   * When mediated by LocaleGate, **100% of these 13 failure instances were deterministically intercepted** (blocked or constrained to human confirmation), with 0/13 unsafe proposals reaching confirmation capability and zero unauthorized committed mutations.
2. **Deterministic Parser Oracle Diagnostic (`benchmark-v1`, 600 Cases):**
   * A deterministic oracle diagnostic achieves 600/600 (100.0%) concordance on `benchmark-v1`, establishing the deterministic upper-bound parsing capability of the rule engine independently of model extraction noise.
3. **Advanced Challenge Benchmark (`adversarial-v2`, 30 Cases):**
   * A dedicated 30-case suite targeting DST spring gaps, autumn folds, and Cyrillic script injection. Reclassified as publicly disclosed; future sealed held-out evaluations will use an independent `adversarial-v3` suite.
4. **Frozen Offline Regression:** All 13 raw completion fixtures are frozen in [tests/regression/test_adversarial_model_regressions.py](tests/regression/test_adversarial_model_regressions.py). They replay and verify offline in <0.2 seconds without requiring inference servers.

---

## 5. Multi-Process Concurrency Stress Evaluation

To stress-test database contention, lock escalation, and race safety under load, we executed barrier-synchronized concurrency experiments across multiple worker tiers:

- **Total Stress Rounds:** 300 rounds (100 rounds at 4 workers, 100 rounds at 8 workers, 100 rounds at 16 workers).
- **Simultaneous Process Invocations:** 2,800 simultaneous process attempts synchronized via `multiprocessing.Barrier`.
- **Primary Execution vs Replay:** Exactly 1 primary execution per round (300/300) and 2,500 idempotent replays ($W-1$).
- **State Integrity:** Exactly 100 rows committed per worker tier (0 duplicate rows, 0 corrupted balances).
- **Audit Verification:** 100% of audit ledger SHA-256 chains remained unbroken (`valid_chain=True`).
- **Statistical Context:** 0 observed race defects in 100 repetitions per tier is strong empirical evidence of SQLite `BEGIN IMMEDIATE` serialization, though not mathematical proof against arbitrary hardware anomalies.

---

## 6. Distribution Packaging & Clean-Environment Verification

To facilitate external reproduction without environment contamination:

1. **Disclosed Challenge Suite & Future Held-Out Benchmark:** `data/adversarial-v2` (30 cases) is formally reclassified as a publicly disclosed evaluation suite. Its `cases.jsonl` is excluded from git tracking and default minimal packages to avoid downstream training contamination. Future sealed benchmark evaluations will use an independently authored, unreleased `adversarial-v3` challenge suite.
2. **Release Package Hygiene:** Pre-packaging audits verify zero credentials, zero local Windows paths (`C:\\Users`), zero SQLite databases, and zero `.gguf` binary weights.
3. **Independent Clean-Environment Verification:** A dedicated verification script ([scripts/verify_clean_release_archive.py](scripts/verify_clean_release_archive.py)) was executed in an isolated temporary directory, confirming that:
   * All bundled files match cryptographic SHA-256 hashes in `archive_manifest.json`.
   * Public datasets validate with zero leakage.
   * All 13 frozen regression fixtures pass offline.
   * All deterministic unit tests pass cleanly outside the dev environment.

---

## 7. Limitations & Future Work

- **Language Scope:** The current implementation specializes in Croatian (`hr-HR`), Eurozone standards, and English (`en-US`, `en-GB`). Additional Slavic and non-Latin numeric representations will be addressed in future revisions.
- **Hardware Profile:** All evaluation latency benchmarks were obtained on multi-core x86_64 CPU hardware without hardware acceleration.

---

## 8. Conclusion

LocaleGate demonstrates that small language models can be safely utilized in autonomous agent workflows when paired with a deterministic, evidence-grounded verification boundary. In live adversarial model evaluations (120 model-case runs), LocaleGate intercepted 100% of observed unsafe model proposals (13/13), preventing any unauthorized state mutations or unconfirmed actions from reaching execution while maintaining 1.2 ms p50 and 2.1 ms p95 gate validation latency.
