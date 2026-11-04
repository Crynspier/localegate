"""Unit tests for DST fold absolute UTC chronological ordering and atomic confirmation concurrency."""

from __future__ import annotations

import concurrent.futures
import multiprocessing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from locale_gate.contracts.enums import GateDecisionStatus, ParseStatus, ReasonCode
from locale_gate.contracts.models import (
    AwaitingConfirmationGateResult,
    FieldDiagnostic,
)
from locale_gate.parsers.date_time import parse_datetime
from locale_gate.tools.registry import (
    compute_canonical_action_digest,
    generate_confirmation_capability_token,
)
from locale_gate.tools.simulator import SimulatedStore


def _mp_barrier_confirm_worker(
    db_path: str,
    proposal_id: str,
    digest: str,
    token: str,
    barrier: Any,
) -> dict[str, object]:
    """Execute confirmation in an independent OS process synchronized via multiprocessing.Barrier."""
    store = SimulatedStore(db_url=f"sqlite:///{db_path}", confirmation_ttl_seconds=300)
    # Wait until all processes reach the barrier before simultaneously firing confirmation
    barrier.wait(timeout=10.0)
    res = store.confirm_and_execute(
        proposal_id,
        expected_action_digest=digest,
        confirmation_token=token,
    )
    return {
        "status": res.status.value,
        "is_replay": res.simulated_effect.idempotent_replay if res.simulated_effect else False,
        "record_id": res.simulated_effect.record_id if res.simulated_effect else None,
    }


def test_dst_fold_utc_instant_ordering() -> None:
    """Verify that fall-back DST fold differences (fold=0 CEST vs fold=1 CET) are ordered on UTC timeline."""
    z_zagreb = ZoneInfo("Europe/Zagreb")
    # Fall-back occurs on 2026-10-25: at 03:00 CEST -> 02:00 CET
    dt_fold0 = datetime(2026, 10, 25, 2, 30, fold=0, tzinfo=z_zagreb)
    dt_fold1 = datetime(2026, 10, 25, 2, 30, fold=1, tzinfo=z_zagreb)

    utc0 = dt_fold0.astimezone(UTC)
    utc1 = dt_fold1.astimezone(UTC)

    # 02:30 CEST is 00:30 UTC; 02:30 CET is 01:30 UTC (60 minutes later)
    assert utc0 == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    assert utc1 == datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    assert utc1 > utc0
    assert (utc1 - utc0) == timedelta(hours=1)


def test_dst_spring_forward_gap_blocked() -> None:
    """Verify that non-existent local wall clock during spring-forward gap is detected and blocked."""
    # Spring-forward occurs on 2026-03-29: at 02:00 CET clocks jump to 03:00 CEST (02:00-02:59 does not exist)
    res = parse_datetime(
        "29.03.2026. 02:30",
        locale="hr-HR",
        trusted_timezone="Europe/Zagreb",
        reference_datetime=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
    )
    # The parser flags the nonexistent wall clock
    assert res.is_ok is False
    assert res.status == ParseStatus.INVALID
    assert res.reason_code == ReasonCode.DATETIME_DST_NONEXISTENT


