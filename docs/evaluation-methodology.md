# Evaluation Methodology & Benchmark Protocol

## 1. Systems & Pipelines Compared

Every benchmark case is evaluated across isolated execution pipelines:

1. **System A — `A_raw_model_baseline` (`RawModelBaselinePipeline`):** Parses raw model JSON and applies naive English/US-biased float and date extraction without source evidence checks, locale profiles, DST checks, or confirmation gates.
2. **System B — `B_schema_only_baseline` (`SchemaOnlyBaselinePipeline`):** Enforces strict Pydantic `ActionProposal` schema and tool/field allowlists, but uses naive non-locale value conversion without exact-quote source verification.
3. **System E — `E_strong_conventional_baseline` (`StrongConventionalBaselinePipeline`):** State-of-the-art conventional tool-use pipeline incorporating `python-dateutil` fuzzy parser, regex heuristics, ISO 4217 currency lookup, and standard JSON schema validation, but lacking exact verbatim quote evidence resolution and deterministic locale grammar profiles.
4. **System C — `C_locale_gate` (`LocaleGatePipeline`):** The full 6-stage LocaleGate pipeline with exact-quote code-point & UTF-16 resolution, semantic role binding, deterministic locale parsers (`hr-HR`, `en-US`, `en-GB`), DST fold/gap detection, cross-field invariants, and capability token confirmation.
5. **System D — `D_parser_oracle` (`ParserOraclePipeline`):** Diagnostic upper-bound comparator that feeds gold source evidence spans directly into `LocaleGatePipeline` to verify deterministic parser coverage independently of LLM extraction errors (not an autonomous agent system).

### Ablation Systems
- **`Ab1_locale_parser_no_evidence`:** Locale-aware deterministic parser enabled, but exact-quote evidence resolution disabled (model-proposed ungrounded strings are accepted directly into the locale parser).
- **`Ab2_evidence_only_naive_parser`:** Exact-quote evidence resolution enabled (quotes must exist in text), but locale-aware parser replaced with naive English/US-biased parser.
- **`Ab3_no_role_binding`:** Exact-quote and locale parsers enabled, but Stage 3b semantic role binding disabled (decoy invoice numbers and subtotals are not checked for role conflicts).

---

## 2. Metrics & Statistical Protocol

- **Full Action Exact Match Rate:** Fraction of all cases where the system matches the gold decision, tool name, and all canonical arguments with zero error.
- **Unsafe Execution Rate:** Fraction of cases where a system would execute or propose for confirmation an ungrounded action, a wrong tool, corrupted canonical arguments, or an action on an ambiguous/malicious/blocked case.
- **Clear Safe Execution Recall:** Proportion of truly valid, unambiguous requests that are successfully brought to `AWAITING_CONFIRMATION` without false blocks.
- **False Block Rate on Valid Cases:** Fraction of unambiguous valid cases incorrectly rejected or blocked.
- **Clarification Recall & Precision:** Recall and precision on cases whose ground truth is `NEEDS_CLARIFICATION`.
- **Wilson Score 95% Confidence Intervals:** Computed for all individual system rates.
- **Paired Cluster-Bootstrap 95% Confidence Intervals:** Computed by resampling entire `paraphrase_group_id` clusters (`n_resamples = 2,000`, `seed = 20261103`) to account for syntactic paraphrase correlations.

---

## 3. Evaluated Model Artifacts

1. **`Qwen3.5-4B-Q4_K_M.gguf`:** SHA-256 `00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4`
2. **`gemma-4-E2B-it-Q4_K_M.gguf`:** SHA-256 `740185b21d22ceb83a11c3aa62ad5842ef32c70f6096d756bbee85a1e4ec34b8`
