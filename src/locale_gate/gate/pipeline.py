"""Deterministic 6-stage validation and execution gate pipeline."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from locale_gate.canonical.types import PARSER_VERSION
from locale_gate.contracts.enums import (
    BusinessApprovalStatus,
    ConfirmationStatus,
    GateDecisionStatus,
    ParseStatus,
    ProposalDecision,
    ReasonCode,
    RoleBindingStatus,
    ToolName,
)
from locale_gate.contracts.models import (
    ActionProposal,
    AwaitingConfirmationGateResult,
    BlockedGateResult,
    DuplicateJSONKeyError,
    ExecutedGateResult,
    FieldDiagnostic,
    GateResult,
    ModelErrorGateResult,
    NeedsClarificationGateResult,
    NoActionGateResult,
    TrustedRequestContext,
    UntrustedJSONBoundsError,
    parse_strict_untrusted_json,
)
from locale_gate.evidence.resolver import (
    ResolvedEvidence,
    check_disjoint_field_spans,
    resolve_evidence_quote,
)
from locale_gate.evidence.role_binding import (
    verify_amount_role_binding,
    verify_temporal_role_binding,
)
from locale_gate.locales.profiles import get_locale_profile
from locale_gate.parsers.date_time import parse_date, parse_datetime, parse_duration
from locale_gate.parsers.money import parse_money
from locale_gate.policy.rules import (
    build_clarification_prompt,
    classify_failure_status,
    get_policy_profile,
)
from locale_gate.tools.registry import (
    ScheduleEventCanonicalArgs,
    SubmitExpenseCanonicalArgs,
    compute_canonical_action_digest,
    generate_confirmation_capability_token,
    get_tool_manifest,
    parse_expense_category,
    sanitize_display_text,
)
from locale_gate.tools.simulator import SimulatedStore

_FORBIDDEN_BYPASS_KEYS = frozenset(
    {
        "canonical_value",
        "canonical_arguments",
        "start",
        "end",
        "start_offset",
        "end_offset",
        "offsets",
        "execute",
        "approved",
        "confirmation_status",
        "business_approval_status",
        "locale",
        "timezone",
        "reference_datetime",
        "policy_profile",
    }
)


def _contains_forbidden_bypass_key(obj: Any) -> str | None:
    """Recursively check untrusted dict for model attempts to inject canonical values/offsets/policy."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _FORBIDDEN_BYPASS_KEYS:
                return str(k)
            nested = _contains_forbidden_bypass_key(v)
            if nested is not None:
                return nested
    elif isinstance(obj, list):
        for item in obj:
            nested = _contains_forbidden_bypass_key(item)
            if nested is not None:
                return nested
    return None


