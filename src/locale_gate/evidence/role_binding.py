"""Deterministic candidate scanning and semantic field-role binding verification (§5.2)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from locale_gate.contracts.enums import ParseStatus, ReasonCode, RoleBindingStatus
from locale_gate.evidence.resolver import ResolvedEvidence

# Candidate money pattern in source_text (matches currency symbols/codes + localized numbers)
_MONEY_CANDIDATE_RE = re.compile(
    r"(?:(?:US\$|CA\$|A\$|NZ\$|HK\$|EUR|USD|GBP|CHF|JPY|PLN|CZK|€|£|\$|¥)[\s\u00a0\u202f]*\d[\d.,\s\u00a0\u202f]*\d|\d[\d.,\s\u00a0\u202f]*\d[\s\u00a0\u202f]*(?:EUR|USD|GBP|CHF|JPY|PLN|CZK|€|£|\$|¥)|\b\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})\b)",
    flags=re.IGNORECASE,
)

# Identifier / reference / invoice number cues immediately preceding a number
_IDENTIFIER_ROLE_PREFIX_RE = re.compile(
    r"(?:račun\s+br\.?|račun\s+broj|broj\s+računa|ponuda\s+br\.?|narudžba\s+br\.?|referenca\s+br\.?|"
    r"invoice\s*(?:no\.?|number|#)|inv\s*#|ref\s*(?:no\.?|#)|order\s*(?:no\.?|#)|receipt\s*#|"
    r"dvorana|room|id)\s*:?\s*$",
    flags=re.IGNORECASE,
)

# Negation / retraction cues immediately preceding a candidate
_NEGATED_PREFIX_RE = re.compile(
    r"(?:^|[\s,(])(?:nije|umjesto|stornirano|pogrešno|ne\s+iznosi|not|instead\s+of|rather\s+than|was\s+originally|cancelled)\s+(?:iznos\s+od\s+|trošak\s+od\s+|na\s+|od\s+|u\s+|at\s+|on\s+)?$",
    flags=re.IGNORECASE,
)

# Subtotal / tax / discount / budget component cues immediately preceding or following a money candidate
_COMPONENT_PREFIX_RE = re.compile(
    r"(?:osnovica|neto|pdv(?:\s+\d+%)?|porez|popust|rabat|napojnica|"
    r"budžet(?:\s+projekta|\s+odjela)?|budget|saldo|stanje|balance|limit|"
    r"subtotal|sub-total|net\s+amount|vat(?:\s+\d+%)?|sales\s+tax|tax|discount|tip)\s*(?:iznosi|od|je|is|of|:|=)?\s*$",
    flags=re.IGNORECASE,
)

_COMPONENT_SUFFIX_RE = re.compile(
    r"^\s*(?:\(\s*)?(?:osnovica|neto|pdv|porez|popust|rabat|subtotal|sub-total|vat|tax|discount|tip|budget|balance)\b",
    flags=re.IGNORECASE,
)

# Explicit gross/total/expense cues in the sentence indicating a true expense total exists elsewhere
_TOTAL_CUE_RE = re.compile(
    r"\b(?:ukupno|sveukupno|ukupna\s+vrijednost|ukupni\s+iznos|za\s+platiti|iznosi|"
    r"trošak|evidentiraj\s+trošak|expense|submit\s+expense|"
    r"total|grand\s+total|amount\s+due|total\s+due|total\s+amount)\b",
    flags=re.IGNORECASE,
)

# Unresolved choice / disjunctive cues between multiple candidates
_UNRESOLVED_CHOICE_RE = re.compile(
    r"(?:\bjedan\s+na\s+.+?\s+drugi\s+na|\bponuda\s+od\s+.+?\s+i\s+račun\s+od|"
    r"\bračuni?\s+od\s+.+?\s+i\s+(?:od\s+)?(?:\$|€|£|USD|EUR|GBP|\d)|"
    r"\bjedan\s+od\s+njih|\bone\s+of\s+them|\beither\s+.+?\s+or|"
    r"\bili\s+(?:na\s+|od\s+|u\s+)?(?:\$|€|£|USD|EUR|GBP|\d)|"
    r"\bor\s+(?:for\s+|of\s+|at\s+|on\s+)?(?:\$|€|£|USD|EUR|GBP|\d)|"
    r"\b(?:i|and)\s+(?:od\s+|for\s+)?(?:\$|€|£|USD|EUR|GBP|\d))",
    flags=re.IGNORECASE,
)

# Regex to match individual temporal cues in the preceding context:
_START_CUE_RE = re.compile(
    r"\b(?:od|počinje(?:\s+u|\s+od)?|početak(?:\s+u|\s+od)?|starta(?:\s+u)?|"
    r"from|starts?(?:\s+at)?|starting(?:\s+at)?|begins?(?:\s+at)?|beginning(?:\s+at)?|start(?:\s+at)?)\b",
    flags=re.IGNORECASE,
)

_END_CUE_RE = re.compile(
    r"\b(?:do|završava(?:\s+u|\s+do)?|kraj(?:\s+u|\s+do)?|"
    r"to|until|till|ends?(?:\s+at)?|ending(?:\s+at)?|finish(?:es)?(?:\s+at)?|finishing(?:\s+at)?|finish(?:\s+at)?)\b",
    flags=re.IGNORECASE,
)

_NEGATION_CUE_RE = re.compile(
    r"\b(?:nije(?:\s+u)?|umjesto(?:\s+u)?|stornirano|pogrešno|ne\s+u|"
    r"not(?:\s+at)?|instead\s+of|rather\s+than|was\s+originally|cancelled)\b",
    flags=re.IGNORECASE,
)


def find_closest_temporal_cue(
    preceding_text: str,
) -> tuple[str, str] | None:
    """Find the closest governing temporal cue (cue_type, cue_text) in preceding text.

    Returns (cue_type, cue_text) where cue_type is 'start', 'end', or 'negation'.
    Picks the cue with the maximum end offset in preceding_text (closest to the token span).
    """
    candidates: list[tuple[int, str, str]] = []
    for m in _START_CUE_RE.finditer(preceding_text):
        candidates.append((m.end(), "start", m.group(0)))
    for m in _END_CUE_RE.finditer(preceding_text):
        candidates.append((m.end(), "end", m.group(0)))
    for m in _NEGATION_CUE_RE.finditer(preceding_text):
        candidates.append((m.end(), "negation", m.group(0)))

    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0], reverse=True)
    _best_end, cue_type, cue_text = candidates[0]
    return cue_type, cue_text


@dataclass(frozen=True)
class RoleBindingOutcome:
    """Result of deterministic semantic field-role verification (§5.2)."""

    role_status: RoleBindingStatus
    parse_status: ParseStatus
    reason_code: ReasonCode | None
    message: str
    candidate_count: int
    competing_candidates: tuple[str, ...]


def extract_money_candidates(source_text: str) -> list[tuple[int, int, str]]:
    """Extract candidate money spans `(start, end, text)` from `source_text`."""
    out: list[tuple[int, int, str]] = []
    for m in _MONEY_CANDIDATE_RE.finditer(source_text):
        span_text = m.group(0).strip()
        # Exclude date strings like 03.11.2026 or 15.10.26 that look like dotted numbers
        if re.match(r"^\d{1,2}\.\d{1,2}\.\d{2,4}\.?$", span_text):
            continue
        # Exclude date substrings like 03.11 in 03.11.2026
        if m.end() < len(source_text) and re.match(r"^\.\d{2,4}", source_text[m.end() :]):
            continue
        if m.start() > 0 and re.search(r"\d{1,2}\.$", source_text[: m.start()]):
            continue
        out.append((m.start(), m.end(), span_text))
    return out


def verify_amount_role_binding(
    source_text: str,
    resolved: ResolvedEvidence,
) -> RoleBindingOutcome:
    """Verify that the resolved `amount` quotation is semantically bound to the expense amount role.

    Detects:
    1. Identifier/invoice number misbound as amount (`Račun broj 450,00 €` vs `85,50 €`).
    2. Negated/retracted amount (`nije 85,50 € nego 58,50 €` where `85,50 €` is selected).
    3. Subtotal/VAT/discount component selected when an explicit total is present in `source_text`.
    4. Unresolved multi-candidate disjunction (`40,00 € ili 95,50 € — evidentiraj jedan od njih`).
    """
    candidates = extract_money_candidates(source_text)
    cand_strings = tuple(dict.fromkeys(c[2] for c in candidates))
    cand_count = max(1, len(cand_strings))

    left_window = source_text[max(0, resolved.start - 48) : resolved.start]
    right_window = source_text[resolved.end : min(len(source_text), resolved.end + 32)]

    # 1. Check if immediately preceded by an invoice / reference / order number cue
    if _IDENTIFIER_ROLE_PREFIX_RE.search(left_window):
        return RoleBindingOutcome(
            role_status=RoleBindingStatus.MISMATCH,
            parse_status=ParseStatus.INVALID,
            reason_code=ReasonCode.FIELD_ROLE_MISMATCH,
            message=(
                f"Quotation {resolved.quote!r} is preceded by an identifier/invoice label "
                f"({left_window.strip()!r}) and cannot be bound to 'amount'."
            ),
            candidate_count=cand_count,
            competing_candidates=cand_strings,
        )

    # 2. Check if immediately preceded by a negation / retraction cue ("nije 85,50 €", "not $85.50")
    if _NEGATED_PREFIX_RE.search(left_window):
        return RoleBindingOutcome(
            role_status=RoleBindingStatus.MISMATCH,
            parse_status=ParseStatus.INVALID,
            reason_code=ReasonCode.FIELD_ROLE_MISMATCH,
            message=(
                f"Quotation {resolved.quote!r} is negated or retracted in source context "
                f"({left_window.strip()!r}) and cannot be bound to 'amount'."
            ),
            candidate_count=cand_count,
            competing_candidates=cand_strings,
        )

    # 3. Check if bound to a tax/subtotal/discount component while a total cue exists in source_text
    if (
        cand_count >= 2
        and _TOTAL_CUE_RE.search(source_text)
        and (_COMPONENT_PREFIX_RE.search(left_window) or _COMPONENT_SUFFIX_RE.search(right_window))
    ):
        return RoleBindingOutcome(
            role_status=RoleBindingStatus.MISMATCH,
            parse_status=ParseStatus.INVALID,
            reason_code=ReasonCode.FIELD_ROLE_MISMATCH,
            message=(
                f"Quotation {resolved.quote!r} is labeled as a subtotal/tax/discount component "
                "while a separate total amount exists in the request."
            ),
            candidate_count=cand_count,
            competing_candidates=cand_strings,
        )

    # 4. Check if multiple competing candidates exist with unresolved disjunctive/choice cues
    if cand_count >= 2 and _UNRESOLVED_CHOICE_RE.search(source_text):
        return RoleBindingOutcome(
            role_status=RoleBindingStatus.AMBIGUOUS,
            parse_status=ParseStatus.AMBIGUOUS,
            reason_code=ReasonCode.FIELD_ROLE_AMBIGUOUS,
            message=(
                f"Multiple competing money candidates {list(cand_strings)} appear in an unresolved "
                "choice context; user clarification is required."
            ),
            candidate_count=cand_count,
            competing_candidates=cand_strings,
        )

    return RoleBindingOutcome(
        role_status=RoleBindingStatus.VERIFIED,
        parse_status=ParseStatus.PARSED,
        reason_code=None,
        message="Verified semantic role binding for amount.",
        candidate_count=cand_count,
        competing_candidates=cand_strings,
    )


def verify_temporal_role_binding(
    source_text: str,
    field_name: str,
    resolved: ResolvedEvidence,
) -> RoleBindingOutcome:
    """Verify that `expense_date`, `start_datetime`, or `end_datetime` is bound to the intended temporal role.

    Identifies the closest applicable semantic cue rather than mere presence in window (§P0.2).
    """
    left_window = source_text[max(0, resolved.start - 48) : resolved.start]
    right_window = source_text[resolved.end : min(len(source_text), resolved.end + 32)]

    # 1. Closest governing cue analysis
    closest = find_closest_temporal_cue(left_window)
    if closest is not None:
        cue_type, cue_text = closest
        if cue_type == "negation":
            return RoleBindingOutcome(
                role_status=RoleBindingStatus.MISMATCH,
                parse_status=ParseStatus.INVALID,
                reason_code=ReasonCode.FIELD_ROLE_MISMATCH,
                message=(
                    f"Temporal quotation {resolved.quote!r} for {field_name!r} is negated or superseded "
                    f"in source context by closest cue {cue_text!r} ({left_window.strip()!r})."
                ),
                candidate_count=2,
                competing_candidates=(resolved.quote,),
            )
        if cue_type == "end" and field_name == "start_datetime":
            return RoleBindingOutcome(
                role_status=RoleBindingStatus.MISMATCH,
                parse_status=ParseStatus.INVALID,
                reason_code=ReasonCode.FIELD_ROLE_MISMATCH,
                message=(
                    f"Quotation {resolved.quote!r} is governed by closest end-time marker {cue_text!r} "
                    f"({left_window.strip()!r}) and cannot be bound to 'start_datetime'. "
                    "Explicit semantic field roles cannot be swapped to satisfy domain validation."
                ),
                candidate_count=2,
                competing_candidates=(resolved.quote,),
            )
        if cue_type == "start" and field_name == "end_datetime":
            return RoleBindingOutcome(
                role_status=RoleBindingStatus.MISMATCH,
                parse_status=ParseStatus.INVALID,
                reason_code=ReasonCode.FIELD_ROLE_MISMATCH,
                message=(
                    f"Quotation {resolved.quote!r} is governed by closest start-time marker {cue_text!r} "
                    f"({left_window.strip()!r}) and cannot be bound to 'end_datetime'. "
                    "Explicit semantic field roles cannot be swapped to satisfy domain validation."
                ),
                candidate_count=2,
                competing_candidates=(resolved.quote,),
            )

    # 2. Negated date or time prefix fallback
    if _NEGATED_PREFIX_RE.search(left_window):
        return RoleBindingOutcome(
            role_status=RoleBindingStatus.MISMATCH,
            parse_status=ParseStatus.INVALID,
            reason_code=ReasonCode.FIELD_ROLE_MISMATCH,
            message=(
                f"Temporal quotation {resolved.quote!r} for {field_name!r} is negated or superseded "
                f"in source context ({left_window.strip()!r})."
            ),
            candidate_count=2,
            competing_candidates=(resolved.quote,),
        )

    # 3. Unresolved disjunctive choice right before or after the date/time ("u 09:00 ili u 14:00", "01.11.2026. ili 02.11.2026.")
    if re.search(
        r"^\s*(?:ili|or)\s+(?:u\s+|at\s+|on\s+)?\d", right_window, flags=re.IGNORECASE
    ) or re.search(
        r"\d\S*\s+(?:ili|or)\s+(?:u\s+|at\s+|on\s+|s\s+datumom\s+)?$",
        left_window,
        flags=re.IGNORECASE,
    ):
        return RoleBindingOutcome(
            role_status=RoleBindingStatus.AMBIGUOUS,
            parse_status=ParseStatus.AMBIGUOUS,
            reason_code=ReasonCode.FIELD_ROLE_AMBIGUOUS,
            message=(
                f"Temporal quotation {resolved.quote!r} for {field_name!r} is part of an unresolved "
                "'or'/'ili' alternative; user clarification is required."
            ),
            candidate_count=2,
            competing_candidates=(resolved.quote,),
        )

    return RoleBindingOutcome(
        role_status=RoleBindingStatus.VERIFIED,
        parse_status=ParseStatus.PARSED,
        reason_code=None,
        message=f"Verified semantic role binding for {field_name}.",
        candidate_count=1,
        competing_candidates=(resolved.quote,),
    )
