"""FastAPI local loopback-only HTTP service for LocaleGate (§10.2 & §9.2)."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from locale_gate import __version__
from locale_gate.agent.orchestrator import LocalAgentOrchestrator
from locale_gate.benchmark.runner import BenchmarkRunner
from locale_gate.benchmark.schema import load_benchmark_cases
from locale_gate.config.settings import (
    Settings,
    load_settings,
    resolve_known_model_spec,
)
from locale_gate.contracts.models import TrustedRequestContext
from locale_gate.gate.pipeline import LocaleGatePipeline
from locale_gate.models.adapter import ModelAdapter, OpenAICompatibleLocalAdapter
from locale_gate.tools.simulator import SimulatedStore

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_ALLOWLISTED_ARTIFACT_FILES = frozenset(
    {
        "run.json",
        "metrics.json",
        "per_case.jsonl",
        "errors.jsonl",
        "confusion_matrix.json",
        "report.md",
        "environment.json",
        "model_manifest.json",
        "dataset_manifest.json",
    }
)
_ALLOWED_HOSTS = frozenset(
    {
        "127.0.0.1",
        "localhost",
        "[::1]",
        "::1",
        "testserver",
    }
)
_ALLOWED_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8765",
    "http://localhost:8765",
)


def _is_allowed_host_header(host_header: str | None) -> bool:
    if not host_header:
        return True
    raw = host_header.strip().lower()
    if raw.startswith("["):
        bracket_end = raw.find("]")
        if bracket_end == -1:
            return False
        host_only = raw[: bracket_end + 1]
    else:
        host_only = raw.split(":", 1)[0]
    return host_only in _ALLOWED_HOSTS


class SubmitRequestPayload(BaseModel):
    """Request body for `POST /api/v1/requests`."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4096)
    locale: str | None = Field(default="hr-HR", max_length=32)
    timezone: str | None = Field(default="Europe/Zagreb", max_length=64)
    reference_datetime: datetime = Field(
        default_factory=lambda: datetime.fromisoformat("2026-11-03T12:00:00+01:00")
    )
    default_currency: str | None = Field(default=None, max_length=8)
    require_confirmation: bool = Field(default=True)
    dry_run: bool = Field(default=False)
    # Optional untrusted proposal payload for deterministic inspection without calling live LLM
    untrusted_proposal: dict[str, Any] | None = Field(default=None)


class ConfirmRequestPayload(BaseModel):
    """Request body for `POST /api/v1/requests/{id}/confirm`."""

    model_config = ConfigDict(extra="forbid")

    action_digest: str = Field(..., min_length=1, max_length=128)
    confirmation_token: str = Field(..., min_length=1, max_length=256)


class ResetStatePayload(BaseModel):
    """Request body for `POST /api/v1/reset`."""

    model_config = ConfigDict(extra="forbid")

    confirm_reset: Literal[True] = Field(...)


class StartEvaluationPayload(BaseModel):
    """Request body for `POST /api/v1/evaluations`."""

    model_config = ConfigDict(extra="forbid")

    suite: Literal["smoke", "pilot", "test", "dev", "adversarial", "adversarial-v2"] = Field(
        default="smoke"
    )
    model: Literal["qwen35-q4km", "gemma4-e2b-q4km", "oracle-only"] = Field(default="oracle-only")
    profile: str = Field(default="qwen35_q4km_primary", max_length=64)


def _validate_safe_segment(segment: str, label: str = "identifier") -> None:
    if (
        not segment
        or ".." in segment
        or "/" in segment
        or "\\" in segment
        or ":" in segment
        or not _SAFE_ID_RE.match(segment)
    ):
        raise HTTPException(status_code=400, detail=f"Invalid {label}: {segment!r}")