class LocaleGatePipeline:
    """Authoritative deterministic validation boundary for untrusted LLM tool proposals."""

    def __init__(
        self,
        store: SimulatedStore | None = None,
        *,
        enable_role_binding: bool = True,
    ) -> None:
        self.store = store
        self.enable_role_binding = enable_role_binding

    def evaluate_raw_json(
        self,
        context: TrustedRequestContext,
        raw_payload: str | dict[str, Any],
    ) -> GateResult:
        """Validate raw JSON string or dict from a model adapter and run the full gate."""
        proposal_id = f"prop_{uuid.uuid4().hex[:16]}"
        audit_id = f"corr_{uuid.uuid4().hex[:16]}"
        pre_snapshot = context.integrity_snapshot()

        if isinstance(raw_payload, str):
            try:
                parsed_dict = parse_strict_untrusted_json(raw_payload)
            except DuplicateJSONKeyError as exc:
                res: GateResult = BlockedGateResult(
                    request_id=context.request_id,
                    proposal_id=proposal_id,
                    audit_correlation_id=audit_id,
                    reason_code=ReasonCode.PROPOSAL_DUPLICATE_JSON_KEY,
                    message=str(exc),
                )
                self._persist_if_configured(context, res)
                return res
            except UntrustedJSONBoundsError as exc:
                res = BlockedGateResult(
                    request_id=context.request_id,
                    proposal_id=proposal_id,
                    audit_correlation_id=audit_id,
                    reason_code=ReasonCode.PROPOSAL_PAYLOAD_TOO_LARGE,
                    message=str(exc),
                )
                self._persist_if_configured(context, res)
                return res
            except json.JSONDecodeError as exc:
                res = ModelErrorGateResult(
                    request_id=context.request_id,
                    proposal_id=proposal_id,
                    audit_correlation_id=audit_id,
                    reason_code=ReasonCode.MODEL_INVALID_JSON,
                    message=f"Model output is not valid JSON: {exc.msg}.",
                )
                self._persist_if_configured(context, res)
                return res
        else:
            parsed_dict = raw_payload

        if not isinstance(parsed_dict, dict):
            res = BlockedGateResult(
                request_id=context.request_id,
                proposal_id=proposal_id,
                audit_correlation_id=audit_id,
                reason_code=ReasonCode.PROPOSAL_SCHEMA_INVALID,
                message="Proposal root must be a JSON object.",
            )
            self._persist_if_configured(context, res)
            return res

        # Stage 1a: Check for canonical value / offset / policy bypass attempts
        bypass_key = _contains_forbidden_bypass_key(parsed_dict)
        if bypass_key is not None:
            res = BlockedGateResult(
                request_id=context.request_id,
                proposal_id=proposal_id,
                audit_correlation_id=audit_id,
                reason_code=ReasonCode.PROPOSAL_CANONICAL_BYPASS_ATTEMPT,
                message=f"Model proposal attempted to supply forbidden authoritative key {bypass_key!r}.",
                untrusted_proposal=parsed_dict,
            )
            self._persist_if_configured(context, res)
            return res

        # Stage 1b: Validate against strict ActionProposal contract
        try:
            proposal = ActionProposal.model_validate(parsed_dict)
        except ValidationError as exc:
            res = BlockedGateResult(
                request_id=context.request_id,
                proposal_id=proposal_id,
                audit_correlation_id=audit_id,
                reason_code=ReasonCode.PROPOSAL_SCHEMA_INVALID,
                message=f"Proposal failed contract schema validation: {exc.errors()[0].get('msg', 'invalid')}",
                untrusted_proposal=parsed_dict,
            )
            self._persist_if_configured(context, res)
            return res

        out = self.evaluate_proposal(
            context=context,
            proposal=proposal,
            proposal_id=proposal_id,
            audit_id=audit_id,
        )
        if context.integrity_snapshot() != pre_snapshot:
            raise RuntimeError("TrustedRequestContext integrity violation detected.")
        return out

    def evaluate_proposal(
        self,
        context: TrustedRequestContext,
        proposal: ActionProposal,
        *,
        proposal_id: str | None = None,
        audit_id: str | None = None,
    ) -> GateResult:
        """Run Stages 1-6 on a validated ActionProposal."""
        prop_id = proposal_id or f"prop_{uuid.uuid4().hex[:16]}"
        corr_id = audit_id or f"corr_{uuid.uuid4().hex[:16]}"
        pre_snapshot = context.integrity_snapshot()
        untrusted_dump = proposal.model_dump()
        policy = get_policy_profile(context.policy_profile)

        # Check unsupported locale upfront if a non-null locale tag is configured
        if context.locale is not None and get_locale_profile(context.locale) is None:
            res: GateResult = BlockedGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.LOCALE_UNSUPPORTED,
                message=f"Configured locale {context.locale!r} is not supported.",
                untrusted_proposal=untrusted_dump,
            )
            self._persist_if_configured(context, res)
            return res

        # Handle NO_ACTION
        if proposal.decision == ProposalDecision.NO_ACTION:
            res = NoActionGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.OK_NO_ACTION,
                message="No tool execution requested.",
                untrusted_proposal=untrusted_dump,
            )
            self._persist_if_configured(context, res)
            return res

        # Handle CLARIFY
        if proposal.decision == ProposalDecision.CLARIFY:
            q = proposal.clarification_question or build_clarification_prompt(
                ReasonCode.CLARIFY_MODEL_REQUESTED, context.locale
            )
            res = NeedsClarificationGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.CLARIFY_MODEL_REQUESTED,
                message=q,
                tool_name=proposal.tool_name,
                clarification_question=q,
                untrusted_proposal=untrusted_dump,
            )
            self._persist_if_configured(context, res)
            return res

        # Decision is PROPOSE: check tool registry and allowlist
        tool_name = proposal.tool_name
        manifest = get_tool_manifest(tool_name)
        if manifest is None or tool_name is None:
            res = BlockedGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.TOOL_UNKNOWN,
                message=f"Proposed tool {tool_name!r} is not in the tool registry.",
                tool_name=tool_name,
                untrusted_proposal=untrusted_dump,
            )
            self._persist_if_configured(context, res)
            return res

        if tool_name not in context.allowed_tools:
            res = BlockedGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.TOOL_NOT_ALLOWED,
                message=f"Tool {tool_name!r} is not permitted by the trusted request context.",
                tool_name=tool_name,
                untrusted_proposal=untrusted_dump,
            )
            self._persist_if_configured(context, res)
            return res

        # Stage 2 & 3: Field allowlist, required fields, and exact-quote resolution
        for field_key in proposal.fields:
            if field_key not in manifest.all_allowed_fields:
                res = BlockedGateResult(
                    request_id=context.request_id,
                    proposal_id=prop_id,
                    audit_correlation_id=corr_id,
                    reason_code=ReasonCode.FIELD_UNKNOWN_FORBIDDEN,
                    message=f"Unknown or forbidden field {field_key!r} for tool {tool_name!r}.",
                    tool_name=tool_name,
                    untrusted_proposal=untrusted_dump,
                )
                self._persist_if_configured(context, res)
                return res

        required_fields = list(manifest.required_fields)
        if (
            manifest.name == ToolName.SUBMIT_EXPENSE
            and policy.require_expense_description
            and "description" not in required_fields
        ):
            required_fields.append("description")

        diagnostics: list[FieldDiagnostic] = []
        for req_f in required_fields:
            if req_f not in proposal.fields:
                diag = FieldDiagnostic(
                    field_name=req_f,
                    parse_status=ParseStatus.MISSING,
                    reason_code=ReasonCode.FIELD_REQUIRED_MISSING,
                    message=f"Required field {req_f!r} is missing from proposal.",
                )
                diagnostics.append(diag)
                q = build_clarification_prompt(
                    ReasonCode.FIELD_REQUIRED_MISSING, context.locale, req_f
                )
                res = NeedsClarificationGateResult(
                    request_id=context.request_id,
                    proposal_id=prop_id,
                    audit_correlation_id=corr_id,
                    reason_code=ReasonCode.FIELD_REQUIRED_MISSING,
                    message=diag.message,
                    tool_name=tool_name,
                    field_diagnostics=diagnostics,
                    clarification_question=q,
                    untrusted_proposal=untrusted_dump,
                )
                self._persist_if_configured(context, res)
                return res

        if (
            manifest.name == ToolName.SCHEDULE_EVENT
            and "end_datetime" not in proposal.fields
            and "duration" not in proposal.fields
        ):
            diag = FieldDiagnostic(
                field_name="end_datetime",
                parse_status=ParseStatus.MISSING,
                reason_code=ReasonCode.EVENT_END_OR_DURATION_MISSING,
                message="Either 'end_datetime' or 'duration' must be provided for schedule_event.",
            )
            diagnostics.append(diag)
            q = build_clarification_prompt(
                ReasonCode.EVENT_END_OR_DURATION_MISSING, context.locale, "end_datetime"
            )
            res = NeedsClarificationGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.EVENT_END_OR_DURATION_MISSING,
                message=diag.message,
                tool_name=tool_name,
                field_diagnostics=diagnostics,
                clarification_question=q,
                untrusted_proposal=untrusted_dump,
            )
            self._persist_if_configured(context, res)
            return res

        resolved_spans: dict[str, ResolvedEvidence] = {}
        for field_name, field_prop in proposal.fields.items():
            ev_outcome = resolve_evidence_quote(context.text, field_prop.evidence)
            if ev_outcome.status != ParseStatus.PARSED or ev_outcome.resolved is None:
                rc = ev_outcome.reason_code or ReasonCode.EVIDENCE_NOT_FOUND
                diag = FieldDiagnostic(
                    field_name=field_name,
                    parse_status=ev_outcome.status,
                    reason_code=rc,
                    message=ev_outcome.message,
                    raw_quote=field_prop.evidence.quote,
                    context_excerpt=field_prop.evidence.context_excerpt,
                )
                diagnostics.append(diag)
                res = self._build_failure_result(
                    context=context,
                    proposal_id=prop_id,
                    audit_id=corr_id,
                    tool_name=tool_name,
                    parse_status=ev_outcome.status,
                    reason_code=rc,
                    message=ev_outcome.message,
                    field_name=field_name,
                    diagnostics=diagnostics,
                    untrusted_proposal=untrusted_dump,
                )
                self._persist_if_configured(context, res)
                return res
            resolved_spans[field_name] = ev_outcome.resolved

        # Check disjoint field spans (e.g. start_datetime and end_datetime cannot share a span)
        disjoint_ok, conflict_pair = check_disjoint_field_spans(resolved_spans)
        if not disjoint_ok and conflict_pair is not None:
            fa, fb = conflict_pair
            msg = f"Fields {fa!r} and {fb!r} resolve to overlapping source spans."
            ev_b = resolved_spans[fb]
            diag = FieldDiagnostic(
                field_name=fb,
                parse_status=ParseStatus.INVALID,
                reason_code=ReasonCode.EVIDENCE_OVERLAP_CONFLICT,
                message=msg,
                raw_quote=ev_b.quote,
                start_offset=ev_b.start,
                end_offset=ev_b.end,
                utf16_start_offset=ev_b.utf16_start,
                utf16_end_offset=ev_b.utf16_end,
                normalization_form_matched=ev_b.normalization_form_matched,
            )
            diagnostics.append(diag)
            res = BlockedGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.EVIDENCE_OVERLAP_CONFLICT,
                message=msg,
                tool_name=tool_name,
                field_diagnostics=diagnostics,
                untrusted_proposal=untrusted_dump,
            )
            self._persist_if_configured(context, res)
            return res

        # Stage 4 & 5: Deterministic canonicalization and tool domain validation
        if manifest.name == ToolName.SUBMIT_EXPENSE:
            res = self._canonicalize_submit_expense(
                context=context,
                policy=policy,
                proposal_id=prop_id,
                audit_id=corr_id,
                resolved_spans=resolved_spans,
                diagnostics=diagnostics,
                untrusted_proposal=untrusted_dump,
            )
        else:
            res = self._canonicalize_schedule_event(
                context=context,
                policy=policy,
                proposal_id=prop_id,
                audit_id=corr_id,
                resolved_spans=resolved_spans,
                diagnostics=diagnostics,
                untrusted_proposal=untrusted_dump,
            )

        if context.integrity_snapshot() != pre_snapshot:
            raise RuntimeError("TrustedRequestContext integrity violation detected.")
        self._persist_if_configured(context, res)
        return res

    def _canonicalize_submit_expense(
        self,
        *,
        context: TrustedRequestContext,
        policy: Any,
        proposal_id: str,
        audit_id: str,
        resolved_spans: dict[str, ResolvedEvidence],
        diagnostics: list[FieldDiagnostic],
        untrusted_proposal: dict[str, Any],
    ) -> GateResult:
        tool_name = ToolName.SUBMIT_EXPENSE.value
        amount_ev = resolved_spans["amount"]
        currency_ev = resolved_spans.get("currency")
        date_ev = resolved_spans["expense_date"]

        # Pre-parse expense_date to pass transaction date into money parser if available
        date_res = parse_date(
            date_ev.quote,
            context.locale,
            reference_datetime=context.reference_datetime,
            timezone_name=context.timezone,
            allow_relative_weekday=policy.allow_relative_weekday,
        )
        tx_date = date_res.value.value if date_res.is_ok and date_res.value is not None else None

        money_res = parse_money(
            amount_ev.quote,
            context.locale,
            explicit_currency_quote=currency_ev.quote if currency_ev else None,
            trusted_default_currency=context.default_currency,
            require_currency=True,
            transaction_date=tx_date,
            allow_historical_currencies=policy.allow_historical_currencies,
        )
        if not money_res.is_ok or money_res.value is None:
            rc = money_res.reason_code or ReasonCode.MONEY_INVALID_FORMAT
            diagnostics.append(
                FieldDiagnostic(
                    field_name="amount",
                    parse_status=money_res.status,
                    reason_code=rc,
                    message=money_res.message,
                    raw_quote=amount_ev.quote,
                    context_excerpt=amount_ev.context_excerpt,
                    start_offset=amount_ev.start,
                    end_offset=amount_ev.end,
                    utf16_start_offset=amount_ev.utf16_start,
                    utf16_end_offset=amount_ev.utf16_end,
                    normalization_form_matched=amount_ev.normalization_form_matched,
                )
            )
            return self._build_failure_result(
                context=context,
                proposal_id=proposal_id,
                audit_id=audit_id,
                tool_name=tool_name,
                parse_status=money_res.status,
                reason_code=rc,
                message=money_res.message,
                field_name="amount",
                diagnostics=diagnostics,
                untrusted_proposal=untrusted_proposal,
            )

        money_val = money_res.value
        if money_val.amount <= Decimal("0"):
            rc = ReasonCode.MONEY_NON_POSITIVE
            msg = f"Expense amount must be strictly positive, got {money_val.amount_str}."
            diagnostics.append(
                FieldDiagnostic(
                    field_name="amount",
                    parse_status=ParseStatus.INVALID,
                    reason_code=rc,
                    message=msg,
                    raw_quote=amount_ev.quote,
                    start_offset=amount_ev.start,
                    end_offset=amount_ev.end,
                    utf16_start_offset=amount_ev.utf16_start,
                    utf16_end_offset=amount_ev.utf16_end,
                    normalization_form_matched=amount_ev.normalization_form_matched,
                    canonical_value=money_val.amount_str,
                    rule_id=money_val.rule_id,
                    parser_version=money_val.parser_version,
                )
            )
            return BlockedGateResult(
                request_id=context.request_id,
                proposal_id=proposal_id,
                audit_correlation_id=audit_id,
                reason_code=rc,
                message=msg,
                tool_name=tool_name,
                field_diagnostics=diagnostics,
                untrusted_proposal=untrusted_proposal,
            )

        if money_val.amount > policy.max_expense_amount:
            rc = ReasonCode.MONEY_EXCEEDS_MAX_LIMIT
            msg = f"Expense amount {money_val.amount_str} exceeds policy maximum {policy.max_expense_amount}."
            diagnostics.append(
                FieldDiagnostic(
                    field_name="amount",
                    parse_status=ParseStatus.INVALID,
                    reason_code=rc,
                    message=msg,
                    raw_quote=amount_ev.quote,
                    start_offset=amount_ev.start,
                    end_offset=amount_ev.end,
                    utf16_start_offset=amount_ev.utf16_start,
                    utf16_end_offset=amount_ev.utf16_end,
                    normalization_form_matched=amount_ev.normalization_form_matched,
                    canonical_value=money_val.amount_str,
                    rule_id=money_val.rule_id,
                    parser_version=money_val.parser_version,
                )
            )
            return BlockedGateResult(
                request_id=context.request_id,
                proposal_id=proposal_id,
                audit_correlation_id=audit_id,
                reason_code=rc,
                message=msg,
                tool_name=tool_name,
                field_diagnostics=diagnostics,
                untrusted_proposal=untrusted_proposal,
            )

        # Stage 3b: Deterministic semantic role-binding verification on amount
        rb_status = RoleBindingStatus.NOT_APPLICABLE
        rb_cand_count = 1
        rb_competing: tuple[str, ...] = ()
        if self.enable_role_binding and policy.enforce_role_binding:
            rb_outcome = verify_amount_role_binding(context.text, amount_ev)
            rb_status = rb_outcome.role_status
            rb_cand_count = rb_outcome.candidate_count
            rb_competing = tuple(rb_outcome.competing_candidates)
            if rb_outcome.role_status != RoleBindingStatus.VERIFIED:
                rc = rb_outcome.reason_code or ReasonCode.FIELD_ROLE_MISMATCH
                p_status = rb_outcome.parse_status
                diagnostics.append(
                    FieldDiagnostic(
                        field_name="amount",
                        parse_status=p_status,
                        reason_code=rc,
                        message=rb_outcome.message,
                        raw_quote=amount_ev.quote,
                        context_excerpt=amount_ev.context_excerpt,
                        start_offset=amount_ev.start,
                        end_offset=amount_ev.end,
                        utf16_start_offset=amount_ev.utf16_start,
                        utf16_end_offset=amount_ev.utf16_end,
                        normalization_form_matched=amount_ev.normalization_form_matched,
                        canonical_value=money_val.amount_str,
                        rule_id=money_val.rule_id,
                        parser_version=money_val.parser_version,
                        role_binding_status=rb_status,
                        candidate_count=rb_cand_count,
                        competing_candidates=rb_competing,
                    )
                )
                return self._build_failure_result(
                    context=context,
                    proposal_id=proposal_id,
                    audit_id=audit_id,
                    tool_name=tool_name,
                    parse_status=p_status,
                    reason_code=rc,
                    message=rb_outcome.message,
                    field_name="amount",
                    diagnostics=diagnostics,
                    untrusted_proposal=untrusted_proposal,
                )

        diagnostics.append(
            FieldDiagnostic(
                field_name="amount",
                parse_status=ParseStatus.PARSED,
                reason_code=None,
                message="Verified and canonicalized amount.",
                raw_quote=amount_ev.quote,
                context_excerpt=amount_ev.context_excerpt,
                start_offset=amount_ev.start,
                end_offset=amount_ev.end,
                utf16_start_offset=amount_ev.utf16_start,
                utf16_end_offset=amount_ev.utf16_end,
                normalization_form_matched=amount_ev.normalization_form_matched,
                canonical_value=money_val.amount_str,
                rule_id=money_val.rule_id,
                parser_version=money_val.parser_version,
                role_binding_status=rb_status,
                candidate_count=rb_cand_count,
                competing_candidates=rb_competing,
            )
        )
        if currency_ev is not None:
            diagnostics.append(
                FieldDiagnostic(
                    field_name="currency",
                    parse_status=ParseStatus.PARSED,
                    reason_code=None,
                    message="Verified currency.",
                    raw_quote=currency_ev.quote,
                    context_excerpt=currency_ev.context_excerpt,
                    start_offset=currency_ev.start,
                    end_offset=currency_ev.end,
                    utf16_start_offset=currency_ev.utf16_start,
                    utf16_end_offset=currency_ev.utf16_end,
                    normalization_form_matched=currency_ev.normalization_form_matched,
                    canonical_value=money_val.currency,
                    rule_id="iso4217_currency",
                    parser_version=PARSER_VERSION,
                )
            )

        # Validate expense_date result
        if not date_res.is_ok or date_res.value is None:
            rc = date_res.reason_code or ReasonCode.DATE_INVALID_FORMAT
            diagnostics.append(
                FieldDiagnostic(
                    field_name="expense_date",
                    parse_status=date_res.status,
                    reason_code=rc,
                    message=date_res.message,
                    raw_quote=date_ev.quote,
                    context_excerpt=date_ev.context_excerpt,
                    start_offset=date_ev.start,
                    end_offset=date_ev.end,
                    utf16_start_offset=date_ev.utf16_start,
                    utf16_end_offset=date_ev.utf16_end,
                    normalization_form_matched=date_ev.normalization_form_matched,
                )
            )
            return self._build_failure_result(
                context=context,
                proposal_id=proposal_id,
                audit_id=audit_id,
                tool_name=tool_name,
                parse_status=date_res.status,
                reason_code=rc,
                message=date_res.message,
                field_name="expense_date",
                diagnostics=diagnostics,
                untrusted_proposal=untrusted_proposal,
            )

        diagnostics.append(
            FieldDiagnostic(
                field_name="expense_date",
                parse_status=ParseStatus.PARSED,
                reason_code=None,
                message="Verified and canonicalized expense_date.",
                raw_quote=date_ev.quote,
                context_excerpt=date_ev.context_excerpt,
                start_offset=date_ev.start,
                end_offset=date_ev.end,
                utf16_start_offset=date_ev.utf16_start,
                utf16_end_offset=date_ev.utf16_end,
                normalization_form_matched=date_ev.normalization_form_matched,
                canonical_value=date_res.value.iso_date,
                rule_id=date_res.value.rule_id,
                parser_version=date_res.value.parser_version,
                role_binding_status=RoleBindingStatus.VERIFIED,
            )
        )

        # Optional category
        cat_enum = None
        if "category" in resolved_spans:
            cat_ev = resolved_spans["category"]
            cat_enum, cat_err = parse_expense_category(cat_ev.quote)
            if cat_err is not None or cat_enum is None:
                msg = f"Category {cat_ev.quote!r} is not in the allowed ExpenseCategory enum."
                diagnostics.append(
                    FieldDiagnostic(
                        field_name="category",
                        parse_status=ParseStatus.INVALID,
                        reason_code=ReasonCode.CATEGORY_INVALID,
                        message=msg,
                        raw_quote=cat_ev.quote,
                        start_offset=cat_ev.start,
                        end_offset=cat_ev.end,
                        utf16_start_offset=cat_ev.utf16_start,
                        utf16_end_offset=cat_ev.utf16_end,
                        normalization_form_matched=cat_ev.normalization_form_matched,
                    )
                )
                return BlockedGateResult(
                    request_id=context.request_id,
                    proposal_id=proposal_id,
                    audit_correlation_id=audit_id,
                    reason_code=ReasonCode.CATEGORY_INVALID,
                    message=msg,
                    tool_name=tool_name,
                    field_diagnostics=diagnostics,
                    untrusted_proposal=untrusted_proposal,
                )
            diagnostics.append(
                FieldDiagnostic(
                    field_name="category",
                    parse_status=ParseStatus.PARSED,
                    reason_code=None,
                    message="Verified category.",
                    raw_quote=cat_ev.quote,
                    start_offset=cat_ev.start,
                    end_offset=cat_ev.end,
                    utf16_start_offset=cat_ev.utf16_start,
                    utf16_end_offset=cat_ev.utf16_end,
                    normalization_form_matched=cat_ev.normalization_form_matched,
                    canonical_value=cat_enum.value,
                    rule_id="category_allowlist",
                    parser_version=PARSER_VERSION,
                )
            )

        # Optional description
        desc_val: str | None = None
        if "description" in resolved_spans:
            desc_ev = resolved_spans["description"]
            desc_val = sanitize_display_text(desc_ev.quote, max_len=240)
            if not desc_val:
                diagnostics.append(
                    FieldDiagnostic(
                        field_name="description",
                        parse_status=ParseStatus.INVALID,
                        reason_code=ReasonCode.FIELD_VALUE_INVALID,
                        message="Sanitized description cannot be empty.",
                        raw_quote=desc_ev.quote,
                        start_offset=desc_ev.start,
                        end_offset=desc_ev.end,
                        utf16_start_offset=desc_ev.utf16_start,
                        utf16_end_offset=desc_ev.utf16_end,
                        normalization_form_matched=desc_ev.normalization_form_matched,
                    )
                )
                return BlockedGateResult(
                    request_id=context.request_id,
                    proposal_id=proposal_id,
                    audit_correlation_id=audit_id,
                    reason_code=ReasonCode.FIELD_VALUE_INVALID,
                    message="Sanitized description cannot be empty.",
                    tool_name=tool_name,
                    field_diagnostics=diagnostics,
                    untrusted_proposal=untrusted_proposal,
                )
            diagnostics.append(
                FieldDiagnostic(
                    field_name="description",
                    parse_status=ParseStatus.PARSED,
                    reason_code=None,
                    message="Verified description.",
                    raw_quote=desc_ev.quote,
                    start_offset=desc_ev.start,
                    end_offset=desc_ev.end,
                    utf16_start_offset=desc_ev.utf16_start,
                    utf16_end_offset=desc_ev.utf16_end,
                    normalization_form_matched=desc_ev.normalization_form_matched,
                    canonical_value=desc_val,
                    rule_id="sanitized_text",
                    parser_version=PARSER_VERSION,
                )
            )

        assert money_val.currency is not None
        validated_args = SubmitExpenseCanonicalArgs(
            amount=money_val.amount_str,
            currency=money_val.currency,
            expense_date=date_res.value.iso_date,
            business_approval_status=BusinessApprovalStatus.PENDING_MANAGER_APPROVAL,
            description=desc_val,
            category=cat_enum,
        )
        canonical_dict = validated_args.to_canonical_dict()
        return self._finalize_valid_action(
            context=context,
            proposal_id=proposal_id,
            audit_id=audit_id,
            tool_name=tool_name,
            canonical_dict=canonical_dict,
            business_status=BusinessApprovalStatus.PENDING_MANAGER_APPROVAL,
            diagnostics=diagnostics,
            untrusted_proposal=untrusted_proposal,
        )

    def _canonicalize_schedule_event(
        self,
        *,
        context: TrustedRequestContext,
        policy: Any,
        proposal_id: str,
        audit_id: str,
        resolved_spans: dict[str, ResolvedEvidence],
        diagnostics: list[FieldDiagnostic],
        untrusted_proposal: dict[str, Any],
    ) -> GateResult:
        tool_name = ToolName.SCHEDULE_EVENT.value
        title_ev = resolved_spans["title"]
        start_ev = resolved_spans["start_datetime"]
        end_ev = resolved_spans.get("end_datetime")
        dur_ev = resolved_spans.get("duration")

        title_clean = sanitize_display_text(title_ev.quote, max_len=160)
        if not title_clean:
            msg = "Event title cannot be empty after sanitization."
            diagnostics.append(
                FieldDiagnostic(
                    field_name="title",
                    parse_status=ParseStatus.INVALID,
                    reason_code=ReasonCode.FIELD_VALUE_INVALID,
                    message=msg,
                    raw_quote=title_ev.quote,
                    start_offset=title_ev.start,
                    end_offset=title_ev.end,
                    utf16_start_offset=title_ev.utf16_start,
                    utf16_end_offset=title_ev.utf16_end,
                    normalization_form_matched=title_ev.normalization_form_matched,
                )
            )
            return BlockedGateResult(
                request_id=context.request_id,
                proposal_id=proposal_id,
                audit_correlation_id=audit_id,
                reason_code=ReasonCode.FIELD_VALUE_INVALID,
                message=msg,
                tool_name=tool_name,
                field_diagnostics=diagnostics,
                untrusted_proposal=untrusted_proposal,
            )

        diagnostics.append(
            FieldDiagnostic(
                field_name="title",
                parse_status=ParseStatus.PARSED,
                reason_code=None,
                message="Verified event title.",
                raw_quote=title_ev.quote,
                context_excerpt=title_ev.context_excerpt,
                start_offset=title_ev.start,
                end_offset=title_ev.end,
                utf16_start_offset=title_ev.utf16_start,
                utf16_end_offset=title_ev.utf16_end,
                normalization_form_matched=title_ev.normalization_form_matched,
                canonical_value=title_clean,
                rule_id="sanitized_text",
                parser_version=PARSER_VERSION,
            )
        )

        # Stage 3b: Temporal role-binding verification (catches swapped start/end or negated/disjunctive times)
        temp_rb_status = RoleBindingStatus.NOT_APPLICABLE
        if self.enable_role_binding and policy.enforce_role_binding:
            temp_rb = verify_temporal_role_binding(context.text, "start_datetime", start_ev)
            temp_rb_status = temp_rb.role_status
            if temp_rb.role_status != RoleBindingStatus.VERIFIED:
                rc = temp_rb.reason_code or ReasonCode.FIELD_ROLE_MISMATCH
                p_status = temp_rb.parse_status
                diagnostics.append(
                    FieldDiagnostic(
                        field_name="start_datetime",
                        parse_status=p_status,
                        reason_code=rc,
                        message=temp_rb.message,
                        raw_quote=start_ev.quote,
                        context_excerpt=start_ev.context_excerpt,
                        start_offset=start_ev.start,
                        end_offset=start_ev.end,
                        utf16_start_offset=start_ev.utf16_start,
                        utf16_end_offset=start_ev.utf16_end,
                        normalization_form_matched=start_ev.normalization_form_matched,
                        role_binding_status=temp_rb.role_status,
                        candidate_count=temp_rb.candidate_count,
                        competing_candidates=tuple(temp_rb.competing_candidates),
                    )
                )
                return self._build_failure_result(
                    context=context,
                    proposal_id=proposal_id,
                    audit_id=audit_id,
                    tool_name=tool_name,
                    parse_status=p_status,
                    reason_code=rc,
                    message=temp_rb.message,
                    field_name="start_datetime",
                    diagnostics=diagnostics,
                    untrusted_proposal=untrusted_proposal,
                )

        start_res = parse_datetime(
            start_ev.quote,
            context.locale,
            trusted_timezone=context.timezone,
            reference_datetime=context.reference_datetime,
            allow_relative_weekday=policy.allow_relative_weekday,
        )
        if not start_res.is_ok or start_res.value is None:
            rc = start_res.reason_code or ReasonCode.DATETIME_INVALID_TIME
            diagnostics.append(
                FieldDiagnostic(
                    field_name="start_datetime",
                    parse_status=start_res.status,
                    reason_code=rc,
                    message=start_res.message,
                    raw_quote=start_ev.quote,
                    context_excerpt=start_ev.context_excerpt,
                    start_offset=start_ev.start,
                    end_offset=start_ev.end,
                    utf16_start_offset=start_ev.utf16_start,
                    utf16_end_offset=start_ev.utf16_end,
                    normalization_form_matched=start_ev.normalization_form_matched,
                )
            )
            return self._build_failure_result(
                context=context,
                proposal_id=proposal_id,
                audit_id=audit_id,
                tool_name=tool_name,
                parse_status=start_res.status,
                reason_code=rc,
                message=start_res.message,
                field_name="start_datetime",
                diagnostics=diagnostics,
                untrusted_proposal=untrusted_proposal,
            )

        start_val = start_res.value
        diagnostics.append(
            FieldDiagnostic(
                field_name="start_datetime",
                parse_status=ParseStatus.PARSED,
                reason_code=None,
                message="Verified start_datetime.",
                raw_quote=start_ev.quote,
                context_excerpt=start_ev.context_excerpt,
                start_offset=start_ev.start,
                end_offset=start_ev.end,
                utf16_start_offset=start_ev.utf16_start,
                utf16_end_offset=start_ev.utf16_end,
                normalization_form_matched=start_ev.normalization_form_matched,
                canonical_value=start_val.rfc3339,
                rule_id=start_val.rule_id,
                parser_version=start_val.parser_version,
                role_binding_status=temp_rb_status,
            )
        )

        end_rfc3339: str | None = None
        end_dt_aware = None
        duration_minutes: int | None = None

        # If a duration quote was placed in end_datetime and duration was omitted, re-route deterministically
        if (
            end_ev is not None
            and dur_ev is None
            and parse_duration(end_ev.quote, context.locale).is_ok
        ):
            dur_ev = end_ev
            end_ev = None

        end_rb_status = RoleBindingStatus.NOT_APPLICABLE
        if end_ev is not None and self.enable_role_binding and policy.enforce_role_binding:
            end_rb = verify_temporal_role_binding(context.text, "end_datetime", end_ev)
            end_rb_status = end_rb.role_status
            if end_rb.role_status != RoleBindingStatus.VERIFIED:
                rc = end_rb.reason_code or ReasonCode.FIELD_ROLE_MISMATCH
                p_status = end_rb.parse_status
                diagnostics.append(
                    FieldDiagnostic(
                        field_name="end_datetime",
                        parse_status=p_status,
                        reason_code=rc,
                        message=end_rb.message,
                        raw_quote=end_ev.quote,
                        context_excerpt=end_ev.context_excerpt,
                        start_offset=end_ev.start,
                        end_offset=end_ev.end,
                        utf16_start_offset=end_ev.utf16_start,
                        utf16_end_offset=end_ev.utf16_end,
                        normalization_form_matched=end_ev.normalization_form_matched,
                        role_binding_status=end_rb.role_status,
                        candidate_count=end_rb.candidate_count,
                        competing_candidates=tuple(end_rb.competing_candidates),
                    )
                )
                return self._build_failure_result(
                    context=context,
                    proposal_id=proposal_id,
                    audit_id=audit_id,
                    tool_name=tool_name,
                    parse_status=p_status,
                    reason_code=rc,
                    message=end_rb.message,
                    field_name="end_datetime",
                    diagnostics=diagnostics,
                    untrusted_proposal=untrusted_proposal,
                )

        if end_ev is not None:
            end_res = parse_datetime(
                end_ev.quote,
                context.locale,
                trusted_timezone=context.timezone,
                reference_datetime=context.reference_datetime,
                allow_relative_weekday=policy.allow_relative_weekday,
            )
            if not end_res.is_ok or end_res.value is None:
                rc = end_res.reason_code or ReasonCode.DATETIME_INVALID_TIME
                diagnostics.append(
                    FieldDiagnostic(
                        field_name="end_datetime",
                        parse_status=end_res.status,
                        reason_code=rc,
                        message=end_res.message,
                        raw_quote=end_ev.quote,
                        context_excerpt=end_ev.context_excerpt,
                        start_offset=end_ev.start,
                        end_offset=end_ev.end,
                        utf16_start_offset=end_ev.utf16_start,
                        utf16_end_offset=end_ev.utf16_end,
                        normalization_form_matched=end_ev.normalization_form_matched,
                    )
                )
                return self._build_failure_result(
                    context=context,
                    proposal_id=proposal_id,
                    audit_id=audit_id,
                    tool_name=tool_name,
                    parse_status=end_res.status,
                    reason_code=rc,
                    message=end_res.message,
                    field_name="end_datetime",
                    diagnostics=diagnostics,
                    untrusted_proposal=untrusted_proposal,
                )
            end_dt_aware = end_res.value.value
            end_rfc3339 = end_res.value.rfc3339
            # Directive §8: Convert both datetimes to UTC before chronological comparison to ensure
            # that fold attributes (DST transition instants) are correctly compared on the absolute UTC timeline.
            start_utc_instant = start_val.value.astimezone(UTC)
            end_utc_instant = end_dt_aware.astimezone(UTC)
            if end_utc_instant <= start_utc_instant:
                rc = ReasonCode.EVENT_END_BEFORE_START
                msg = (
                    f"Event end_datetime ({end_rfc3339}) must be strictly after "
                    f"start_datetime ({start_val.rfc3339})."
                )
                diagnostics.append(
                    FieldDiagnostic(
                        field_name="end_datetime",
                        parse_status=ParseStatus.INVALID,
                        reason_code=rc,
                        message=msg,
                        raw_quote=end_ev.quote,
                        start_offset=end_ev.start,
                        end_offset=end_ev.end,
                        utf16_start_offset=end_ev.utf16_start,
                        utf16_end_offset=end_ev.utf16_end,
                        normalization_form_matched=end_ev.normalization_form_matched,
                        canonical_value=end_rfc3339,
                        rule_id=end_res.value.rule_id,
                        parser_version=end_res.value.parser_version,
                    )
                )
                return BlockedGateResult(
                    request_id=context.request_id,
                    proposal_id=proposal_id,
                    audit_correlation_id=audit_id,
                    reason_code=rc,
                    message=msg,
                    tool_name=tool_name,
                    field_diagnostics=diagnostics,
                    untrusted_proposal=untrusted_proposal,
                )
            diagnostics.append(
                FieldDiagnostic(
                    field_name="end_datetime",
                    parse_status=ParseStatus.PARSED,
                    reason_code=None,
                    message="Verified end_datetime.",
                    raw_quote=end_ev.quote,
                    context_excerpt=end_ev.context_excerpt,
                    start_offset=end_ev.start,
                    end_offset=end_ev.end,
                    utf16_start_offset=end_ev.utf16_start,
                    utf16_end_offset=end_ev.utf16_end,
                    normalization_form_matched=end_ev.normalization_form_matched,
                    canonical_value=end_rfc3339,
                    rule_id=end_res.value.rule_id,
                    parser_version=end_res.value.parser_version,
                    role_binding_status=end_rb_status,
                )
            )

        if dur_ev is not None:
            dur_res = parse_duration(dur_ev.quote, context.locale)
            if not dur_res.is_ok or dur_res.value is None:
                rc = dur_res.reason_code or ReasonCode.DURATION_INVALID
                diagnostics.append(
                    FieldDiagnostic(
                        field_name="duration",
                        parse_status=dur_res.status,
                        reason_code=rc,
                        message=dur_res.message,
                        raw_quote=dur_ev.quote,
                        start_offset=dur_ev.start,
                        end_offset=dur_ev.end,
                        utf16_start_offset=dur_ev.utf16_start,
                        utf16_end_offset=dur_ev.utf16_end,
                        normalization_form_matched=dur_ev.normalization_form_matched,
                    )
                )
                return self._build_failure_result(
                    context=context,
                    proposal_id=proposal_id,
                    audit_id=audit_id,
                    tool_name=tool_name,
                    parse_status=dur_res.status,
                    reason_code=rc,
                    message=dur_res.message,
                    field_name="duration",
                    diagnostics=diagnostics,
                    untrusted_proposal=untrusted_proposal,
                )
            duration_minutes = dur_res.value.minutes
            computed_end_utc = start_val.value.astimezone(UTC) + timedelta(minutes=duration_minutes)
            z = ZoneInfo(start_val.iana_timezone)
            computed_end_local = computed_end_utc.astimezone(z)

            if end_dt_aware is not None and end_dt_aware != computed_end_local:
                rc = ReasonCode.EVENT_END_AND_DURATION_CONFLICT
                msg = (
                    f"Provided end_datetime ({end_rfc3339}) conflicts with "
                    f"start_datetime + duration ({computed_end_local.isoformat(timespec='seconds')})."
                )
                diagnostics.append(
                    FieldDiagnostic(
                        field_name="duration",
                        parse_status=ParseStatus.INVALID,
                        reason_code=rc,
                        message=msg,
                        raw_quote=dur_ev.quote,
                        start_offset=dur_ev.start,
                        end_offset=dur_ev.end,
                        utf16_start_offset=dur_ev.utf16_start,
                        utf16_end_offset=dur_ev.utf16_end,
                        normalization_form_matched=dur_ev.normalization_form_matched,
                        canonical_value=duration_minutes,
                    )
                )
                return BlockedGateResult(
                    request_id=context.request_id,
                    proposal_id=proposal_id,
                    audit_correlation_id=audit_id,
                    reason_code=rc,
                    message=msg,
                    tool_name=tool_name,
                    field_diagnostics=diagnostics,
                    untrusted_proposal=untrusted_proposal,
                )

            end_rfc3339 = computed_end_local.isoformat(timespec="seconds")
            diagnostics.append(
                FieldDiagnostic(
                    field_name="duration",
                    parse_status=ParseStatus.PARSED,
                    reason_code=None,
                    message="Verified duration.",
                    raw_quote=dur_ev.quote,
                    context_excerpt=dur_ev.context_excerpt,
                    start_offset=dur_ev.start,
                    end_offset=dur_ev.end,
                    utf16_start_offset=dur_ev.utf16_start,
                    utf16_end_offset=dur_ev.utf16_end,
                    normalization_form_matched=dur_ev.normalization_form_matched,
                    canonical_value=duration_minutes,
                    rule_id=dur_res.value.rule_id,
                    parser_version=dur_res.value.parser_version,
                    role_binding_status=temp_rb_status,
                )
            )

        if duration_minutes is None and end_dt_aware is not None:
            duration_minutes = int(
                (end_dt_aware.astimezone(UTC) - start_val.value.astimezone(UTC)).total_seconds()
                // 60
            )

        assert end_rfc3339 is not None
        validated_event = ScheduleEventCanonicalArgs(
            title=title_clean,
            start_datetime=start_val.rfc3339,
            end_datetime=end_rfc3339,
            timezone=start_val.iana_timezone,
            duration_minutes=duration_minutes,
        )
        canonical_dict = validated_event.to_canonical_dict()
        return self._finalize_valid_action(
            context=context,
            proposal_id=proposal_id,
            audit_id=audit_id,
            tool_name=tool_name,
            canonical_dict=canonical_dict,
            business_status=BusinessApprovalStatus.NOT_APPLICABLE,
            diagnostics=diagnostics,
            untrusted_proposal=untrusted_proposal,
        )

    def _finalize_valid_action(
        self,
        *,
        context: TrustedRequestContext,
        proposal_id: str,
        audit_id: str,
        tool_name: str,
        canonical_dict: dict[str, Any],
        business_status: BusinessApprovalStatus,
        diagnostics: list[FieldDiagnostic],
        untrusted_proposal: dict[str, Any],
    ) -> GateResult:
        action_digest = compute_canonical_action_digest(
            request_id=context.request_id,
            tool_name=tool_name,
            canonical_arguments=canonical_dict,
        )

        if context.require_confirmation:
            conf_token, _ = generate_confirmation_capability_token()
            return AwaitingConfirmationGateResult(
                request_id=context.request_id,
                proposal_id=proposal_id,
                audit_correlation_id=audit_id,
                reason_code=ReasonCode.OK_AWAITING_CONFIRMATION,
                message=f"Verified canonical arguments for {tool_name}; awaiting explicit user confirmation.",
                tool_name=tool_name,
                canonical_arguments=canonical_dict,
                field_diagnostics=diagnostics,
                confirmation_status=ConfirmationStatus.PENDING,
                business_approval_status=business_status,
                action_digest=action_digest,
                confirmation_token=conf_token,
                untrusted_proposal=untrusted_proposal,
            )

        effect = None
        if self.store is not None and not context.dry_run:
            effect = self.store.execute_immediate_simulated(
                request_id=context.request_id,
                proposal_id=proposal_id,
                tool_name=tool_name,
                canonical_args=canonical_dict,
                action_digest=action_digest,
            )

        return ExecutedGateResult(
            request_id=context.request_id,
            proposal_id=proposal_id,
            audit_correlation_id=audit_id,
            reason_code=ReasonCode.OK_EXECUTED,
            message=f"Verified and executed simulated {tool_name}.",
            tool_name=tool_name,
            canonical_arguments=canonical_dict,
            field_diagnostics=diagnostics,
            confirmation_status=ConfirmationStatus.CONFIRMED,
            business_approval_status=business_status,
            action_digest=action_digest,
            simulated_effect=effect,
            untrusted_proposal=untrusted_proposal,
        )

    def _build_failure_result(
        self,
        *,
        context: TrustedRequestContext,
        proposal_id: str,
        audit_id: str,
        tool_name: str | None,
        parse_status: ParseStatus,
        reason_code: ReasonCode,
        message: str,
        field_name: str | None,
        diagnostics: list[FieldDiagnostic],
        untrusted_proposal: dict[str, Any],
    ) -> GateResult:
        target_status = classify_failure_status(parse_status, reason_code)
        if target_status == GateDecisionStatus.NEEDS_CLARIFICATION:
            q = build_clarification_prompt(reason_code, context.locale, field_name)
            return NeedsClarificationGateResult(
                request_id=context.request_id,
                proposal_id=proposal_id,
                audit_correlation_id=audit_id,
                reason_code=reason_code,
                message=message,
                tool_name=tool_name,
                field_diagnostics=diagnostics,
                clarification_question=q,
                untrusted_proposal=untrusted_proposal,
            )
        return BlockedGateResult(
            request_id=context.request_id,
            proposal_id=proposal_id,
            audit_correlation_id=audit_id,
            reason_code=reason_code,
            message=message,
            tool_name=tool_name,
            field_diagnostics=diagnostics,
            untrusted_proposal=untrusted_proposal,
        )

    def _persist_if_configured(self, context: TrustedRequestContext, result: GateResult) -> None:
        if self.store is not None and not context.dry_run:
            self.store.record_gate_decision(
                result,
                locale=context.locale,
                timezone_name=context.timezone,
                reference_datetime=(
                    context.reference_datetime.isoformat() if context.reference_datetime else None
                ),
                request_text=context.text,
                is_dry_run=context.dry_run,
            )
