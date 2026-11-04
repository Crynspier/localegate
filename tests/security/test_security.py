"""Security, prompt-injection, confinement, and loopback-enforcement tests (§13.4)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from locale_gate.api.app import create_app
from locale_gate.config.settings import AppSettings, ModelSettings, Settings
from locale_gate.contracts.enums import GateDecisionStatus, ReasonCode
from locale_gate.contracts.models import TrustedRequestContext
from locale_gate.gate.pipeline import LocaleGatePipeline
from locale_gate.models.adapter import (
    DeterministicMockAdapter,
    OpenAICompatibleLocalAdapter,
)
from locale_gate.tools.registry import html_safe_text
from locale_gate.tools.simulator import SimulatedStore

REF_DT = datetime.fromisoformat("2026-11-03T12:00:00+01:00")


class TestSecurityAndThreatModel:
    """Adversarial and trust-boundary tests (§5 & §13.4)."""

    @pytest.mark.parametrize(
        "malicious_payload",
        [
            {
                "contract_version": "1.0",
                "decision": "propose",
                "tool_name": "submit_expense",
                "canonical_value": "99999.99",
                "fields": {},
                "clarification_question": None,
            },
            {
                "contract_version": "1.0",
                "decision": "propose",
                "tool_name": "submit_expense",
                "execute": True,
                "fields": {},
                "clarification_question": None,
            },
            {
                "contract_version": "1.0",
                "decision": "propose",
                "tool_name": "submit_expense",
                "locale": "en-US",
                "fields": {},
                "clarification_question": None,
            },
            {
                "contract_version": "1.0",
                "decision": "propose",
                "tool_name": "submit_expense",
                "fields": {"amount": {"evidence": {"quote": "1.234,56 €", "start": 0, "end": 10}}},
                "clarification_question": None,
            },
            {
                "contract_version": "1.0",
                "decision": "propose",
                "tool_name": "submit_expense",
                "business_approval_status": "approved",
                "fields": {},
                "clarification_question": None,
            },
        ],
    )
    def test_canonical_and_offset_bypass_attempts_blocked(
        self, malicious_payload: dict[str, Any]
    ) -> None:
        gate = LocaleGatePipeline()
        ctx = TrustedRequestContext(
            request_id="sec_1",
            text="Evidentiraj trošak od 1.234,56 € od 03.11.2026.",
            locale="hr-HR",
            timezone="Europe/Zagreb",
            reference_datetime=REF_DT,
        )
        res = gate.evaluate_raw_json(ctx, malicious_payload)
        assert res.status == GateDecisionStatus.BLOCKED
        assert res.reason_code == ReasonCode.PROPOSAL_CANONICAL_BYPASS_ATTEMPT

    def test_forbidden_tool_and_unknown_field_blocked(self) -> None:
        gate = LocaleGatePipeline()
        ctx = TrustedRequestContext(
            request_id="sec_2",
            text="Evidentiraj trošak od 1.234,56 € od 03.11.2026.",
            locale="hr-HR",
            timezone="Europe/Zagreb",
            reference_datetime=REF_DT,
            allowed_tools=("submit_expense",),
        )

        # Unknown tool such as shell_exec
        res_shell = gate.evaluate_raw_json(
            ctx,
            {
                "contract_version": "1.0",
                "decision": "propose",
                "tool_name": "shell_exec",
                "fields": {},
                "clarification_question": None,
            },
        )
        assert res_shell.status == GateDecisionStatus.BLOCKED
        assert res_shell.reason_code == ReasonCode.TOOL_UNKNOWN

        # Registered tool not in context.allowed_tools
        res_disallowed = gate.evaluate_raw_json(
            ctx,
            {
                "contract_version": "1.0",
                "decision": "propose",
                "tool_name": "schedule_event",
                "fields": {},
                "clarification_question": None,
            },
        )
        assert res_disallowed.status == GateDecisionStatus.BLOCKED
        assert res_disallowed.reason_code == ReasonCode.TOOL_NOT_ALLOWED

        # Extra unallowed field on submit_expense
        res_extra = gate.evaluate_raw_json(
            ctx,
            {
                "contract_version": "1.0",
                "decision": "propose",
                "tool_name": "submit_expense",
                "fields": {
                    "amount": {"evidence": {"quote": "1.234,56 €"}},
                    "expense_date": {"evidence": {"quote": "03.11.2026."}},
                    "recipient_iban": {"evidence": {"quote": "1.234,56 €"}},
                },
                "clarification_question": None,
            },
        )
        assert res_extra.status == GateDecisionStatus.BLOCKED
        assert res_extra.reason_code == ReasonCode.FIELD_UNKNOWN_FORBIDDEN

    def test_non_loopback_network_rejected_by_default(self) -> None:
        with pytest.raises(ValueError, match="Non-loopback"):
            Settings(app=AppSettings(bind_host="0.0.0.0")).validate_security_posture()

        with pytest.raises(ValueError, match="Non-loopback"):
            Settings(
                model=ModelSettings(base_url="https://api.openai.com/v1")
            ).validate_security_posture()

        with pytest.raises(ValueError, match="not a loopback address"):
            OpenAICompatibleLocalAdapter(ModelSettings(base_url="http://192.168.1.50:8080/v1"))

    def test_api_path_traversal_and_body_size_limits(self, tmp_path: Path) -> None:
        cfg = Settings(
            app=AppSettings(
                db_url="sqlite:///:memory:",
                artifacts_dir=str(tmp_path / "artifacts"),
                max_request_body_bytes=2048,
            ),
        )
        app = create_app(
            cfg,
            store=SimulatedStore(db_url="sqlite:///:memory:"),
            adapter=DeterministicMockAdapter(),
        )
        client = TestClient(app)

        # Path traversal attempts rejected
        for malicious_path in [
            "/api/v1/evaluations/..%2F..%2F/artifacts/run.json",
            "/api/v1/evaluations/valid_run/artifacts/../../pyproject.toml",
            "/api/v1/evaluations/valid_run/artifacts/.env",
        ]:
            resp = client.get(malicious_path)
            assert resp.status_code in {400, 403, 404}

        # Oversized body rejected with HTTP 413
        huge_text = "A" * 4000
        resp_big = client.post("/api/v1/requests", json={"text": huge_text})
        assert resp_big.status_code == 413

    def test_xss_payload_in_title_escaped_as_plain_text(self) -> None:
        raw = "<script>alert('xss')</script>"
        safe = html_safe_text(raw)
        assert "<script>" not in safe
        assert "&lt;script&gt;" in safe
