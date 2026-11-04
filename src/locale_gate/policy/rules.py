"""Policy profiles, risk classification, and clarification vs block decision rules."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from locale_gate.contracts.enums import GateDecisionStatus, ParseStatus, ReasonCode

CLARIFICATION_REASON_CODES: frozenset[ReasonCode] = frozenset(
    {
        ReasonCode.CLARIFY_MODEL_REQUESTED,
        ReasonCode.EVIDENCE_AMBIGUOUS,
        ReasonCode.EVIDENCE_CONTEXT_AMBIGUOUS,
        ReasonCode.FIELD_ROLE_AMBIGUOUS,
        ReasonCode.LOCALE_MISSING,
        ReasonCode.MONEY_AMBIGUOUS_LOCALE,
        ReasonCode.CURRENCY_MISSING,
        ReasonCode.DATE_YEAR_MISSING,
        ReasonCode.DATE_AMBIGUOUS_ORDER,
        ReasonCode.DATE_RELATIVE_MISSING_REF,
        ReasonCode.DATETIME_DATE_ONLY_NOT_ALLOWED,
        ReasonCode.DATETIME_AMPM_MISSING,
        ReasonCode.DATETIME_DST_AMBIGUOUS,
        ReasonCode.TIMEZONE_MISSING,
        ReasonCode.EVENT_END_OR_DURATION_MISSING,
        ReasonCode.FIELD_REQUIRED_MISSING,
    }
)


@dataclass(frozen=True)
class PolicyProfile:
    """Immutable policy rules governing validation and confirmation."""

    profile_id: str = "research_simulation_v1"
    max_expense_amount: Decimal = Decimal("100000.00")
    require_expense_description: bool = False
    allow_relative_weekday: bool = False
    allow_historical_currencies: bool = True
    enforce_role_binding: bool = True
    require_user_confirmation_for_writes: bool = True


POLICY_PROFILES: dict[str, PolicyProfile] = {
    "research_simulation_v1": PolicyProfile(
        profile_id="research_simulation_v1",
        max_expense_amount=Decimal("100000.00"),
        require_expense_description=False,
        allow_relative_weekday=False,
        allow_historical_currencies=True,
        enforce_role_binding=True,
        require_user_confirmation_for_writes=True,
    ),
    "strict_description_v1": PolicyProfile(
        profile_id="strict_description_v1",
        max_expense_amount=Decimal("100000.00"),
        require_expense_description=True,
        allow_relative_weekday=False,
        allow_historical_currencies=True,
        enforce_role_binding=True,
        require_user_confirmation_for_writes=True,
    ),
    "current_currencies_only_v1": PolicyProfile(
        profile_id="current_currencies_only_v1",
        max_expense_amount=Decimal("100000.00"),
        require_expense_description=False,
        allow_relative_weekday=False,
        allow_historical_currencies=False,
        enforce_role_binding=True,
        require_user_confirmation_for_writes=True,
    ),
}


def get_policy_profile(profile_id: str | None) -> PolicyProfile:
    """Return the configured PolicyProfile, defaulting to research_simulation_v1."""
    if not profile_id:
        return POLICY_PROFILES["research_simulation_v1"]
    return POLICY_PROFILES.get(profile_id, POLICY_PROFILES["research_simulation_v1"])


def classify_failure_status(
    parse_status: ParseStatus,
    reason_code: ReasonCode,
) -> GateDecisionStatus:
    """Map a field or policy failure to NEEDS_CLARIFICATION or BLOCKED."""
    if parse_status in {ParseStatus.AMBIGUOUS, ParseStatus.MISSING}:
        return GateDecisionStatus.NEEDS_CLARIFICATION
    if reason_code in CLARIFICATION_REASON_CODES:
        return GateDecisionStatus.NEEDS_CLARIFICATION
    return GateDecisionStatus.BLOCKED


def build_clarification_prompt(
    reason_code: ReasonCode,
    locale: str | None,
    field_name: str | None = None,
) -> str:
    """Generate a concise, deterministic clarification question in the user's locale."""
    is_hr = (locale or "hr-HR") == "hr-HR"
    if reason_code == ReasonCode.CURRENCY_MISSING:
        return (
            "Koju valutu trebamo koristiti za ovaj iznos (npr. EUR, USD, GBP)?"
            if is_hr
            else "Which currency should be used for this amount (e.g., EUR, USD, GBP)?"
        )
    if reason_code == ReasonCode.DATE_YEAR_MISSING:
        return (
            "Možete li navesti punu četveroznamenkastu godinu za datum?"
            if is_hr
            else "Could you specify the full four-digit year for the date?"
        )
    if reason_code == ReasonCode.DATETIME_AMPM_MISSING:
        return (
            "Je li navedeno vrijeme prijepodne (AM) ili poslijepodne (PM)?"
            if is_hr
            else "Please clarify whether the time is AM or PM (or use 24-hour format)."
        )
    if reason_code == ReasonCode.DATETIME_DST_AMBIGUOUS:
        return (
            "Navedeno lokalno vrijeme ponavlja se zbog prijelaza sa ljetnog na zimsko računanje "
            "vremena (+02:00 ili +01:00). Koji pomak trebamo koristiti?"
            if is_hr
            else "The specified local time occurs twice during the daylight saving fall-back transition. Please specify the UTC offset."
        )
    if reason_code == ReasonCode.DATETIME_DATE_ONLY_NOT_ALLOWED:
        return (
            "Za zakazivanje događaja potrebno je točno vrijeme početka uz datum."
            if is_hr
            else "Scheduling an event requires an explicit start time in addition to the date."
        )
    if reason_code == ReasonCode.EVENT_END_OR_DURATION_MISSING:
        return (
            "Molimo navedite vrijeme završetka ili trajanje događaja."
            if is_hr
            else "Please provide either an end time or a duration for the event."
        )
    if reason_code in {
        ReasonCode.EVIDENCE_AMBIGUOUS,
        ReasonCode.EVIDENCE_CONTEXT_AMBIGUOUS,
        ReasonCode.FIELD_ROLE_AMBIGUOUS,
    }:
        return (
            f"Vrijednost za polje '{field_name or ''}' je višeznačna u poruci. Možete li pojasniti koju točno vrijednost trebamo koristiti?"
            if is_hr
            else f"The value for '{field_name or ''}' is ambiguous in your message. Could you clarify which candidate value applies?"
        )
    if reason_code in {
        ReasonCode.LOCALE_MISSING,
        ReasonCode.MONEY_AMBIGUOUS_LOCALE,
        ReasonCode.DATE_AMBIGUOUS_ORDER,
    }:
        return (
            "Format broja ili datuma je višeznačan bez odabranog lokaliteta (npr. hr-HR, en-US, en-GB)."
            if is_hr
            else "The number or date format is ambiguous without an explicit locale profile (e.g., hr-HR, en-US, en-GB)."
        )
    if reason_code == ReasonCode.TIMEZONE_MISSING:
        return (
            "Koju vremensku zonu trebamo koristiti za ovaj događaj (npr. Europe/Zagreb)?"
            if is_hr
            else "Which timezone should be used for this event (e.g., Europe/Zagreb)?"
        )
    return (
        f"Potrebno je pojašnjenje za polje '{field_name or 'zahtjev'}' ({reason_code.value})."
        if is_hr
        else f"Clarification is required for '{field_name or 'request'}' ({reason_code.value})."
    )
