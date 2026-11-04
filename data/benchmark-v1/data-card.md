# LocaleGate Diagnostic Benchmark v1 — Data Card

## 1. Dataset Summary

- **Dataset Version:** `1.1.0`
- **SHA-256 (`cases.jsonl`):** `b535072ca516099cab49256f33892e3f46e9d50dd1d1e73668e011dfc511d734`
- **Total Audited Cases:** `600` (`0` draft cases)
- **Locale Distribution:**
  - Croatian (`hr-HR`): `300` cases
  - American English (`en-US`): `150` cases
  - British English (`en-GB`): `150` cases
- **Suites:**
  - `smoke`: `12` cases
  - `pilot`: `60` cases
  - `full`: `600` cases
- **Splits (Grouped by `paraphrase_group` with zero group leakage):**
  - `dev`: `120` cases
  - `test`: `480` cases

## 2. Intended Use & Scope

This corpus is a controlled diagnostic benchmark evaluating whether an evidence-backed, deterministic locale-aware execution boundary (`LocaleGate`) prevents silent tool-argument corruption on Croatian/EU and English financial and scheduling tool calls (`submit_expense`, `schedule_event`).

## 3. Provenance, Privacy & Review Process

- All 600 prompts are synthetic, authored specifically for this benchmark, and contain zero personally identifiable information (PII) or scraped private messages.
- Every case includes a fixed `reference_datetime`, explicit BCP 47 `locale`, explicit IANA `timezone`, gold expected decision, canonical arguments (stored strictly as strings, never binary floats), and a `gold_proposal` verified through the deterministic `ParserOraclePipeline`.

## 4. Limitations

- This benchmark is a diagnostic instrument over explicit grammar and ambiguity categories (`hr-HR`, `en-US`, `en-GB`); it does not claim to represent all natural Croatian or English conversational dialects or all enterprise tool domains.
