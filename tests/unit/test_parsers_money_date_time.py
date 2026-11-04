"""Unit tests for Croatian/English money, currency, date, time, DST, and duration parsers."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from locale_gate.contracts.enums import ParseStatus, ReasonCode
from locale_gate.parsers.date_time import (
    parse_date,
    parse_datetime,
    parse_duration,
)
from locale_gate.parsers.money import parse_currency, parse_money

REF_DT = datetime.fromisoformat("2026-11-03T12:00:00+01:00")


class TestMoneyAndCurrencyParsing:
    """Comprehensive money and currency parser tests (§7.2 & §13.1)."""

    @pytest.mark.parametrize(
        ("quote", "locale", "expected_amount", "expected_currency", "expected_rule"),
        [
            ("1.234,56 €", "hr-HR", "1234.56", "EUR", "hr-HR_money_grouped_decimal"),
            ("1234,56 EUR", "hr-HR", "1234.56", "EUR", "hr-HR_money_plain_decimal"),
            ("1 234,56 EUR", "hr-HR", "1234.56", "EUR", "hr-HR_money_grouped_decimal"),
            ("1\u00a0234,56 EUR", "hr-HR", "1234.56", "EUR", "hr-HR_money_grouped_decimal"),
            ("1\u202f234,56 EUR", "hr-HR", "1234.56", "EUR", "hr-HR_money_grouped_decimal"),
            ("1234 EUR", "hr-HR", "1234.00", "EUR", "hr-HR_money_plain_int"),
            ("1.234 €", "hr-HR", "1234.00", "EUR", "hr-HR_money_grouped_int"),
            ("€ 99,95", "hr-HR", "99.95", "EUR", "hr-HR_money_plain_decimal"),
            ("$1,234.56", "en-US", "1234.56", "USD", "en-US_money_grouped_decimal"),
            ("1,234.56 USD", "en-US", "1234.56", "USD", "en-US_money_grouped_decimal"),
            ("£1,234.56", "en-GB", "1234.56", "GBP", "en-GB_money_grouped_decimal"),
            ("15.000 JPY", "hr-HR", "15000", "JPY", "hr-HR_money_grouped_int"),
            ("12,345 KWD", "hr-HR", "12.345", "KWD", "hr-HR_money_plain_decimal"),
        ],
    )
    def test_valid_money_cases(
        self,
        quote: str,
        locale: str,
        expected_amount: str,
        expected_currency: str,
        expected_rule: str,
    ) -> None:
        res = parse_money(quote, locale)
        assert res.is_ok
        assert res.value is not None
        assert isinstance(res.value.amount, Decimal)
        assert res.value.amount_str == expected_amount
        assert res.value.currency == expected_currency
        assert res.value.rule_id == expected_rule

    def test_negative_money_parsed_by_parser_blocked_by_domain(self) -> None:
        res = parse_money("-12,50 €", "hr-HR")
        assert res.is_ok
        assert res.value is not None
        assert res.value.amount == Decimal("-12.50")
        assert res.value.amount_str == "-12.50"

    def test_parenthetical_negative_explicitly_rejected(self) -> None:
        res = parse_money("(12,50 €)", "hr-HR")
        assert res.status == ParseStatus.INVALID
        assert res.reason_code == ReasonCode.MONEY_INVALID_FORMAT

    @pytest.mark.parametrize(
        ("quote", "locale", "expected_status", "expected_reason"),
        [
            ("12.34,56 €", "hr-HR", ParseStatus.INVALID, ReasonCode.MONEY_INVALID_GROUPING),
            ("12.50 €", "hr-HR", ParseStatus.INVALID, ReasonCode.MONEY_INVALID_GROUPING),
            ("12,50 USD", "en-US", ParseStatus.INVALID, ReasonCode.MONEY_INVALID_GROUPING),
            ("12,50 GBP", "en-GB", ParseStatus.INVALID, ReasonCode.MONEY_INVALID_GROUPING),
            ("1.234 567,89 €", "hr-HR", ParseStatus.INVALID, ReasonCode.MONEY_INVALID_GROUPING),
            ("12,34,56 €", "hr-HR", ParseStatus.INVALID, ReasonCode.MONEY_MULTIPLE_DECIMALS),
            ("$12.34.56", "en-US", ParseStatus.INVALID, ReasonCode.MONEY_MULTIPLE_DECIMALS),
            ("12,345 €", "hr-HR", ParseStatus.INVALID, ReasonCode.MONEY_EXCESS_PRECISION),
            ("100,50 JPY", "hr-HR", ParseStatus.INVALID, ReasonCode.MONEY_EXCESS_PRECISION),
            ("1.234,56", "hr-HR", ParseStatus.AMBIGUOUS, ReasonCode.CURRENCY_MISSING),
            ("$100,00", "hr-HR", ParseStatus.AMBIGUOUS, ReasonCode.CURRENCY_MISSING),
            ("$100.00", "en-GB", ParseStatus.AMBIGUOUS, ReasonCode.CURRENCY_MISSING),
            ("€100,00 USD", "hr-HR", ParseStatus.INVALID, ReasonCode.CURRENCY_CONFLICT),
            ("100,00 DEM", "hr-HR", ParseStatus.INVALID, ReasonCode.CURRENCY_UNSUPPORTED),
            ("100,00 XYZ", "hr-HR", ParseStatus.INVALID, ReasonCode.CURRENCY_UNSUPPORTED),
            ("1\u3002234,56 €", "hr-HR", ParseStatus.INVALID, ReasonCode.MONEY_INVALID_FORMAT),
            ("\u221212,50 €", "hr-HR", ParseStatus.INVALID, ReasonCode.MONEY_INVALID_FORMAT),
            ("\uff11\uff12,50 €", "hr-HR", ParseStatus.INVALID, ReasonCode.MONEY_INVALID_FORMAT),
            ("1.234 €", None, ParseStatus.AMBIGUOUS, ReasonCode.MONEY_AMBIGUOUS_LOCALE),
            ("1,234 USD", None, ParseStatus.AMBIGUOUS, ReasonCode.MONEY_AMBIGUOUS_LOCALE),
        ],
    )
    def test_money_rejection_and_ambiguity_rules(
        self,
        quote: str,
        locale: str | None,
        expected_status: ParseStatus,
        expected_reason: ReasonCode,
    ) -> None:
        res = parse_money(quote, locale)
        assert res.status == expected_status
        assert res.reason_code == expected_reason

    def test_currency_parser_direct(self) -> None:
        assert parse_currency("EUR", "hr-HR").value == "EUR"
        assert parse_currency("€", "hr-HR").value == "EUR"
        assert parse_currency("$", "en-US").value == "USD"
        assert parse_currency("$", "hr-HR").status == ParseStatus.AMBIGUOUS
        assert parse_currency("DEM", "hr-HR").reason_code == ReasonCode.CURRENCY_UNSUPPORTED
        assert parse_currency("eur", "hr-HR").reason_code == ReasonCode.CURRENCY_UNSUPPORTED


class TestDateTimeAndDSTParsing:
    """Comprehensive date, time, DST, and duration parser tests (§7.3 & §7.4)."""

    @pytest.mark.parametrize(
        ("quote", "locale", "expected_iso"),
        [
            ("03.11.2026.", "hr-HR", "2026-11-03"),
            ("3. 11. 2026.", "hr-HR", "2026-11-03"),
            ("03/11/2026", "hr-HR", "2026-11-03"),
            ("2026-11-03", "hr-HR", "2026-11-03"),
            ("3. studenoga 2026.", "hr-HR", "2026-11-03"),
            ("15. siječnja 2026.", "hr-HR", "2026-01-15"),
            ("utorak, 03.11.2026.", "hr-HR", "2026-11-03"),
            ("danas", "hr-HR", "2026-11-03"),
            ("sutra", "hr-HR", "2026-11-04"),
            ("prekosutra", "hr-HR", "2026-11-05"),
            ("jučer", "hr-HR", "2026-11-02"),
            ("03/04/2026", "hr-HR", "2026-04-03"),
            ("03/04/2026", "en-GB", "2026-04-03"),
            ("03/04/2026", "en-US", "2026-03-04"),
            ("November 3, 2026", "en-US", "2026-11-03"),
            ("3 November 2026", "en-GB", "2026-11-03"),
        ],
    )
    def test_valid_dates(self, quote: str, locale: str, expected_iso: str) -> None:
        res = parse_date(quote, locale, reference_datetime=REF_DT, timezone_name="Europe/Zagreb")
        assert res.is_ok
        assert res.value is not None
        assert res.value.iso_date == expected_iso

    @pytest.mark.parametrize(
        ("quote", "locale", "expected_status", "expected_reason"),
        [
            ("03.11.", "hr-HR", ParseStatus.AMBIGUOUS, ReasonCode.DATE_YEAR_MISSING),
            ("03.11.26.", "hr-HR", ParseStatus.AMBIGUOUS, ReasonCode.DATE_YEAR_MISSING),
            ("3. studenoga", "hr-HR", ParseStatus.AMBIGUOUS, ReasonCode.DATE_YEAR_MISSING),
            ("November 3", "en-US", ParseStatus.AMBIGUOUS, ReasonCode.DATE_YEAR_MISSING),
            ("03/04/2026", None, ParseStatus.AMBIGUOUS, ReasonCode.DATE_AMBIGUOUS_ORDER),
            ("31.02.2026.", "hr-HR", ParseStatus.INVALID, ReasonCode.DATE_INVALID_CALENDAR),
            ("29.02.2026.", "hr-HR", ParseStatus.INVALID, ReasonCode.DATE_INVALID_CALENDAR),
            (
                "ponedjeljak, 03.11.2026.",
                "hr-HR",
                ParseStatus.INVALID,
                ReasonCode.DATE_WEEKDAY_MISMATCH,
            ),
            ("03.11.2026.", "en-US", ParseStatus.INVALID, ReasonCode.DATE_INVALID_FORMAT),
            ("03\u300211\u30022026", "hr-HR", ParseStatus.INVALID, ReasonCode.DATE_INVALID_FORMAT),
            ("sljedeći petak", "hr-HR", ParseStatus.AMBIGUOUS, ReasonCode.DATE_AMBIGUOUS_ORDER),
        ],
    )
    def test_date_rejection_and_ambiguity(
        self,
        quote: str,
        locale: str | None,
        expected_status: ParseStatus,
        expected_reason: ReasonCode,
    ) -> None:
        res = parse_date(quote, locale, reference_datetime=REF_DT, timezone_name="Europe/Zagreb")
        assert res.status == expected_status
        assert res.reason_code == expected_reason

    def test_europe_zagreb_dst_fall_back_ambiguous(self) -> None:
        """25.10.2026. 02:30 occurs twice in Europe/Zagreb (+02:00 and +01:00) (§7.4)."""
        res = parse_datetime(
            "25.10.2026. u 02:30",
            "hr-HR",
            trusted_timezone="Europe/Zagreb",
            reference_datetime=REF_DT,
        )
        assert res.status == ParseStatus.AMBIGUOUS
        assert res.reason_code == ReasonCode.DATETIME_DST_AMBIGUOUS
        assert "2026-10-25T02:30:00+02:00" in res.candidates
        assert "2026-10-25T02:30:00+01:00" in res.candidates

    def test_europe_zagreb_dst_spring_forward_nonexistent(self) -> None:
        """29.03.2026. 02:30 does not exist in Europe/Zagreb (§7.4)."""
        res = parse_datetime(
            "29.03.2026. u 02:30",
            "hr-HR",
            trusted_timezone="Europe/Zagreb",
            reference_datetime=REF_DT,
        )
        assert res.status == ParseStatus.INVALID
        assert res.reason_code == ReasonCode.DATETIME_DST_NONEXISTENT

    def test_datetime_explicit_offset_disambiguates_dst_fold(self) -> None:
        res0 = parse_datetime(
            "2026-10-25T02:30:00+02:00",
            "hr-HR",
            trusted_timezone="Europe/Zagreb",
        )
        assert res0.is_ok and res0.value is not None
        assert res0.value.rfc3339 == "2026-10-25T02:30:00+02:00"
        assert res0.value.fold == 0

        res1 = parse_datetime(
            "2026-10-25T02:30:00+01:00",
            "hr-HR",
            trusted_timezone="Europe/Zagreb",
        )
        assert res1.is_ok and res1.value is not None
        assert res1.value.rfc3339 == "2026-10-25T02:30:00+01:00"
        assert res1.value.fold == 1

    def test_date_only_rejected_for_datetime(self) -> None:
        res = parse_datetime(
            "03.11.2026.",
            "hr-HR",
            trusted_timezone="Europe/Zagreb",
            reference_datetime=REF_DT,
        )
        assert res.status == ParseStatus.AMBIGUOUS
        assert res.reason_code == ReasonCode.DATETIME_DATE_ONLY_NOT_ALLOWED

    def test_duration_parsing(self) -> None:
        assert parse_duration("45 minuta", "hr-HR").value.minutes == 45  # type: ignore[union-attr]
        assert parse_duration("1 sat i 30 minuta", "hr-HR").value.minutes == 90  # type: ignore[union-attr]
        assert parse_duration("pola sata", "hr-HR").value.minutes == 30  # type: ignore[union-attr]
        assert parse_duration("PT2H", "en-US").value.minutes == 120  # type: ignore[union-attr]
        assert parse_duration("0 minuta", "hr-HR").status == ParseStatus.INVALID
