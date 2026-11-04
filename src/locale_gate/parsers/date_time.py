"""Deterministic date, time, timezone, DST fold/gap, and duration parsers."""

from __future__ import annotations

import contextlib
import re
from datetime import UTC, date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from locale_gate.canonical.types import (
    CanonicalDate,
    CanonicalDateTime,
    CanonicalDuration,
)
from locale_gate.contracts.enums import ParseStatus, ReasonCode
from locale_gate.locales.profiles import (
    LocaleProfile,
    get_locale_profile,
)
from locale_gate.parsers.result import ParserResult

_WS_CHARS = " \t\u00a0\u202f"

_PROHIBITED_DATE_LOOKALIKES = frozenset(
    {
        "\u3002",
        "\uff0e",
        "\uff0f",
        "\u2215",
        "\u2044",
        "\u2212",
        "\u2013",
        "\u2014",
    }
)

_ASCII_DIGITS = frozenset("0123456789")

_RELATIVE_DAY_OFFSETS_HR: dict[str, int] = {
    "danas": 0,
    "sutra": 1,
    "prekosutra": 2,
    "jučer": -1,
    "jucer": -1,
}

_RELATIVE_DAY_OFFSETS_EN: dict[str, int] = {
    "today": 0,
    "tomorrow": 1,
    "yesterday": -1,
}


def _has_prohibited_date_chars(s: str) -> bool:
    for ch in s:
        if ch in _PROHIBITED_DATE_LOOKALIKES:
            return True
        if ch.isdigit() and ch not in _ASCII_DIGITS:
            return True
    return False


def _construct_valid_date(
    year: int,
    month: int,
    day: int,
    raw_quote: str,
    locale_tag: str,
    rule_id: str,
    expected_weekday: int | None = None,
) -> ParserResult[CanonicalDate]:
    if year < 100:
        return ParserResult(
            status=ParseStatus.AMBIGUOUS,
            reason_code=ReasonCode.DATE_YEAR_MISSING,
            message=f"Two-digit year {year:02d} in {raw_quote!r} is ambiguous; a 4-digit year is required.",
        )
    if year < 1900 or year > 2100:
        return ParserResult(
            status=ParseStatus.INVALID,
            reason_code=ReasonCode.DATE_INVALID_CALENDAR,
            message=f"Year {year} in {raw_quote!r} is outside supported range [1900, 2100].",
        )
    try:
        d = date(year, month, day)
    except ValueError as exc:
        return ParserResult(
            status=ParseStatus.INVALID,
            reason_code=ReasonCode.DATE_INVALID_CALENDAR,
            message=f"Impossible calendar date in {raw_quote!r}: {exc}.",
        )

    if expected_weekday is not None and d.weekday() != expected_weekday:
        return ParserResult(
            status=ParseStatus.INVALID,
            reason_code=ReasonCode.DATE_WEEKDAY_MISMATCH,
            message=(
                f"Weekday in {raw_quote!r} contradicts calendar date {d.isoformat()} "
                f"(actual weekday index {d.weekday()}, stated {expected_weekday})."
            ),
        )

    return ParserResult(
        status=ParseStatus.PARSED,
        value=CanonicalDate(
            value=d,
            iso_date=d.isoformat(),
            original_quote=raw_quote,
            locale=locale_tag,
            rule_id=rule_id,
        ),
        rule_id=rule_id,
        message="Successfully parsed calendar date.",
    )


def _strip_optional_weekday_prefix(
    text: str,
    profile: LocaleProfile | None,
) -> tuple[str, int | None]:
    """Extract an optional leading weekday name like 'utorak, 03.11.2026.' or 'Tuesday, Nov 3, 2026'."""
    if profile is None:
        return text, None
    m = re.match(r"^(?:u\s+|on\s+)?([A-Za-zčćđšžČĆĐŠŽ]+)\s*,\s*(.+)$", text)
    if m:
        wd_token = m.group(1).lower()
        rest = m.group(2).strip(_WS_CHARS)
        if wd_token in profile.weekday_names:
            return rest, profile.weekday_names[wd_token]
    return text, None


