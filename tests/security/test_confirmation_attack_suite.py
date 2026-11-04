"""Dedicated confirmation attack suite (§P1.5 & §P1.6).

Tests the authorization boundary under adversarial confirmation attempts:
- Missing bearer capability tokens when required
- Incorrect / forged bearer capability tokens
- Modified / tampered action digests
- Expired tokens past TTL
- Cross-request token confusion (token from request A applied to request B)
- Unauthenticated replay attempts (calling confirm without token after initial execution)
- Forged token replay attempts
- Tampered digest replay attempts
- Verification of zero state mutation and zero duplicate database commits on replay
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from locale_gate.contracts.enums import GateDecisionStatus, ReasonCode
from locale_gate.contracts.models import GateResult, TrustedRequestContext
from locale_gate.gate.pipeline import LocaleGatePipeline
from locale_gate.tools.simulator import SimulatedStore

REF_DT = datetime.fromisoformat("2026-11-03T12:00:00+01:00")


def _create_pending_proposal(
    store: SimulatedStore,
    request_id: str,
    amount_str: str = "120,00 €",
    ttl_seconds: int = 900,
) -> tuple[TrustedRequestContext, GateResult]:
    gate = LocaleGatePipeline(store=store)
    ctx = TrustedRequestContext(
        request_id=request_id,
        text=f"Evidentiraj trošak od {amount_str} od 03.11.2026.",
        locale="hr-HR",
        timezone="Europe/Zagreb",
        reference_datetime=REF_DT,
        require_confirmation=True,
    )
    raw_payload = {
        "contract_version": "1.0",
        "decision": "propose",
        "tool_name": "submit_expense",
        "fields": {
            "amount": {"evidence": {"quote": amount_str}},
            "expense_date": {"evidence": {"quote": "03.11.2026."}},
        },
    }
    res = gate.evaluate_raw_json(ctx, raw_payload)
    assert res.status == GateDecisionStatus.AWAITING_CONFIRMATION
    assert res.confirmation_token is not None
    assert res.action_digest is not None
    return ctx, res


class TestConfirmationAttackSuite:
    """Rigorous tests specifically targeting the confirmation authorization boundary."""

    def test_attack_missing_token_when_required(self) -> None:
        """Attack vector 1: Caller omits the bearer capability token when token is required."""
        store = SimulatedStore(db_url="sqlite:///:memory:")
        _, res = _create_pending_proposal(store, "req_attack_missing_tok")

        with pytest.raises(ValueError) as exc:
            store.confirm_and_execute(
                res.proposal_id,
                expected_action_digest=res.action_digest,
                require_token=True,
                confirmation_token=None,
            )
        assert ReasonCode.CONFIRMATION_TOKEN_INVALID.value in str(exc.value)
        assert store.get_simulated_state()["counts"]["expenses"] == 0

    def test_attack_incorrect_token(self) -> None:
        """Attack vector 2: Caller supplies an invalid/forged bearer capability token."""
        store = SimulatedStore(db_url="sqlite:///:memory:")
        _, res = _create_pending_proposal(store, "req_attack_bad_tok")

        with pytest.raises(ValueError) as exc:
            store.confirm_and_execute(
                res.proposal_id,
                expected_action_digest=res.action_digest,
                confirmation_token="forged_token_0123456789abcdef0123456789abcdef",
            )
        assert ReasonCode.CONFIRMATION_TOKEN_INVALID.value in str(exc.value)
        assert store.get_simulated_state()["counts"]["expenses"] == 0

    def test_attack_modified_digest(self) -> None:
        """Attack vector 3: Caller presents a tampered action digest."""
        store = SimulatedStore(db_url="sqlite:///:memory:")
        _, res = _create_pending_proposal(store, "req_attack_bad_digest")

        tampered_digest = "tampered_digest_99999999999999999999999999999999"
        with pytest.raises(ValueError) as exc:
            store.confirm_and_execute(
                res.proposal_id,
                expected_action_digest=tampered_digest,
                confirmation_token=res.confirmation_token,
            )
        assert ReasonCode.CONFIRMATION_DIGEST_MISMATCH.value in str(exc.value)
        assert store.get_simulated_state()["counts"]["expenses"] == 0

    def test_attack_expired_token(self) -> None:
        """Attack vector 4: Caller attempts confirmation after TTL expiration."""
        store = SimulatedStore(db_url="sqlite:///:memory:", confirmation_ttl_seconds=60)
        _, res = _create_pending_proposal(store, "req_attack_expired", ttl_seconds=60)

        # Fast forward time past 60s expiration window
        future_time = datetime.now(UTC) + timedelta(seconds=120)
        with pytest.raises(ValueError) as exc:
            store.confirm_and_execute(
                res.proposal_id,
                expected_action_digest=res.action_digest,
                confirmation_token=res.confirmation_token,
                now_utc=future_time,
            )
        assert ReasonCode.CONFIRMATION_INVALID_OR_EXPIRED.value in str(exc.value)
        assert store.get_simulated_state()["counts"]["expenses"] == 0

    def test_attack_cross_request_token(self) -> None:
        """Attack vector 5: Caller attempts to use a valid capability token issued for proposal A on proposal B."""
        store = SimulatedStore(db_url="sqlite:///:memory:")
        _, res_a = _create_pending_proposal(store, "req_attack_cross_a", amount_str="100,00 €")
        _, res_b = _create_pending_proposal(store, "req_attack_cross_b", amount_str="200,00 €")

        assert res_a.confirmation_token != res_b.confirmation_token

        # Try confirming proposal B using token A
        with pytest.raises(ValueError) as exc:
            store.confirm_and_execute(
                res_b.proposal_id,
                expected_action_digest=res_b.action_digest,
                confirmation_token=res_a.confirmation_token,
            )
        assert ReasonCode.CONFIRMATION_TOKEN_INVALID.value in str(exc.value)
        assert store.get_simulated_state()["counts"]["expenses"] == 0

    def test_attack_unauthenticated_replay_protection(self) -> None:
        """Attack vector 6: Unauthenticated caller attempts to replay after first execution to read result."""
        store = SimulatedStore(db_url="sqlite:///:memory:")
        _, res = _create_pending_proposal(store, "req_attack_unauth_replay")

        # Step 1: Legitimate caller executes confirmation
        exec_res = store.confirm_and_execute(
            res.proposal_id,
            expected_action_digest=res.action_digest,
            confirmation_token=res.confirmation_token,
        )
        assert exec_res.status == GateDecisionStatus.EXECUTED
        assert store.get_simulated_state()["counts"]["expenses"] == 1

        # Step 2: Unauthenticated caller calls confirm_and_execute omitting the bearer token
        with pytest.raises(ValueError) as exc:
            store.confirm_and_execute(
                res.proposal_id,
                expected_action_digest=res.action_digest,
                confirmation_token=None,
                require_token=True,
            )
        assert ReasonCode.CONFIRMATION_TOKEN_INVALID.value in str(exc.value)
        assert "Replay request rejected" in str(exc.value)
        # Ensure zero state mutation occurred
        assert store.get_simulated_state()["counts"]["expenses"] == 1

    def test_attack_forged_token_replay_protection(self) -> None:
        """Attack vector 7: Caller attempts replay with an incorrect/forged bearer token."""
        store = SimulatedStore(db_url="sqlite:///:memory:")
        _, res = _create_pending_proposal(store, "req_attack_forged_replay")

        # Step 1: Legitimate execution
        store.confirm_and_execute(
            res.proposal_id,
            expected_action_digest=res.action_digest,
            confirmation_token=res.confirmation_token,
        )

        # Step 2: Attacker attempts replay with a forged token
        with pytest.raises(ValueError) as exc:
            store.confirm_and_execute(
                res.proposal_id,
                expected_action_digest=res.action_digest,
                confirmation_token="forged_attacker_token_xyz987654321",
            )
        assert ReasonCode.CONFIRMATION_TOKEN_INVALID.value in str(exc.value)
        assert "Replay request rejected" in str(exc.value)
        assert store.get_simulated_state()["counts"]["expenses"] == 1

    def test_attack_tampered_digest_replay_protection(self) -> None:
        """Attack vector 8: Caller attempts replay with a tampered action digest."""
        store = SimulatedStore(db_url="sqlite:///:memory:")
        _, res = _create_pending_proposal(store, "req_attack_digest_replay")

        # Step 1: Legitimate execution
        store.confirm_and_execute(
            res.proposal_id,
            expected_action_digest=res.action_digest,
            confirmation_token=res.confirmation_token,
        )

        # Step 2: Replay with wrong digest
        with pytest.raises(ValueError) as exc:
            store.confirm_and_execute(
                res.proposal_id,
                expected_action_digest="wrong_digest_value_1234567890",
                confirmation_token=res.confirmation_token,
            )
        assert ReasonCode.CONFIRMATION_DIGEST_MISMATCH.value in str(exc.value)
        assert store.get_simulated_state()["counts"]["expenses"] == 1

    def test_legitimate_authenticated_replay_zero_state_mutation(self) -> None:
        """Verify that legitimate authenticated replay returns idempotent_replay=True with zero side effects."""
        store = SimulatedStore(db_url="sqlite:///:memory:")
        _, res = _create_pending_proposal(store, "req_legit_replay")

        # Initial execution
        exec_1 = store.confirm_and_execute(
            res.proposal_id,
            expected_action_digest=res.action_digest,
            confirmation_token=res.confirmation_token,
        )
        assert exec_1.status == GateDecisionStatus.EXECUTED
        assert exec_1.simulated_effect is not None
        assert exec_1.simulated_effect.idempotent_replay is False
        assert store.get_simulated_state()["counts"]["expenses"] == 1

        # Replay with identical valid credentials
        exec_2 = store.confirm_and_execute(
            res.proposal_id,
            expected_action_digest=res.action_digest,
            confirmation_token=res.confirmation_token,
        )
        assert exec_2.status == GateDecisionStatus.EXECUTED
        assert exec_2.simulated_effect is not None
        assert exec_2.simulated_effect.idempotent_replay is True
        assert exec_2.simulated_effect.record_id == exec_1.simulated_effect.record_id
        # Crucial check: still exactly 1 expense committed (zero duplicate database commits)
        assert store.get_simulated_state()["counts"]["expenses"] == 1

    def test_attack_inter_process_cancellation_race(self) -> None:
        """Attack vector 9: Rejection race: User cancels proposal, concurrent confirmation must fail."""
        store = SimulatedStore(db_url="sqlite:///:memory:")
        _, res = _create_pending_proposal(store, "req_attack_cancel_race")

        # User cancels/rejects the proposal
        rej_res = store.reject_pending(res.proposal_id)
        assert rej_res.confirmation_status.value == "rejected"

        # Attacker attempts to confirm the cancelled proposal with legitimate credentials
        with pytest.raises(ValueError) as exc:
            store.confirm_and_execute(
                res.proposal_id,
                expected_action_digest=res.action_digest,
                confirmation_token=res.confirmation_token,
            )
        assert ReasonCode.CONFIRMATION_REJECTED.value in str(exc.value)
        assert store.get_simulated_state()["counts"]["expenses"] == 0

    def test_process_restart_preserves_token_security(self, tmp_path: Path) -> None:
        """Attack vector 10: Process restart / reload preserves cryptographic token boundary."""
        db_path = tmp_path / "restart_test.db"
        store1 = SimulatedStore(
            db_url=f"sqlite:///{db_path.as_posix()}", confirmation_ttl_seconds=300
        )
        _, res = _create_pending_proposal(store1, "req_restart_token")

        # Simulate process termination by instantiating clean store on same file
        del store1
        store2 = SimulatedStore(
            db_url=f"sqlite:///{db_path.as_posix()}", confirmation_ttl_seconds=300
        )

        # 1. Missing token must fail
        with pytest.raises(ValueError) as exc:
            store2.confirm_and_execute(
                res.proposal_id,
                expected_action_digest=res.action_digest,
                confirmation_token=None,
            )
        assert ReasonCode.CONFIRMATION_TOKEN_INVALID.value in str(exc.value)

        # 2. Forged token must fail
        with pytest.raises(ValueError) as exc:
            store2.confirm_and_execute(
                res.proposal_id,
                expected_action_digest=res.action_digest,
                confirmation_token="forged_token_across_process_restart",
            )
        assert ReasonCode.CONFIRMATION_TOKEN_INVALID.value in str(exc.value)

        # 3. Legitimate token succeeds across restart
        exec_res = store2.confirm_and_execute(
            res.proposal_id,
            expected_action_digest=res.action_digest,
            confirmation_token=res.confirmation_token,
        )
        assert exec_res.status == GateDecisionStatus.EXECUTED
        assert store2.get_simulated_state()["counts"]["expenses"] == 1

        # 4. Audit chain verifies cleanly
        valid_chain, count = store2.verify_audit_chain()
        assert valid_chain is True
        assert count >= 2
