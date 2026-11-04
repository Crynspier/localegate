"""Pydantic v2 public contracts for requests, untrusted proposals, and gate results."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    TypeAdapter,
    field_validator,
    model_validator,
)

from locale_gate.contracts.enums import (
    CONTRACT_VERSION,
    BusinessApprovalStatus,
    ConfirmationStatus,
    GateDecisionStatus,
    OffsetUnit,
    ParseStatus,
    ProposalDecision,
    ReasonCode,
    RoleBindingStatus,
)


class DuplicateJSONKeyError(ValueError):
    """Raised when an untrusted JSON payload contains duplicate keys in any object (§5.1)."""

    def __init__(self, key: str) -> None:
        super().__init__(f"Duplicate JSON key {key!r} is forbidden at the trust boundary.")
        self.key = key


class UntrustedJSONBoundsError(ValueError):
    """Raised when an untrusted JSON payload exceeds byte size, depth, or key count limits (§5.1)."""


def _check_json_depth_and_size(obj: Any, current_depth: int, max_depth: int) -> int:
    if current_depth > max_depth:
        raise UntrustedJSONBoundsError(
            f"JSON nesting depth {current_depth} exceeds maximum allowed depth {max_depth}."
        )
    total_items = 0
    if isinstance(obj, dict):
        total_items += len(obj)
        for v in obj.values():
            total_items += _check_json_depth_and_size(v, current_depth + 1, max_depth)
    elif isinstance(obj, list):
        total_items += len(obj)
        for item in obj:
            total_items += _check_json_depth_and_size(item, current_depth + 1, max_depth)
    return total_items


def parse_strict_untrusted_json(
    raw_json: str,
    *,
    max_bytes: int = 32768,
    max_depth: int = 8,
    max_total_items: int = 128,
) -> Any:
    """Parse untrusted JSON string rejecting duplicate keys, oversized payloads, and deep nesting."""
    raw_bytes = len(raw_json.encode("utf-8", errors="replace"))
    if raw_bytes > max_bytes:
        raise UntrustedJSONBoundsError(
            f"Untrusted JSON payload size ({raw_bytes} bytes) exceeds limit ({max_bytes} bytes)."
        )

    def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in pairs:
            if k in out:
                raise DuplicateJSONKeyError(k)
            out[k] = v
        return out

    parsed = json.loads(raw_json, object_pairs_hook=_reject_duplicate_keys)
    total_items = _check_json_depth_and_size(parsed, 1, max_depth)
    if total_items > max_total_items:
        raise UntrustedJSONBoundsError(
            f"Untrusted JSON item count ({total_items}) exceeds limit ({max_total_items})."
        )
    return parsed


class TrustedRequestContext(BaseModel):
    """Trusted context supplied by the application or benchmark harness.

    The model cannot overwrite any field in this contract.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=8192)
    locale: str | None = Field(default="hr-HR", max_length=32)
    timezone: str | None = Field(default="Europe/Zagreb", max_length=64)
    reference_datetime: datetime | None = Field(default=None)
    allowed_tools: tuple[str, ...] = Field(
        default=("submit_expense", "schedule_event"),
    )
    policy_profile: str = Field(default="research_simulation_v1", max_length=64)
    default_currency: str | None = Field(default=None, max_length=8)
    require_confirmation: bool = Field(default=True)
    dry_run: bool = Field(default=False)

    @field_validator("reference_datetime")
    @classmethod
    def validate_tz_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and (v.tzinfo is None or v.tzinfo.utcoffset(v) is None):
            raise ValueError("reference_datetime must be timezone-aware")
        return v

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown IANA timezone: {v}") from exc
        return v

    def integrity_snapshot(self) -> tuple[Any, ...]:
        """Return an immutable snapshot tuple to assert zero mutation across the pipeline."""
        return (
            self.request_id,
            self.text,
            self.locale,
            self.timezone,
            self.reference_datetime.isoformat() if self.reference_datetime else None,
            self.allowed_tools,
            self.policy_profile,
            self.default_currency,
            self.require_confirmation,
            self.dry_run,
        )


class EvidenceQuote(BaseModel):
    """Model-proposed verbatim quotation, optional context excerpt, and optional role cue."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    quote: StrictStr = Field(min_length=1, max_length=160)
    context_excerpt: StrictStr | None = Field(default=None, max_length=320)
    role_evidence: StrictStr | None = Field(default=None, max_length=240)


class FieldEvidenceProposal(BaseModel):
    """Untrusted proposal for a single field backed by an exact source quotation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence: EvidenceQuote