def parse_date(
    raw_quote: str,
    locale: str | None,
    *,
    reference_datetime: datetime | None = None,
    timezone_name: str | None = None,
    allow_relative_weekday: bool = False,
) -> ParserResult[CanonicalDate]:
    """Parse a date quotation into a CanonicalDate under an explicit locale profile."""
    if not raw_quote or not raw_quote.strip(_WS_CHARS):
        return ParserResult(
            status=ParseStatus.MISSING,
            reason_code=ReasonCode.FIELD_REQUIRED_MISSING,
            message="Date quotation is empty.",
        )

    if locale is not None and get_locale_profile(locale) is None:
        return ParserResult(
            status=ParseStatus.UNSUPPORTED,
            reason_code=ReasonCode.LOCALE_UNSUPPORTED,
            message=f"Locale {locale!r} is not supported.",
        )

    s = raw_quote.strip(_WS_CHARS)
    if _has_prohibited_date_chars(s):
        return ParserResult(
            status=ParseStatus.INVALID,
            reason_code=ReasonCode.DATE_INVALID_FORMAT,
            message=f"Date quotation {raw_quote!r} contains prohibited Unicode lookalike characters.",
        )

    # Strip optional leading grammatical date prepositions if included in verbatim span
    s = re.sub(
        r"^(?:na\s+datum|na\s+dan|nastao|dana|od|za|dated|from|on|for)[\s\u00a0\u202f]+",
        "",
        s,
        flags=re.IGNORECASE,
    ).strip(_WS_CHARS)

    profile = get_locale_profile(locale)
    s_body, expected_weekday = _strip_optional_weekday_prefix(s, profile)

    # 1. Unambiguous ISO 8601 date YYYY-MM-DD (allowed in all locales)
    iso_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s_body)
    if iso_match:
        y, m, d = int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3))
        return _construct_valid_date(
            y,
            m,
            d,
            raw_quote,
            profile.tag if profile else "unlocalized",
            "iso_date_ymd",
            expected_weekday=expected_weekday,
        )

    # Reject ISO-like dates with missing year or 2-digit year (e.g., 26-11-03 or --11-03)
    if re.match(r"^\d{2}-\d{2}-\d{2}$", s_body):
        return ParserResult(
            status=ParseStatus.AMBIGUOUS,
            reason_code=ReasonCode.DATE_YEAR_MISSING,
            message=f"Date {raw_quote!r} uses a 2-digit year.",
        )

    # 2. Relative dates
    lower_body = re.sub(r"[\s\u00a0\u202f]+", " ", s_body.lower()).rstrip(".")
    if profile is not None:
        rel_map = _RELATIVE_DAY_OFFSETS_HR if profile.tag == "hr-HR" else _RELATIVE_DAY_OFFSETS_EN
        if lower_body in rel_map:
            if reference_datetime is None:
                return ParserResult(
                    status=ParseStatus.AMBIGUOUS,
                    reason_code=ReasonCode.DATE_RELATIVE_MISSING_REF,
                    message=f"Relative date {raw_quote!r} requires an injected reference_datetime.",
                )
            ref_dt = reference_datetime
            if timezone_name:
                with contextlib.suppress(ZoneInfoNotFoundError):
                    ref_dt = ref_dt.astimezone(ZoneInfo(timezone_name))
            target_date = ref_dt.date() + timedelta(days=rel_map[lower_body])
            return _construct_valid_date(
                target_date.year,
                target_date.month,
                target_date.day,
                raw_quote,
                profile.tag,
                f"{profile.tag}_relative_day",
                expected_weekday=expected_weekday,
            )

        # Relative weekday ("sljedeći petak", "next friday")
        rel_wd_match = re.match(
            r"^(?:sljedeći|sljedeci|sljedeću|sljedecu|idući|iduci|iduću|iducu|next)\s+([a-zčćđšž]+)$",
            lower_body,
        )
        if rel_wd_match:
            wd_word = rel_wd_match.group(1)
            if wd_word in profile.weekday_names:
                if reference_datetime is None:
                    return ParserResult(
                        status=ParseStatus.AMBIGUOUS,
                        reason_code=ReasonCode.DATE_RELATIVE_MISSING_REF,
                        message=f"Relative weekday {raw_quote!r} requires reference_datetime.",
                    )
                if not allow_relative_weekday:
                    return ParserResult(
                        status=ParseStatus.AMBIGUOUS,
                        reason_code=ReasonCode.DATE_AMBIGUOUS_ORDER,
                        message=(
                            f"Relative weekday phrase {raw_quote!r} is ambiguous under strict policy; "
                            "please provide an explicit calendar date."
                        ),
                    )
                ref_dt = reference_datetime
                if timezone_name:
                    ref_dt = ref_dt.astimezone(ZoneInfo(timezone_name))
                target_wd = profile.weekday_names[wd_word]
                days_ahead = (target_wd - ref_dt.date().weekday()) % 7
                if days_ahead == 0:
                    days_ahead = 7
                target_date = ref_dt.date() + timedelta(days=days_ahead)
                return _construct_valid_date(
                    target_date.year,
                    target_date.month,
                    target_date.day,
                    raw_quote,
                    profile.tag,
                    f"{profile.tag}_relative_weekday",
                    expected_weekday=expected_weekday,
                )

    # 3. Check for missing locale on non-ISO dates
    if profile is None:
        if re.match(r"^\d{1,2}[./]\d{1,2}[./]\d{2,4}\.?$", s_body):
            return ParserResult(
                status=ParseStatus.AMBIGUOUS,
                reason_code=ReasonCode.DATE_AMBIGUOUS_ORDER,
                message=f"Numeric date {raw_quote!r} is ambiguous without a trusted locale profile.",
            )
        return ParserResult(
            status=ParseStatus.AMBIGUOUS,
            reason_code=ReasonCode.LOCALE_MISSING,
            message=f"Cannot parse localized date {raw_quote!r} without a trusted locale profile.",
        )

    # 4. Dotted numeric dates (e.g., "03.11.2026.", "3. 11. 2026.", "03.11.2026")
    dotted_full = re.match(
        r"^(\d{1,2})\.[\s\u00a0\u202f]*(\d{1,2})\.[\s\u00a0\u202f]*(\d{2,4})\.?$",
        s_body,
    )
    if dotted_full:
        if not profile.allow_dotted_dates:
            return ParserResult(
                status=ParseStatus.INVALID,
                reason_code=ReasonCode.DATE_INVALID_FORMAT,
                message=f"Dotted date {raw_quote!r} is not supported under locale {profile.tag}.",
            )
        d_val = int(dotted_full.group(1))
        m_val = int(dotted_full.group(2))
        y_str = dotted_full.group(3)
        if len(y_str) != 4:
            return ParserResult(
                status=ParseStatus.AMBIGUOUS,
                reason_code=ReasonCode.DATE_YEAR_MISSING,
                message=f"Date {raw_quote!r} must have a 4-digit year.",
            )
        return _construct_valid_date(
            int(y_str),
            m_val,
            d_val,
            raw_quote,
            profile.tag,
            f"{profile.tag}_date_dmy_dotted",
            expected_weekday=expected_weekday,
        )

    # Dotted date missing year (e.g., "03.11." or "3. 11.")
    if re.match(r"^\d{1,2}\.[\s\u00a0\u202f]*\d{1,2}\.?$", s_body):
        return ParserResult(
            status=ParseStatus.AMBIGUOUS,
            reason_code=ReasonCode.DATE_YEAR_MISSING,
            message=f"Date {raw_quote!r} is missing an explicit 4-digit year.",
        )

    # 5. Slash numeric dates (e.g., "03/11/2026" or "11/03/2026")
    slash_full = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", s_body)
    if slash_full:
        if not profile.allow_slash_dates:
            return ParserResult(
                status=ParseStatus.INVALID,
                reason_code=ReasonCode.DATE_INVALID_FORMAT,
                message=f"Slash date {raw_quote!r} is not supported under locale {profile.tag}.",
            )
        first_num = int(slash_full.group(1))
        second_num = int(slash_full.group(2))
        y_str = slash_full.group(3)
        if len(y_str) != 4:
            return ParserResult(
                status=ParseStatus.AMBIGUOUS,
                reason_code=ReasonCode.DATE_YEAR_MISSING,
                message=f"Date {raw_quote!r} must have a 4-digit year.",
            )
        if profile.numeric_date_order == "DMY":
            d_val, m_val = first_num, second_num
            rule_id = f"{profile.tag}_date_dmy_slash"
        else:
            m_val, d_val = first_num, second_num
            rule_id = f"{profile.tag}_date_mdy_slash"
        return _construct_valid_date(
            int(y_str),
            m_val,
            d_val,
            raw_quote,
            profile.tag,
            rule_id,
            expected_weekday=expected_weekday,
        )

    # Slash date missing year (e.g., "03/11" or "11/03")
    if re.match(r"^\d{1,2}/\d{1,2}$", s_body):
        return ParserResult(
            status=ParseStatus.AMBIGUOUS,
            reason_code=ReasonCode.DATE_YEAR_MISSING,
            message=f"Date {raw_quote!r} is missing an explicit 4-digit year.",
        )

    # 6. Croatian month name grammar (e.g. "3. studenoga 2026.", "03. studenog 2026")
    if profile.tag == "hr-HR":
        hr_named = re.match(
            r"^(\d{1,2})\.?[\s\u00a0\u202f]+([A-Za-zčćđšžČĆĐŠŽ]+)(?:[\s\u00a0\u202f]+(\d{2,4})\.?)?$",
            s_body,
        )
        if hr_named:
            day_int = int(hr_named.group(1))
            month_word = hr_named.group(2).lower()
            year_str = hr_named.group(3)
            if month_word in profile.month_names:
                if not year_str or len(year_str) != 4:
                    return ParserResult(
                        status=ParseStatus.AMBIGUOUS,
                        reason_code=ReasonCode.DATE_YEAR_MISSING,
                        message=f"Croatian date {raw_quote!r} is missing a 4-digit year.",
                    )
                return _construct_valid_date(
                    int(year_str),
                    profile.month_names[month_word],
                    day_int,
                    raw_quote,
                    profile.tag,
                    "hr-HR_date_month_name",
                    expected_weekday=expected_weekday,
                )

    # 7. English month name grammars ("November 3, 2026", "3 November 2026", "3rd Nov 2026")
    if profile.tag in {"en-US", "en-GB"}:
        # Month Day, Year
        en_mdy = re.match(
            r"^([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{2,4}))?$",
            s_body,
        )
        if en_mdy:
            month_word = en_mdy.group(1).lower()
            day_int = int(en_mdy.group(2))
            year_str = en_mdy.group(3)
            if month_word in profile.month_names:
                if not year_str or len(year_str) != 4:
                    return ParserResult(
                        status=ParseStatus.AMBIGUOUS,
                        reason_code=ReasonCode.DATE_YEAR_MISSING,
                        message=f"English date {raw_quote!r} is missing a 4-digit year.",
                    )
                return _construct_valid_date(
                    int(year_str),
                    profile.month_names[month_word],
                    day_int,
                    raw_quote,
                    profile.tag,
                    f"{profile.tag}_date_month_name_mdy",
                    expected_weekday=expected_weekday,
                )

        # Day Month Year
        en_dmy = re.match(
            r"^(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\.?(?:,?\s+(\d{2,4}))?$",
            s_body,
        )
        if en_dmy:
            day_int = int(en_dmy.group(1))
            month_word = en_dmy.group(2).lower()
            year_str = en_dmy.group(3)
            if month_word in profile.month_names:
                if not year_str or len(year_str) != 4:
                    return ParserResult(
                        status=ParseStatus.AMBIGUOUS,
                        reason_code=ReasonCode.DATE_YEAR_MISSING,
                        message=f"English date {raw_quote!r} is missing a 4-digit year.",
                    )
                return _construct_valid_date(
                    int(year_str),
                    profile.month_names[month_word],
                    day_int,
                    raw_quote,
                    profile.tag,
                    f"{profile.tag}_date_month_name_dmy",
                    expected_weekday=expected_weekday,
                )

    return ParserResult(
        status=ParseStatus.INVALID,
        reason_code=ReasonCode.DATE_INVALID_FORMAT,
        message=f"Date quotation {raw_quote!r} does not match any supported grammar for {profile.tag}.",
    )


