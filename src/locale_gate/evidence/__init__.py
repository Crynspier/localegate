"""Evidence resolution package."""

from locale_gate.evidence.resolver import (
    EvidenceResolutionOutcome,
    ResolvedEvidence,
    check_disjoint_field_spans,
    find_all_exact_occurrences,
    resolve_evidence_quote,
    spans_overlap,
)

__all__ = [
    "EvidenceResolutionOutcome",
    "ResolvedEvidence",
    "check_disjoint_field_spans",
    "find_all_exact_occurrences",
    "resolve_evidence_quote",
    "spans_overlap",
]