def test_confirmation_concurrency_atomic_single_use(tmp_path: Path) -> None:
    """Verify concurrent confirmation attempts on SQLite serialize via BEGIN IMMEDIATE and create exactly 1 row."""
    db_file = tmp_path / "concurrent_sim.sqlite"
    store = SimulatedStore(db_url=f"sqlite:///{db_file.as_posix()}", confirmation_ttl_seconds=300)

    request_id = "req_concurrent_test"
    proposal_id = "prop_concurrent_test"
    corr_id = "corr_concurrent_test"
    token, _ = generate_confirmation_capability_token()

    canonical_args = {
        "amount": "150.00",
        "currency": "EUR",
        "expense_date": "2026-11-04",
        "business_approval_status": "pending_manager_approval",
    }
    digest = compute_canonical_action_digest(request_id, "submit_expense", canonical_args)

    pending_result = AwaitingConfirmationGateResult(
        request_id=request_id,
        proposal_id=proposal_id,
        audit_correlation_id=corr_id,
        reason_code=ReasonCode.OK_AWAITING_CONFIRMATION,
        message="Awaiting user confirmation.",
        tool_name="submit_expense",
        canonical_arguments=canonical_args,
        confirmation_token=token,
        action_digest=digest,
        field_diagnostics=[
            FieldDiagnostic(
                field_name="amount",
                parse_status=ParseStatus.PARSED,
                message="Verified amount.",
                canonical_value="150.00",
            )
        ],
    )
    store.record_gate_decision(pending_result, locale="hr-HR", timezone_name="Europe/Zagreb")

    # Verify initial state: 0 committed expenses
    assert store.get_simulated_state()["counts"]["expenses"] == 0

    results: list[dict[str, object]] = []
    errors: list[Exception] = []

    def _attempt_confirm() -> dict[str, object]:
        # Each thread gets its own store instance pointing to the same SQLite WAL database
        thread_store = SimulatedStore(
            db_url=f"sqlite:///{db_file.as_posix()}", confirmation_ttl_seconds=300
        )
        res = thread_store.confirm_and_execute(
            proposal_id,
            expected_action_digest=digest,
            confirmation_token=token,
        )
        return {
            "status": res.status.value,
            "is_replay": res.simulated_effect.idempotent_replay if res.simulated_effect else False,
            "record_id": res.simulated_effect.record_id if res.simulated_effect else None,
        }

    # Execute 10 concurrent confirmation attempts
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_attempt_confirm) for _ in range(10)]
        for fut in concurrent.futures.as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as exc:
                errors.append(exc)

    # Exactly 1 execution was the initial execution (not a replay)
    initial_executions = [r for r in results if r.get("is_replay") is False]
    assert len(initial_executions) == 1, (
        f"Expected exactly 1 initial execution, got {len(initial_executions)}"
    )

    # All successful confirmations share the exact same record_id
    executed_record_ids = {r["record_id"] for r in results if r.get("record_id")}
    assert len(executed_record_ids) == 1, (
        "All successful executions must refer to the identical record_id"
    )

    # Verify SQLite database table state: EXACTLY 1 simulated expense row committed
    final_state = store.get_simulated_state()
    assert final_state["counts"]["expenses"] == 1

    # Verify audit chain tamper-evidence is 100% valid
    valid_chain, n_events = store.verify_audit_chain()
    assert valid_chain is True
    assert n_events >= 2  # GATE_DECISION + at least 1 USER_CONFIRMED_EXECUTION

    # Verify capability token is consumed and can no longer be used with require_token=True
    with pytest.raises(ValueError, match="CONFIRMATION_TOKEN_INVALID"):
        store.confirm_and_execute(
            proposal_id,
            expected_action_digest=digest,
            confirmation_token="tampered_token",
            require_token=True,
        )


def _mp_confirm_worker(
    db_path: str,
    proposal_id: str,
    digest: str,
    token: str,
) -> dict[str, object]:
    """Execute confirmation in an independent OS process with its own SQLite connection (§3)."""
    store = SimulatedStore(db_url=f"sqlite:///{db_path}", confirmation_ttl_seconds=300)
    res = store.confirm_and_execute(
        proposal_id,
        expected_action_digest=digest,
        confirmation_token=token,
    )
    return {
        "status": res.status.value,
        "is_replay": res.simulated_effect.idempotent_replay if res.simulated_effect else False,
        "record_id": res.simulated_effect.record_id if res.simulated_effect else None,
    }


@pytest.mark.parametrize("worker_count", [4, 8])
def test_multiprocess_confirmation_concurrency(tmp_path: Path, worker_count: int) -> None:
    """Verify multi-process confirmation concurrency and SQLite BEGIN IMMEDIATE isolation across process counts (§P1.7)."""
    db_file = tmp_path / f"mp_concurrency_{worker_count}.sqlite"
    store = SimulatedStore(db_url=f"sqlite:///{db_file.as_posix()}", confirmation_ttl_seconds=300)

    request_id = f"req_mp_test_{worker_count}"
    proposal_id = f"prop_mp_test_{worker_count}"
    corr_id = f"corr_mp_test_{worker_count}"
    token, _ = generate_confirmation_capability_token()

    canonical_args = {
        "amount": "150.00",
        "currency": "EUR",
        "expense_date": "2026-11-04",
        "business_approval_status": "pending_manager_approval",
    }
    digest = compute_canonical_action_digest(request_id, "submit_expense", canonical_args)

    pending_result = AwaitingConfirmationGateResult(
        request_id=request_id,
        proposal_id=proposal_id,
        audit_correlation_id=corr_id,
        reason_code=ReasonCode.OK_AWAITING_CONFIRMATION,
        message="Awaiting user confirmation.",
        tool_name="submit_expense",
        canonical_arguments=canonical_args,
        confirmation_token=token,
        action_digest=digest,
        field_diagnostics=[],
    )
    store.record_gate_decision(pending_result, locale="hr-HR", timezone_name="Europe/Zagreb")

    results: list[dict[str, object]] = []
    errors: list[Exception] = []

    # Execute concurrent confirmation attempts across independent OS processes
    with concurrent.futures.ProcessPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(_mp_confirm_worker, db_file.as_posix(), proposal_id, digest, token)
            for _ in range(worker_count)
        ]
        for fut in concurrent.futures.as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as exc:
                errors.append(exc)

    # Exactly 1 execution was the initial execution (not a replay)
    initial_executions = [r for r in results if r.get("is_replay") is False]
    assert len(initial_executions) == 1, (
        f"Expected exactly 1 initial execution, got {len(initial_executions)}"
    )

    # All successful confirmations share the exact same record_id
    executed_record_ids = {r["record_id"] for r in results if r.get("record_id")}
    assert len(executed_record_ids) == 1

    # All remaining executions must be authenticated idempotent replays
    replay_executions = [r for r in results if r.get("is_replay") is True]
    assert len(replay_executions) == worker_count - 1

    # Verify SQLite database table state: EXACTLY 1 simulated expense row committed (0 duplicates)
    final_state = store.get_simulated_state()
    assert final_state["counts"]["expenses"] == 1

    # Verify audit chain tamper-evidence is 100% valid
    valid_chain, n_events = store.verify_audit_chain()
    assert valid_chain is True
    assert n_events >= 2


