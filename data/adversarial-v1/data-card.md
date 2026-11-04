# LocaleGate Adversarial & Stress Benchmark v1 (`adversarial-v1`) — Data Card

## 1. Dataset Summary

- **Dataset Version:** `1.1.0`
- **SHA-256 (`cases.jsonl`):** `22bb60d717bb883d37c4c7c72c087442a81a2f0a45256c1ce8f532876543c9e4`
- **Total Audited Cases:** `60` (`30` `hr-HR`, `15` `en-US`, `15` `en-GB`)
- **Decision Distribution:** `{"awaiting_confirmation": 15, "blocked": 24, "needs_clarification": 10, "no_action": 11}`

## 2. Adversarial Threat Categories Covered

1. **Multi-Candidate Semantic Role-Binding (`FIELD_ROLE_MISMATCH` / `FIELD_ROLE_AMBIGUOUS`):**
   - Invoice/reference numbers alongside subtotals, VAT/tax components, discounts, and totals.
   - Retracted/corrected values (`"nije 85,50 € nego 58,50 €"`, `"not $95.00, instead $59.00"`).
   - Unresolved disjunctive choices (`"85,50 € ili 120,00 €"`).
   - Swapped start/end temporal markers.
2. **Unicode Spoofing & Bidi Override Attacks (`EVIDENCE_SUSPICIOUS_UNICODE`):**
   - Right-to-Left Override (`U+202E`) and zero-width space (`U+200B`) inside monetary literals.
   - Cyrillic/Latin mixed-script homoglyphs (`U+0430` / `U+043E`) in event titles.
   - Combining diacritical marks attached to ASCII digits.
3. **Unsupported and Conflicting Currency Attacks (`CURRENCY_UNSUPPORTED` / `CURRENCY_CONFLICT`):**
   - Expenses with unsupported currency codes (`DEM`, `XYZ`) blocked with `CURRENCY_UNSUPPORTED`.
   - Conflicting currency symbols/codes blocked with `CURRENCY_CONFLICT`.
4. **NFD vs NFC & Supplementary Plane (Emoji) Offset Alignment:**
   - Decomposed Croatian diacritics (`s` + `U+030C`) and leading astral-plane emojis (`🧾`, `✈️`, `🚆`).
5. **Prompt Injection & JSON Duplicate-Key Smuggling (`OK_NO_ACTION`):**
   - Embedded fake tool-call JSON payloads and system override instructions.
