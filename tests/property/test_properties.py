"""Property-based tests using Hypothesis (§13.2)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from hypothesis import given, settings
from hypothesis import strategies as st

from locale_gate.contracts.enums import ParseStatus
from locale_gate.contracts.models import EvidenceQuote
from locale_gate.evidence.resolver import resolve_evidence_quote
from locale_gate.parsers.date_time import (
    parse_date,
    resolve_local_wall_datetime_dst,
)
from locale_gate.parsers.money import parse_money
from locale_gate.tools.registry import compute_canonical_action_digest


def _format_hr_money(cents: int) -> tuple[str, Decimal]:
    dec = Decimal(cents) / Decimal(100)
    whole = cents // 100
    frac = cents % 100
    whole_str = f"{whole:,}".replace(",", ".")
    return f"{whole_str},{frac:02d} €", dec


def _format_us_money(cents: int) -> tuple[str, Decimal]:
    dec = Decimal(cents) / Decimal(100)
    whole = cents // 100
    frac = cents % 100
    whole_str = f"{whole:,}"
    return f"${whole_str}.{frac:02d}", dec


@settings(max_examples=100, deadline=None)
@given(cents=st.integers(min_value=1, max_value=999_999_999))
def test_property_money_roundtrip_and_no_cross_locale_parse(cents: int) -> None:
    """parse(format(value)) round-trips exact Decimal and rejects cross-locale decimal separators."""
    hr_str, expected_dec = _format_hr_money(cents)
    res_hr = parse_money(hr_str, "hr-HR")
    assert res_hr.is_ok and res_hr.value is not None
    assert res_hr.value.amount == expected_dec
    assert Decimal(res_hr.value.amount_str) == expected_dec

    # Cross-locale check: hr-HR comma-decimal with 2 fractional digits must NEVER parse under en-US/en-GB
    res_cross_us = parse_money(hr_str, "en-US")
    assert not res_cross_us.is_ok

    us_str, expected_us_dec = _format_us_money(cents)
    res_us = parse_money(us_str, "en-US")
    assert res_us.is_ok and res_us.value is not None
    assert res_us.value.amount == expected_us_dec

    # Cross-locale check: en-US dot-decimal with 2 fractional digits must NEVER parse under hr-HR
    res_cross_hr = parse_money(us_str.replace("$", "") + " €", "hr-HR")
    assert not res_cross_hr.is_ok


@settings(max_examples=80, deadline=None)
@given(d=st.dates(min_value=date(1970, 1, 1), max_value=date(2099, 12, 31)))
def test_property_date_roundtrip_all_locales(d: date) -> None:
    """parse(format(date)) round-trips for hr-HR dotted, en-US MDY slash, en-GB DMY slash, and ISO."""
    hr_str = f"{d.day:02d}.{d.month:02d}.{d.year:04d}."
    r_hr = parse_date(hr_str, "hr-HR")
    assert r_hr.is_ok and r_hr.value is not None
    assert r_hr.value.value == d
    assert r_hr.value.iso_date == d.isoformat()

    us_str = f"{d.month:02d}/{d.day:02d}/{d.year:04d}"
    r_us = parse_date(us_str, "en-US")
    assert r_us.is_ok and r_us.value is not None
    assert r_us.value.value == d

    gb_str = f"{d.day:02d}/{d.month:02d}/{d.year:04d}"
    r_gb = parse_date(gb_str, "en-GB")
    assert r_gb.is_ok and r_gb.value is not None
    assert r_gb.value.value == d


@settings(max_examples=80, deadline=None)
@given(
    prefix=st.text(alphabet="abcčćđšž123 ", min_size=1, max_size=30),
    suffix=st.text(alphabet="xyzčćđšž987 .", min_size=1, max_size=30),
    cents=st.integers(min_value=100, max_value=999999),
)
def test_property_resolved_evidence_maps_to_exact_slice_and_mutation_invalidates(
    prefix: str,
    suffix: str,
    cents: int,
) -> None:
    """Every resolved quote maps to source[start:end] == quote, and a 1-char mutation invalidates it."""
    quote, _ = _format_hr_money(cents)
    source = f"{prefix} [{quote}] {suffix}"
    out = resolve_evidence_quote(source, EvidenceQuote(quote=quote))
    assert out.status == ParseStatus.PARSED
    assert out.resolved is not None
    assert source[out.resolved.start : out.resolved.end] == quote

    # Mutate one character of the quote so it does not appear in source
    mutated = quote[:-1] + "$"
    if mutated not in source:
        out_mut = resolve_evidence_quote(source, EvidenceQuote(quote=mutated))
        assert out_mut.status == ParseStatus.INVALID


@settings(max_examples=80, deadline=None)
@given(
    naive_dt=st.datetimes(
        min_value=datetime(2026, 1, 1, 0, 0),
        max_value=datetime(2026, 12, 31, 23, 59),
    )
)
def test_property_dst_resolution_never_returns_unverified_wall_time(
    naive_dt: datetime,
) -> None:
    """Whenever resolve_local_wall_datetime_dst returns PARSED, round-trip UTC wall time equals input."""
    naive_clean = naive_dt.replace(second=0, microsecond=0)
    resolved, _fold, status, _reason, _msg, _cands = resolve_local_wall_datetime_dst(
        naive_clean, "Europe/Zagreb"
    )
    if status == ParseStatus.PARSED:
        assert resolved is not None
        z = ZoneInfo("Europe/Zagreb")
        from datetime import UTC

        rt = resolved.astimezone(UTC).astimezone(z)
        assert rt.replace(tzinfo=None) == naive_clean


@settings(max_examples=80, deadline=None)
@given(
    amt_a=st.integers(min_value=1, max_value=100000),
    amt_b=st.integers(min_value=1, max_value=100000),
)
def test_property_confirmation_digest_collision_resistance(amt_a: int, amt_b: int) -> None:
    """Confirmation digest changes whenever tool_name or any canonical argument changes."""
    args_a = {"amount": f"{amt_a}.00", "currency": "EUR", "expense_date": "2026-11-03"}
    args_b = {"amount": f"{amt_b}.00", "currency": "EUR", "expense_date": "2026-11-03"}

    d1 = compute_canonical_action_digest("req_1", "submit_expense", args_a)
    d2 = compute_canonical_action_digest("req_1", "schedule_event", args_a)
    assert d1 != d2

    if amt_a != amt_b:
        d3 = compute_canonical_action_digest("req_1", "submit_expense", args_b)
        assert d1 != d3
