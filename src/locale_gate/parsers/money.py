"""Deterministic locale-aware money, decimal, and date-aware ISO 4217 currency parser (§5.5)."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from locale_gate.canonical.types import CanonicalMoney
from locale_gate.contracts.enums import ParseStatus, ReasonCode
from locale_gate.locales.profiles import (
    ISO_4217_CURRENCIES,
    LocaleProfile,
    get_locale_profile,
    validate_currency_for_date,
)
from locale_gate.parsers.result import ParserResult

# Characters that look like digits, minus signs, or separators but are prohibited
_PROHIBITED_LOOKALIKES = frozenset(
    {
        "\u3002",  # Ideographic full stop (U+3002)
        "\uff0e",  # Fullwidth full stop (U+FF0E)
        "\uff0c",  # Fullwidth comma (U+FF0C)
        "\u066b",  # Arabic decimal separator
        "\u066c",  # Arabic thousands separator
        "\u2212",  # Unicode minus (U+2212)
        "\u2013",  # En dash (U+2013)
        "\u2014",  # Em dash (U+2014)
        "\ufe63",  # Small hyphen-minus
        "\uff0d",  # Fullwidth hyphen-minus
        "'",  # Apostrophe grouping not in supported profiles
        "\u2019",  # Right single quotation mark
        "_",  # Underscore grouping prohibited
    }
)

_ASCII_DIGITS = frozenset("0123456789")

# Allowed whitespace between currency token and number
_WS_CHARS = " \u00a0\u202f"

# Regex to tokenize optional sign, currency prefix/suffix, and numeric core
# Never uses float conversion.
_CURRENCY_SYMBOLS = ("US$", "CA$", "A$", "NZ$", "HK$", "€", "£", "$", "¥")
_AMBIGUOUS_DOLLAR_SYMBOLS = frozenset({"CA$", "A$", "NZ$", "HK$"})
_RETIRED_OR_LOCAL_ABBREVS = frozenset({"din", "kr", "fr.", "sfr"})


def _has_non_ascii_digits_or_lookalikes(text: str) -> bool:
    for ch in text:
        if ch in _PROHIBITED_LOOKALIKES:
            return True
        if ch.isdigit() and ch not in _ASCII_DIGITS:
            return True
    return False


def parse_currency(
    raw_quote: str,
    locale: str | None,
    *,
    transaction_date: date | None = None,
    allow_historical_currencies: bool = True,
) -> ParserResult[str]:
    """Parse an isolated currency code or symbol string into a validated ISO 4217 code."""
    if not raw_quote or not raw_quote.strip(_WS_CHARS):
        return ParserResult(
            status=ParseStatus.MISSING,
            reason_code=ReasonCode.CURRENCY_MISSING,
            message="Currency evidence is empty.",
        )

    profile = get_locale_profile(locale)
    cleaned = raw_quote.strip(_WS_CHARS)

    if _has_non_ascii_digits_or_lookalikes(cleaned):
        return ParserResult(
            status=ParseStatus.INVALID,
            reason_code=ReasonCode.CURRENCY_UNSUPPORTED,
            message=f"Currency token {raw_quote!r} contains unsupported lookalike characters.",
        )

    if cleaned in _AMBIGUOUS_DOLLAR_SYMBOLS:
        return ParserResult(
            status=ParseStatus.AMBIGUOUS,
            reason_code=ReasonCode.CURRENCY_UNSUPPORTED,
            message=f"Currency symbol {cleaned!r} requires an explicit ISO 4217 code.",
        )

    if cleaned == "$":
        if profile is not None and "$" in profile.symbol_currency_map:
            return ParserResult(
                status=ParseStatus.PARSED,
                value=profile.symbol_currency_map["$"],
                rule_id=f"{profile.tag}_currency_symbol",
                message="Parsed currency symbol.",
            )
        return ParserResult(
            status=ParseStatus.AMBIGUOUS,
            reason_code=ReasonCode.CURRENCY_MISSING,
            message="Bare '$' symbol is ambiguous outside en-US; specify USD, CAD, AUD, etc.",
            candidates=("USD", "CAD", "AUD"),
        )

    if profile is not None and cleaned in profile.symbol_currency_map:
        code = profile.symbol_currency_map[cleaned]
        return ParserResult(
            status=ParseStatus.PARSED,
            value=code,
            rule_id=f"{profile.tag}_currency_symbol",
            message="Parsed currency symbol.",
        )

    if cleaned in {"€", "£", "¥", "US$"}:
        fallback_map = {"€": "EUR", "£": "GBP", "¥": "JPY", "US$": "USD"}
        return ParserResult(
            status=ParseStatus.PARSED,
            value=fallback_map[cleaned],
            rule_id="unambiguous_currency_symbol",
            message="Parsed unambiguous currency symbol.",
        )

    upper = cleaned.upper()
    if upper in ISO_4217_CURRENCIES:
        spec = ISO_4217_CURRENCIES[upper]
        if cleaned != upper:
            return ParserResult(
                status=ParseStatus.INVALID,
                reason_code=ReasonCode.CURRENCY_UNSUPPORTED,
                message=f"ISO 4217 currency code must be uppercase ({upper}), got {cleaned!r}.",
            )
        ok_date, err_rc, msg = validate_currency_for_date(
            upper, transaction_date, allow_historical=allow_historical_currencies
        )
        if not ok_date:
            return ParserResult(
                status=ParseStatus.INVALID,
                reason_code=err_rc or ReasonCode.CURRENCY_UNSUPPORTED,
                message=msg,
            )
        return ParserResult(
            status=ParseStatus.PARSED,
            value=spec.code,
            rule_id="iso4217_historical_code" if not spec.active else "iso4217_code",
            message=msg,
        )

    return ParserResult(
        status=ParseStatus.INVALID,
        reason_code=ReasonCode.CURRENCY_UNSUPPORTED,
        message=f"Unsupported or unrecognized currency {raw_quote!r}.",
    )


def _extract_currency_and_number(
    raw: str,
    profile: LocaleProfile | None,
    *,
    transaction_date: date | None = None,
    allow_historical_currencies: bool = True,
) -> tuple[str | None, str | None, ReasonCode | None, ParseStatus | None, str]:
    """Extract currency token(s), sign, and numeric string from a raw money quotation.

    Returns:
      (numeric_part, resolved_currency_code, error_reason, error_status, error_message)
    """
    s = raw.strip(_WS_CHARS)
    if not s:
        return (
            None,
            None,
            ReasonCode.MONEY_INVALID_FORMAT,
            ParseStatus.MISSING,
            "Empty money string.",
        )

    # Reject parenthetical negatives explicitly
    if s.startswith("(") and s.endswith(")"):
        return (
            None,
            None,
            ReasonCode.MONEY_INVALID_FORMAT,
            ParseStatus.INVALID,
            "Parenthetical negative amounts are not supported.",
        )

    if _has_non_ascii_digits_or_lookalikes(s):
        return (
            None,
            None,
            ReasonCode.MONEY_INVALID_FORMAT,
            ParseStatus.INVALID,
            "Money quotation contains prohibited Unicode lookalikes or non-ASCII digits.",
        )

    found_currencies: list[str] = []
    ambiguous_dollar = False
    unsupported_token: str | None = None

    working = s
    sign = ""
    if working.startswith("+") or working.startswith("-"):
        sign = working[0]
        working = working[1:].lstrip(_WS_CHARS)

    # Check prefix symbols (longest first)
    for sym in sorted(_CURRENCY_SYMBOLS, key=len, reverse=True):
        if working.startswith(sym):
            working = working[len(sym) :].lstrip(_WS_CHARS)
            if sym in _AMBIGUOUS_DOLLAR_SYMBOLS or (
                sym == "$" and (profile is None or "$" not in profile.symbol_currency_map)
            ):
                ambiguous_dollar = True
            elif sym == "$" and profile is not None:
                found_currencies.append(profile.symbol_currency_map["$"])
            elif sym == "US$":
                found_currencies.append("USD")
            elif sym == "€":
                found_currencies.append("EUR")
            elif sym == "£":
                found_currencies.append("GBP")
            elif sym == "¥":
                found_currencies.append("JPY")
            break

    # Check if sign appeared after prefix currency symbol
    if working.startswith("+") or working.startswith("-"):
        if sign:
            return (
                None,
                None,
                ReasonCode.MONEY_INVALID_FORMAT,
                ParseStatus.INVALID,
                "Multiple sign characters in money quotation.",
            )
        sign = working[0]
        working = working[1:].lstrip(_WS_CHARS)

    # Check suffix symbols (longest first)
    for sym in sorted(_CURRENCY_SYMBOLS, key=len, reverse=True):
        if working.endswith(sym):
            working = working[: -len(sym)].rstrip(_WS_CHARS)
            if sym in _AMBIGUOUS_DOLLAR_SYMBOLS or (
                sym == "$" and (profile is None or "$" not in profile.symbol_currency_map)
            ):
                ambiguous_dollar = True
            elif sym == "$" and profile is not None:
                found_currencies.append(profile.symbol_currency_map["$"])
            elif sym == "US$":
                found_currencies.append("USD")
            elif sym == "€":
                found_currencies.append("EUR")
            elif sym == "£":
                found_currencies.append("GBP")
            elif sym == "¥":
                found_currencies.append("JPY")
            break

    # Check prefix or suffix alphabetic currency codes
    prefix_alpha = re.match(r"^([A-Za-z.]{2,5})[\s\u00a0\u202f]+(.+)$", working)
    if prefix_alpha:
        token, rest = prefix_alpha.group(1), prefix_alpha.group(2)
        code_ok, code_val, err_reason = _classify_alpha_currency(
            token,
            profile=profile,
            transaction_date=transaction_date,
            allow_historical_currencies=allow_historical_currencies,
        )
        if not code_ok:
            unsupported_token = token
            return (
                None,
                None,
                err_reason,
                ParseStatus.INVALID,
                f"Unsupported currency token {token!r}.",
            )
        assert code_val is not None
        found_currencies.append(code_val)
        working = rest.strip(_WS_CHARS)

    suffix_alpha = re.match(r"^(.+?)[\s\u00a0\u202f]*([A-Za-z.]{2,5})$", working)
    if suffix_alpha:
        rest, token = suffix_alpha.group(1), suffix_alpha.group(2)
        code_ok, code_val, err_reason = _classify_alpha_currency(
            token,
            profile=profile,
            transaction_date=transaction_date,
            allow_historical_currencies=allow_historical_currencies,
        )
        if not code_ok:
            unsupported_token = token
            return (
                None,
                None,
                err_reason,
                ParseStatus.INVALID,
                f"Unsupported currency token {token!r}.",
            )
        assert code_val is not None
        found_currencies.append(code_val)
        working = rest.strip(_WS_CHARS)

    if unsupported_token is not None:
        return (
            None,
            None,
            ReasonCode.CURRENCY_UNSUPPORTED,
            ParseStatus.INVALID,
            f"Unsupported currency {unsupported_token!r}.",
        )

    if ambiguous_dollar:
        return (
            None,
            None,
            ReasonCode.CURRENCY_MISSING,
            ParseStatus.AMBIGUOUS,
            "Dollar symbol is ambiguous in this locale; specify USD, CAD, AUD, etc.",
        )

    unique_currencies = set(found_currencies)
    if len(unique_currencies) > 1 or len(found_currencies) > 1:
        return (
            None,
            None,
            ReasonCode.CURRENCY_CONFLICT,
            ParseStatus.INVALID,
            f"Multiple or conflicting currency markers in quotation: {found_currencies}.",
        )

    resolved_currency = found_currencies[0] if found_currencies else None
    numeric_str = (sign + working) if sign else working
    return numeric_str, resolved_currency, None, None, ""


def _classify_alpha_currency(
    token: str,
    *,
    profile: LocaleProfile | None = None,
    transaction_date: date | None = None,
    allow_historical_currencies: bool = True,
) -> tuple[bool, str | None, ReasonCode]:
    lowered = token.lower()
    if lowered in _RETIRED_OR_LOCAL_ABBREVS:
        return False, None, ReasonCode.CURRENCY_UNSUPPORTED

    upper = token.upper()
    if upper in ISO_4217_CURRENCIES:
        if token != upper:
            return False, None, ReasonCode.CURRENCY_UNSUPPORTED
        ok_date, err_rc, _ = validate_currency_for_date(
            upper, transaction_date, allow_historical=allow_historical_currencies
        )
        if not ok_date:
            return False, None, err_rc or ReasonCode.CURRENCY_UNSUPPORTED
        return True, ISO_4217_CURRENCIES[upper].code, ReasonCode.OK_EXECUTED
    return False, None, ReasonCode.CURRENCY_UNSUPPORTED


def _parse_numeric_body(
    numeric_str: str,
    profile: LocaleProfile | None,
) -> tuple[Decimal | None, int, str | None, ParseStatus, ReasonCode | None, str]:
    """Parse the numeric body according to the explicit locale profile.

    Returns:
      (decimal_value, frac_digits, rule_id, status, reason_code, message)
    """
    s = numeric_str
    sign = ""
    if s.startswith("+") or s.startswith("-"):
        sign = "-" if s[0] == "-" else ""
        s = s[1:]

    if not s:
        return (
            None,
            0,
            None,
            ParseStatus.INVALID,
            ReasonCode.MONEY_INVALID_FORMAT,
            "Missing digits in money quotation.",
        )

    # Check if purely ASCII digits (integer without any separators)
    if all(ch in _ASCII_DIGITS for ch in s):
        if len(s) > 1 and s.startswith("0"):
            return (
                None,
                0,
                None,
                ParseStatus.INVALID,
                ReasonCode.MONEY_INVALID_FORMAT,
                f"Leading zeros are not permitted on integer amounts: {numeric_str!r}.",
            )
        val = Decimal(sign + s)
        tag = profile.tag if profile else "unlocalized"
        return val, 0, f"{tag}_money_plain_int", ParseStatus.PARSED, None, "Parsed integer amount."

    # Any separator present requires a known locale profile
    if profile is None:
        return (
            None,
            0,
            None,
            ParseStatus.AMBIGUOUS,
            ReasonCode.MONEY_AMBIGUOUS_LOCALE,
            f"Cannot parse localized number {numeric_str!r} without a trusted locale profile.",
        )

    dec_sep = profile.decimal_separator
    grp_seps = profile.group_separators

    # Verify all characters are digits, dec_sep, or in grp_seps
    allowed_chars = set(_ASCII_DIGITS) | {dec_sep} | set(grp_seps)
    for ch in s:
        if ch not in allowed_chars:
            return (
                None,
                0,
                None,
                ParseStatus.INVALID,
                ReasonCode.MONEY_INVALID_FORMAT,
                f"Unexpected character {ch!r} in number {numeric_str!r} for locale {profile.tag}.",
            )

    dec_count = s.count(dec_sep)
    if dec_count > 1:
        return (
            None,
            0,
            None,
            ParseStatus.INVALID,
            ReasonCode.MONEY_MULTIPLE_DECIMALS,
            f"Number {numeric_str!r} contains {dec_count} decimal separators ({dec_sep!r}).",
        )

    if dec_count == 1:
        int_part, frac_part = s.split(dec_sep)
        if not int_part or not frac_part:
            return (
                None,
                0,
                None,
                ParseStatus.INVALID,
                ReasonCode.MONEY_INVALID_FORMAT,
                f"Incomplete decimal number {numeric_str!r}.",
            )
        if not all(ch in _ASCII_DIGITS for ch in frac_part):
            return (
                None,
                0,
                None,
                ParseStatus.INVALID,
                ReasonCode.MONEY_INVALID_GROUPING,
                f"Fractional part of {numeric_str!r} must contain only digits.",
            )
    else:
        int_part = s
        frac_part = ""

    # Validate integer part grouping
    present_grp_seps = [sep for sep in grp_seps if sep in int_part]
    if len(present_grp_seps) > 1:
        return (
            None,
            0,
            None,
            ParseStatus.INVALID,
            ReasonCode.MONEY_INVALID_GROUPING,
            f"Mixed grouping separators in {numeric_str!r} are not permitted.",
        )

    if len(present_grp_seps) == 1:
        gsep = present_grp_seps[0]
        groups = int_part.split(gsep)
        if not groups or any(not g or not all(ch in _ASCII_DIGITS for ch in g) for g in groups):
            return (
                None,
                0,
                None,
                ParseStatus.INVALID,
                ReasonCode.MONEY_INVALID_GROUPING,
                f"Malformed grouping in {numeric_str!r}.",
            )
        first_group = groups[0]
        if len(first_group) < 1 or len(first_group) > 3 or first_group.startswith("0"):
            return (
                None,
                0,
                None,
                ParseStatus.INVALID,
                ReasonCode.MONEY_INVALID_GROUPING,
                f"Invalid leading digit group {first_group!r} in {numeric_str!r}.",
            )
        for grp in groups[1:]:
            if len(grp) != 3:
                return (
                    None,
                    0,
                    None,
                    ParseStatus.INVALID,
                    ReasonCode.MONEY_INVALID_GROUPING,
                    f"Group {grp!r} in {numeric_str!r} does not have exactly 3 digits.",
                )
        normalized_int = "".join(groups)
        grouped = True
    else:
        if not all(ch in _ASCII_DIGITS for ch in int_part):
            return (
                None,
                0,
                None,
                ParseStatus.INVALID,
                ReasonCode.MONEY_INVALID_FORMAT,
                f"Invalid integer digits in {numeric_str!r}.",
            )
        if len(int_part) > 1 and int_part.startswith("0"):
            return (
                None,
                0,
                None,
                ParseStatus.INVALID,
                ReasonCode.MONEY_INVALID_FORMAT,
                f"Leading zeros are not permitted in {numeric_str!r}.",
            )
        normalized_int = int_part
        grouped = False

    canonical_dec_str = (
        f"{sign}{normalized_int}.{frac_part}" if frac_part else f"{sign}{normalized_int}"
    )
    try:
        dec_val = Decimal(canonical_dec_str)
    except InvalidOperation:
        return (
            None,
            0,
            None,
            ParseStatus.INVALID,
            ReasonCode.MONEY_INVALID_FORMAT,
            f"Could not construct Decimal from {canonical_dec_str!r}.",
        )

    if grouped and frac_part:
        rule_id = f"{profile.tag}_money_grouped_decimal"
    elif grouped:
        rule_id = f"{profile.tag}_money_grouped_int"
    elif frac_part:
        rule_id = f"{profile.tag}_money_plain_decimal"
    else:
        rule_id = f"{profile.tag}_money_plain_int"

    return dec_val, len(frac_part), rule_id, ParseStatus.PARSED, None, "Parsed numeric value."


def parse_money(
    raw_quote: str,
    locale: str | None,
    *,
    explicit_currency_quote: str | None = None,
    trusted_default_currency: str | None = None,
    require_currency: bool = True,
    transaction_date: date | None = None,
    allow_historical_currencies: bool = True,
) -> ParserResult[CanonicalMoney]:
    """Parse a locale-formatted money quotation into a CanonicalMoney instance.

    Never uses binary floats or silent rounding.
    """
    if not raw_quote or not raw_quote.strip(_WS_CHARS):
        return ParserResult(
            status=ParseStatus.MISSING,
            reason_code=ReasonCode.FIELD_REQUIRED_MISSING,
            message="Money quotation is empty.",
        )

    if locale is not None and get_locale_profile(locale) is None:
        return ParserResult(
            status=ParseStatus.UNSUPPORTED,
            reason_code=ReasonCode.LOCALE_UNSUPPORTED,
            message=f"Locale {locale!r} is not supported.",
        )

    profile = get_locale_profile(locale)

    numeric_str, embedded_currency, err_reason, err_status, err_msg = _extract_currency_and_number(
        raw_quote,
        profile,
        transaction_date=transaction_date,
        allow_historical_currencies=allow_historical_currencies,
    )
    if err_status is not None or numeric_str is None:
        return ParserResult(
            status=err_status or ParseStatus.INVALID,
            reason_code=err_reason or ReasonCode.MONEY_INVALID_FORMAT,
            message=err_msg,
        )

    dec_val, frac_digits, rule_id, num_status, num_reason, num_msg = _parse_numeric_body(
        numeric_str, profile
    )
    if num_status != ParseStatus.PARSED or dec_val is None or rule_id is None:
        return ParserResult(
            status=num_status,
            reason_code=num_reason or ReasonCode.MONEY_INVALID_FORMAT,
            message=num_msg,
        )

    # Resolve currency from embedded symbol/code, explicit_currency_quote, or trusted default
    explicit_currency: str | None = None
    if explicit_currency_quote is not None:
        cur_res = parse_currency(
            explicit_currency_quote,
            locale,
            transaction_date=transaction_date,
            allow_historical_currencies=allow_historical_currencies,
        )
        if not cur_res.is_ok:
            return ParserResult(
                status=cur_res.status,
                reason_code=cur_res.reason_code,
                message=cur_res.message,
                candidates=cur_res.candidates,
            )
        explicit_currency = cur_res.value

    if embedded_currency and explicit_currency and embedded_currency != explicit_currency:
        return ParserResult(
            status=ParseStatus.INVALID,
            reason_code=ReasonCode.CURRENCY_CONFLICT,
            message=(
                f"Embedded currency {embedded_currency} in amount conflicts with "
                f"separate currency field {explicit_currency}."
            ),
        )

    final_currency = embedded_currency or explicit_currency or trusted_default_currency
    if final_currency is not None:
        final_currency = final_currency.upper()
        ok_date, err_rc, msg = validate_currency_for_date(
            final_currency,
            transaction_date,
            allow_historical=allow_historical_currencies,
        )
        if not ok_date:
            return ParserResult(
                status=ParseStatus.INVALID,
                reason_code=err_rc or ReasonCode.CURRENCY_UNSUPPORTED,
                message=msg,
            )

    if final_currency is None and require_currency:
        return ParserResult(
            status=ParseStatus.AMBIGUOUS,
            reason_code=ReasonCode.CURRENCY_MISSING,
            message="Currency is missing and no trusted default currency is configured.",
        )

    minor_units = (
        ISO_4217_CURRENCIES[final_currency].minor_units if final_currency else max(2, frac_digits)
    )
    if frac_digits > minor_units:
        return ParserResult(
            status=ParseStatus.INVALID,
            reason_code=ReasonCode.MONEY_EXCESS_PRECISION,
            message=(
                f"Amount {raw_quote!r} has {frac_digits} fractional digits, exceeding "
                f"the {minor_units} minor units allowed for {final_currency or 'decimal'}."
            ),
        )

    quantizer = Decimal(10) ** (-minor_units) if minor_units > 0 else Decimal(1)
    quantized = dec_val.quantize(quantizer)
    # Invariant: since frac_digits <= minor_units, quantize never rounds or alters value
    if quantized != dec_val:
        return ParserResult(
            status=ParseStatus.INVALID,
            reason_code=ReasonCode.MONEY_EXCESS_PRECISION,
            message="Quantization altered exact decimal value.",
        )

    amount_str = f"{quantized:.{minor_units}f}" if minor_units > 0 else f"{quantized:.0f}"
    return ParserResult(
        status=ParseStatus.PARSED,
        value=CanonicalMoney(
            amount=quantized,
            amount_str=amount_str,
            currency=final_currency,
            minor_units=minor_units,
            original_quote=raw_quote,
            locale=profile.tag if profile else "unlocalized",
            rule_id=rule_id,
        ),
        rule_id=rule_id,
        message="Parsed money amount and currency.",
    )
