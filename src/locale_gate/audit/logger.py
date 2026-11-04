"""Append-only JSONL audit logger with strict privacy redaction defaults."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from locale_gate.contracts.models import GateResult


class JsonlAuditLogger:
    """Append-only JSONL audit writer that never logs secrets or chain-of-thought."""

    def __init__(
        self,
        log_path: Path,
        *,
        store_raw_user_text: bool = False,
    ) -> None:
        self.log_path = log_path
        self.store_raw_user_text = store_raw_user_text

    def append_decision(
        self,
        result: GateResult,
        *,
        locale: str | None = None,
        timezone_name: str | None = None,
        request_text: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append one structured audit event line."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "audit_correlation_id": result.audit_correlation_id,
            "request_id": result.request_id,
            "proposal_id": result.proposal_id,
            "status": result.status.value,
            "reason_code": result.reason_code.value,
            "tool_name": result.tool_name,
            "locale": locale,
            "timezone": timezone_name,
            "confirmation_status": result.confirmation_status.value,
            "business_approval_status": result.business_approval_status.value,
            "action_digest": result.action_digest,
            "canonical_arguments": result.canonical_arguments,
            "field_diagnostics": [fd.model_dump() for fd in result.field_diagnostics],
        }
        if self.store_raw_user_text and request_text is not None:
            record["request_text"] = request_text
        if extra_metadata:
            record["metadata"] = extra_metadata

        line = json.dumps(record, sort_keys=True, ensure_ascii=False)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
