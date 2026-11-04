# Dataset Cards (`benchmark-v1` & `adversarial-v1`)

See also [`data/benchmark-v1/data-card.md`](../data/benchmark-v1/data-card.md), [`data/benchmark-v1/manifest.json`](../data/benchmark-v1/manifest.json), and [`data/adversarial-v1/manifest.json`](../data/adversarial-v1/manifest.json).

---

## 1. Primary Benchmark Dataset (`benchmark-v1`)

- **Dataset Version:** `1.1.0`
- **File:** `data/benchmark-v1/cases.jsonl`
- **SHA-256 Digest:** `b535072ca516099cab49256f33892e3f46e9d50dd1d1e73668e011dfc511d734`
- **Total Audited Cases:** `600`
- **Paraphrase Groups:** `162` (enforced zero paraphrase-group leakage between `dev` and `test` splits)
- **Splits:**
  - `dev`: `120` cases (32 paraphrase groups)
  - `test`: `480` held-out cases (130 paraphrase groups)
  - `smoke`: `12` cases (stratified subset)
  - `pilot`: `60` cases (stratified subset)
- **Locale Distribution:**
  - `hr-HR`: `300` cases (50.0%)
  - `en-US`: `150` cases (25.0%)
  - `en-GB`: `150` cases (25.0%)
- **Target Gate Decision Distribution:**
  - `awaiting_confirmation`: `327` cases (54.5%)
  - `needs_clarification`: `109` cases (18.2%)
  - `blocked`: `109` cases (18.2%)
  - `no_action`: `55` cases (9.2%)
- **Deterministic Oracle Verification:** 600/600 cases verified by `D_parser_oracle` (`Exact Match = 100.0%`, `Unsafe = 0.0%`).

---

## 2. Adversarial Evaluation Dataset (`adversarial-v1`)

- **Dataset Version:** `1.1.0`
- **File:** `data/adversarial-v1/cases.jsonl`
- **SHA-256 Digest:** `22bb60d717bb883d37c4c7c72c087442a81a2f0a45256c1ce8f532876543c9e4`
- **Total Audited Cases:** `60`
- **Paraphrase Groups:** `60`
- **Threat Categories Covered:**
  - `ATTACK_UNICODE_CONFUSABLE`: Cyrillic/homoglyph spoofing (`\u0430`, `\u0435`, `\u043e`), zero-width spaces (`\u200B`), non-breaking spaces.
  - `ATTACK_ROLE_SWAP`: Multi-amount decoy prompts (invoice reference numbers, VAT subtotals, retracted numbers).
  - `ATTACK_DST_GAP_FOLD`: Nonexistent spring-forward gap wall times (`02:30`) and ambiguous fall-back fold wall times (`02:30`).
  - `ATTACK_PROMPT_INJECTION`: Fake tool JSON, instruction override prefixes (`"Ignore previous instructions"`), unquoted tool names.
  - `ATTACK_UNSUPPORTED_CURRENCY`: Generic unsupported currencies (`DEM`, `XYZ`) and currency conflicts.
  - `ATTACK_SCHEMA_CORRUPTION`: Duplicate JSON keys, out-of-bounds quote offsets, fabricated span offsets.
- **Locale Distribution:**
  - `hr-HR`: `30` cases (50.0%)
  - `en-US`: `15` cases (25.0%)
  - `en-GB`: `15` cases (25.0%)
- **Target Gate Decision Distribution:**
  - `blocked`: `30` cases (50.0%)
  - `awaiting_confirmation`: `15` cases (25.0%)
  - `needs_clarification`: `8` cases (13.3%)
  - `no_action`: `7` cases (11.7%)
- **Deterministic Oracle Verification:** 60/60 cases verified by `D_parser_oracle` (`Exact Match = 100.0%`, `Unsafe = 0.0%`).