class ActionProposal(BaseModel):
    """Untrusted proposal normalized from model output (tool call or structured JSON).

    Strictly forbids canonical values, character offsets, approval overrides, or extra keys.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: StrictStr = Field(default=CONTRACT_VERSION)
    decision: ProposalDecision
    tool_name: StrictStr | None = Field(default=None, max_length=64)
    fields: dict[StrictStr, FieldEvidenceProposal] = Field(default_factory=dict)
    clarification_question: StrictStr | None = Field(default=None, max_length=512)

    @field_validator("decision", mode="before")
    @classmethod
    def validate_decision_is_str(cls, v: Any) -> Any:
        if not isinstance(v, (str, ProposalDecision)):
            raise ValueError("decision must be a string")
        return v

    @field_validator("fields")
    @classmethod
    def validate_fields_count(
        cls, v: dict[StrictStr, FieldEvidenceProposal]
    ) -> dict[StrictStr, FieldEvidenceProposal]:
        if len(v) > 16:
            raise ValueError(f"Proposal contains {len(v)} fields, exceeding maximum allowed (16).")
        return v

    @model_validator(mode="after")
    def validate_decision_consistency(self) -> ActionProposal:
        if self.contract_version != CONTRACT_VERSION:
            raise ValueError(f"Unsupported contract_version: {self.contract_version}")
        if self.decision == ProposalDecision.PROPOSE and not self.tool_name:
            raise ValueError("tool_name is required when decision is 'propose'")
        if self.decision == ProposalDecision.NO_ACTION and self.tool_name is not None:
            raise ValueError("tool_name must be null when decision is 'no_action'")
        return self


class FieldDiagnostic(BaseModel):
    """Detailed verification, offset-mapping, role-binding, and canonicalization trace for a field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field_name: str
    parse_status: ParseStatus
    reason_code: ReasonCode | None = None
    message: str
    raw_quote: str | None = None
    context_excerpt: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    offset_unit: OffsetUnit = OffsetUnit.UNICODE_CODE_POINT
    utf16_start_offset: int | None = None
    utf16_end_offset: int | None = None
    normalization_form_matched: Literal["EXACT", "NFC"] | None = None
    role_binding_status: RoleBindingStatus = RoleBindingStatus.NOT_APPLICABLE
    candidate_count: int = 0
    competing_candidates: tuple[str, ...] = ()
    canonical_value: Any = None
    rule_id: str | None = None
    parser_version: str | None = None


class SimulatedToolEffect(BaseModel):
    """Record of a simulated state change created after user confirmation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str
    tool_name: str
    canonical_arguments: dict[str, Any]
    confirmation_status: ConfirmationStatus
    business_approval_status: BusinessApprovalStatus
    action_digest: str
    created_at_utc: str
    idempotent_replay: bool = False


class BaseGateResult(BaseModel):
    """Shared fields across all discriminated union gate results."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    proposal_id: str
    audit_correlation_id: str
    reason_code: ReasonCode
    message: str
    tool_name: str | None = None
    canonical_arguments: dict[str, Any] = Field(default_factory=dict)
    field_diagnostics: list[FieldDiagnostic] = Field(default_factory=list)
    confirmation_status: ConfirmationStatus = ConfirmationStatus.NOT_APPLICABLE
    business_approval_status: BusinessApprovalStatus = BusinessApprovalStatus.NOT_APPLICABLE
    action_digest: str | None = None
    confirmation_token: str | None = None
    clarification_question: str | None = None
    simulated_effect: SimulatedToolEffect | None = None
    untrusted_proposal: dict[str, Any] | None = None


class ExecutedGateResult(BaseGateResult):
    """Gate result when a valid, explicitly confirmed simulated tool completed."""

    status: Literal[GateDecisionStatus.EXECUTED] = GateDecisionStatus.EXECUTED


class AwaitingConfirmationGateResult(BaseGateResult):
    """Gate result when a proposal is valid and awaits single-use user confirmation."""

    status: Literal[GateDecisionStatus.AWAITING_CONFIRMATION] = (
        GateDecisionStatus.AWAITING_CONFIRMATION
    )
    confirmation_status: ConfirmationStatus = ConfirmationStatus.PENDING


class NeedsClarificationGateResult(BaseGateResult):
    """Gate result when ambiguity or missing information requires user clarification."""

    status: Literal[GateDecisionStatus.NEEDS_CLARIFICATION] = GateDecisionStatus.NEEDS_CLARIFICATION


class BlockedGateResult(BaseGateResult):
    """Gate result when evidence, parsing, schema, or policy verification fails closed."""

    status: Literal[GateDecisionStatus.BLOCKED] = GateDecisionStatus.BLOCKED


class NoActionGateResult(BaseGateResult):
    """Gate result when no tool action was requested."""

    status: Literal[GateDecisionStatus.NO_ACTION] = GateDecisionStatus.NO_ACTION


class ModelErrorGateResult(BaseGateResult):
    """Gate result when the model adapter failed or emitted an unparseable response."""

    status: Literal[GateDecisionStatus.MODEL_ERROR] = GateDecisionStatus.MODEL_ERROR


GateResult = Annotated[
    ExecutedGateResult
    | AwaitingConfirmationGateResult
    | NeedsClarificationGateResult
    | BlockedGateResult
    | NoActionGateResult
    | ModelErrorGateResult,
    Field(discriminator="status"),
]

GateResultAdapter: TypeAdapter[GateResult] = TypeAdapter(GateResult)  # type: ignore[arg-type]


def action_proposal_json_schema() -> dict[str, Any]:
    """Return strict JSON Schema for structured model proposal extraction."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "contract_version",
            "decision",
            "tool_name",
            "fields",
            "clarification_question",
        ],
        "properties": {
            "contract_version": {"type": "string", "enum": [CONTRACT_VERSION]},
            "decision": {
                "type": "string",
                "enum": [d.value for d in ProposalDecision],
            },
            "tool_name": {
                "anyOf": [
                    {"type": "string", "enum": ["submit_expense", "schedule_event"]},
                    {"type": "null"},
                ]
            },
            "fields": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["evidence"],
                    "properties": {
                        "evidence": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["quote"],
                            "properties": {
                                "quote": {"type": "string", "minLength": 1, "maxLength": 160},
                                "context_excerpt": {
                                    "anyOf": [
                                        {"type": "string", "maxLength": 320},
                                        {"type": "null"},
                                    ]
                                },
                            },
                        }
                    },
                },
            },
            "clarification_question": {
                "anyOf": [
                    {"type": "string", "maxLength": 512},
                    {"type": "null"},
                ]
            },
        },
    }
