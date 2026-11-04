# LocaleGate Adversarial Benchmark v2 (`adversarial-v2`) — Data Card

## 1. Dataset Summary

- **Dataset Version:** `1.2.0`
- **SHA-256 (`cases.jsonl`):** `e10c4934c7a98eb74b44c24ace30b0b0af77520583205691388c639cd55dbc8d`
- **Total Audited Cases:** `30` (`10` `hr-HR`, `10` `en-US`, `10` `en-GB`)
- **Paraphrase Groups:** `30` isolated groups (`0` cross-split or cross-case leakage)
- **Status:** 100% audited, zero draft cases
- **Decision Distribution:**
  - `awaiting_confirmation`: 9
  - `blocked`: 13
  - `needs_clarification`: 6
  - `no_action`: 2
- **Suite Membership:** `full`, `adversarial`
- **Split Membership:** `test`

## 2. Threat Categories & Coverage

1. **Croatian EUR Formatting & Generic Unsupported Currencies:**
   - Standard EUR amounts using Croatian decimal-comma and grouping conventions (`150,00 €`, `2.500,00 €`).
   - Malformed grouping (`12.50,00 €`) blocked with `MONEY_INVALID_GROUPING`.
   - Generic unsupported currency markers (`DEM`) blocked with `CURRENCY_UNSUPPORTED`.
2. **Daylight Saving Time (DST) Fall-Back & Spring-Forward Transitions:**
   - Ambiguous fall-back local wall time (`2026-10-25 02:30` in `Europe/Zagreb`) occurring twice, yielding `needs_clarification` with `DATETIME_DST_AMBIGUOUS`.
   - Spring-forward non-existent local wall clock gaps (`2026-03-29 02:30` Zagreb, `2026-03-08 02:30` New York, `2026-03-29 01:30` London) blocked with `DATETIME_DST_NONEXISTENT`.
3. **Multi-Role Financial Decoys & Semantic Roles:**
   - Net total vs discounts, tips, subtotals, VAT amounts, and refunds.
4. **Unicode Spoofing & Format Injection:**
   - Mixed-script Cyrillic confusable homoglyphs (`U+0415` Cyrillic capital IE in currency literals).
   - Zero-width space (`U+200B`) numeric injection.
   - Bidirectional formatting overrides (`U+202E`).
5. **Temporal Inversion Bounds:**
   - End datetime strictly prior to start datetime blocked with `EVENT_END_BEFORE_START`.
6. **Policy Ambiguity & Negative Constraints:**
   - Strict relative weekday dates without explicit calendar date requiring clarification (`DATE_AMBIGUOUS_ORDER`).
   - Explicit negative instructions correctly yielding `no_action` (`OK_NO_ACTION`).

## 3. Dataset Integrity & Isolation Verification

- **Split Isolation:** 0 paraphrase groups overlap between dev and test.
- **Oracle Pipeline Validation:** 30/30 (100%) gold proposals yield exact expected decisions, reason codes, and canonical arguments when evaluated through `ParserOraclePipeline`.

## 4. Benchmark Disclosure & Secrecy Reclassification

- **Initial Design:** `adversarial-v2` was originally designed as a held-out challenge benchmark for evaluating post-v1 model robustness.
- **Disclosure Status:** Following public repository disclosure in early commits, `adversarial-v2` is formally reclassified as a **publicly disclosed evaluation and regression challenge suite**. It is no longer represented as an unexposed sealed benchmark in research reporting.
- **Working Tree Policy:** To avoid contaminating downstream training corpora or evaluation setups, `cases.jsonl` is un-tracked from the public repository working tree and excluded from minimal release archives.
- **Future Challenge Benchmark:** Subsequent sealed/held-out model evaluations will be conducted against an independently maintained `adversarial-v3` benchmark suite hosted outside public repositories.

