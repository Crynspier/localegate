"""Transactional SQLite and in-memory simulated execution environment (§5.4 & §12)."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from locale_gate.contracts.enums import (
    BusinessApprovalStatus,
    ConfirmationStatus,
    GateDecisionStatus,
    ReasonCode,
    ToolName,
)
from locale_gate.contracts.models import (
    BlockedGateResult,
    ExecutedGateResult,
    FieldDiagnostic,
    GateResult,
    GateResultAdapter,
    SimulatedToolEffect,
)
from locale_gate.tools.registry import verify_confirmation_capability_token

SCHEMA_VERSION = "1.1.0"

metadata = MetaData()

schema_info_table = Table(
    "schema_info",
    metadata,
    Column("version", String(32), primary_key=True),
    Column("created_at_utc", String(64), nullable=False),
)

requests_table = Table(
    "requests",
    metadata,
    Column("request_id", String(128), primary_key=True),
    Column("proposal_id", String(128), unique=True, nullable=False, index=True),
    Column("audit_correlation_id", String(128), nullable=False),
    Column("locale", String(32), nullable=True),
    Column("timezone", String(64), nullable=True),
    Column("reference_datetime", String(64), nullable=True),
    Column("request_text", Text, nullable=True),
    Column("status", String(64), nullable=False),
    Column("reason_code", String(64), nullable=False),
    Column("tool_name", String(64), nullable=True),
    Column("canonical_arguments_json", Text, nullable=False),
    Column("action_digest", String(128), nullable=True, index=True),
    Column("confirmation_token_sha256", String(128), nullable=True),
    Column("consumed_token_sha256", String(128), nullable=True),
    Column("confirmation_status", String(32), nullable=False),
    Column("business_approval_status", String(64), nullable=False),
    Column("expires_at_utc", DateTime(timezone=True), nullable=True),
    Column("gate_result_json", Text, nullable=False),
    Column("created_at_utc", DateTime(timezone=True), nullable=False),
    Column("updated_at_utc", DateTime(timezone=True), nullable=False),
)

simulated_expenses_table = Table(
    "simulated_expenses",
    metadata,
    Column("record_id", String(128), primary_key=True),
    Column("request_id", String(128), nullable=False, index=True),
    Column("proposal_id", String(128), nullable=False, unique=True),
    Column("action_digest", String(128), nullable=False, unique=True),
    Column("amount", String(64), nullable=False),
    Column("currency", String(8), nullable=False),
    Column("expense_date", String(16), nullable=False),
    Column("description", Text, nullable=True),
    Column("category", String(64), nullable=True),
    Column("confirmation_status", String(32), nullable=False),
    Column("business_approval_status", String(64), nullable=False),
    Column("created_at_utc", String(64), nullable=False),
)

simulated_events_table = Table(
    "simulated_events",
    metadata,
    Column("record_id", String(128), primary_key=True),
    Column("request_id", String(128), nullable=False, index=True),
    Column("proposal_id", String(128), nullable=False, unique=True),
    Column("action_digest", String(128), nullable=False, unique=True),
    Column("title", String(256), nullable=False),
    Column("start_datetime", String(64), nullable=False),
    Column("end_datetime", String(64), nullable=False),
    Column("timezone", String(64), nullable=False),
    Column("duration_minutes", Integer, nullable=True),
    Column("confirmation_status", String(32), nullable=False),
    Column("business_approval_status", String(64), nullable=False),
    Column("created_at_utc", String(64), nullable=False),
)

audit_events_table = Table(
    "audit_events",
    metadata,
    Column("event_id", String(128), primary_key=True),
    Column("audit_correlation_id", String(128), nullable=False, index=True),
    Column("request_id", String(128), nullable=False, index=True),
    Column("proposal_id", String(128), nullable=False),
    Column("event_type", String(64), nullable=False),
    Column("status", String(64), nullable=False),
    Column("reason_code", String(64), nullable=False),
    Column("action_digest", String(128), nullable=True),
    Column("details_json", Text, nullable=False),
    Column("created_at_utc", String(64), nullable=False),
    Column("is_dry_run", Boolean, nullable=False, default=False),
    Column("prev_event_hash", String(128), nullable=True),
    Column("event_hash", String(128), nullable=True),
)


def _compute_audit_event_hash(
    prev_hash: str,
    event_id: str,
    request_id: str,
    proposal_id: str,
    event_type: str,
    status: str,
    reason_code: str,
    action_digest: str | None,
    details_json: str,
    created_at_utc: str,
) -> str:
    payload = {
        "domain": "locale-gate:audit-chain:v1",
        "prev_hash": prev_hash,
        "event_id": event_id,
        "request_id": request_id,
        "proposal_id": proposal_id,
        "event_type": event_type,
        "status": status,
        "reason_code": reason_code,
        "action_digest": action_digest,
        "details_json": details_json,
        "created_at_utc": created_at_utc,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SimulatedStore:
    """Transactional persistence for requests, single-use confirmations, and simulated records."""

    def __init__(
        self,
        db_url: str = "sqlite:///:memory:",
        store_raw_user_text: bool = False,
        confirmation_ttl_seconds: int = 900,
    ) -> None:
        self.db_url = db_url
        self.store_raw_user_text = store_raw_user_text
        self.confirmation_ttl_seconds = confirmation_ttl_seconds

        if db_url.startswith("sqlite:///") and db_url != "sqlite:///:memory:":
            raw_path = db_url.replace("sqlite:///", "", 1)
            db_file = Path(raw_path)
            db_file.parent.mkdir(parents=True, exist_ok=True)

        if ":memory:" in db_url:
            self.engine: Engine = create_engine(
                db_url,
                future=True,
                connect_args={"check_same_thread": False, "timeout": 10.0},
                poolclass=StaticPool,
            )

            @event.listens_for(self.engine, "connect")
            def _set_mem_sqlite_pragma(dbapi_connection: Any, _connection_record: Any) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.close()
        elif db_url.startswith("sqlite"):
            self.engine = create_engine(
                db_url,
                future=True,
                connect_args={"check_same_thread": False, "timeout": 10.0},
            )

            @event.listens_for(self.engine, "connect")
            def _set_sqlite_pragma(dbapi_connection: Any, _connection_record: Any) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.close()
        else:
            self.engine = create_engine(db_url, future=True)

        self._init_schema()

    def _init_schema(self) -> None:
        metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            insp = inspect(conn)
            req_cols = {c["name"] for c in insp.get_columns("requests")}
            if "confirmation_token_sha256" not in req_cols:
                conn.execute(
                    text("ALTER TABLE requests ADD COLUMN confirmation_token_sha256 VARCHAR(128)")
                )
            if "consumed_token_sha256" not in req_cols:
                conn.execute(
                    text("ALTER TABLE requests ADD COLUMN consumed_token_sha256 VARCHAR(128)")
                )
            aud_cols = {c["name"] for c in insp.get_columns("audit_events")}
            if "prev_event_hash" not in aud_cols:
                conn.execute(
                    text("ALTER TABLE audit_events ADD COLUMN prev_event_hash VARCHAR(128)")
                )
            if "event_hash" not in aud_cols:
                conn.execute(text("ALTER TABLE audit_events ADD COLUMN event_hash VARCHAR(128)"))

            row = conn.execute(select(schema_info_table.c.version)).first()
            if row is None:
                conn.execute(
                    schema_info_table.insert().values(
                        version=SCHEMA_VERSION,
                        created_at_utc=datetime.now(UTC).isoformat(),
                    )
                )

    def reset_all(self, *, confirmed: bool = False) -> None:
        """Reset all simulated tables. Requires explicit confirmed=True."""
        if not confirmed:
            raise PermissionError("Database reset requires explicit confirmation (confirmed=True).")
        with self.engine.begin() as conn:
            conn.execute(simulated_expenses_table.delete())
            conn.execute(simulated_events_table.delete())
            conn.execute(audit_events_table.delete())
            conn.execute(requests_table.delete())

    def _append_audit_event_in_tx(
        self,
        conn: Any,
        *,
        audit_correlation_id: str,
        request_id: str,
        proposal_id: str,
        event_type: str,
        status: str,
        reason_code: str,
        action_digest: str | None,
        details_dict: dict[str, Any],
        created_at_utc: str,
        is_dry_run: bool,
    ) -> None:
        last_row = conn.execute(
            select(audit_events_table.c.event_hash)
            .order_by(audit_events_table.c.created_at_utc.desc())
            .limit(1)
        ).first()
        prev_hash = str(last_row[0]) if last_row and last_row[0] else "GENESIS"
        event_id = f"aud_{uuid.uuid4().hex[:16]}"
        details_json = json.dumps(details_dict, sort_keys=True, ensure_ascii=False)
        ev_hash = _compute_audit_event_hash(
            prev_hash=prev_hash,
            event_id=event_id,
            request_id=request_id,
            proposal_id=proposal_id,
            event_type=event_type,
            status=status,
            reason_code=reason_code,
            action_digest=action_digest,
            details_json=details_json,
            created_at_utc=created_at_utc,
        )
        conn.execute(
            audit_events_table.insert().values(
                event_id=event_id,
                audit_correlation_id=audit_correlation_id,
                request_id=request_id,
                proposal_id=proposal_id,
                event_type=event_type,
                status=status,
                reason_code=reason_code,
                action_digest=action_digest,
                details_json=details_json,
                created_at_utc=created_at_utc,
                is_dry_run=is_dry_run,
                prev_event_hash=prev_hash,
                event_hash=ev_hash,
            )
        )

    def verify_audit_chain(self) -> tuple[bool, int]:
        """Verify the tamper-evident SHA-256 hash chain across all audit events (§11).

        Note: Provides application-level tamper-evidence, not tamper-proof guarantees against a
        local user/administrator with direct SQLite write access.
        """
        with self.engine.begin() as conn:
            rows = (
                conn.execute(
                    select(audit_events_table).order_by(audit_events_table.c.created_at_utc.asc())
                )
                .mappings()
                .all()
            )
            prev = "GENESIS"
            for r in rows:
                if r["prev_event_hash"] is None or r["event_hash"] is None:
                    continue
                if r["prev_event_hash"] != prev:
                    return False, len(rows)
                expected = _compute_audit_event_hash(
                    prev_hash=prev,
                    event_id=r["event_id"],
                    request_id=r["request_id"],
                    proposal_id=r["proposal_id"],
                    event_type=r["event_type"],
                    status=r["status"],
                    reason_code=r["reason_code"],
                    action_digest=r["action_digest"],
                    details_json=r["details_json"],
                    created_at_utc=r["created_at_utc"],
                )
                if expected != r["event_hash"]:
                    return False, len(rows)
                prev = expected
            return True, len(rows)

    def record_gate_decision(
        self,
        result: GateResult,
        *,
        locale: str | None = None,
        timezone_name: str | None = None,
        reference_datetime: str | None = None,
        request_text: str | None = None,
        is_dry_run: bool = False,
        now_utc: datetime | None = None,
    ) -> None:
        """Persist a gate decision, store only the SHA-256 hash of confirmation_token, and append an audit event."""
        now = now_utc or datetime.now(UTC)
        expires_at = (
            now + timedelta(seconds=self.confirmation_ttl_seconds)
            if result.status == GateDecisionStatus.AWAITING_CONFIRMATION
            else None
        )
        stored_text = request_text if self.store_raw_user_text else None

        token_hash: str | None = None
        if result.confirmation_token:
            token_hash = hashlib.sha256(result.confirmation_token.encode("utf-8")).hexdigest()

        # Redact raw confirmation_token before persisting gate_result_json to SQLite (§11)
        redacted_result = result.model_copy(update={"confirmation_token": None})
        result_json = redacted_result.model_dump_json()

        with self.engine.begin() as conn:
            existing = conn.execute(
                select(requests_table.c.request_id).where(
                    requests_table.c.request_id == result.request_id
                )
            ).first()
            if existing is None:
                conn.execute(
                    requests_table.insert().values(
                        request_id=result.request_id,
                        proposal_id=result.proposal_id,
                        audit_correlation_id=result.audit_correlation_id,
                        locale=locale,
                        timezone=timezone_name,
                        reference_datetime=reference_datetime,
                        request_text=stored_text,
                        status=result.status.value,
                        reason_code=result.reason_code.value,
                        tool_name=result.tool_name,
                        canonical_arguments_json=json.dumps(
                            result.canonical_arguments, sort_keys=True
                        ),
                        action_digest=result.action_digest,
                        confirmation_token_sha256=token_hash,
                        confirmation_status=result.confirmation_status.value,
                        business_approval_status=result.business_approval_status.value,
                        expires_at_utc=expires_at,
                        gate_result_json=result_json,
                        created_at_utc=now,
                        updated_at_utc=now,
                    )
                )
            else:
                conn.execute(
                    requests_table.update()
                    .where(requests_table.c.request_id == result.request_id)
                    .values(
                        status=result.status.value,
                        reason_code=result.reason_code.value,
                        confirmation_status=result.confirmation_status.value,
                        business_approval_status=result.business_approval_status.value,
                        confirmation_token_sha256=token_hash,
                        gate_result_json=result_json,
                        updated_at_utc=now,
                    )
                )

            self._append_audit_event_in_tx(
                conn,
                audit_correlation_id=result.audit_correlation_id,
                request_id=result.request_id,
                proposal_id=result.proposal_id,
                event_type="GATE_DECISION",
                status=result.status.value,
                reason_code=result.reason_code.value,
                action_digest=result.action_digest,
                details_dict={
                    "tool_name": result.tool_name,
                    "canonical_arguments": result.canonical_arguments,
                    "confirmation_status": result.confirmation_status.value,
                    "business_approval_status": result.business_approval_status.value,
                },
                created_at_utc=now.isoformat(),
                is_dry_run=is_dry_run,
            )

    def get_request_by_id_or_proposal(self, identifier: str) -> dict[str, Any] | None:
        """Retrieve a stored request row by request_id or proposal_id."""
        with self.engine.begin() as conn:
            row = (
                conn.execute(
                    select(requests_table).where(
                        (requests_table.c.request_id == identifier)
                        | (requests_table.c.proposal_id == identifier)
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            data = dict(row)
            data["gate_result"] = GateResultAdapter.validate_json(data["gate_result_json"])
            data["canonical_arguments"] = json.loads(data["canonical_arguments_json"])
            return data

    def list_requests(self, limit: int = 50) -> list[dict[str, Any]]:
        """List recent requests ordered by created_at_utc descending."""
        with self.engine.begin() as conn:
            rows = (
                conn.execute(
                    select(requests_table)
                    .order_by(requests_table.c.created_at_utc.desc())
                    .limit(limit)
                )
                .mappings()
                .all()
            )
            out: list[dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                d.pop("confirmation_token_sha256", None)
                d["gate_result"] = json.loads(d["gate_result_json"])
                d["canonical_arguments"] = json.loads(d["canonical_arguments_json"])
                if isinstance(d.get("created_at_utc"), datetime):
                    d["created_at_utc"] = d["created_at_utc"].isoformat()
                if isinstance(d.get("updated_at_utc"), datetime):
                    d["updated_at_utc"] = d["updated_at_utc"].isoformat()
                if isinstance(d.get("expires_at_utc"), datetime):
                    d["expires_at_utc"] = d["expires_at_utc"].isoformat()
                out.append(d)
            return out

    def confirm_and_execute(
        self,
        identifier: str,
        expected_action_digest: str | None = None,
        *,
        confirmation_token: str | None = None,
        require_token: bool = True,
        now_utc: datetime | None = None,
    ) -> GateResult:
        """Confirm a pending proposal once and transactionally commit the simulated record (§5.4).

        Enforces:
        - `BEGIN IMMEDIATE` transaction isolation on SQLite to serialize concurrent callers.
        - Exact binding to `action_digest` when supplied.
        - Constant-time verification of `confirmation_token` against `confirmation_token_sha256` when supplied or required.
        - Expiry check against `expires_at_utc`.
        - Atomic conditional `UPDATE ... WHERE confirmation_status = 'pending'` (`rowcount == 1`).
        - Idempotency: if already confirmed with the exact same digest, returns the existing
          `ExecutedGateResult` with `idempotent_replay=True` without creating a duplicate record.
        """
        now = now_utc or datetime.now(UTC)

        with self.engine.connect() as conn:
            if self.db_url.startswith("sqlite"):
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                conn.begin()
            try:
                row = (
                    conn.execute(
                        select(requests_table).where(
                            (requests_table.c.request_id == identifier)
                            | (requests_table.c.proposal_id == identifier)
                        )
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    raise KeyError(f"Unknown request or proposal ID: {identifier}")

                prior_result = GateResultAdapter.validate_json(row["gate_result_json"])
                stored_digest: str | None = row["action_digest"]
                stored_token_hash: str | None = row.get("confirmation_token_sha256")
                consumed_token_hash: str | None = row.get("consumed_token_sha256")
                conf_status = row["confirmation_status"]

                if expected_action_digest is not None and expected_action_digest != stored_digest:
                    raise ValueError(
                        f"{ReasonCode.CONFIRMATION_DIGEST_MISMATCH.value}: Supplied action_digest "
                        f"{expected_action_digest!r} does not match pending digest {stored_digest!r}."
                    )

                # Idempotent replay check: if already confirmed, authenticate caller and return existing record with replay flag
                if conf_status == ConfirmationStatus.CONFIRMED.value and isinstance(
                    prior_result, ExecutedGateResult
                ):
                    # Replay authentication: subsequent calls MUST still present matching credentials (§P1.6)
                    if confirmation_token is not None:
                        if not consumed_token_hash or not verify_confirmation_capability_token(
                            confirmation_token, consumed_token_hash
                        ):
                            raise ValueError(
                                f"{ReasonCode.CONFIRMATION_TOKEN_INVALID.value}: Replay request rejected: "
                                "invalid confirmation capability token."
                            )
                    elif require_token and consumed_token_hash is not None:
                        raise ValueError(
                            f"{ReasonCode.CONFIRMATION_TOKEN_INVALID.value}: Replay request rejected: "
                            "confirmation capability token is required."
                        )

                    conn.commit()
                    assert prior_result.simulated_effect is not None
                    replayed_effect = SimulatedToolEffect(
                        record_id=prior_result.simulated_effect.record_id,
                        tool_name=prior_result.simulated_effect.tool_name,
                        canonical_arguments=prior_result.simulated_effect.canonical_arguments,
                        confirmation_status=ConfirmationStatus.CONFIRMED,
                        business_approval_status=prior_result.simulated_effect.business_approval_status,
                        action_digest=prior_result.simulated_effect.action_digest,
                        created_at_utc=prior_result.simulated_effect.created_at_utc,
                        idempotent_replay=True,
                    )
                    return prior_result.model_copy(update={"simulated_effect": replayed_effect})

                if conf_status == ConfirmationStatus.REJECTED.value:
                    raise ValueError(
                        f"{ReasonCode.CONFIRMATION_REJECTED.value}: Proposal {identifier} was already rejected."
                    )

                if row["status"] != GateDecisionStatus.AWAITING_CONFIRMATION.value:
                    raise ValueError(
                        f"{ReasonCode.CONFIRMATION_INVALID_OR_EXPIRED.value}: Proposal {identifier} "
                        f"is in state {row['status']} and cannot be confirmed."
                    )

                if confirmation_token is not None:
                    if not stored_token_hash or not verify_confirmation_capability_token(
                        confirmation_token, stored_token_hash
                    ):
                        raise ValueError(
                            f"{ReasonCode.CONFIRMATION_TOKEN_INVALID.value}: Invalid confirmation capability token."
                        )
                elif stored_token_hash is not None:
                    raise ValueError(
                        f"{ReasonCode.CONFIRMATION_TOKEN_INVALID.value}: Confirmation capability token is required."
                    )

                expires_at = row["expires_at_utc"]
                if expires_at is not None:
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=UTC)
                    if now > expires_at:
                        conn.execute(
                            requests_table.update()
                            .where(requests_table.c.request_id == row["request_id"])
                            .values(
                                confirmation_status=ConfirmationStatus.EXPIRED.value,
                                updated_at_utc=now,
                            )
                        )
                        conn.commit()
                        raise ValueError(
                            f"{ReasonCode.CONFIRMATION_INVALID_OR_EXPIRED.value}: Confirmation window expired."
                        )

                # Atomic conditional state transition: PENDING -> CONFIRMED
                cond_upd = conn.execute(
                    requests_table.update()
                    .where(
                        (requests_table.c.request_id == row["request_id"])
                        & (requests_table.c.confirmation_status == ConfirmationStatus.PENDING.value)
                        & (
                            requests_table.c.status
                            == GateDecisionStatus.AWAITING_CONFIRMATION.value
                        )
                    )
                    .values(
                        status=GateDecisionStatus.EXECUTED.value,
                        reason_code=ReasonCode.OK_EXECUTED.value,
                        confirmation_status=ConfirmationStatus.CONFIRMED.value,
                        consumed_token_sha256=stored_token_hash,  # retain consumed capability hash for replay authentication
                        confirmation_token_sha256=None,  # consume capability token once used
                        updated_at_utc=now,
                    )
                )
                if cond_upd.rowcount != 1:
                    raise ValueError(
                        f"{ReasonCode.CONFIRMATION_ALREADY_USED.value}: Concurrent confirmation already consumed proposal {identifier}."
                    )

                canonical_args: dict[str, Any] = json.loads(row["canonical_arguments_json"])
                tool_name: str = row["tool_name"]
                assert stored_digest is not None

                effect = self._commit_simulated_record_in_tx(
                    conn=conn,
                    request_id=row["request_id"],
                    proposal_id=row["proposal_id"],
                    tool_name=tool_name,
                    canonical_args=canonical_args,
                    action_digest=stored_digest,
                    now_iso=now.isoformat(),
                )

                executed_result = ExecutedGateResult(
                    request_id=row["request_id"],
                    proposal_id=row["proposal_id"],
                    audit_correlation_id=row["audit_correlation_id"],
                    reason_code=ReasonCode.OK_EXECUTED,
                    message=f"Confirmed and executed simulated {tool_name}.",
                    tool_name=tool_name,
                    canonical_arguments=canonical_args,
                    field_diagnostics=[
                        FieldDiagnostic.model_validate(fd) for fd in prior_result.field_diagnostics
                    ],
                    confirmation_status=ConfirmationStatus.CONFIRMED,
                    business_approval_status=effect.business_approval_status,
                    action_digest=stored_digest,
                    simulated_effect=effect,
                    untrusted_proposal=prior_result.untrusted_proposal,
                )

                conn.execute(
                    requests_table.update()
                    .where(requests_table.c.request_id == row["request_id"])
                    .values(
                        business_approval_status=effect.business_approval_status.value,
                        gate_result_json=executed_result.model_dump_json(),
                        updated_at_utc=now,
                    )
                )

                self._append_audit_event_in_tx(
                    conn,
                    audit_correlation_id=row["audit_correlation_id"],
                    request_id=row["request_id"],
                    proposal_id=row["proposal_id"],
                    event_type="USER_CONFIRMED_EXECUTION",
                    status=GateDecisionStatus.EXECUTED.value,
                    reason_code=ReasonCode.OK_EXECUTED.value,
                    action_digest=stored_digest,
                    details_dict={
                        "record_id": effect.record_id,
                        "tool_name": tool_name,
                        "business_approval_status": effect.business_approval_status.value,
                    },
                    created_at_utc=now.isoformat(),
                    is_dry_run=False,
                )
                conn.commit()
                return executed_result
            except Exception:
                conn.rollback()
                raise

    def reject_pending(
        self,
        identifier: str,
        *,
        now_utc: datetime | None = None,
    ) -> GateResult:
        """Reject a pending proposal so it can never be executed."""
        now = now_utc or datetime.now(UTC)
        with self.engine.begin() as conn:
            row = (
                conn.execute(
                    select(requests_table).where(
                        (requests_table.c.request_id == identifier)
                        | (requests_table.c.proposal_id == identifier)
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise KeyError(f"Unknown request or proposal ID: {identifier}")

            if row["confirmation_status"] == ConfirmationStatus.CONFIRMED.value:
                raise ValueError(
                    f"{ReasonCode.CONFIRMATION_ALREADY_USED.value}: Proposal was already confirmed."
                )

            prior_result = GateResultAdapter.validate_json(row["gate_result_json"])

            rejected_result = BlockedGateResult(
                request_id=row["request_id"],
                proposal_id=row["proposal_id"],
                audit_correlation_id=row["audit_correlation_id"],
                reason_code=ReasonCode.CONFIRMATION_REJECTED,
                message="User explicitly rejected the pending proposal.",
                tool_name=prior_result.tool_name,
                canonical_arguments=prior_result.canonical_arguments,
                field_diagnostics=prior_result.field_diagnostics,
                confirmation_status=ConfirmationStatus.REJECTED,
                business_approval_status=BusinessApprovalStatus.NOT_APPLICABLE,
                action_digest=prior_result.action_digest,
                untrusted_proposal=prior_result.untrusted_proposal,
            )

            conn.execute(
                requests_table.update()
                .where(requests_table.c.request_id == row["request_id"])
                .values(
                    status=GateDecisionStatus.BLOCKED.value,
                    reason_code=ReasonCode.CONFIRMATION_REJECTED.value,
                    confirmation_status=ConfirmationStatus.REJECTED.value,
                    confirmation_token_sha256=None,
                    business_approval_status=BusinessApprovalStatus.NOT_APPLICABLE.value,
                    gate_result_json=rejected_result.model_dump_json(),
                    updated_at_utc=now,
                )
            )

            self._append_audit_event_in_tx(
                conn,
                audit_correlation_id=row["audit_correlation_id"],
                request_id=row["request_id"],
                proposal_id=row["proposal_id"],
                event_type="USER_REJECTED_PROPOSAL",
                status=GateDecisionStatus.BLOCKED.value,
                reason_code=ReasonCode.CONFIRMATION_REJECTED.value,
                action_digest=prior_result.action_digest,
                details_dict={"rejected_by": "local_user"},
                created_at_utc=now.isoformat(),
                is_dry_run=False,
            )
            return rejected_result

    def execute_immediate_simulated(
        self,
        *,
        request_id: str,
        proposal_id: str,
        tool_name: str,
        canonical_args: dict[str, Any],
        action_digest: str,
        now_utc: datetime | None = None,
    ) -> SimulatedToolEffect:
        """Execute a simulated tool directly (used when require_confirmation=False in dry/eval sims)."""
        now_iso = (now_utc or datetime.now(UTC)).isoformat()
        with self.engine.begin() as conn:
            return self._commit_simulated_record_in_tx(
                conn=conn,
                request_id=request_id,
                proposal_id=proposal_id,
                tool_name=tool_name,
                canonical_args=canonical_args,
                action_digest=action_digest,
                now_iso=now_iso,
            )

    def _commit_simulated_record_in_tx(
        self,
        *,
        conn: Any,
        request_id: str,
        proposal_id: str,
        tool_name: str,
        canonical_args: dict[str, Any],
        action_digest: str,
        now_iso: str,
    ) -> SimulatedToolEffect:
        if tool_name == ToolName.SUBMIT_EXPENSE.value:
            existing = (
                conn.execute(
                    select(simulated_expenses_table).where(
                        simulated_expenses_table.c.action_digest == action_digest
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                return SimulatedToolEffect(
                    record_id=existing["record_id"],
                    tool_name=tool_name,
                    canonical_arguments=canonical_args,
                    confirmation_status=ConfirmationStatus.CONFIRMED,
                    business_approval_status=BusinessApprovalStatus.PENDING_MANAGER_APPROVAL,
                    action_digest=action_digest,
                    created_at_utc=existing["created_at_utc"],
                    idempotent_replay=True,
                )
            rec_id = f"exp_{uuid.uuid4().hex[:12]}"
            conn.execute(
                simulated_expenses_table.insert().values(
                    record_id=rec_id,
                    request_id=request_id,
                    proposal_id=proposal_id,
                    action_digest=action_digest,
                    amount=str(canonical_args["amount"]),
                    currency=str(canonical_args["currency"]),
                    expense_date=str(canonical_args["expense_date"]),
                    description=canonical_args.get("description"),
                    category=canonical_args.get("category"),
                    confirmation_status=ConfirmationStatus.CONFIRMED.value,
                    business_approval_status=BusinessApprovalStatus.PENDING_MANAGER_APPROVAL.value,
                    created_at_utc=now_iso,
                )
            )
            return SimulatedToolEffect(
                record_id=rec_id,
                tool_name=tool_name,
                canonical_arguments=canonical_args,
                confirmation_status=ConfirmationStatus.CONFIRMED,
                business_approval_status=BusinessApprovalStatus.PENDING_MANAGER_APPROVAL,
                action_digest=action_digest,
                created_at_utc=now_iso,
                idempotent_replay=False,
            )

        if tool_name == ToolName.SCHEDULE_EVENT.value:
            existing = (
                conn.execute(
                    select(simulated_events_table).where(
                        simulated_events_table.c.action_digest == action_digest
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                return SimulatedToolEffect(
                    record_id=existing["record_id"],
                    tool_name=tool_name,
                    canonical_arguments=canonical_args,
                    confirmation_status=ConfirmationStatus.CONFIRMED,
                    business_approval_status=BusinessApprovalStatus.NOT_APPLICABLE,
                    action_digest=action_digest,
                    created_at_utc=existing["created_at_utc"],
                    idempotent_replay=True,
                )
            rec_id = f"evt_{uuid.uuid4().hex[:12]}"
            conn.execute(
                simulated_events_table.insert().values(
                    record_id=rec_id,
                    request_id=request_id,
                    proposal_id=proposal_id,
                    action_digest=action_digest,
                    title=str(canonical_args["title"]),
                    start_datetime=str(canonical_args["start_datetime"]),
                    end_datetime=str(canonical_args["end_datetime"]),
                    timezone=str(canonical_args["timezone"]),
                    duration_minutes=canonical_args.get("duration_minutes"),
                    confirmation_status=ConfirmationStatus.CONFIRMED.value,
                    business_approval_status=BusinessApprovalStatus.NOT_APPLICABLE.value,
                    created_at_utc=now_iso,
                )
            )
            return SimulatedToolEffect(
                record_id=rec_id,
                tool_name=tool_name,
                canonical_arguments=canonical_args,
                confirmation_status=ConfirmationStatus.CONFIRMED,
                business_approval_status=BusinessApprovalStatus.NOT_APPLICABLE,
                action_digest=action_digest,
                created_at_utc=now_iso,
                idempotent_replay=False,
            )

        raise ValueError(f"Unsupported tool for simulation: {tool_name}")

    def get_simulated_state(self) -> dict[str, Any]:
        """Return all committed simulated expenses, events, pending confirmations, and counts."""
        with self.engine.begin() as conn:
            expenses = [
                dict(r)
                for r in conn.execute(
                    select(simulated_expenses_table).order_by(
                        simulated_expenses_table.c.created_at_utc.desc()
                    )
                )
                .mappings()
                .all()
            ]
            events = [
                dict(r)
                for r in conn.execute(
                    select(simulated_events_table).order_by(
                        simulated_events_table.c.created_at_utc.desc()
                    )
                )
                .mappings()
                .all()
            ]
            pending_rows = (
                conn.execute(
                    select(requests_table)
                    .where(requests_table.c.confirmation_status == ConfirmationStatus.PENDING.value)
                    .order_by(requests_table.c.created_at_utc.desc())
                )
                .mappings()
                .all()
            )
            pending = []
            for p in pending_rows:
                item = dict(p)
                item["canonical_arguments"] = json.loads(item["canonical_arguments_json"])
                item.pop("gate_result_json", None)
                item.pop("canonical_arguments_json", None)
                item.pop("confirmation_token_sha256", None)
                if isinstance(item.get("created_at_utc"), datetime):
                    item["created_at_utc"] = item["created_at_utc"].isoformat()
                if isinstance(item.get("updated_at_utc"), datetime):
                    item["updated_at_utc"] = item["updated_at_utc"].isoformat()
                if isinstance(item.get("expires_at_utc"), datetime):
                    item["expires_at_utc"] = item["expires_at_utc"].isoformat()
                pending.append(item)

            audit_count = len(conn.execute(select(audit_events_table.c.event_id)).all())

        return {
            "schema_version": SCHEMA_VERSION,
            "expenses": expenses,
            "events": events,
            "pending_confirmations": pending,
            "counts": {
                "expenses": len(expenses),
                "events": len(events),
                "pending_confirmations": len(pending),
                "audit_events": audit_count,
            },
        }