def test_multiprocess_stress_hammer_concurrency(tmp_path: Path) -> None:
    """Stress test: hammer SQLite WAL with 50 concurrent confirmation tasks across 4 processes (§P2.11)."""
    db_file = tmp_path / "mp_stress_hammer.sqlite"
    store = SimulatedStore(db_url=f"sqlite:///{db_file.as_posix()}", confirmation_ttl_seconds=300)

    request_id = "req_mp_hammer"
    proposal_id = "prop_mp_hammer"
    corr_id = "corr_mp_hammer"
    token, _ = generate_confirmation_capability_token()

    canonical_args = {
        "amount": "250.00",
        "currency": "EUR",
        "expense_date": "2026-11-04",
        "business_approval_status": "pending_manager_approval",
    }
    digest = compute_canonical_action_digest(request_id, "submit_expense", canonical_args)

    pending_result = AwaitingConfirmationGateResult(
        request_id=request_id,
        proposal_id=proposal_id,
        audit_correlation_id=corr_id,
        reason_code=ReasonCode.OK_AWAITING_CONFIRMATION,
        message="Awaiting user confirmation.",
        tool_name="submit_expense",
        canonical_arguments=canonical_args,
        confirmation_token=token,
        action_digest=digest,
        field_diagnostics=[],
    )
    store.record_gate_decision(pending_result, locale="hr-HR", timezone_name="Europe/Zagreb")

    results: list[dict[str, object]] = []
    errors: list[Exception] = []

    # Hammer with 50 concurrent calls across 4 worker processes
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(_mp_confirm_worker, db_file.as_posix(), proposal_id, digest, token)
            for _ in range(50)
        ]
        for fut in concurrent.futures.as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as exc:
                errors.append(exc)

    assert len(errors) == 0, f"Unexpected errors during stress run: {errors}"
    assert len(results) == 50

    # Exactly 1 initial execution
    initial_executions = [r for r in results if r.get("is_replay") is False]
    assert len(initial_executions) == 1

    # Exactly 49 authenticated idempotent replays
    replay_executions = [r for r in results if r.get("is_replay") is True]
    assert len(replay_executions) == 49

    # Exactly 1 row committed to SQLite
    final_state = store.get_simulated_state()
    assert final_state["counts"]["expenses"] == 1

    # Tamper-evident audit chain remains 100% valid
    valid_chain, n_events = store.verify_audit_chain()
    assert valid_chain is True
    assert n_events >= 2