def resolve_local_wall_datetime_dst(
    naive_dt: datetime,
    iana_timezone: str,
) -> tuple[datetime | None, int, ParseStatus, ReasonCode | None, str, tuple[str, ...]]:
    """Apply the explicit §7.4 7-step DST algorithm for a local naive datetime and IANA zone.

    Steps:
    1. candidate0 = dt.replace(tzinfo=z, fold=0)
    2. candidate1 = dt.replace(tzinfo=z, fold=1)
    3. Convert each to UTC and back to z.
    4. Candidate is valid only if the round-trip wall time equals the original naive datetime.
    5. Neither valid = nonexistent spring-forward time -> block/clarify (DATETIME_DST_NONEXISTENT).
    6. Both valid with different UTC offsets = ambiguous fall-back time -> clarify (DATETIME_DST_AMBIGUOUS).
    7. Exactly one valid (or both valid with identical UTC offset) = use it.
    """
    try:
        z = ZoneInfo(iana_timezone)
    except ZoneInfoNotFoundError:
        return (
            None,
            0,
            ParseStatus.INVALID,
            ReasonCode.TIMEZONE_INVALID,
            f"Unknown IANA timezone {iana_timezone!r}.",
            (),
        )

    candidate0 = naive_dt.replace(tzinfo=z, fold=0)
    candidate1 = naive_dt.replace(tzinfo=z, fold=1)

    rt0 = candidate0.astimezone(UTC).astimezone(z)
    rt1 = candidate1.astimezone(UTC).astimezone(z)

    valid0 = rt0.replace(tzinfo=None) == naive_dt
    valid1 = rt1.replace(tzinfo=None) == naive_dt

    if not valid0 and not valid1:
        return (
            None,
            0,
            ParseStatus.INVALID,
            ReasonCode.DATETIME_DST_NONEXISTENT,
            (
                f"Local wall time {naive_dt.isoformat()} does not exist in {iana_timezone} "
                "(spring-forward DST transition gap)."
            ),
            (),
        )

    if valid0 and valid1:
        offset0 = candidate0.utcoffset()
        offset1 = candidate1.utcoffset()
        if offset0 != offset1:
            return (
                None,
                0,
                ParseStatus.AMBIGUOUS,
                ReasonCode.DATETIME_DST_AMBIGUOUS,
                (
                    f"Local wall time {naive_dt.isoformat()} occurs twice in {iana_timezone} "
                    "(fall-back DST transition fold); specify UTC offset."
                ),
                (candidate0.isoformat(), candidate1.isoformat()),
            )
        return candidate0, 0, ParseStatus.PARSED, None, "Resolved unambiguous local wall time.", ()

    if valid0:
        return candidate0, 0, ParseStatus.PARSED, None, "Resolved fold=0 local wall time.", ()
    return candidate1, 1, ParseStatus.PARSED, None, "Resolved fold=1 local wall time.", ()


