"""Baseline, ablation, and oracle comparator pipelines for the §12.1 / §10 evaluation protocol."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from locale_gate.contracts.enums import (
    BusinessApprovalStatus,
    ConfirmationStatus,
    ParseStatus,
    ProposalDecision,
    ReasonCode,
    ToolName,
)
from locale_gate.contracts.models import (
    ActionProposal,
    BlockedGateResult,
    EvidenceQuote,
    ExecutedGateResult,
    FieldEvidenceProposal,
    GateResult,
    ModelErrorGateResult,
    NeedsClarificationGateResult,
    NoActionGateResult,
    TrustedRequestContext,
)
from locale_gate.evidence.resolver import resolve_evidence_quote
from locale_gate.gate.pipeline import LocaleGatePipeline
from locale_gate.tools.registry import compute_canonical_action_digest, get_tool_manifest


def _naive_en_number_extract(raw: str) -> tuple[str, str]:
    """Simulate standard naive English/JSON float normalization used by unguarded baselines.

    Treats '.' as decimal point and strips ',' as thousands separator, which silently corrupts
    Croatian numbers like '1.234,56 €' or '1.200 €' into '1.234' or '1.2'!
    """
    s = raw.strip()
    currency = "EUR"
    if "$" in s or "USD" in s.upper():
        currency = "USD"
    elif "£" in s or "GBP" in s.upper():
        currency = "GBP"
    elif "CHF" in s.upper():
        currency = "CHF"
    elif "JPY" in s.upper() or "¥" in s:
        currency = "JPY"

    # Naive US/JSON cleanup: strip commas, keep first dot-number
    cleaned = s.replace(",", "")
    m = re.search(r"[-+]?\d+(?:\.\d+)?", cleaned)
    if not m:
        return ("0.00", currency)
    num_str = m.group(0)
    try:
        val = float(num_str)
        return (f"{val:.2f}", currency)
    except ValueError:
        return ("0.00", currency)


def _conventional_number_extract(raw: str, locale: str | None = None) -> tuple[str, str]:
    """Simulate a strong conventional baseline parser (System E) utilizing Babel CLDR numbers.

    Parses decimals using Babel CLDR locale formatting when available, detecting currency symbols,
    falling back to last-separator heuristics. However, it lacks strict evidence quote verification,
    ambiguous 3-digit grouping blocking, historical currency rules, DST fold handling, and
    semantic role-binding.
    """
    s = raw.strip()
    currency = "EUR"
    if "$" in s or "USD" in s.upper():
        currency = "USD"
    elif "£" in s or "GBP" in s.upper():
        currency = "GBP"
    elif "CHF" in s.upper():
        currency = "CHF"
    elif "JPY" in s.upper() or "¥" in s:
        currency = "JPY"

    # Isolate numeric substring
    m = re.search(r"[-+]?\d[\d.,\s]*\d|[-+]?\d+", s)
    if not m:
        return ("0.00", currency)
    num_token = m.group(0).strip()

    # Try Babel CLDR locale-aware parsing if locale is provided
    if locale:
        try:
            import babel.numbers

            b_loc = locale.replace("-", "_")
            dec = babel.numbers.parse_decimal(num_token, locale=b_loc)
            return (f"{float(dec):.2f}", currency)
        except Exception:
            pass

    # Fallback to last-separator heuristic
    tok = re.sub(r"\s+", "", num_token)
    if "." in tok and "," in tok:
        if tok.rfind(",") > tok.rfind("."):
            tok = tok.replace(".", "").replace(",", ".")
        else:
            tok = tok.replace(",", "")
    elif "," in tok:
        parts = tok.split(",")
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            tok = tok.replace(",", ".")
        else:
            tok = tok.replace(",", "")
    elif "." in tok:
        parts = tok.split(".")
        if len(parts) > 2:
            tok = tok.replace(".", "")
    try:
        val = float(tok)
        return (f"{val:.2f}", currency)
    except ValueError:
        return ("0.00", currency)


def _naive_date_extract(raw: str, ref_year: int = 2026) -> str:
    """Simulate permissive naive date parsing (US-biased MDY fallback and silent year fill)."""
    s = raw.strip().rstrip(".")
    iso_m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if iso_m:
        return f"{iso_m.group(1)}-{iso_m.group(2)}-{iso_m.group(3)}"
    # Permissive US-biased numeric parse: treats first number as month if <= 12!
    num_m = re.search(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", s)
    if num_m:
        n1, n2 = int(num_m.group(1)), int(num_m.group(2))
        y_str = num_m.group(3)
        y = int(y_str) if y_str else ref_year
        if y < 100:
            y += 2000
        if n1 <= 12:
            m, d = n1, n2
        else:
            d, m = n1, n2
        return f"{y:04d}-{m:02d}-{d:02d}"
    return f"{ref_year:04d}-01-01"


def _conventional_date_extract(raw: str, locale: str | None, ref_year: int = 2026) -> str:
    """Simulate conventional date parser with basic locale DMY/MDY preference, but silent year fill
    and no slash-ambiguity blocking on en-US/en-GB/hr-HR."""
    s = raw.strip().rstrip(".")
    iso_m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if iso_m:
        return f"{iso_m.group(1)}-{iso_m.group(2)}-{iso_m.group(3)}"
    num_m = re.search(r"(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?", s)
    if num_m:
        n1, n2 = int(num_m.group(1)), int(num_m.group(2))
        y_str = num_m.group(3)
        y = int(y_str) if y_str else ref_year
        if y < 100:
            y += 2000
        if (locale or "hr-HR") == "en-US":
            m, d = (n1, n2) if n1 <= 12 else (n2, n1)
        else:
            d, m = (n1, n2) if n2 <= 12 else (n2, n1)
        return f"{y:04d}-{m:02d}-{d:02d}"
    return f"{ref_year:04d}-01-01"


def _naive_datetime_extract(raw: str, tz: str | None, ref_year: int = 2026) -> str:
    """Simulate permissive naive datetime parsing without DST fold/gap checks or locale awareness."""
    d_str = _naive_date_extract(raw, ref_year=ref_year)
    t_m = re.search(r"(\d{1,2}):(\d{2})", raw)
    if t_m:
        hh, mm = int(t_m.group(1)), int(t_m.group(2))
        if "pm" in raw.lower() and hh < 12:
            hh += 12
    else:
        hh, mm = 0, 0
    offset = "+01:00" if (tz or "Europe/Zagreb") == "Europe/Zagreb" else "+00:00"
    return f"{d_str}T{hh:02d}:{mm:02d}:00{offset}"


class RawModelBaselinePipeline:
    """System A (§12.1): Raw model proposal dispatched after basic JSON parsing only.

    Performs no exact-quote source verification, no locale-aware canonicalization,
    no DST fold/gap checks, and no user confirmation gate. Dispatches only in isolated simulation.
    """

    def evaluate_raw_json(
        self,
        context: TrustedRequestContext,
        raw_payload: str | dict[str, Any],
    ) -> GateResult:
        prop_id = f"raw_{uuid.uuid4().hex[:16]}"
        corr_id = f"corr_{uuid.uuid4().hex[:16]}"

        if isinstance(raw_payload, str):
            try:
                data = json.loads(raw_payload)
            except json.JSONDecodeError as exc:
                return ModelErrorGateResult(
                    request_id=context.request_id,
                    proposal_id=prop_id,
                    audit_correlation_id=corr_id,
                    reason_code=ReasonCode.MODEL_INVALID_JSON,
                    message=str(exc),
                )
        else:
            data = raw_payload

        if not isinstance(data, dict):
            return BlockedGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.PROPOSAL_SCHEMA_INVALID,
                message="Not a JSON object.",
            )

        decision = data.get("decision", "propose")
        if decision == "no_action":
            return NoActionGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.OK_NO_ACTION,
                message="Raw baseline no_action.",
            )
        if decision == "clarify":
            return NeedsClarificationGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.CLARIFY_MODEL_REQUESTED,
                message=str(data.get("clarification_question") or "Clarify."),
                clarification_question=str(data.get("clarification_question") or "Clarify."),
            )

        tool_name = str(data.get("tool_name") or "")
        fields = data.get("fields") or {}
        if not tool_name or not isinstance(fields, dict):
            return BlockedGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.PROPOSAL_SCHEMA_INVALID,
                message="Missing tool_name or fields.",
            )

        def _get_quote(f_key: str) -> str:
            f_obj = fields.get(f_key)
            if isinstance(f_obj, dict):
                ev = f_obj.get("evidence")
                if isinstance(ev, dict):
                    return str(ev.get("quote") or "")
            return str(f_obj or "")

        ref_year = context.reference_datetime.year if context.reference_datetime else 2026
        if tool_name == ToolName.SUBMIT_EXPENSE.value:
            amt_str, cur_str = _naive_en_number_extract(_get_quote("amount"))
            if "currency" in fields:
                cur_q = _get_quote("currency").strip().upper()
                if len(cur_q) == 3:
                    cur_str = cur_q
            exp_date = _naive_date_extract(_get_quote("expense_date"), ref_year=ref_year)
            canonical_args: dict[str, Any] = {
                "amount": amt_str,
                "currency": cur_str,
                "expense_date": exp_date,
                "business_approval_status": BusinessApprovalStatus.PENDING_MANAGER_APPROVAL.value,
            }
            if "description" in fields and _get_quote("description"):
                canonical_args["description"] = _get_quote("description")
            if "category" in fields and _get_quote("category"):
                canonical_args["category"] = _get_quote("category").lower()
        elif tool_name == ToolName.SCHEDULE_EVENT.value:
            start_iso = _naive_datetime_extract(
                _get_quote("start_datetime"), context.timezone, ref_year=ref_year
            )
            end_iso = (
                _naive_datetime_extract(
                    _get_quote("end_datetime"), context.timezone, ref_year=ref_year
                )
                if "end_datetime" in fields
                else start_iso
            )
            canonical_args = {
                "title": _get_quote("title") or "Untitled",
                "start_datetime": start_iso,
                "end_datetime": end_iso,
                "timezone": context.timezone or "UTC",
            }
        else:
            canonical_args = {k: _get_quote(k) for k in fields}

        digest = compute_canonical_action_digest(context.request_id, tool_name, canonical_args)
        return ExecutedGateResult(
            request_id=context.request_id,
            proposal_id=prop_id,
            audit_correlation_id=corr_id,
            reason_code=ReasonCode.OK_EXECUTED,
            message="Raw baseline executed without locale/evidence gate.",
            tool_name=tool_name,
            canonical_arguments=canonical_args,
            confirmation_status=ConfirmationStatus.CONFIRMED,
            business_approval_status=(
                BusinessApprovalStatus.PENDING_MANAGER_APPROVAL
                if tool_name == ToolName.SUBMIT_EXPENSE.value
                else BusinessApprovalStatus.NOT_APPLICABLE
            ),
            action_digest=digest,
            untrusted_proposal=data,
        )


class SchemaOnlyBaselinePipeline:
    """System B (§12.1): Structured tool output and strict schema validation, but no exact-quote
    verification against original source and no locale-aware canonicalization.

    Isolates the benefit of LocaleGate's evidence resolution and deterministic locale parsers
    beyond constrained JSON schema output.
    """

    def evaluate_raw_json(
        self,
        context: TrustedRequestContext,
        raw_payload: str | dict[str, Any],
    ) -> GateResult:
        prop_id = f"sch_{uuid.uuid4().hex[:16]}"
        corr_id = f"corr_{uuid.uuid4().hex[:16]}"

        if isinstance(raw_payload, str):
            try:
                data = json.loads(raw_payload)
            except json.JSONDecodeError as exc:
                return ModelErrorGateResult(
                    request_id=context.request_id,
                    proposal_id=prop_id,
                    audit_correlation_id=corr_id,
                    reason_code=ReasonCode.MODEL_INVALID_JSON,
                    message=str(exc),
                )
        else:
            data = raw_payload

        try:
            proposal = ActionProposal.model_validate(data)
        except Exception as exc:
            return BlockedGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.PROPOSAL_SCHEMA_INVALID,
                message=str(exc),
            )

        if proposal.decision == ProposalDecision.NO_ACTION:
            return NoActionGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.OK_NO_ACTION,
                message="Schema baseline no_action.",
            )
        if proposal.decision == ProposalDecision.CLARIFY:
            q = proposal.clarification_question or "Clarification required."
            return NeedsClarificationGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.CLARIFY_MODEL_REQUESTED,
                message=q,
                tool_name=proposal.tool_name,
                clarification_question=q,
            )

        manifest = get_tool_manifest(proposal.tool_name)
        if manifest is None or proposal.tool_name is None:
            return BlockedGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.TOOL_UNKNOWN,
                message="Unknown tool.",
            )
        if proposal.tool_name not in context.allowed_tools:
            return BlockedGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.TOOL_NOT_ALLOWED,
                message="Tool not allowed.",
            )

        for fk in proposal.fields:
            if fk not in manifest.all_allowed_fields:
                return BlockedGateResult(
                    request_id=context.request_id,
                    proposal_id=prop_id,
                    audit_correlation_id=corr_id,
                    reason_code=ReasonCode.FIELD_UNKNOWN_FORBIDDEN,
                    message=f"Unknown field {fk}.",
                )
        for req_f in manifest.required_fields:
            if req_f not in proposal.fields:
                return NeedsClarificationGateResult(
                    request_id=context.request_id,
                    proposal_id=prop_id,
                    audit_correlation_id=corr_id,
                    reason_code=ReasonCode.FIELD_REQUIRED_MISSING,
                    message=f"Missing required field {req_f}.",
                )

        # Schema-only baseline uses naive non-locale conversion without checking source evidence
        ref_year = context.reference_datetime.year if context.reference_datetime else 2026
        if manifest.name == ToolName.SUBMIT_EXPENSE:
            amt_q = proposal.fields["amount"].evidence.quote
            amt_str, cur_str = _naive_en_number_extract(amt_q)
            if "currency" in proposal.fields:
                cq = proposal.fields["currency"].evidence.quote.strip().upper()
                if len(cq) == 3:
                    cur_str = cq
            exp_date = _naive_date_extract(
                proposal.fields["expense_date"].evidence.quote, ref_year=ref_year
            )
            if amt_str.startswith("-") or amt_str == "0.00":
                return BlockedGateResult(
                    request_id=context.request_id,
                    proposal_id=prop_id,
                    audit_correlation_id=corr_id,
                    reason_code=ReasonCode.MONEY_NON_POSITIVE,
                    message="Non-positive amount.",
                )
            canonical_args: dict[str, Any] = {
                "amount": amt_str,
                "currency": cur_str,
                "expense_date": exp_date,
                "business_approval_status": BusinessApprovalStatus.PENDING_MANAGER_APPROVAL.value,
            }
        else:
            if "end_datetime" not in proposal.fields and "duration" not in proposal.fields:
                return NeedsClarificationGateResult(
                    request_id=context.request_id,
                    proposal_id=prop_id,
                    audit_correlation_id=corr_id,
                    reason_code=ReasonCode.EVENT_END_OR_DURATION_MISSING,
                    message="Missing end or duration.",
                )
            start_iso = _naive_datetime_extract(
                proposal.fields["start_datetime"].evidence.quote,
                context.timezone,
                ref_year=ref_year,
            )
            end_iso = (
                _naive_datetime_extract(
                    proposal.fields["end_datetime"].evidence.quote,
                    context.timezone,
                    ref_year=ref_year,
                )
                if "end_datetime" in proposal.fields
                else start_iso
            )
            canonical_args = {
                "title": proposal.fields["title"].evidence.quote.strip(),
                "start_datetime": start_iso,
                "end_datetime": end_iso,
                "timezone": context.timezone or "UTC",
            }

        digest = compute_canonical_action_digest(
            context.request_id, proposal.tool_name, canonical_args
        )
        return ExecutedGateResult(
            request_id=context.request_id,
            proposal_id=prop_id,
            audit_correlation_id=corr_id,
            reason_code=ReasonCode.OK_EXECUTED,
            message="Schema-only baseline executed.",
            tool_name=proposal.tool_name,
            canonical_arguments=canonical_args,
            confirmation_status=ConfirmationStatus.CONFIRMED,
            business_approval_status=(
                BusinessApprovalStatus.PENDING_MANAGER_APPROVAL
                if proposal.tool_name == ToolName.SUBMIT_EXPENSE.value
                else BusinessApprovalStatus.NOT_APPLICABLE
            ),
            action_digest=digest,
        )


class StrongConventionalBaselinePipeline:
    """System E (§10.1): Stronger conventional structured-tool baseline.

    Performs schema validation, tool/field allowlist checks, last-separator decimal heuristics,
    and basic locale DMY/MDY ordering, but omits exact-quote source verification, strict
    ambiguous-separator blocking, DST fold/gap blocking, and semantic role-binding verification.
    """

    def evaluate_raw_json(
        self,
        context: TrustedRequestContext,
        raw_payload: str | dict[str, Any],
    ) -> GateResult:
        prop_id = f"conv_{uuid.uuid4().hex[:16]}"
        corr_id = f"corr_{uuid.uuid4().hex[:16]}"

        if isinstance(raw_payload, str):
            try:
                data = json.loads(raw_payload)
            except json.JSONDecodeError as exc:
                return ModelErrorGateResult(
                    request_id=context.request_id,
                    proposal_id=prop_id,
                    audit_correlation_id=corr_id,
                    reason_code=ReasonCode.MODEL_INVALID_JSON,
                    message=str(exc),
                )
        else:
            data = raw_payload

        try:
            proposal = ActionProposal.model_validate(data)
        except Exception as exc:
            return BlockedGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.PROPOSAL_SCHEMA_INVALID,
                message=str(exc),
            )

        if proposal.decision == ProposalDecision.NO_ACTION:
            return NoActionGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.OK_NO_ACTION,
                message="Conventional baseline no_action.",
            )
        if proposal.decision == ProposalDecision.CLARIFY:
            q = proposal.clarification_question or "Clarification required."
            return NeedsClarificationGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.CLARIFY_MODEL_REQUESTED,
                message=q,
                tool_name=proposal.tool_name,
                clarification_question=q,
            )

        manifest = get_tool_manifest(proposal.tool_name)
        if manifest is None or proposal.tool_name is None:
            return BlockedGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.TOOL_UNKNOWN,
                message="Unknown tool.",
            )
        if proposal.tool_name not in context.allowed_tools:
            return BlockedGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.TOOL_NOT_ALLOWED,
                message="Tool not allowed.",
            )
        for fk in proposal.fields:
            if fk not in manifest.all_allowed_fields:
                return BlockedGateResult(
                    request_id=context.request_id,
                    proposal_id=prop_id,
                    audit_correlation_id=corr_id,
                    reason_code=ReasonCode.FIELD_UNKNOWN_FORBIDDEN,
                    message=f"Unknown field {fk}.",
                )
        for req_f in manifest.required_fields:
            if req_f not in proposal.fields:
                return NeedsClarificationGateResult(
                    request_id=context.request_id,
                    proposal_id=prop_id,
                    audit_correlation_id=corr_id,
                    reason_code=ReasonCode.FIELD_REQUIRED_MISSING,
                    message=f"Missing required field {req_f}.",
                )

        ref_year = context.reference_datetime.year if context.reference_datetime else 2026
        if manifest.name == ToolName.SUBMIT_EXPENSE:
            amt_q = proposal.fields["amount"].evidence.quote
            amt_str, cur_str = _conventional_number_extract(amt_q, locale=context.locale)
            if "currency" in proposal.fields:
                cq = proposal.fields["currency"].evidence.quote.strip().upper()
                if len(cq) == 3:
                    cur_str = cq
            exp_date = _conventional_date_extract(
                proposal.fields["expense_date"].evidence.quote,
                context.locale,
                ref_year=ref_year,
            )
            if amt_str.startswith("-") or amt_str == "0.00":
                return BlockedGateResult(
                    request_id=context.request_id,
                    proposal_id=prop_id,
                    audit_correlation_id=corr_id,
                    reason_code=ReasonCode.MONEY_NON_POSITIVE,
                    message="Non-positive amount.",
                )
            canonical_args: dict[str, Any] = {
                "amount": amt_str,
                "currency": cur_str,
                "expense_date": exp_date,
                "business_approval_status": BusinessApprovalStatus.PENDING_MANAGER_APPROVAL.value,
            }
        else:
            if "end_datetime" not in proposal.fields and "duration" not in proposal.fields:
                return NeedsClarificationGateResult(
                    request_id=context.request_id,
                    proposal_id=prop_id,
                    audit_correlation_id=corr_id,
                    reason_code=ReasonCode.EVENT_END_OR_DURATION_MISSING,
                    message="Missing end or duration.",
                )
            start_iso = _naive_datetime_extract(
                proposal.fields["start_datetime"].evidence.quote,
                context.timezone,
                ref_year=ref_year,
            )
            end_iso = (
                _naive_datetime_extract(
                    proposal.fields["end_datetime"].evidence.quote,
                    context.timezone,
                    ref_year=ref_year,
                )
                if "end_datetime" in proposal.fields
                else start_iso
            )
            canonical_args = {
                "title": proposal.fields["title"].evidence.quote.strip(),
                "start_datetime": start_iso,
                "end_datetime": end_iso,
                "timezone": context.timezone or "UTC",
            }

        digest = compute_canonical_action_digest(
            context.request_id, proposal.tool_name, canonical_args
        )
        return ExecutedGateResult(
            request_id=context.request_id,
            proposal_id=prop_id,
            audit_correlation_id=corr_id,
            reason_code=ReasonCode.OK_EXECUTED,
            message="Strong conventional baseline executed.",
            tool_name=proposal.tool_name,
            canonical_arguments=canonical_args,
            confirmation_status=ConfirmationStatus.CONFIRMED,
            business_approval_status=(
                BusinessApprovalStatus.PENDING_MANAGER_APPROVAL
                if proposal.tool_name == ToolName.SUBMIT_EXPENSE.value
                else BusinessApprovalStatus.NOT_APPLICABLE
            ),
            action_digest=digest,
        )


class LocaleParserNoEvidenceAblationPipeline:
    """Ablation Ab1 (§10.2): Deterministic locale parsers WITHOUT exact-quote source verification
    or semantic role-binding verification.

    Applies `LocaleGatePipeline` canonicalization directly to whatever string the model placed
    in `quote` by synthesizing a dummy `ResolvedEvidence` span without checking `context.text`.
    """

    def __init__(self) -> None:
        self._inner = LocaleGatePipeline(enable_role_binding=False)

    def evaluate_raw_json(
        self,
        context: TrustedRequestContext,
        raw_payload: str | dict[str, Any],
    ) -> GateResult:
        # Build a synthetic context whose text concatenates the proposed quotes so Stage 3 never rejects
        data = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        if isinstance(data, dict) and isinstance(data.get("fields"), dict):
            synthetic_parts = [context.text]
            for f_val in data["fields"].values():
                if isinstance(f_val, dict) and isinstance(f_val.get("evidence"), dict):
                    q = str(f_val["evidence"].get("quote") or "")
                    if q and q not in context.text:
                        synthetic_parts.append(q)
            synthetic_ctx = TrustedRequestContext(
                request_id=context.request_id,
                text=" | ".join(synthetic_parts),
                locale=context.locale,
                timezone=context.timezone,
                reference_datetime=context.reference_datetime,
                default_currency=context.default_currency,
                allowed_tools=context.allowed_tools,
                policy_profile=context.policy_profile,
                require_confirmation=context.require_confirmation,
                dry_run=context.dry_run,
            )
            return self._inner.evaluate_raw_json(synthetic_ctx, raw_payload)
        return self._inner.evaluate_raw_json(context, raw_payload)


class EvidenceOnlyNaiveParserAblationPipeline:
    """Ablation Ab2 (§10.2): Exact-quote source verification WITH naive English-only non-locale parser.

    Verifies that every proposed field quote resolves uniquely in `context.text` via Stage 3,
    then delegates canonicalization to naive parser (`SchemaOnlyBaselinePipeline`).
    """

    def __init__(self) -> None:
        self._naive = SchemaOnlyBaselinePipeline()

    def evaluate_raw_json(
        self,
        context: TrustedRequestContext,
        raw_payload: str | dict[str, Any],
    ) -> GateResult:
        prop_id = f"ab2_{uuid.uuid4().hex[:16]}"
        corr_id = f"corr_{uuid.uuid4().hex[:16]}"
        data = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        try:
            proposal = ActionProposal.model_validate(data)
        except Exception as exc:
            return BlockedGateResult(
                request_id=context.request_id,
                proposal_id=prop_id,
                audit_correlation_id=corr_id,
                reason_code=ReasonCode.PROPOSAL_SCHEMA_INVALID,
                message=str(exc),
            )
        if proposal.decision == ProposalDecision.PROPOSE:
            for _field_name, field_prop in proposal.fields.items():
                ev_out = resolve_evidence_quote(context.text, field_prop.evidence)
                if ev_out.status != ParseStatus.PARSED or ev_out.resolved is None:
                    rc = ev_out.reason_code or ReasonCode.EVIDENCE_NOT_FOUND
                    if ev_out.status == ParseStatus.AMBIGUOUS:
                        return NeedsClarificationGateResult(
                            request_id=context.request_id,
                            proposal_id=prop_id,
                            audit_correlation_id=corr_id,
                            reason_code=rc,
                            message=ev_out.message,
                            tool_name=proposal.tool_name,
                            clarification_question=ev_out.message,
                        )
                    return BlockedGateResult(
                        request_id=context.request_id,
                        proposal_id=prop_id,
                        audit_correlation_id=corr_id,
                        reason_code=rc,
                        message=ev_out.message,
                        tool_name=proposal.tool_name,
                    )
        return self._naive.evaluate_raw_json(context, raw_payload)


class NoRoleBindingAblationPipeline:
    """Ablation Ab3 (§10.2): Full LocaleGate pipeline with semantic role-binding verification disabled."""

    def __init__(self) -> None:
        self._inner = LocaleGatePipeline(enable_role_binding=False)

    def evaluate_raw_json(
        self,
        context: TrustedRequestContext,
        raw_payload: str | dict[str, Any],
    ) -> GateResult:
        return self._inner.evaluate_raw_json(context, raw_payload)


class ParserOraclePipeline:
    """System D (§12.1): Diagnostic oracle applying LocaleGate to gold field evidence spans.

    Used strictly to quantify the upper bound / coverage of the deterministic locale rules.
    Never compared as an autonomous agent system.
    """

    def __init__(self) -> None:
        self.gate = LocaleGatePipeline()

    def evaluate_gold_evidence(
        self,
        context: TrustedRequestContext,
        gold_proposal_dict: dict[str, Any],
    ) -> GateResult:
        proposal = ActionProposal(
            decision=ProposalDecision(gold_proposal_dict["decision"]),
            tool_name=gold_proposal_dict.get("tool_name"),
            fields={
                k: FieldEvidenceProposal(
                    evidence=EvidenceQuote(
                        quote=v["evidence"]["quote"],
                        context_excerpt=v["evidence"].get("context_excerpt"),
                    )
                )
                for k, v in (gold_proposal_dict.get("fields") or {}).items()
            },
            clarification_question=gold_proposal_dict.get("clarification_question"),
        )
        return self.gate.evaluate_proposal(context, proposal)