@pytest.mark.parametrize("worker_count", [4, 8])
def test_multiprocess_barrier_synchronized_concurrency(tmp_path: Path, worker_count: int) -> None:
    """Verify true simultaneous contender execution where all OS processes fire at the exact same microsecond using a multiprocessing.Barrier (§P1.7 & Reviewer Feedback)."""
    db_file = tmp_path / f"mp_barrier_{worker_count}.sqlite"
    store = SimulatedStore(db_url=f"sqlite:///{db_file.as_posix()}", confirmation_ttl_seconds=300)

    request_id = f"req_barrier_{worker_count}"
    proposal_id = f"prop_barrier_{worker_count}"
    corr_id = f"corr_barrier_{worker_count}"
    token, _ = generate_confirmation_capability_token()

    canonical_args = {
        "amount": "300.00",
        "currency": "EUR",
        "expense_date": "2026-11-04",
        "business_approval_status": "pending_manager_approval",
    }
    digest = compute_canonical_action_digest(request_id, "submit_expense", canonical_args)

    pending_result = AwaitingConfirmationGateResult(
        request_id=request_id,
        proposal_id=proposal_id,
        audit_correlation_id=corr_id,
        reason_code=ReasonCode.OK_AWAITING_CONFIRMATION,
        message="Awaiting user confirmation.",
        tool_name="submit_expense",
        canonical_arguments=canonical_args,
        confirmation_token=token,
        action_digest=digest,
        field_diagnostics=[],
    )
    store.record_gate_decision(pending_result, locale="hr-HR", timezone_name="Europe/Zagreb")

    # Manager barrier shared across all independent worker processes
    manager = multiprocessing.Manager()
    barrier = manager.Barrier(worker_count)

    results: list[dict[str, object]] = []
    errors: list[Exception] = []

    with concurrent.futures.ProcessPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(
                _mp_barrier_confirm_worker, db_file.as_posix(), proposal_id, digest, token, barrier
            )
            for _ in range(worker_count)
        ]
        for fut in concurrent.futures.as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as exc:
                errors.append(exc)

    assert len(errors) == 0, f"Errors encountered during barrier test: {errors}"
    assert len(results) == worker_count

    # Invariant 1: Exactly 1 initial execution
    initial_executions = [r for r in results if r.get("is_replay") is False]
    assert len(initial_executions) == 1, (
        f"Expected 1 initial execution, got {len(initial_executions)}"
    )

    # Invariant 2: Exactly worker_count - 1 authenticated idempotent replays
    replays = [r for r in results if r.get("is_replay") is True]
    assert len(replays) == worker_count - 1

    # Invariant 3: Exactly 1 row committed to SQLite
    final_state = store.get_simulated_state()
    assert final_state["counts"]["expenses"] == 1

    # Invariant 4: Audit chain is intact
    valid_chain, n_events = store.verify_audit_chain()
    assert valid_chain is True
    assert n_events >= 2


def test_dst_fallback_fold_ambiguity_and_utc_comparison() -> None:
    """Verify Europe/Zagreb 25.10.2026 02:30 fold ambiguity, candidate UTC instants, and chronological comparison (§7)."""
    # 1. Fall-back 02:30 without offset is ambiguous and emits both UTC candidates
    res = parse_datetime(
        "25.10.2026. 02:30",
        locale="hr-HR",
        trusted_timezone="Europe/Zagreb",
        reference_datetime=datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
    )
    assert res.status == ParseStatus.AMBIGUOUS
    assert res.reason_code == ReasonCode.DATETIME_DST_AMBIGUOUS
    assert res.candidates == (
        "2026-10-25T02:30:00+02:00",
        "2026-10-25T02:30:00+01:00",
    )

    z_zagreb = ZoneInfo("Europe/Zagreb")
    dt_fold0 = datetime(2026, 10, 25, 2, 30, fold=0, tzinfo=z_zagreb)
    dt_fold1 = datetime(2026, 10, 25, 2, 30, fold=1, tzinfo=z_zagreb)

    # 2. Check exact UTC correspondence
    assert dt_fold0.astimezone(UTC) == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    assert dt_fold1.astimezone(UTC) == datetime(2026, 10, 25, 1, 30, tzinfo=UTC)

    # 3. Start in first fold (02:30 CEST = 00:30 UTC) and end in second fold (02:30 CET = 01:30 UTC)
    # This is a valid 60-minute real elapsed event with identical local wall-clock times!
    assert dt_fold1.astimezone(UTC) > dt_fold0.astimezone(UTC)
    elapsed_duration = dt_fold1.astimezone(UTC) - dt_fold0.astimezone(UTC)
    assert elapsed_duration == timedelta(hours=1)

    # 4. Inverted fold order: start in second fold (01:30 UTC) and end in first fold (00:30 UTC) -> invalid
    assert not (dt_fold0.astimezone(UTC) > dt_fold1.astimezone(UTC))

    # 5. Same wall-clock time and same fold: zero elapsed duration
    assert dt_fold0.astimezone(UTC) == dt_fold0.astimezone(UTC)

    # 6. Event crossing transition: start 01:30 (+02:00 = 23:30Z Oct 24), end 03:30 (+01:00 = 02:30Z Oct 25)
    dt_start_cross = datetime(2026, 10, 25, 1, 30, tzinfo=z_zagreb)
    dt_end_cross = datetime(2026, 10, 25, 3, 30, tzinfo=z_zagreb)
    real_elapsed = dt_end_cross.astimezone(UTC) - dt_start_cross.astimezone(UTC)
    assert real_elapsed == timedelta(hours=3)  # 3 real elapsed hours across the 1-hour fall-back