def _parse_time_expression(
    time_str: str,
    locale_tag: str,
    is_iso_t: bool = False,
) -> tuple[
    time | None,
    timezone | None,
    str | None,
    str,
    ParseStatus,
    ReasonCode | None,
    str,
]:
    """Parse time and optional offset/zone suffix.

    Returns:
      (time_obj, explicit_tz_offset, explicit_iana_zone, rule_suffix, status, reason, message)
    """
    s = time_str.strip(_WS_CHARS)

    # Strip Croatian prefix "u " or English "at "
    had_at_prefix = False
    if locale_tag == "hr-HR" and re.match(r"^u[\s\u00a0\u202f]+", s, flags=re.IGNORECASE):
        s = re.sub(r"^u[\s\u00a0\u202f]+", "", s, flags=re.IGNORECASE).strip(_WS_CHARS)
    elif locale_tag in {"en-US", "en-GB"} and re.match(
        r"^at[\s\u00a0\u202f]+", s, flags=re.IGNORECASE
    ):
        had_at_prefix = True
        s = re.sub(r"^at[\s\u00a0\u202f]+", "", s, flags=re.IGNORECASE).strip(_WS_CHARS)

    # Check for explicit IANA zone suffix (e.g., "Europe/Zagreb", "America/New_York", "UTC")
    explicit_iana: str | None = None
    iana_match = re.search(r"[\s\u00a0\u202f]+([A-Za-z_]+/[A-Za-z_]+|UTC|GMT)$", s)
    if iana_match:
        explicit_iana = iana_match.group(1)
        if explicit_iana == "GMT":
            explicit_iana = "UTC"
        s = s[: iana_match.start()].strip(_WS_CHARS)

    # Check for explicit numeric offset suffix (e.g., "+01:00", "-05:00", "Z")
    explicit_offset: timezone | None = None
    if s.endswith("Z") or s.endswith("z"):
        explicit_offset = UTC
        s = s[:-1].strip(_WS_CHARS)
    else:
        off_match = re.search(r"([+-])(\d{2}):(\d{2})$", s)
        if off_match:
            sign_char, oh_str, om_str = off_match.group(1), off_match.group(2), off_match.group(3)
            oh, om = int(oh_str), int(om_str)
            if oh > 14 or om >= 60:
                return (
                    None,
                    None,
                    None,
                    "",
                    ParseStatus.INVALID,
                    ReasonCode.TIMEZONE_INVALID,
                    f"Invalid UTC offset in {time_str!r}.",
                )
            delta = timedelta(hours=oh, minutes=om)
            if sign_char == "-":
                delta = -delta
            explicit_offset = timezone(delta)
            s = s[: off_match.start()].strip(_WS_CHARS)

    # Croatian "X sati" / "X sata" / "X sat"
    if locale_tag == "hr-HR":
        hr_sati = re.match(
            r"^(\d{1,2})(?:[\s\u00a0\u202f]+(?:sat|sata|sati))(?:\s*i\s*(\d{1,2})\s*(?:min|minuta))?$",
            s,
            flags=re.IGNORECASE,
        )
        if hr_sati:
            hh = int(hr_sati.group(1))
            mm = int(hr_sati.group(2)) if hr_sati.group(2) else 0
            if hh > 23 or mm > 59:
                return (
                    None,
                    None,
                    None,
                    "",
                    ParseStatus.INVALID,
                    ReasonCode.DATETIME_INVALID_TIME,
                    f"Hour/minute out of range in {time_str!r}.",
                )
            return (
                time(hh, mm, 0),
                explicit_offset,
                explicit_iana,
                "hr_sati",
                ParseStatus.PARSED,
                None,
                "",
            )

    # English "X o'clock" without AM/PM -> ambiguous!
    if locale_tag in {"en-US", "en-GB"} and re.match(
        r"^\d{1,2}\s*o'?clock$", s, flags=re.IGNORECASE
    ):
        return (
            None,
            None,
            None,
            "",
            ParseStatus.AMBIGUOUS,
            ReasonCode.DATETIME_AMPM_MISSING,
            f"English 12-hour expression {time_str!r} is missing explicit AM/PM.",
        )

    # Check 12-hour format with explicit AM/PM
    ampm_match = re.match(
        r"^(\d{1,2})(?::(\d{2})(?::(\d{2}))?)?[\s\u00a0\u202f]*(am|pm|a\.m\.|p\.m\.)$",
        s,
        flags=re.IGNORECASE,
    )
    if ampm_match:
        hh = int(ampm_match.group(1))
        mm = int(ampm_match.group(2)) if ampm_match.group(2) else 0
        ss = int(ampm_match.group(3)) if ampm_match.group(3) else 0
        meridiem = ampm_match.group(4).lower().replace(".", "")
        if hh < 1 or hh > 12 or mm > 59 or ss > 59:
            return (
                None,
                None,
                None,
                "",
                ParseStatus.INVALID,
                ReasonCode.DATETIME_INVALID_TIME,
                f"Invalid 12-hour time {time_str!r}.",
            )
        hh_24 = (0 if hh == 12 else hh) if meridiem == "am" else 12 if hh == 12 else hh + 12
        return (
            time(hh_24, mm, ss),
            explicit_offset,
            explicit_iana,
            "12h_ampm",
            ParseStatus.PARSED,
            None,
            "",
        )

    # Check 24-hour clock HH:MM(:SS)? (with optional 'h' suffix in hr-HR)
    h24_match = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?(?:[\s\u00a0\u202f]*h)?$", s)
    if h24_match:
        h_str = h24_match.group(1)
        hh = int(h_str)
        mm = int(h24_match.group(2))
        ss = int(h24_match.group(3)) if h24_match.group(3) else 0
        if hh > 23 or mm > 59 or ss > 59:
            return (
                None,
                None,
                None,
                "",
                ParseStatus.INVALID,
                ReasonCode.DATETIME_INVALID_TIME,
                f"Invalid 24-hour time {time_str!r}.",
            )
        # In English locales, single-digit hour (1..9 without leading zero) or 12:xx with "at" or non-ISO
        # without AM/PM is ambiguous between AM and PM
        if locale_tag in {"en-US", "en-GB"} and not is_iso_t:
            if len(h_str) == 1 and 1 <= hh <= 9:
                return (
                    None,
                    None,
                    None,
                    "",
                    ParseStatus.AMBIGUOUS,
                    ReasonCode.DATETIME_AMPM_MISSING,
                    f"Time {time_str!r} in {locale_tag} is ambiguous without leading zero or explicit AM/PM.",
                )
            if hh == 12 or (had_at_prefix and not h_str.startswith("0") and 1 <= hh <= 12):
                return (
                    None,
                    None,
                    None,
                    "",
                    ParseStatus.AMBIGUOUS,
                    ReasonCode.DATETIME_AMPM_MISSING,
                    f"Time {time_str!r} in {locale_tag} is ambiguous without explicit AM/PM.",
                )
        return (
            time(hh, mm, ss),
            explicit_offset,
            explicit_iana,
            "24h_hm",
            ParseStatus.PARSED,
            None,
            "",
        )

    # Bare integer hour like "at 5" in English -> ambiguous AM/PM
    if locale_tag in {"en-US", "en-GB"} and had_at_prefix and re.match(r"^\d{1,2}$", s):
        return (
            None,
            None,
            None,
            "",
            ParseStatus.AMBIGUOUS,
            ReasonCode.DATETIME_AMPM_MISSING,
            f"Time {time_str!r} in {locale_tag} is missing explicit AM/PM.",
        )

    return (
        None,
        None,
        None,
        "",
        ParseStatus.INVALID,
        ReasonCode.DATETIME_INVALID_TIME,
        f"Unrecognized time format in {time_str!r}.",
    )


