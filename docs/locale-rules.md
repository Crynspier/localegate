# Locale-Specific Parsing & Canonicalization Rules

This document specifies the exact grammar, syntax, and policy rules enforced by LocaleGate for each supported locale (`hr-HR`, `en-US`, `en-GB`).

---

## 1. Croatian (`hr-HR`)

### 1.1 Money & Numbers
- **Decimal Separator:** `,` (comma) strictly required for decimal fractions.
- **Thousands Grouping:** `.` (period), space (`\u0020`), non-breaking space (`\u00A0`), or narrow non-breaking space (`\u202F`).
- **Grouping Rule:** Digits before the decimal must appear in groups of 3 (e.g. `1.234,56 €` or `12 000 €`). Dangling single digits or invalid groupings (e.g. `1.23,45`) are rejected as malformed (`MONEY_FORMAT_INVALID`).
- **Currencies:**
  - Modern currency for `hr-HR` is **Euro** (`EUR`, `€`).
  - Unsupported currencies (e.g. `DEM`, `XYZ`) follow the generic rejection path (`CURRENCY_UNSUPPORTED`).
  - Dollar symbols (`$`): Ambiguous outside `en-US`. Must be clarified (`CURRENCY_AMBIGUOUS_SYMBOL`).

### 1.2 Dates & Times
- **Date Format:** Day-Month-Year (`DD.MM.YYYY.` or `D.M.YYYY.`).
- **Trailing Dot:** Croatian formal orthography requires a trailing dot after the year (e.g. `03.11.2026.` or `3. studenoga 2026.`). LocaleGate accepts both dotted and undotted year representations.
- **Month Names:** Supports both Croatian nominative (`siječanj`, `veljača`, ..., `prosinac`) and genitive forms (`siječnja`, `veljače`, ..., `prosinca`).
- **Weekday Consistency:** If a weekday is specified (e.g. `"utorak, 03.11.2026."`), LocaleGate verifies against the calendar. Mismatches trigger `DATE_WEEKDAY_MISMATCH` (`BLOCKED`).
- **Timezone:** `Europe/Zagreb` (Central European Time / Central European Summer Time).
  - **Spring-Forward Gap:** On the last Sunday of March (e.g., `2026-03-29 02:30`), the clock jumps from `02:00` to `03:00`. Wall times in this interval do not exist and are rejected with `DATETIME_DST_NONEXISTENT`.
  - **Fall-Back Fold:** On the last Sunday of October (e.g., `2026-10-25 02:30`), the clock repeats `02:00` to `03:00`. LocaleGate flags `DATETIME_DST_AMBIGUOUS` and emits both candidate timestamps (`+02:00` CEST and `+01:00` CET) for user clarification.

---

## 2. United States (`en-US`)

### 2.1 Money & Numbers
- **Decimal Separator:** `.` (period).
- **Thousands Grouping:** `,` (comma) or space.
- **Currencies:** `USD`, `$`.

### 2.2 Dates & Times
- **Date Format:** Month-Day-Year (`MM/DD/YYYY` or `Month D, YYYY`).
- **Weekday Consistency:** Validated against calendar.
- **Timezones:** `America/New_York`, `America/Chicago`, `America/Denver`, `America/Los_Angeles`.
  - Spring-forward gaps and fall-back folds validated via `zoneinfo`.

---

## 3. United Kingdom (`en-GB`)

### 3.1 Money & Numbers
- **Decimal Separator:** `.` (period).
- **Thousands Grouping:** `,` (comma) or space.
- **Currencies:** `GBP`, `£`. Bare `$` triggers `CURRENCY_AMBIGUOUS_SYMBOL`.

### 3.2 Dates & Times
- **Date Format:** Day-Month-Year (`DD/MM/YYYY` or `D Month YYYY`).
- **Timezone:** `Europe/London` (GMT / BST).
