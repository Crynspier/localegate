# Deterministic Locale Parser Rules (`v1.0.0`)

All parsers in [`src/locale_gate/parsers/`](../src/locale_gate/parsers/) are pure, deterministic Python functions that require zero ML/LLM dependencies.

## Supported Locale Profiles

| Locale Tag | Decimal Separator | Grouping Separators | Date Order | Default Time Convention | Unambiguous Symbol Map |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `hr-HR` | `,` | `.`, `U+00A0`, `U+202F` | `DMY` (`DD.MM.YYYY.`) | 24-hour (`09:30`, `14:00h`) | `€ -> EUR`, `£ -> GBP`, `¥ -> JPY`, `US$ -> USD` (bare `$` is ambiguous) |
| `en-US` | `.` | `,` | `MDY` (`MM/DD/YYYY`) | 12h AM/PM or 24h | `$ -> USD`, `US$ -> USD`, `€ -> EUR`, `£ -> GBP`, `¥ -> JPY` |
| `en-GB` | `.` | `,` | `DMY` (`DD/MM/YYYY`) | 24h or 12h AM/PM | `£ -> GBP`, `€ -> EUR`, `¥ -> JPY`, `US$ -> USD` (bare `$` is ambiguous) |

## Money & Currency Rules (`parse_money`, `parse_currency`)

1. **No Binary Float Conversion:** Numeric bodies are validated character-by-character and converted directly to ` decimal.Decimal`.
2. **Grouping Validation:** When grouping separators are present, the leading group must have `1..3` digits (not starting with `0`), and every subsequent integer group must have **3 digits**. Mixed grouping separators in a single number are rejected (`MONEY_INVALID_GROUPING`).
3. **Minor-Unit Precision:** Amounts are quantized to the ISO 4217 currency's `minor_units` (`2` for `EUR`, `USD`, `GBP`, `CHF`, `CAD`, `AUD`; `0` for `JPY`). Any fractional precision exceeding `minor_units` is rejected (`MONEY_EXCESS_PRECISION`)—never rounded.
4. **Unsupported & Ambiguous Currencies:** Unrecognized or unsupported currencies (e.g. `DEM`, `XYZ`) are rejected (`CURRENCY_UNSUPPORTED`). Bare `"$"` is resolved to `USD` only in `en-US` and flagged as `AMBIGUOUS` (`CURRENCY_MISSING`) in `hr-HR` and `en-GB`.

## Date, Datetime, Timezone & DST Rules (`parse_date`, `parse_datetime`, `parse_duration`)

1. **Croatian Dotted Dates (`hr-HR`):** Accepts `DD.MM.YYYY.` and `DD. MM. YYYY.` (with or without trailing dot). Slash-separated dates (`03/04/2026`) in `hr-HR` are rejected as `AMBIGUOUS` (`DATE_AMBIGUOUS_ORDER`) when both components are `<= 12`, and rejected as `INVALID` (`DATE_INVALID_FORMAT`) when one component is `> 12`.
2. **Explicit Year Required:** Dates omitting a year (`03.11.`, `11/03`, `3 November`) return `AMBIGUOUS` (`DATE_MISSING_YEAR`) rather than guessing the current year.
3. **Relative Dates:** `"danas"`, `"sutra"`, `"prekosutra"`, `"jučer"` (`hr-HR`) and `"today"`, `"tomorrow"`, `"yesterday"` (`en-US`, `en-GB`) require an injected `reference_datetime` (`DATE_RELATIVE_MISSING_REF` if absent). Relative weekday phrases (`"sljedeći petak"`, `"next Friday"`) default to `AMBIGUOUS` (`DATE_AMBIGUOUS_RELATIVE`) under `research_simulation_v1`.
4. **IANA Timezone & DST Transitions (`Europe/Zagreb`):**
   - **Spring-Forward Gap:** Nonexistent local wall-clock times (e.g. `2026-03-29 02:30` in `Europe/Zagreb`) are detected by round-tripping through UTC and rejected as `INVALID` (`DATETIME_NONEXISTENT_DST_GAP`).
   - **Autumn Fall-Back Fold:** Repeated local wall-clock times (e.g. `2026-10-25 02:30` in `Europe/Zagreb`, which occurs in both CEST `+02:00` and CET `+01:00`) are detected when no explicit UTC offset is provided in the quote and flagged as `AMBIGUOUS` (`DATETIME_AMBIGUOUS_DST_FOLD`).
