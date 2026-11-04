"""Local agent workflow orchestrating model adapter, deterministic gate, and simulator."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from locale_gate.config.settings import ModelProfileConfig
from locale_gate.contracts.enums import ReasonCode
from locale_gate.contracts.models import (
    GateResult,
    ModelErrorGateResult,
    TrustedRequestContext,
)
from locale_gate.gate.pipeline import LocaleGatePipeline
from locale_gate.models.adapter import ModelAdapter, ModelCallTelemetry
from locale_gate.tools.simulator import SimulatedStore


@dataclass(frozen=True)
class AgentTurnOutcome:
    """Result of a single local agent turn (gate decision + model telemetry)."""

    gate_result: GateResult
    telemetry: ModelCallTelemetry


class LocalAgentOrchestrator:
    """Single-turn and clarification-aware local agent loop with zero external side effects."""

    def __init__(
        self,
        adapter: ModelAdapter,
        gate: LocaleGatePipeline | None = None,
        store: SimulatedStore | None = None,
    ) -> None:
        self.adapter = adapter
        self.store = store
        self.gate = gate or LocaleGatePipeline(store=store)

    def run_turn(
        self,
        context: TrustedRequestContext,
        *,
        profile_override: ModelProfileConfig | None = None,
    ) -> AgentTurnOutcome:
        """Propose an action via the model adapter and validate it through LocaleGatePipeline."""
        mod_out = self.adapter.propose(context, profile_override=profile_override)
        if mod_out.error_reason is not None or mod_out.raw_proposal is None:
            err_res = ModelErrorGateResult(
                request_id=context.request_id,
                proposal_id=f"prop_{uuid.uuid4().hex[:16]}",
                audit_correlation_id=f"corr_{uuid.uuid4().hex[:16]}",
                reason_code=mod_out.error_reason or ReasonCode.MODEL_UNAVAILABLE,
                message=mod_out.error_message or "Model failed to return a proposal.",
            )
            if self.store is not None and not context.dry_run:
                self.store.record_gate_decision(
                    err_res,
                    locale=context.locale,
                    timezone_name=context.timezone,
                    reference_datetime=(
                        context.reference_datetime.isoformat()
                        if context.reference_datetime
                        else None
                    ),
                    request_text=context.text,
                    is_dry_run=context.dry_run,
                )
            return AgentTurnOutcome(gate_result=err_res, telemetry=mod_out.telemetry)

        gate_res = self.gate.evaluate_raw_json(context, mod_out.raw_proposal)
        return AgentTurnOutcome(gate_result=gate_res, telemetry=mod_out.telemetry)
