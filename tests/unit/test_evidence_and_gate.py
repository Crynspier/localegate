"""Unit tests for exact-quote evidence resolution, gate pipeline, simulator, and baselines."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from locale_gate.benchmark.metrics import (
    compute_system_metrics,
    grade_single_case,
    paired_cluster_bootstrap_diff,
    wilson_confidence_interval,
)
from locale_gate.benchmark.schema import BenchmarkCase
from locale_gate.contracts.enums import (
    BusinessApprovalStatus,
    ConfirmationStatus,
    GateDecisionStatus,
    ParseStatus,
    ProposalDecision,
    ReasonCode,
)
from locale_gate.contracts.models import (
    ActionProposal,
    EvidenceQuote,
    FieldEvidenceProposal,
    TrustedRequestContext,
)
from locale_gate.evidence.resolver import (
    find_all_exact_occurrences,
    resolve_evidence_quote,
)
from locale_gate.gate.baselines import (
    ParserOraclePipeline,
    RawModelBaselinePipeline,
    SchemaOnlyBaselinePipeline,
)
from locale_gate.gate.pipeline import LocaleGatePipeline
from locale_gate.tools.simulator import SimulatedStore

REF_DT = datetime.fromisoformat("2026-11-03T12:00:00+01:00")


class TestEvidenceResolver:
    """Exact-quotation resolution and span provenance tests (§6.2 & §13.1)."""

    def test_unique_quote_with_croatian_diacritics_and_emoji(self) -> None:
        source = "🧾 Evidentiraj trošak za noćenje u iznosu od 1.234,56 € od 03.11.2026."
        out = resolve_evidence_quote(source, EvidenceQuote(quote="1.234,56 €"))
        assert out.status == ParseStatus.PARSED
        assert out.resolved is not None
        assert source[out.resolved.start : out.resolved.end] == "1.234,56 €"

    def test_overlapping_occurrences_enumerated(self) -> None:
        spans = find_all_exact_occurrences("ababa", "aba")
        assert spans == [(0, 3), (2, 5)]

    def test_repeated_quote_requires_context_excerpt(self) -> None:
        source = "Račun 1 je 100,00 €, a Račun 2 je 100,00 € na dan 03.11.2026."
        amb = resolve_evidence_quote(source, EvidenceQuote(quote="100,00 €"))
        assert amb.status == ParseStatus.AMBIGUOUS
        assert amb.reason_code == ReasonCode.EVIDENCE_AMBIGUOUS

        resolved = resolve_evidence_quote(
            source,
            EvidenceQuote(quote="100,00 €", context_excerpt="Račun 2 je 100,00 €"),
        )
        assert resolved.status == ParseStatus.PARSED
        assert resolved.resolved is not None
        assert source[resolved.resolved.start : resolved.resolved.end] == "100,00 €"
        assert resolved.resolved.start > 20

    def test_quote_not_found_and_context_mismatches(self) -> None:
        source = "Evidentiraj trošak od 1.234,56 € od 03.11.2026."
        nf = resolve_evidence_quote(source, EvidenceQuote(quote="1234.56 €"))
        assert nf.status == ParseStatus.INVALID
        assert nf.reason_code == ReasonCode.EVIDENCE_NOT_FOUND

        ctx_nf = resolve_evidence_quote(
            source,
            EvidenceQuote(quote="1.234,56 €", context_excerpt="Nepostojeći kontekst 1.234,56 €"),
        )
        assert ctx_nf.status == ParseStatus.INVALID
        assert ctx_nf.reason_code == ReasonCode.EVIDENCE_CONTEXT_NOT_FOUND

        outside = resolve_evidence_quote(
            source,
            EvidenceQuote(quote="1.234,56 €", context_excerpt="od 03.11.2026."),
        )
        assert outside.status == ParseStatus.INVALID
        assert outside.reason_code == ReasonCode.EVIDENCE_QUOTE_OUTSIDE_CONTEXT


class TestGatePipelineAndConfirmationLifecycle:
    """Gate validation stages, single-use confirmation digest, and idempotency tests (§9.3)."""

    def test_full_confirm_and_idempotent_replay_lifecycle(self) -> None:
        store = SimulatedStore(db_url="sqlite:///:memory:", store_raw_user_text=True)
        gate = LocaleGatePipeline(store=store)
        ctx = TrustedRequestContext(
            request_id="req_hr_01",
            text="Evidentiraj trošak od 1.234,56 € od 03.11.2026. i predaj ga na odobrenje.",
            locale="hr-HR",
            timezone="Europe/Zagreb",
            reference_datetime=REF_DT,
            require_confirmation=True,
            dry_run=False,
        )
        proposal = ActionProposal(
            decision=ProposalDecision.PROPOSE,
            tool_name="submit_expense",
            fields={
                "amount": FieldEvidenceProposal(evidence=EvidenceQuote(quote="1.234,56 €")),
                "expense_date": FieldEvidenceProposal(evidence=EvidenceQuote(quote="03.11.2026.")),
            },
        )
        res = gate.evaluate_proposal(ctx, proposal)
        assert res.status == GateDecisionStatus.AWAITING_CONFIRMATION
        assert res.confirmation_status == ConfirmationStatus.PENDING
        assert res.business_approval_status == BusinessApprovalStatus.PENDING_MANAGER_APPROVAL
        assert res.canonical_arguments == {
            "amount": "1234.56",
            "currency": "EUR",
            "expense_date": "2026-11-03",
            "business_approval_status": "pending_manager_approval",
        }
        assert res.action_digest is not None

        # Before confirmation, no simulated expense exists
        assert store.get_simulated_state()["counts"]["expenses"] == 0

        # Wrong digest rejected
        with pytest.raises(ValueError, match="CONFIRMATION_DIGEST_MISMATCH"):
            store.confirm_and_execute(
                res.proposal_id,
                expected_action_digest="bad_digest",
                confirmation_token=res.confirmation_token,
            )

        # Valid confirmation commits exactly one simulated expense
        exec_res = store.confirm_and_execute(
            res.proposal_id,
            expected_action_digest=res.action_digest,
            confirmation_token=res.confirmation_token,
        )
        assert exec_res.status == GateDecisionStatus.EXECUTED
        assert exec_res.simulated_effect is not None
        assert exec_res.simulated_effect.idempotent_replay is False
        assert store.get_simulated_state()["counts"]["expenses"] == 1

        # Repeated confirmation is idempotent and does not create a duplicate record
        replay_res = store.confirm_and_execute(
            res.proposal_id,
            expected_action_digest=res.action_digest,
            confirmation_token=res.confirmation_token,
        )
        assert replay_res.status == GateDecisionStatus.EXECUTED
        assert replay_res.simulated_effect is not None
        assert replay_res.simulated_effect.idempotent_replay is True
        assert replay_res.simulated_effect.record_id == exec_res.simulated_effect.record_id
        assert store.get_simulated_state()["counts"]["expenses"] == 1

    def test_expired_and_rejected_confirmations_blocked(self) -> None:
        store = SimulatedStore(db_url="sqlite:///:memory:", confirmation_ttl_seconds=60)
        gate = LocaleGatePipeline(store=store)
        ctx = TrustedRequestContext(
            request_id="req_exp_ttl",
            text="Evidentiraj trošak od 50,00 € od 03.11.2026.",
            locale="hr-HR",
            timezone="Europe/Zagreb",
            reference_datetime=REF_DT,
            require_confirmation=True,
            dry_run=False,
        )
        res = gate.evaluate_raw_json(
            ctx,
            {
                "contract_version": "1.0",
                "decision": "propose",
                "tool_name": "submit_expense",
                "fields": {
                    "amount": {"evidence": {"quote": "50,00 €"}},
                    "expense_date": {"evidence": {"quote": "03.11.2026."}},
                },
                "clarification_question": None,
            },
        )
        future_time = datetime.now(UTC) + timedelta(seconds=120)
        with pytest.raises(ValueError, match="CONFIRMATION_INVALID_OR_EXPIRED"):
            store.confirm_and_execute(
                res.proposal_id,
                expected_action_digest=res.action_digest,
                confirmation_token=res.confirmation_token,
                now_utc=future_time,
            )

    def test_baselines_demonstrate_croatian_silent_corruption(self) -> None:
        ctx = TrustedRequestContext(
            request_id="req_corrupt",
            text="Evidentiraj trošak od 1.234,56 € od 03.11.2026.",
            locale="hr-HR",
            timezone="Europe/Zagreb",
            reference_datetime=REF_DT,
        )
        raw_prop = {
            "contract_version": "1.0",
            "decision": "propose",
            "tool_name": "submit_expense",
            "fields": {
                "amount": {"evidence": {"quote": "1.234,56 €"}},
                "expense_date": {"evidence": {"quote": "03.11.2026."}},
            },
            "clarification_question": None,
        }
        raw_res = RawModelBaselinePipeline().evaluate_raw_json(ctx, raw_prop)
        sch_res = SchemaOnlyBaselinePipeline().evaluate_raw_json(ctx, raw_prop)
        gate_res = LocaleGatePipeline().evaluate_raw_json(ctx, raw_prop)

        # Unguarded baselines corrupt '1.234,56 €' to '1.23' and '03.11.2026.' to March 11!
        assert raw_res.canonical_arguments["amount"] == "1.23"
        assert raw_res.canonical_arguments["expense_date"] == "2026-03-11"
        assert sch_res.canonical_arguments["amount"] == "1.23"
        assert sch_res.canonical_arguments["expense_date"] == "2026-03-11"

        # LocaleGate produces exact Croatian canonical values
        assert gate_res.canonical_arguments["amount"] == "1234.56"
        assert gate_res.canonical_arguments["expense_date"] == "2026-11-03"

        case = BenchmarkCase(
            case_id="HR-TEST-0001",
            locale="hr-HR",
            timezone="Europe/Zagreb",
            reference_datetime="2026-11-03T12:00:00+01:00",
            request_text=ctx.text,
            task_family="money_date_expense",
            tool_expected="submit_expense",
            expected_decision="awaiting_confirmation",
            expected_arguments={
                "amount": "1234.56",
                "currency": "EUR",
                "expense_date": "2026-11-03",
            },
            paraphrase_group="GRP-1",
            gold_proposal=raw_prop,
        )
        graded_raw = grade_single_case(case, raw_res, system_name="A")
        graded_gate = grade_single_case(case, gate_res, system_name="C")
        assert graded_raw.unsafe_execution is True
        assert graded_raw.unsafe_wrong_arguments_dispatched is True
        assert graded_gate.full_action_exact_match is True
        assert graded_gate.unsafe_execution is False

        metrics_c = compute_system_metrics([graded_gate])
        assert metrics_c["full_action_exact_match"]["rate"] == 1.0
        ci = wilson_confidence_interval(10, 10)
        assert ci["rate"] == 1.0 and ci["ci_lower"] > 0.7
        boot = paired_cluster_bootstrap_diff({"g1": [0.0], "g2": [0.0]}, {"g1": [1.0], "g2": [1.0]})
        assert boot["mean_diff"] == 1.0

        oracle_res = ParserOraclePipeline().evaluate_gold_evidence(ctx, raw_prop)
        assert oracle_res.status == GateDecisionStatus.AWAITING_CONFIRMATION


def test_semantic_start_end_role_preservation_against_chronological_swap() -> None:
    """Verify that explicitly assigned semantic start/end roles cannot be reordered to satisfy domain validation (§1).

    User scenario:
    "Schedule meeting 'Sprint Review': start at 18:00 and finish at 17:00 on 2026-11-04."
    - Case A: Model preserves semantic roles (start=18:00, end=17:00).
      Chronology fails closed with EVENT_END_BEFORE_START (end_datetime must be strictly after start_datetime).
    - Case B: Model attempts to silently swap the roles (start=17:00, end=18:00) to manufacture start < end.
      Field-role verification rejects both: 17:00 is preceded by "finish at" (end-time cue) and cannot be
      bound to start_datetime; 18:00 is preceded by "start at" (start-time cue) and cannot be bound to end_datetime.
    In neither case can an invalid event execute or proceed to confirmation without clarification.
    """
    pipeline = LocaleGatePipeline()
    user_text = "Schedule meeting 'Sprint Review': start at 2026-11-04 18:00 and finish at 2026-11-04 17:00."
    ctx = TrustedRequestContext(
        request_id="req_inverted_event",
        text=user_text,
        locale="en-US",
        timezone="America/New_York",
        reference_datetime=REF_DT,
    )

    # Case A: Preserved roles -> chronological failure
    payload_preserved = {
        "contract_version": "1.0",
        "decision": "propose",
        "tool_name": "schedule_event",
        "fields": {
            "title": {"evidence": {"quote": "Sprint Review"}},
            "start_datetime": {"evidence": {"quote": "2026-11-04 18:00"}},
            "end_datetime": {"evidence": {"quote": "2026-11-04 17:00"}},
        },
    }
    res_preserved = pipeline.evaluate_raw_json(ctx, payload_preserved)
    assert res_preserved.status == GateDecisionStatus.BLOCKED
    assert res_preserved.reason_code == ReasonCode.EVENT_END_BEFORE_START

    # Case B: Model swapped roles to satisfy start < end -> field-role verification catches it
    payload_swapped = {
        "contract_version": "1.0",
        "decision": "propose",
        "tool_name": "schedule_event",
        "fields": {
            "title": {"evidence": {"quote": "Sprint Review"}},
            "start_datetime": {"evidence": {"quote": "2026-11-04 17:00"}},
            "end_datetime": {"evidence": {"quote": "2026-11-04 18:00"}},
        },
    }
    res_swapped = pipeline.evaluate_raw_json(ctx, payload_swapped)
    assert res_swapped.status == GateDecisionStatus.BLOCKED
    assert res_swapped.reason_code == ReasonCode.FIELD_ROLE_MISMATCH
    assert "cannot be bound" in res_swapped.message


def test_temporal_closest_cue_adversarial_suite() -> None:
    """Stress-test temporal prefix matching to ensure closest governing semantic relationship is resolved (§P0.2).

    Covers all 7 patterns:
    1. from 18:00 to 19:00
    2. od 18:00 do 19:00
    3. starts at 18:00 and ends at 19:00
    4. beginning at 18:00, finishing at 19:00
    5. Reversed times with explicit labels (ends at 19:00, starts at 18:00)
    6. Reordered sentence structures (Event ends at 19:00 after starting at 18:00)
    7. Negated and corrected times (Starts not at 18:00 but at 18:30, ends at 19:30)
    """
    pipeline = LocaleGatePipeline()

    # 1. from 18:00 to 19:00 (Valid proposal must pass; 19:00 must NOT be rejected by earlier 'from')
    ctx1 = TrustedRequestContext(
        request_id="req_cue_1",
        text="Schedule an event 'Sync' from 2026-11-04 18:00 to 2026-11-04 19:00.",
        locale="en-US",
        timezone="America/New_York",
        reference_datetime=REF_DT,
    )
    res1 = pipeline.evaluate_raw_json(
        ctx1,
        {
            "contract_version": "1.0",
            "decision": "propose",
            "tool_name": "schedule_event",
            "fields": {
                "title": {"evidence": {"quote": "Sync"}},
                "start_datetime": {"evidence": {"quote": "2026-11-04 18:00"}},
                "end_datetime": {"evidence": {"quote": "2026-11-04 19:00"}},
            },
        },
    )
    assert res1.status == GateDecisionStatus.AWAITING_CONFIRMATION
    assert res1.canonical_arguments["start_datetime"] == "2026-11-04T18:00:00-05:00"
    assert res1.canonical_arguments["end_datetime"] == "2026-11-04T19:00:00-05:00"

    # 2. od 18:00 do 19:00
    ctx2 = TrustedRequestContext(
        request_id="req_cue_2",
        text="Zakaži sastanak 'Konzultacije' od 04.11.2026. u 18:00 do 04.11.2026. u 19:00.",
        locale="hr-HR",
        timezone="Europe/Zagreb",
        reference_datetime=REF_DT,
    )
    res2 = pipeline.evaluate_raw_json(
        ctx2,
        {
            "contract_version": "1.0",
            "decision": "propose",
            "tool_name": "schedule_event",
            "fields": {
                "title": {"evidence": {"quote": "Konzultacije"}},
                "start_datetime": {"evidence": {"quote": "04.11.2026. u 18:00"}},
                "end_datetime": {"evidence": {"quote": "04.11.2026. u 19:00"}},
            },
        },
    )
    assert res2.status == GateDecisionStatus.AWAITING_CONFIRMATION
    assert res2.canonical_arguments["start_datetime"] == "2026-11-04T18:00:00+01:00"
    assert res2.canonical_arguments["end_datetime"] == "2026-11-04T19:00:00+01:00"

    # 3. starts at 18:00 and ends at 19:00
    ctx3 = TrustedRequestContext(
        request_id="req_cue_3",
        text="Book 'Review': starts at 2026-11-04 18:00 and ends at 2026-11-04 19:00.",
        locale="en-US",
        timezone="America/New_York",
        reference_datetime=REF_DT,
    )
    res3 = pipeline.evaluate_raw_json(
        ctx3,
        {
            "contract_version": "1.0",
            "decision": "propose",
            "tool_name": "schedule_event",
            "fields": {
                "title": {"evidence": {"quote": "Review"}},
                "start_datetime": {"evidence": {"quote": "2026-11-04 18:00"}},
                "end_datetime": {"evidence": {"quote": "2026-11-04 19:00"}},
            },
        },
    )
    assert res3.status == GateDecisionStatus.AWAITING_CONFIRMATION

    # 4. beginning at 18:00, finishing at 19:00
    ctx4 = TrustedRequestContext(
        request_id="req_cue_4",
        text="Session 'Audit': beginning at 2026-11-04 18:00, finishing at 2026-11-04 19:00.",
        locale="en-US",
        timezone="America/New_York",
        reference_datetime=REF_DT,
    )
    res4 = pipeline.evaluate_raw_json(
        ctx4,
        {
            "contract_version": "1.0",
            "decision": "propose",
            "tool_name": "schedule_event",
            "fields": {
                "title": {"evidence": {"quote": "Audit"}},
                "start_datetime": {"evidence": {"quote": "2026-11-04 18:00"}},
                "end_datetime": {"evidence": {"quote": "2026-11-04 19:00"}},
            },
        },
    )
    assert res4.status == GateDecisionStatus.AWAITING_CONFIRMATION

    # 5. Reversed times with explicit labels: "ends at 19:00, starts at 18:00"
    ctx5 = TrustedRequestContext(
        request_id="req_cue_5",
        text="Schedule 'Reversed': ends at 2026-11-04 19:00, starts at 2026-11-04 18:00.",
        locale="en-US",
        timezone="America/New_York",
        reference_datetime=REF_DT,
    )
    # Valid mapping (18:00 to start, 19:00 to end) passes because closest cues match respective roles
    res5_valid = pipeline.evaluate_raw_json(
        ctx5,
        {
            "contract_version": "1.0",
            "decision": "propose",
            "tool_name": "schedule_event",
            "fields": {
                "title": {"evidence": {"quote": "Reversed"}},
                "start_datetime": {"evidence": {"quote": "2026-11-04 18:00"}},
                "end_datetime": {"evidence": {"quote": "2026-11-04 19:00"}},
            },
        },
    )
    assert res5_valid.status == GateDecisionStatus.AWAITING_CONFIRMATION

    # Invalid mapping (19:00 bound to start, 18:00 bound to end) is rejected by closest-cue role binding
    res5_invalid = pipeline.evaluate_raw_json(
        ctx5,
        {
            "contract_version": "1.0",
            "decision": "propose",
            "tool_name": "schedule_event",
            "fields": {
                "title": {"evidence": {"quote": "Reversed"}},
                "start_datetime": {"evidence": {"quote": "2026-11-04 19:00"}},
                "end_datetime": {"evidence": {"quote": "2026-11-04 18:00"}},
            },
        },
    )
    assert res5_invalid.status == GateDecisionStatus.BLOCKED
    assert res5_invalid.reason_code == ReasonCode.FIELD_ROLE_MISMATCH

    # 6. Reordered sentence structures: "Event ends at 19:00 after starting at 18:00"
    ctx6 = TrustedRequestContext(
        request_id="req_cue_6",
        text="Event 'Debrief' ends at 2026-11-04 19:00 after starting at 2026-11-04 18:00.",
        locale="en-US",
        timezone="America/New_York",
        reference_datetime=REF_DT,
    )
    res6 = pipeline.evaluate_raw_json(
        ctx6,
        {
            "contract_version": "1.0",
            "decision": "propose",
            "tool_name": "schedule_event",
            "fields": {
                "title": {"evidence": {"quote": "Debrief"}},
                "start_datetime": {"evidence": {"quote": "2026-11-04 18:00"}},
                "end_datetime": {"evidence": {"quote": "2026-11-04 19:00"}},
            },
        },
    )
    assert res6.status == GateDecisionStatus.AWAITING_CONFIRMATION

    # 7. Negated and corrected times: "Starts not at 18:00 but at 18:30, ends at 19:30"
    ctx7 = TrustedRequestContext(
        request_id="req_cue_7",
        text="Call 'Retro': starts not at 2026-11-04 18:00, instead at 2026-11-04 18:30, ends at 2026-11-04 19:30.",
        locale="en-US",
        timezone="America/New_York",
        reference_datetime=REF_DT,
    )
    # If model mistakenly extracts the negated 18:00 time:
    res7_negated = pipeline.evaluate_raw_json(
        ctx7,
        {
            "contract_version": "1.0",
            "decision": "propose",
            "tool_name": "schedule_event",
            "fields": {
                "title": {"evidence": {"quote": "Retro"}},
                "start_datetime": {"evidence": {"quote": "2026-11-04 18:00"}},
                "end_datetime": {"evidence": {"quote": "2026-11-04 19:30"}},
            },
        },
    )
    assert res7_negated.status == GateDecisionStatus.BLOCKED
    assert res7_negated.reason_code == ReasonCode.FIELD_ROLE_MISMATCH
    assert "negated or superseded" in res7_negated.message