def parse_datetime(
    raw_quote: str,
    locale: str | None,
    *,
    trusted_timezone: str | None = None,
    reference_datetime: datetime | None = None,
    allow_relative_weekday: bool = False,
) -> ParserResult[CanonicalDateTime]:
    """Parse a date+time quotation into a verified CanonicalDateTime with DST fold/gap checks."""
    if not raw_quote or not raw_quote.strip(_WS_CHARS):
        return ParserResult(
            status=ParseStatus.MISSING,
            reason_code=ReasonCode.FIELD_REQUIRED_MISSING,
            message="Datetime quotation is empty.",
        )

    if locale is not None and get_locale_profile(locale) is None:
        return ParserResult(
            status=ParseStatus.UNSUPPORTED,
            reason_code=ReasonCode.LOCALE_UNSUPPORTED,
            message=f"Locale {locale!r} is not supported.",
        )

    s = raw_quote.strip(_WS_CHARS)
    if _has_prohibited_date_chars(s):
        return ParserResult(
            status=ParseStatus.INVALID,
            reason_code=ReasonCode.DATE_INVALID_FORMAT,
            message=f"Datetime quotation {raw_quote!r} contains prohibited Unicode lookalikes.",
        )

    # Strip optional leading grammatical clause prepositions if included in verbatim span
    s = re.sub(
        r"^(?:s\s+početkom(?:\s+u)?|na\s+dan|na\s+datum|dana|za|od|do|starting(?:\s+on)?|from|until|on|to)[\s\u00a0\u202f]+",
        "",
        s,
        flags=re.IGNORECASE,
    ).strip(_WS_CHARS)

    profile = get_locale_profile(locale)
    locale_tag = profile.tag if profile else "unlocalized"

    # First check if input is a pure date without any time component
    pure_date_check = parse_date(
        s,
        locale,
        reference_datetime=reference_datetime,
        timezone_name=trusted_timezone,
        allow_relative_weekday=allow_relative_weekday,
    )
    if pure_date_check.is_ok:
        return ParserResult(
            status=ParseStatus.AMBIGUOUS,
            reason_code=ReasonCode.DATETIME_DATE_ONLY_NOT_ALLOWED,
            message=(
                f"Quotation {raw_quote!r} contains only a calendar date without a time; "
                "midnight is never silently assumed for an event datetime."
            ),
        )

    # Split date part and time part
    date_part: str | None = None
    time_part: str | None = None
    is_iso_t = False

    # Case A: ISO 'T' separator: YYYY-MM-DDTHH:MM...
    iso_t_match = re.match(r"^(\d{4}-\d{2}-\d{2})[T](\S.*)$", s)
    if iso_t_match:
        date_part = iso_t_match.group(1)
        time_part = iso_t_match.group(2)
        is_iso_t = True
    else:
        # Case B: Croatian/English connector " u " or " at "
        conn_match = re.search(
            r"[\s\u00a0\u202f]+(u|at)[\s\u00a0\u202f]+(.+)$", s, flags=re.IGNORECASE
        )
        if conn_match:
            date_part = s[: conn_match.start()].strip(_WS_CHARS)
            time_part = f"{conn_match.group(1)} {conn_match.group(2)}".strip(_WS_CHARS)
        else:
            # Case C: Date followed by whitespace and time token starting with digit
            # Try splitting before the time component
            time_tail = re.search(
                r"[\s\u00a0\u202f]+(\d{1,2}:\d{2}(?::\d{2})?(?:[\s\u00a0\u202f]*(?:am|pm|a\.m\.|p\.m\.|h))?(?:[\s\u00a0\u202f]*(?:[+-]\d{2}:\d{2}|Z|[A-Za-z_]+/[A-Za-z_]+|UTC|GMT))?)$",
                s,
                flags=re.IGNORECASE,
            )
            if time_tail:
                date_part = s[: time_tail.start()].strip(_WS_CHARS).rstrip(",")
                time_part = time_tail.group(1).strip(_WS_CHARS)
            else:
                # Also check 12h without minutes like "2026-11-03 9am" or "3. studenoga 2026. 9 sati"
                alt_tail = re.search(
                    r"[\s\u00a0\u202f]+(\d{1,2}[\s\u00a0\u202f]*(?:am|pm|a\.m\.|p\.m\.|sati|sata|sat|o'?clock).*)$",
                    s,
                    flags=re.IGNORECASE,
                )
                if alt_tail:
                    date_part = s[: alt_tail.start()].strip(_WS_CHARS).rstrip(",")
                    time_part = alt_tail.group(1).strip(_WS_CHARS)

    if not date_part or not time_part:
        # Check if only a time was given without a date (e.g., "09:30" or "u 9:30")
        _t_only, _, _, _, t_status, _, _ = _parse_time_expression(s, locale_tag, is_iso_t=False)
        if t_status in {ParseStatus.PARSED, ParseStatus.AMBIGUOUS}:
            return ParserResult(
                status=ParseStatus.AMBIGUOUS,
                reason_code=ReasonCode.FIELD_REQUIRED_MISSING,
                message=f"Datetime quotation {raw_quote!r} is missing a calendar date.",
            )
        return ParserResult(
            status=ParseStatus.INVALID,
            reason_code=ReasonCode.DATE_INVALID_FORMAT,
            message=f"Could not separate date and time components in {raw_quote!r}.",
        )

    date_res = parse_date(
        date_part,
        locale,
        reference_datetime=reference_datetime,
        timezone_name=trusted_timezone,
        allow_relative_weekday=allow_relative_weekday,
    )
    if not date_res.is_ok or date_res.value is None:
        return ParserResult(
            status=date_res.status,
            reason_code=date_res.reason_code,
            message=date_res.message,
            candidates=date_res.candidates,
        )

    t_obj, exp_offset, exp_iana, t_rule, t_status, t_reason, t_msg = _parse_time_expression(
        time_part, locale_tag, is_iso_t=is_iso_t
    )
    if t_status != ParseStatus.PARSED or t_obj is None:
        return ParserResult(
            status=t_status,
            reason_code=t_reason or ReasonCode.DATETIME_INVALID_TIME,
            message=t_msg,
        )

    # Validate timezone / offset consistency
    if exp_iana is not None:
        try:
            ZoneInfo(exp_iana)
        except ZoneInfoNotFoundError:
            return ParserResult(
                status=ParseStatus.INVALID,
                reason_code=ReasonCode.TIMEZONE_INVALID,
                message=f"Invalid IANA timezone {exp_iana!r} in {raw_quote!r}.",
            )
        if trusted_timezone and exp_iana != trusted_timezone:
            return ParserResult(
                status=ParseStatus.INVALID,
                reason_code=ReasonCode.TIMEZONE_CONFLICT,
                message=(
                    f"Explicit timezone {exp_iana!r} in quotation conflicts with "
                    f"trusted context timezone {trusted_timezone!r}."
                ),
            )

    effective_iana = exp_iana or trusted_timezone
    if effective_iana is None and exp_offset is None:
        return ParserResult(
            status=ParseStatus.AMBIGUOUS,
            reason_code=ReasonCode.TIMEZONE_MISSING,
            message=f"No timezone supplied for local datetime {raw_quote!r}.",
        )

    naive_dt = datetime.combine(date_res.value.value, t_obj)

    # If an explicit UTC offset was written in the string:
    if exp_offset is not None:
        aware_dt = naive_dt.replace(tzinfo=exp_offset)
        if effective_iana is not None:
            z = ZoneInfo(effective_iana)
            # Check that at this UTC instant, the zone's offset matches exp_offset
            in_zone = aware_dt.astimezone(z)
            if (
                in_zone.utcoffset() != exp_offset.utcoffset(naive_dt)
                or in_zone.replace(tzinfo=None) != naive_dt
            ):
                return ParserResult(
                    status=ParseStatus.INVALID,
                    reason_code=ReasonCode.TIMEZONE_CONFLICT,
                    message=(
                        f"Explicit UTC offset {exp_offset} in {raw_quote!r} does not match "
                        f"timezone {effective_iana} on {date_res.value.iso_date} "
                        f"(expected {in_zone.strftime('%z')})."
                    ),
                )
            fold = in_zone.fold
            aware_dt = in_zone
        else:
            effective_iana = "UTC" if exp_offset == UTC else str(exp_offset)
            fold = 0
    else:
        assert effective_iana is not None
        resolved_dt, fold, dst_status, dst_reason, dst_msg, dst_cands = (
            resolve_local_wall_datetime_dst(naive_dt, effective_iana)
        )
        if dst_status != ParseStatus.PARSED or resolved_dt is None:
            return ParserResult(
                status=dst_status,
                reason_code=dst_reason,
                message=dst_msg,
                candidates=dst_cands,
            )
        aware_dt = resolved_dt

    utc_dt = aware_dt.astimezone(UTC)
    rfc3339_str = aware_dt.isoformat(timespec="seconds")
    utc_str = utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    rule_id = f"{date_res.value.rule_id}__{t_rule}"

    return ParserResult(
        status=ParseStatus.PARSED,
        value=CanonicalDateTime(
            value=aware_dt,
            rfc3339=rfc3339_str,
            utc_rfc3339=utc_str,
            iana_timezone=effective_iana,
            fold=fold,
            local_wall_iso=naive_dt.isoformat(timespec="seconds"),
            original_quote=raw_quote,
            locale=locale_tag,
            rule_id=rule_id,
        ),
        rule_id=rule_id,
        message="Successfully parsed timezone-aware datetime.",
    )


