"""Deterministic exact-quotation resolution, Unicode threat detection, and span provenance (§5.3)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from locale_gate.contracts.enums import OffsetUnit, ParseStatus, ReasonCode
from locale_gate.contracts.models import EvidenceQuote

# Bidirectional formatting and override characters (UTS #39 / Unicode Bidi Algorithm)
_BIDI_CONTROL_CHARS = frozenset(
    {
        "\u061c",  # ARABIC LETTER MARK
        "\u200e",  # LEFT-TO-RIGHT MARK
        "\u200f",  # RIGHT-TO-LEFT MARK
        "\u202a",  # LEFT-TO-RIGHT EMBEDDING
        "\u202b",  # RIGHT-TO-LEFT EMBEDDING
        "\u202c",  # POP DIRECTIONAL FORMATTING
        "\u202d",  # LEFT-TO-RIGHT OVERRIDE
        "\u202e",  # RIGHT-TO-LEFT OVERRIDE
        "\u2066",  # LEFT-TO-RIGHT ISOLATE
        "\u2067",  # RIGHT-TO-LEFT ISOLATE
        "\u2068",  # FIRST STRONG ISOLATE
        "\u2069",  # POP DIRECTIONAL ISOLATE
    }
)

# Zero-width / invisible format characters prohibited in or adjacent to evidence spans
_ZERO_WIDTH_CHARS = frozenset(
    {
        "\u200b",  # ZERO WIDTH SPACE
        "\u200c",  # ZERO WIDTH NON-JOINER
        "\u200d",  # ZERO WIDTH JOINER
        "\u2060",  # WORD JOINER
        "\ufeff",  # ZERO WIDTH NO-BREAK SPACE (BOM)
    }
)

# Common Cyrillic/Greek homoglyphs that visually spoof Latin letters/digits
_CONFUSABLE_HOMOGLYPHS = frozenset(
    {
        "\u0430",  # Cyrillic small a
        "\u0435",  # Cyrillic small ie
        "\u043e",  # Cyrillic small o
        "\u0440",  # Cyrillic small er
        "\u0441",  # Cyrillic small es
        "\u0443",  # Cyrillic small u
        "\u0445",  # Cyrillic small ha
        "\u0456",  # Cyrillic small byelorussian-ukrainian i
        "\u0458",  # Cyrillic small je
        "\u0455",  # Cyrillic small dze
        "\u0410",  # Cyrillic capital A
        "\u0412",  # Cyrillic capital VE
        "\u0415",  # Cyrillic capital IE
        "\u041a",  # Cyrillic capital KA
        "\u041c",  # Cyrillic capital EM
        "\u041d",  # Cyrillic capital EN
        "\u041e",  # Cyrillic capital O
        "\u0420",  # Cyrillic capital ER
        "\u0421",  # Cyrillic capital ES
        "\u0422",  # Cyrillic capital TE
        "\u0425",  # Cyrillic capital HA
        "\u0406",  # Cyrillic capital I
    }
)


@dataclass(frozen=True)
class ResolvedEvidence:
    """Backend-resolved verbatim quotation with exact Unicode code-point and UTF-16 offsets."""

    quote: str
    start: int  # backend-computed, inclusive Unicode code-point offset in original source_text
    end: int  # backend-computed, exclusive Unicode code-point offset in original source_text
    context_excerpt: str | None
    offset_unit: OffsetUnit = OffsetUnit.UNICODE_CODE_POINT
    utf16_start: int = 0
    utf16_end: int = 0
    normalization_form_matched: Literal["EXACT", "NFC"] = "EXACT"


@dataclass(frozen=True)
class EvidenceResolutionOutcome:
    """Result of resolving an untrusted EvidenceQuote against the original source string."""

    status: ParseStatus
    resolved: ResolvedEvidence | None
    reason_code: ReasonCode | None
    message: str
    occurrence_count: int = 0


def code_point_to_utf16_offset(text: str, cp_offset: int) -> int:
    """Convert a Python Unicode code-point index in `text` to a UTF-16 code-unit offset (for JS/DOM)."""
    if cp_offset <= 0:
        return 0
    clamped = min(cp_offset, len(text))
    return len(text[:clamped].encode("utf-16-le")) // 2


def utf16_to_code_point_offset(text: str, utf16_offset: int) -> int:
    """Convert a UTF-16 code-unit offset back to a Python Unicode code-point index in `text`."""
    if utf16_offset <= 0:
        return 0
    u16_units = 0
    for idx, ch in enumerate(text):
        if u16_units >= utf16_offset:
            return idx
        u16_units += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


def is_valid_grapheme_boundary(text: str, offset: int) -> bool:
    """Return False if `offset` splits a base character from a trailing combining mark (`M*`)."""
    if offset < 0 or offset > len(text):
        return False
    return not (0 < offset < len(text) and unicodedata.category(text[offset]).startswith("M"))


def scan_unicode_threats(text: str) -> str | None:
    """Detect bidi controls, zero-width spoofing characters, combining-mark numeric spoofing, or mixed-script confusables."""
    for ch in text:
        if ch in _BIDI_CONTROL_CHARS:
            return f"Bidirectional control character U+{ord(ch):04X} detected."
        if ch in _ZERO_WIDTH_CHARS:
            return f"Zero-width / invisible character U+{ord(ch):04X} detected."

    # Check combining marks attached to digits, currency symbols, or punctuation separators
    for idx, ch in enumerate(text):
        if unicodedata.category(ch).startswith("M") and idx > 0:
            prev = text[idx - 1]
            if prev.isdigit() or prev in ".,/:-+€$£¥":
                return (
                    f"Combining mark U+{ord(ch):04X} attached to numeric/separator "
                    f"character {prev!r}."
                )

    # Check mixed-script confusable homoglyphs within tokens
    for token in re.findall(r"\S+", text):
        has_confusable = any(ch in _CONFUSABLE_HOMOGLYPHS for ch in token)
        has_latin_or_digit = any(
            ("A" <= ch <= "Z") or ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch in "čćđšžČĆĐŠŽ€$£"
            for ch in token
        )
        if has_confusable and has_latin_or_digit:
            return f"Mixed-script confusable homoglyph detected in token {token!r}."
        if has_confusable and len(token) <= 4:
            # Isolated Cyrillic lookalike used as currency code or unit (e.g. 'ЕUR' or 'мин')
            return f"Confusable homoglyph token {token!r} detected."

    return None


def find_all_exact_occurrences(haystack: str, needle: str) -> list[tuple[int, int]]:
    """Enumerate all exact (including overlapping) code-point spans of needle in haystack.

    Performs no case-folding, whitespace normalization, or Unicode normalization.
    """
    if not needle or not haystack:
        return []
    spans: list[tuple[int, int]] = []
    pos = 0
    needle_len = len(needle)
    while True:
        idx = haystack.find(needle, pos)
        if idx == -1:
            break
        spans.append((idx, idx + needle_len))
        pos = idx + 1  # step by 1 code point to catch overlapping occurrences
    return spans


def find_all_nfc_equivalent_occurrences(haystack: str, needle: str) -> list[tuple[int, int]]:
    """Find spans `(orig_start, orig_end)` in unmodified `haystack` whose NFC form equals `NFC(needle)`.

    Crucially, `orig_start` and `orig_end` are code-point offsets into `haystack` (not `NFC(haystack)`),
    and only spans aligned to grapheme cluster boundaries (`is_valid_grapheme_boundary`) are returned.
    """
    if not needle or not haystack:
        return []
    target_nfc = unicodedata.normalize("NFC", needle)
    if not target_nfc:
        return []

    # Partition haystack into grapheme clusters [c_start, c_end) so we never split combining marks
    clusters: list[tuple[int, int, str]] = []
    n = len(haystack)
    i = 0
    while i < n:
        start_i = i
        i += 1
        while i < n and unicodedata.category(haystack[i]).startswith("M"):
            i += 1
        raw_cluster = haystack[start_i:i]
        clusters.append((start_i, i, unicodedata.normalize("NFC", raw_cluster)))

    spans: list[tuple[int, int]] = []
    num_clusters = len(clusters)
    for c_idx in range(num_clusters):
        acc = ""
        orig_start = clusters[c_idx][0]
        for end_idx in range(c_idx, num_clusters):
            acc += clusters[end_idx][2]
            if len(acc) < len(target_nfc):
                continue
            orig_end = clusters[end_idx][1]
            if unicodedata.normalize("NFC", haystack[orig_start:orig_end]) == target_nfc:
                spans.append((orig_start, orig_end))
                break
            if len(acc) > len(target_nfc) + 4:
                break
    return spans


def _build_resolved_evidence(
    source_text: str,
    start: int,
    end: int,
    context_excerpt: str | None,
    norm_form: Literal["EXACT", "NFC"],
) -> ResolvedEvidence:
    return ResolvedEvidence(
        quote=source_text[start:end],
        start=start,
        end=end,
        context_excerpt=context_excerpt,
        offset_unit=OffsetUnit.UNICODE_CODE_POINT,
        utf16_start=code_point_to_utf16_offset(source_text, start),
        utf16_end=code_point_to_utf16_offset(source_text, end),
        normalization_form_matched=norm_form,
    )


def resolve_evidence_quote(
    source_text: str,
    evidence: EvidenceQuote,
    *,
    allow_nfc_fallback: bool = True,
) -> EvidenceResolutionOutcome:
    """Resolve an EvidenceQuote to a unique span in the unmodified source_text (§5.3)."""
    quote = evidence.quote
    context = evidence.context_excerpt

    # 1. Scan quote and source_text for hostile Unicode (bidi overrides, zero-width chars, confusables)
    quote_threat = scan_unicode_threats(quote)
    if quote_threat is not None:
        return EvidenceResolutionOutcome(
            status=ParseStatus.INVALID,
            resolved=None,
            reason_code=ReasonCode.EVIDENCE_SUSPICIOUS_UNICODE,
            message=f"Suspicious Unicode in evidence quote: {quote_threat}",
            occurrence_count=0,
        )
    source_threat = scan_unicode_threats(source_text)
    if source_threat is not None:
        return EvidenceResolutionOutcome(
            status=ParseStatus.INVALID,
            resolved=None,
            reason_code=ReasonCode.EVIDENCE_SUSPICIOUS_UNICODE,
            message=f"Suspicious Unicode in source text: {source_threat}",
            occurrence_count=0,
        )

    # 2. Search untouched source_text for exact occurrences first
    norm_form: Literal["EXACT", "NFC"] = "EXACT"
    raw_spans = find_all_exact_occurrences(source_text, quote)

    # Check if an exact match splits a combining character sequence in source_text
    valid_spans = [
        (s, e)
        for (s, e) in raw_spans
        if is_valid_grapheme_boundary(source_text, s) and is_valid_grapheme_boundary(source_text, e)
    ]
    if raw_spans and not valid_spans:
        return EvidenceResolutionOutcome(
            status=ParseStatus.INVALID,
            resolved=None,
            reason_code=ReasonCode.EVIDENCE_OFFSET_MAPPING_FAILURE,
            message=(
                f"Quotation {quote!r} splits a Unicode combining character sequence "
                "and is not aligned to grapheme boundaries."
            ),
            occurrence_count=0,
        )

    # 3. Optional NFC canonical-equivalence fallback (maps back to original source_text offsets!)
    if not valid_spans and allow_nfc_fallback:
        nfc_spans = find_all_nfc_equivalent_occurrences(source_text, quote)
        if nfc_spans:
            valid_spans = nfc_spans
            norm_form = "NFC"

    if not valid_spans:
        return EvidenceResolutionOutcome(
            status=ParseStatus.INVALID,
            resolved=None,
            reason_code=ReasonCode.EVIDENCE_NOT_FOUND,
            message=f"Exact quotation {quote!r} was not found verbatim in the source message.",
            occurrence_count=0,
        )

    if context is not None:
        context_spans = find_all_exact_occurrences(source_text, context)
        if not context_spans and allow_nfc_fallback:
            context_spans = find_all_nfc_equivalent_occurrences(source_text, context)
        if not context_spans:
            return EvidenceResolutionOutcome(
                status=ParseStatus.INVALID,
                resolved=None,
                reason_code=ReasonCode.EVIDENCE_CONTEXT_NOT_FOUND,
                message=f"Context excerpt {context!r} was not found verbatim in the source message.",
                occurrence_count=len(valid_spans),
            )
        if len(context_spans) > 1:
            return EvidenceResolutionOutcome(
                status=ParseStatus.AMBIGUOUS,
                resolved=None,
                reason_code=ReasonCode.EVIDENCE_CONTEXT_AMBIGUOUS,
                message=(
                    f"Context excerpt {context!r} occurs {len(context_spans)} times in the source "
                    "message and cannot uniquely disambiguate the quotation."
                ),
                occurrence_count=len(valid_spans),
            )

        ctx_start, ctx_end = context_spans[0]
        contained_spans = [(s, e) for (s, e) in valid_spans if s >= ctx_start and e <= ctx_end]
        if not contained_spans:
            return EvidenceResolutionOutcome(
                status=ParseStatus.INVALID,
                resolved=None,
                reason_code=ReasonCode.EVIDENCE_QUOTE_OUTSIDE_CONTEXT,
                message=(
                    f"Quotation {quote!r} does not lie wholly within the supplied "
                    f"context_excerpt {context!r}."
                ),
                occurrence_count=len(valid_spans),
            )
        if len(contained_spans) > 1:
            return EvidenceResolutionOutcome(
                status=ParseStatus.AMBIGUOUS,
                resolved=None,
                reason_code=ReasonCode.EVIDENCE_AMBIGUOUS,
                message=(
                    f"Quotation {quote!r} occurs {len(contained_spans)} times inside the supplied "
                    "context_excerpt and remains ambiguous."
                ),
                occurrence_count=len(contained_spans),
            )

        abs_start, abs_end = contained_spans[0]
        if not (
            is_valid_grapheme_boundary(source_text, abs_start)
            and is_valid_grapheme_boundary(source_text, abs_end)
        ):
            return EvidenceResolutionOutcome(
                status=ParseStatus.INVALID,
                resolved=None,
                reason_code=ReasonCode.EVIDENCE_OFFSET_MAPPING_FAILURE,
                message="Resolved offsets split a Unicode grapheme cluster.",
                occurrence_count=1,
            )
        if norm_form == "EXACT" and source_text[abs_start:abs_end] != quote:
            return EvidenceResolutionOutcome(
                status=ParseStatus.INVALID,
                resolved=None,
                reason_code=ReasonCode.EVIDENCE_MISMATCH,
                message="Resolved offsets failed slice equality check against original source.",
                occurrence_count=len(valid_spans),
            )
        if norm_form == "NFC" and unicodedata.normalize(
            "NFC", source_text[abs_start:abs_end]
        ) != unicodedata.normalize("NFC", quote):
            return EvidenceResolutionOutcome(
                status=ParseStatus.INVALID,
                resolved=None,
                reason_code=ReasonCode.EVIDENCE_NORMALIZATION_MISMATCH,
                message="Resolved offsets failed NFC slice equality check against original source.",
                occurrence_count=len(valid_spans),
            )
        return EvidenceResolutionOutcome(
            status=ParseStatus.PARSED,
            resolved=_build_resolved_evidence(source_text, abs_start, abs_end, context, norm_form),
            reason_code=None,
            message="Resolved uniquely via context excerpt.",
            occurrence_count=1,
        )

    # No context_excerpt provided
    if len(valid_spans) > 1:
        return EvidenceResolutionOutcome(
            status=ParseStatus.AMBIGUOUS,
            resolved=None,
            reason_code=ReasonCode.EVIDENCE_AMBIGUOUS,
            message=(
                f"Quotation {quote!r} occurs {len(valid_spans)} times in the source message; "
                "a unique context_excerpt is required to disambiguate."
            ),
            occurrence_count=len(valid_spans),
        )

    start, end = valid_spans[0]
    if norm_form == "EXACT" and source_text[start:end] != quote:
        return EvidenceResolutionOutcome(
            status=ParseStatus.INVALID,
            resolved=None,
            reason_code=ReasonCode.EVIDENCE_MISMATCH,
            message="Resolved offsets failed slice equality check against original source.",
            occurrence_count=1,
        )
    if norm_form == "NFC" and unicodedata.normalize(
        "NFC", source_text[start:end]
    ) != unicodedata.normalize("NFC", quote):
        return EvidenceResolutionOutcome(
            status=ParseStatus.INVALID,
            resolved=None,
            reason_code=ReasonCode.EVIDENCE_NORMALIZATION_MISMATCH,
            message="Resolved offsets failed NFC slice equality check against original source.",
            occurrence_count=1,
        )
    return EvidenceResolutionOutcome(
        status=ParseStatus.PARSED,
        resolved=_build_resolved_evidence(source_text, start, end, None, norm_form),
        reason_code=None,
        message="Resolved uniquely in source text.",
        occurrence_count=1,
    )


def spans_overlap(span_a: tuple[int, int], span_b: tuple[int, int]) -> bool:
    """Return True if two half-open intervals [start, end) overlap."""
    return max(span_a[0], span_b[0]) < min(span_a[1], span_b[1])


def check_disjoint_field_spans(
    resolved_fields: dict[str, ResolvedEvidence],
    allowed_overlap_pairs: set[frozenset[str]] | None = None,
) -> tuple[bool, tuple[str, str] | None]:
    """Verify that mutually exclusive semantic fields do not overlap in source offsets.

    For example, `amount` and `currency` may overlap (`"1.234,56 €"`), whereas
    `start_datetime` and `end_datetime` or `amount` and `expense_date` must never overlap.
    """
    allowed = allowed_overlap_pairs or {frozenset({"amount", "currency"})}
    items = list(resolved_fields.items())
    for i in range(len(items)):
        field_a, ev_a = items[i]
        for j in range(i + 1, len(items)):
            field_b, ev_b = items[j]
            if frozenset({field_a, field_b}) in allowed:
                continue
            if spans_overlap((ev_a.start, ev_a.end), (ev_b.start, ev_b.end)):
                return False, (field_a, field_b)
    return True, None
