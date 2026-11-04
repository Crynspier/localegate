"""Typed result container for all deterministic locale parsers."""

from __future__ import annotations

from dataclasses import dataclass, field

from locale_gate.canonical.types import PARSER_VERSION
from locale_gate.contracts.enums import ParseStatus, ReasonCode


@dataclass(frozen=True)
class ParserResult[T]:
    """Outcome of a deterministic parser call.

    Never returns None + a guess. Always carries a ParseStatus and stable ReasonCode on failure.
    """

    status: ParseStatus
    value: T | None = None
    reason_code: ReasonCode | None = None
    message: str = ""
    rule_id: str | None = None
    candidates: tuple[str, ...] = field(default_factory=tuple)
    parser_version: str = PARSER_VERSION

    @property
    def is_ok(self) -> bool:
        return self.status == ParseStatus.PARSED and self.value is not None