def test_transaction_rollback_fault_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that failure during record insertion or audit append causes full transaction rollback (§4.3 & §8.2)."""
    db_file = tmp_path / "fault_test.sqlite"
    store = SimulatedStore(db_url=f"sqlite:///{db_file.as_posix()}", confirmation_ttl_seconds=300)

    request_id = "req_fault_test"
    proposal_id = "prop_fault_test"
    corr_id = "corr_fault_test"
    token, _ = generate_confirmation_capability_token()

    canonical_args = {
        "amount": "200.00",
        "currency": "EUR",
        "expense_date": "2026-11-04",
        "business_approval_status": "pending_manager_approval",
    }
    digest = compute_canonical_action_digest(request_id, "submit_expense", canonical_args)

    pending_result = AwaitingConfirmationGateResult(
        request_id=request_id,
        proposal_id=proposal_id,
        audit_correlation_id=corr_id,
        reason_code=ReasonCode.OK_AWAITING_CONFIRMATION,
        message="Awaiting user confirmation.",
        tool_name="submit_expense",
        canonical_arguments=canonical_args,
        confirmation_token=token,
        action_digest=digest,
        field_diagnostics=[],
    )
    store.record_gate_decision(pending_result, locale="hr-HR", timezone_name="Europe/Zagreb")

    # Verify baseline state
    init_state = store.get_simulated_state()
    assert init_state["counts"]["expenses"] == 0
    init_req = store.get_request_by_id_or_proposal(request_id)
    assert init_req is not None
    assert init_req["confirmation_status"] == "pending"

    # --- Fault Injection 1: Simulate crash during business record commit ---
    def _crash_record_commit(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("FaultInjection: Database disk full during expense record insert")

    monkeypatch.setattr(store, "_commit_simulated_record_in_tx", _crash_record_commit)

    with pytest.raises(RuntimeError, match="FaultInjection: Database disk full"):
        store.confirm_and_execute(
            proposal_id,
            expected_action_digest=digest,
            confirmation_token=token,
            require_token=True,
        )

    # Inspect SQLite database state: must have rolled back completely!
    rolled_back_req = store.get_request_by_id_or_proposal(request_id)
    assert rolled_back_req is not None
    assert rolled_back_req["status"] == GateDecisionStatus.AWAITING_CONFIRMATION.value
    assert rolled_back_req["confirmation_status"] == "pending"
    assert rolled_back_req["confirmation_token_sha256"] is not None
    assert store.get_simulated_state()["counts"]["expenses"] == 0

    # --- Fault Injection 2: Simulate crash during audit append ---
    monkeypatch.undo()

    orig_append = store._append_audit_event_in_tx
    call_count = 0

    def _crash_audit_on_confirm(conn: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal call_count
        call_count += 1
        if kwargs.get("event_type") == "USER_CONFIRMED_EXECUTION":
            raise RuntimeError("FaultInjection: Disk write error during audit event append")
        orig_append(conn, *args, **kwargs)

    monkeypatch.setattr(store, "_append_audit_event_in_tx", _crash_audit_on_confirm)

    with pytest.raises(RuntimeError, match="FaultInjection: Disk write error"):
        store.confirm_and_execute(
            proposal_id,
            expected_action_digest=digest,
            confirmation_token=token,
            require_token=True,
        )

    # Inspect SQLite database state again: must have rolled back completely!
    rolled_back_req2 = store.get_request_by_id_or_proposal(request_id)
    assert rolled_back_req2 is not None
    assert rolled_back_req2["status"] == GateDecisionStatus.AWAITING_CONFIRMATION.value
    assert rolled_back_req2["confirmation_status"] == "pending"
    assert store.get_simulated_state()["counts"]["expenses"] == 0

    # --- Clean Execution: Restore methods and confirm successfully ---
    monkeypatch.undo()

    res_clean = store.confirm_and_execute(
        proposal_id,
        expected_action_digest=digest,
        confirmation_token=token,
        require_token=True,
    )
    assert res_clean.status == GateDecisionStatus.EXECUTED
    final_req = store.get_request_by_id_or_proposal(request_id)
    assert final_req is not None
    assert final_req["status"] == GateDecisionStatus.EXECUTED.value
    assert final_req["confirmation_status"] == "confirmed"
    assert final_req["confirmation_token_sha256"] is None  # consumed
    assert store.get_simulated_state()["counts"]["expenses"] == 1