def create_app(
    settings: Settings | None = None,
    *,
    store: SimulatedStore | None = None,
    adapter: ModelAdapter | None = None,
) -> FastAPI:
    """Create and configure the loopback-only FastAPI application."""
    cfg = settings or load_settings()
    cfg.validate_security_posture()

    sim_store = store or SimulatedStore(
        db_url=cfg.app.db_url,
        store_raw_user_text=cfg.logging.store_raw_user_text,
        confirmation_ttl_seconds=cfg.app.confirmation_ttl_seconds,
    )
    model_adapter = adapter or OpenAICompatibleLocalAdapter(
        cfg.model,
        prompts_dir=Path(cfg.app.prompts_dir),
    )
    gate = LocaleGatePipeline(store=sim_store)
    orchestrator = LocalAgentOrchestrator(adapter=model_adapter, gate=gate, store=sim_store)

    api = FastAPI(
        title="LocaleGate Local API",
        version=__version__,
        description="Loopback-only deterministic execution guard API for locale-sensitive tool calls.",
    )

    # Strict local-only CORS
    api.add_middleware(
        CORSMiddleware,
        allow_origins=list(_ALLOWED_ORIGINS),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Accept"],
    )

    @api.middleware("http")
    async def enforce_loopback_and_body_limits(request: Request, call_next: Any) -> Any:
        # 1. Host header validation (DNS rebinding defense)
        host_hdr = request.headers.get("host")
        if not cfg.app.allow_non_loopback_bind and not _is_allowed_host_header(host_hdr):
            return JSONResponse(
                status_code=403,
                content={"detail": f"Forbidden Host header {host_hdr!r}; loopback host required."},
            )

        # 2. Cross-Origin POST defense
        if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
            origin_hdr = request.headers.get("origin")
            if origin_hdr is not None and origin_hdr not in _ALLOWED_ORIGINS:
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": f"Forbidden cross-origin state-changing request from {origin_hdr!r}."
                    },
                )

        # 3. Body size limit enforcement
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > cfg.app.max_request_body_bytes:
            return JSONResponse(
                status_code=413,
                content={"detail": "Request body exceeds maximum allowed size."},
            )
        body = await request.body()
        if len(body) > cfg.app.max_request_body_bytes:
            return JSONResponse(
                status_code=413,
                content={"detail": "Request body exceeds maximum allowed size."},
            )
        return await call_next(request)

    @api.get("/health")
    @api.get("/api/v1/health")
    def get_health() -> dict[str, Any]:
        db_state = sim_store.get_simulated_state()
        if isinstance(model_adapter, OpenAICompatibleLocalAdapter):
            m_health = model_adapter.check_health()
        else:
            m_health = {"reachable": True, "status": "mock", "model_alias": "mock-adapter"}
        return {
            "status": "ok",
            "version": __version__,
            "default_locale": cfg.app.default_locale,
            "default_timezone": cfg.app.default_timezone,
            "database": {
                "reachable": True,
                "schema_version": db_state["schema_version"],
                "audit_chain_intact": db_state.get("audit_chain_intact", True),
            },
            "model_server": m_health,
            "local_models": cfg.model.discover_local_models(),
        }

    @api.get("/api/v1/config")
    def get_config() -> dict[str, Any]:
        return cfg.to_public_dict()

    @api.post("/api/v1/requests")
    def submit_request(payload: SubmitRequestPayload) -> dict[str, Any]:
        try:
            ctx = TrustedRequestContext(
                request_id=f"req_{uuid.uuid4().hex[:12]}",
                text=payload.text,
                locale=payload.locale,
                timezone=payload.timezone,
                reference_datetime=payload.reference_datetime,
                default_currency=payload.default_currency,
                require_confirmation=payload.require_confirmation,
                dry_run=payload.dry_run,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if payload.untrusted_proposal is not None:
            gate_res = gate.evaluate_raw_json(ctx, payload.untrusted_proposal)
            out = cast(dict[str, Any], json.loads(gate_res.model_dump_json()))
            out["telemetry"] = {"extraction_mode": "direct_proposal_override"}
            return out

        outcome = orchestrator.run_turn(ctx)
        out = cast(dict[str, Any], json.loads(outcome.gate_result.model_dump_json()))
        out["telemetry"] = outcome.telemetry.__dict__
        return out

    @api.get("/api/v1/requests")
    def list_requests_endpoint(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
        return {"items": sim_store.list_requests(limit=limit)}

    @api.get("/api/v1/requests/{identifier}")
    def get_request_endpoint(identifier: str) -> dict[str, Any]:
        _validate_safe_segment(identifier, "request_id")
        record = sim_store.get_request_by_id_or_proposal(identifier)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Request {identifier!r} not found.")
        gate_res = record["gate_result"]
        return {
            "request_id": record["request_id"],
            "proposal_id": record["proposal_id"],
            "locale": record["locale"],
            "timezone": record["timezone"],
            "reference_datetime": record["reference_datetime"],
            "confirmation_status": record["confirmation_status"],
            "business_approval_status": record["business_approval_status"],
            "action_digest": record["action_digest"],
            "gate_result": json.loads(gate_res.model_dump_json()),
        }

    @api.post("/api/v1/requests/{identifier}/confirm")
    def confirm_request_endpoint(
        identifier: str,
        payload: ConfirmRequestPayload,
    ) -> dict[str, Any]:
        _validate_safe_segment(identifier, "request_id")
        try:
            res = sim_store.confirm_and_execute(
                identifier,
                expected_action_digest=payload.action_digest,
                confirmation_token=payload.confirmation_token,
                require_token=True,
            )
            return cast(dict[str, Any], json.loads(res.model_dump_json()))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.post("/api/v1/requests/{identifier}/reject")
    def reject_request_endpoint(identifier: str) -> dict[str, Any]:
        _validate_safe_segment(identifier, "request_id")
        try:
            res = sim_store.reject_pending(identifier)
            return cast(dict[str, Any], json.loads(res.model_dump_json()))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.get("/api/v1/simulated-state")
    def get_simulated_state_endpoint() -> dict[str, Any]:
        return sim_store.get_simulated_state()

    @api.get("/api/v1/audit/verify")
    def verify_audit_chain_endpoint() -> dict[str, Any]:
        intact, count = sim_store.verify_audit_chain()
        return dict(intact=intact, verified_events=count)

    @api.post("/api/v1/reset")
    def reset_simulated_state_endpoint(payload: ResetStatePayload) -> dict[str, Any]:
        if payload.confirm_reset:
            sim_store.reset_all(confirmed=True)
        return {"status": "reset", "simulated_state": sim_store.get_simulated_state()}

    @api.post("/api/v1/evaluations")
    def start_evaluation_endpoint(payload: StartEvaluationPayload) -> dict[str, Any]:
        if payload.suite == "adversarial":
            cases_file = Path("./data/adversarial-v1/cases.jsonl")
        elif payload.suite == "adversarial-v2":
            cases_file = Path("./data/adversarial-v2/cases.jsonl")
        else:
            cases_file = Path(cfg.app.data_dir) / "cases.jsonl"
        if not cases_file.is_file():
            raise HTTPException(status_code=404, detail=f"Dataset {cases_file} not found.")

        eval_adapter = None if payload.model == "oracle-only" else model_adapter
        spec = resolve_known_model_spec(payload.model) if payload.model != "oracle-only" else None
        runner = BenchmarkRunner(settings=cfg, adapter=eval_adapter, model_spec=spec)
        summary = runner.run_evaluation(
            cases_path=cases_file,
            suite=payload.suite,
            profile_name=payload.profile,
            compute_gguf_hash=False,
        )
        return summary

    @api.get("/api/v1/evaluations")
    def list_evaluations_endpoint() -> dict[str, Any]:
        evals_root = (Path(cfg.app.artifacts_dir) / "evaluations").resolve()
        if not evals_root.is_dir():
            return {"items": []}
        items: list[dict[str, Any]] = []
        for child in sorted(evals_root.iterdir(), reverse=True):
            if child.is_dir() and (child / "run.json").is_file():
                try:
                    run_info = json.loads((child / "run.json").read_text(encoding="utf-8"))
                    metrics_info = (
                        json.loads((child / "metrics.json").read_text(encoding="utf-8"))
                        if (child / "metrics.json").is_file()
                        else {}
                    )
                    items.append({"run": run_info, "metrics": metrics_info})
                except Exception:
                    continue
        return {"items": items}

    @api.get("/api/v1/evaluations/{run_id}")
    def get_evaluation_endpoint(run_id: str) -> dict[str, Any]:
        _validate_safe_segment(run_id, "run_id")
        evals_root = (Path(cfg.app.artifacts_dir) / "evaluations").resolve()
        target_dir = (evals_root / run_id).resolve()
        if not target_dir.is_relative_to(evals_root) or not target_dir.is_dir():
            raise HTTPException(status_code=404, detail=f"Evaluation run {run_id!r} not found.")
        run_file = target_dir / "run.json"
        metrics_file = target_dir / "metrics.json"
        if not run_file.is_file():
            raise HTTPException(status_code=404, detail=f"run.json missing for {run_id!r}.")
        return {
            "run": json.loads(run_file.read_text(encoding="utf-8")),
            "metrics": json.loads(metrics_file.read_text(encoding="utf-8"))
            if metrics_file.is_file()
            else {},
        }

    @api.get("/api/v1/evaluations/{run_id}/artifacts")
    def list_evaluation_artifacts_endpoint(run_id: str) -> dict[str, Any]:
        _validate_safe_segment(run_id, "run_id")
        evals_root = (Path(cfg.app.artifacts_dir) / "evaluations").resolve()
        target_dir = (evals_root / run_id).resolve()
        if not target_dir.is_relative_to(evals_root) or not target_dir.is_dir():
            raise HTTPException(status_code=404, detail=f"Evaluation run {run_id!r} not found.")

        files: list[dict[str, Any]] = []
        for fname in sorted(_ALLOWLISTED_ARTIFACT_FILES):
            fpath = (target_dir / fname).resolve()
            if fpath.is_relative_to(target_dir) and fpath.is_file():
                files.append(
                    {
                        "filename": fname,
                        "size_bytes": fpath.stat().st_size,
                        "download_path": f"/api/v1/evaluations/{run_id}/artifacts/{fname}",
                    }
                )
        return {"run_id": run_id, "artifacts": files}

    @api.get("/api/v1/evaluations/{run_id}/artifacts/{filename:path}")
    def download_evaluation_artifact_endpoint(run_id: str, filename: str) -> FileResponse:
        _validate_safe_segment(run_id, "run_id")
        _validate_safe_segment(filename, "filename")
        if filename not in _ALLOWLISTED_ARTIFACT_FILES:
            raise HTTPException(
                status_code=403,
                detail=f"Artifact {filename!r} is not in the allowlisted evaluation artifacts.",
            )
        evals_root = (Path(cfg.app.artifacts_dir) / "evaluations").resolve()
        target_dir = (evals_root / run_id).resolve()
        target_file = (target_dir / filename).resolve()
        if (
            not target_dir.is_relative_to(evals_root)
            or not target_file.is_relative_to(target_dir)
            or not target_file.is_file()
        ):
            raise HTTPException(status_code=404, detail="Requested artifact file not found.")
        return FileResponse(path=target_file, filename=filename)

    @api.get("/api/v1/dataset/cases")
    def list_dataset_cases_endpoint(
        locale: str | None = Query(None),
        suite: str = Query("full"),
        decision: str | None = Query(None),
        limit: int = Query(100, ge=1, le=600),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        if suite == "adversarial":
            cases_file = Path("./data/adversarial-v1/cases.jsonl")
        elif suite == "adversarial-v2":
            cases_file = Path("./data/adversarial-v2/cases.jsonl")
        else:
            cases_file = Path(cfg.app.data_dir) / "cases.jsonl"
        if not cases_file.is_file():
            return {"total": 0, "items": []}
        all_cases = load_benchmark_cases(
            cases_file,
            suite="full" if suite in {"adversarial", "adversarial-v2"} else suite,
            only_audited=True,
        )
        filtered = [
            c
            for c in all_cases
            if (locale is None or c.locale == locale)
            and (decision is None or c.expected_decision == decision)
        ]
        sliced = filtered[offset : offset + limit]
        return {
            "total": len(filtered),
            "offset": offset,
            "limit": limit,
            "items": [
                {
                    "case_id": c.case_id,
                    "locale": c.locale,
                    "timezone": c.timezone,
                    "split": c.split,
                    "suites": list(c.suites),
                    "task_family": c.task_family,
                    "request_text": c.request_text,
                    "tool_expected": c.tool_expected,
                    "expected_decision": c.expected_decision,
                    "expected_arguments": c.expected_arguments,
                    "expected_reason_code": c.expected_reason_code,
                    "paraphrase_group": c.paraphrase_group,
                }
                for c in sliced
            ],
        }

    return api
