"""Immutable locale profiles and versioned, date-aware ISO 4217 currency metadata (§5.5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from locale_gate.contracts.enums import ReasonCode

DateOrder = Literal["DMY", "MDY", "YMD"]

ISO_4217_TABLE_VERSION = "SIX-ISO4217-2026-01 (Amendment 174)"


@dataclass(frozen=True)
class CurrencySpec:
    """ISO 4217 currency specification with minor-unit precision and historical effective dates."""

    code: str
    minor_units: int
    name: str
    numeric_code: str = "000"
    active: bool = True
    effective_from: date | None = None
    effective_until: date | None = None
    replaced_by: str | None = None


# Maintained ISO 4217 table (SIX Group ISO 4217 List One & List Three Amendment 174)
ISO_4217_CURRENCIES: dict[str, CurrencySpec] = {
    "EUR": CurrencySpec("EUR", 2, "Euro", numeric_code="978", effective_from=date(1999, 1, 1)),
    "USD": CurrencySpec("USD", 2, "US Dollar", numeric_code="840"),
    "GBP": CurrencySpec("GBP", 2, "Pound Sterling", numeric_code="826"),
    "CHF": CurrencySpec("CHF", 2, "Swiss Franc", numeric_code="756"),
    "CAD": CurrencySpec("CAD", 2, "Canadian Dollar", numeric_code="124"),
    "AUD": CurrencySpec("AUD", 2, "Australian Dollar", numeric_code="036"),
    "NZD": CurrencySpec("NZD", 2, "New Zealand Dollar", numeric_code="554"),
    "SEK": CurrencySpec("SEK", 2, "Swedish Krona", numeric_code="752"),
    "NOK": CurrencySpec("NOK", 2, "Norwegian Krone", numeric_code="578"),
    "DKK": CurrencySpec("DKK", 2, "Danish Krone", numeric_code="208"),
    "PLN": CurrencySpec("PLN", 2, "Polish Zloty", numeric_code="985"),
    "CZK": CurrencySpec("CZK", 2, "Czech Koruna", numeric_code="203"),
    "HUF": CurrencySpec("HUF", 2, "Hungarian Forint", numeric_code="348"),
    "RON": CurrencySpec("RON", 2, "Romanian Leu", numeric_code="946"),
    "BGN": CurrencySpec("BGN", 2, "Bulgarian Lev", numeric_code="975"),
    "BAM": CurrencySpec("BAM", 2, "Convertible Mark", numeric_code="977"),
    "RSD": CurrencySpec("RSD", 2, "Serbian Dinar", numeric_code="941"),
    "JPY": CurrencySpec("JPY", 0, "Japanese Yen", numeric_code="392"),
    "KRW": CurrencySpec("KRW", 0, "South Korean Won", numeric_code="410"),
    "ISK": CurrencySpec("ISK", 0, "Icelandic Krona", numeric_code="352"),
    "BHD": CurrencySpec("BHD", 3, "Bahraini Dinar", numeric_code="048"),
    "KWD": CurrencySpec("KWD", 3, "Kuwaiti Dinar", numeric_code="414"),
    "OMR": CurrencySpec("OMR", 3, "Omani Rial", numeric_code="512"),
    "TND": CurrencySpec("TND", 3, "Tunisian Dinar", numeric_code="788"),
}


def validate_currency_for_date(
    code: str,
    transaction_date: date | None = None,
    *,
    allow_historical: bool = True,
) -> tuple[bool, ReasonCode | None, str]:
    """Validate whether an ISO 4217 currency code is valid for `transaction_date` (§5.5).

    Distinguishes:
    - Active currencies: valid unconditionally.
    - Historical entry: valid if transaction_date is within the historical legal-tender window.
    """
    upper = code.upper()
    spec = ISO_4217_CURRENCIES.get(upper)
    if spec is None:
        return False, ReasonCode.CURRENCY_UNSUPPORTED, f"Unsupported currency code {code!r}."

    if spec.active:
        return True, None, f"Active ISO 4217 currency {upper}."

    # Historical currency
    if not allow_historical or transaction_date is None:
        return (
            False,
            ReasonCode.CURRENCY_UNSUPPORTED,
            (
                f"Currency {upper} ({spec.name}) is retired as active legal tender (effective until "
                f"{spec.effective_until.isoformat() if spec.effective_until else 'unknown'}) "
                "and requires a valid historical transaction date."
            ),
        )

    if spec.effective_from is not None and transaction_date < spec.effective_from:
        return (
            False,
            ReasonCode.CURRENCY_UNSUPPORTED,
            f"Currency {upper} was not in effect on {transaction_date.isoformat()} (effective from {spec.effective_from.isoformat()}).",
        )

    if spec.effective_until is not None and transaction_date > spec.effective_until:
        return (
            False,
            ReasonCode.CURRENCY_UNSUPPORTED,
            (
                f"Currency {upper} ceased to be legal tender after {spec.effective_until.isoformat()} "
                f"(replaced by {spec.replaced_by or 'successor'}) "
                f"and is invalid for transaction date {transaction_date.isoformat()}."
            ),
        )

    return (
        True,
        None,
        f"Validated historical currency {upper} for date {transaction_date.isoformat()}.",
    )


# Unambiguous currency symbol mappings
UNAMBIGUOUS_SYMBOL_TO_CURRENCY: dict[str, str] = {
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
}

CROATIAN_MONTHS: dict[str, int] = {
    "siječanj": 1,
    "siječnja": 1,
    "sijecanj": 1,
    "sijecnja": 1,
    "veljača": 2,
    "veljače": 2,
    "veljaca": 2,
    "veljace": 2,
    "ožujak": 3,
    "ožujka": 3,
    "ozujak": 3,
    "ozujka": 3,
    "travanj": 4,
    "travnja": 4,
    "svibanj": 5,
    "svibnja": 5,
    "lipanj": 6,
    "lipnja": 6,
    "srpanj": 7,
    "srpnja": 7,
    "kolovoz": 8,
    "kolovoza": 8,
    "rujan": 9,
    "rujna": 9,
    "listopad": 10,
    "listopada": 10,
    "studeni": 11,
    "studenoga": 11,
    "studenog": 11,
    "prosinac": 12,
    "prosinca": 12,
}

ENGLISH_MONTHS: dict[str, int] = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

# Python date.weekday(): Monday == 0 ... Sunday == 6
CROATIAN_WEEKDAYS: dict[str, int] = {
    "ponedjeljak": 0,
    "utorak": 1,
    "srijeda": 2,
    "srijedu": 2,
    "četvrtak": 3,
    "cetvrtak": 3,
    "petak": 4,
    "subota": 5,
    "subotu": 5,
    "nedjelja": 6,
    "nedjelju": 6,
}

ENGLISH_WEEKDAYS: dict[str, int] = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}


@dataclass(frozen=True)
class LocaleProfile:
    """Explicit, immutable parsing rules for a single BCP 47 locale profile."""

    tag: str
    decimal_separator: str
    group_separators: tuple[str, ...]
    numeric_date_order: DateOrder
    allow_dotted_dates: bool
    allow_slash_dates: bool
    month_names: dict[str, int]
    weekday_names: dict[str, int]
    symbol_currency_map: dict[str, str]


HR_HR_PROFILE = LocaleProfile(
    tag="hr-HR",
    decimal_separator=",",
    # Explicitly enumerated grouping characters: ASCII dot, ASCII space, NBSP, narrow NBSP
    group_separators=(".", " ", "\u00a0", "\u202f"),
    numeric_date_order="DMY",
    allow_dotted_dates=True,
    allow_slash_dates=True,
    month_names=CROATIAN_MONTHS,
    weekday_names=CROATIAN_WEEKDAYS,
    symbol_currency_map={
        "€": "EUR",
        "£": "GBP",
    },
)

EN_US_PROFILE = LocaleProfile(
    tag="en-US",
    decimal_separator=".",
    group_separators=(",",),
    numeric_date_order="MDY",
    allow_dotted_dates=False,
    allow_slash_dates=True,
    month_names=ENGLISH_MONTHS,
    weekday_names=ENGLISH_WEEKDAYS,
    symbol_currency_map={
        "$": "USD",
        "US$": "USD",
        "€": "EUR",
        "£": "GBP",
    },
)

EN_GB_PROFILE = LocaleProfile(
    tag="en-GB",
    decimal_separator=".",
    group_separators=(",",),
    numeric_date_order="DMY",
    allow_dotted_dates=False,
    allow_slash_dates=True,
    month_names=ENGLISH_MONTHS,
    weekday_names=ENGLISH_WEEKDAYS,
    symbol_currency_map={
        "£": "GBP",
        "US$": "USD",
        "€": "EUR",
    },
)

LOCALE_PROFILES: dict[str, LocaleProfile] = {
    "hr-HR": HR_HR_PROFILE,
    "en-US": EN_US_PROFILE,
    "en-GB": EN_GB_PROFILE,
}


def get_locale_profile(tag: str | None) -> LocaleProfile | None:
    """Return the immutable LocaleProfile for a BCP 47 tag, or None if unknown/missing."""
    if not tag:
        return None
    return LOCALE_PROFILES.get(tag.strip())