def parse_duration(
    raw_quote: str,
    locale: str | None,
) -> ParserResult[CanonicalDuration]:
    """Parse a positive duration expression (Croatian, English, or ISO 8601) into minutes."""
    if not raw_quote or not raw_quote.strip(_WS_CHARS):
        return ParserResult(
            status=ParseStatus.MISSING,
            reason_code=ReasonCode.DURATION_INVALID,
            message="Duration quotation is empty.",
        )

    s = raw_quote.strip(_WS_CHARS)
    if _has_prohibited_date_chars(s):
        return ParserResult(
            status=ParseStatus.INVALID,
            reason_code=ReasonCode.DURATION_INVALID,
            message=f"Duration {raw_quote!r} contains prohibited characters.",
        )

    # Strip optional leading grammatical duration prepositions if included in verbatim span
    s = re.sub(
        r"^(?:u\s+trajanju\s+od|trajanje(?:\s+od)?|duration(?:\s+of)?|lasting|za|for)[\s\u00a0\u202f]+",
        "",
        s,
        flags=re.IGNORECASE,
    ).strip(_WS_CHARS)

    locale_tag = locale or "unlocalized"

    # 1. ISO 8601 duration: PT1H30M, PT45M, PT2H
    iso_m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?$", s, flags=re.IGNORECASE)
    if iso_m and (iso_m.group(1) or iso_m.group(2)):
        hours = int(iso_m.group(1)) if iso_m.group(1) else 0
        mins = int(iso_m.group(2)) if iso_m.group(2) else 0
        total = hours * 60 + mins
        if total <= 0:
            return ParserResult(
                status=ParseStatus.INVALID,
                reason_code=ReasonCode.DURATION_INVALID,
                message="Duration must be strictly positive.",
            )
        return ParserResult(
            status=ParseStatus.PARSED,
            value=CanonicalDuration(
                minutes=total,
                iso_duration=f"PT{total}M",
                original_quote=raw_quote,
                locale=locale_tag,
                rule_id="iso8601_duration",
            ),
            rule_id="iso8601_duration",
            message="Parsed ISO 8601 duration.",
        )

    # 2. Combined hours + minutes (Croatian & English): "1 sat i 30 minuta", "1h 30m", "1 hour 30 minutes"
    combo_m = re.match(
        r"^(\d+)[\s\u00a0\u202f]*(?:h|hr|hrs|hour|hours|sat|sata|sati)"
        r"(?:[\s\u00a0\u202f]*(?:i|and)?[\s\u00a0\u202f]*(\d+)[\s\u00a0\u202f]*(?:m|min|mins|minute|minutes|minuta|minute))?$",
        s,
        flags=re.IGNORECASE,
    )
    if combo_m:
        hours = int(combo_m.group(1))
        mins = int(combo_m.group(2)) if combo_m.group(2) else 0
        total = hours * 60 + mins
        if total <= 0:
            return ParserResult(
                status=ParseStatus.INVALID,
                reason_code=ReasonCode.DURATION_INVALID,
                message="Duration must be strictly positive.",
            )
        return ParserResult(
            status=ParseStatus.PARSED,
            value=CanonicalDuration(
                minutes=total,
                iso_duration=f"PT{total}M",
                original_quote=raw_quote,
                locale=locale_tag,
                rule_id=f"{locale_tag}_duration_hours_minutes",
            ),
            rule_id=f"{locale_tag}_duration_hours_minutes",
            message="Parsed duration.",
        )

    # 3. Minutes only: "30 minuta", "45 min", "90 minutes", "30m"
    min_m = re.match(
        r"^(\d+)[\s\u00a0\u202f]*(?:m|min|mins|minute|minutes|minuta)$",
        s,
        flags=re.IGNORECASE,
    )
    if min_m:
        total = int(min_m.group(1))
        if total <= 0:
            return ParserResult(
                status=ParseStatus.INVALID,
                reason_code=ReasonCode.DURATION_INVALID,
                message="Duration must be strictly positive.",
            )
        return ParserResult(
            status=ParseStatus.PARSED,
            value=CanonicalDuration(
                minutes=total,
                iso_duration=f"PT{total}M",
                original_quote=raw_quote,
                locale=locale_tag,
                rule_id=f"{locale_tag}_duration_minutes",
            ),
            rule_id=f"{locale_tag}_duration_minutes",
            message="Parsed duration in minutes.",
        )

    # 4. Croatian "pola sata" / English "half an hour" (30 minutes)
    if s.lower() in {"pola sata", "half an hour", "half hour"}:
        return ParserResult(
            status=ParseStatus.PARSED,
            value=CanonicalDuration(
                minutes=30,
                iso_duration="PT30M",
                original_quote=raw_quote,
                locale=locale_tag,
                rule_id=f"{locale_tag}_duration_half_hour",
            ),
            rule_id=f"{locale_tag}_duration_half_hour",
            message="Parsed half-hour duration.",
        )

    if s.lower() in {"sat i pol", "an hour and a half", "1.5 hours", "1,5 sat", "1,5 sata"}:
        return ParserResult(
            status=ParseStatus.PARSED,
            value=CanonicalDuration(
                minutes=90,
                iso_duration="PT90M",
                original_quote=raw_quote,
                locale=locale_tag,
                rule_id=f"{locale_tag}_duration_90m",
            ),
            rule_id=f"{locale_tag}_duration_90m",
            message="Parsed 90-minute duration.",
        )

    return ParserResult(
        status=ParseStatus.INVALID,
        reason_code=ReasonCode.DURATION_INVALID,
        message=f"Unsupported or invalid duration expression {raw_quote!r}.",
    )
