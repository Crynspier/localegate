"""Integration tests for FastAPI endpoints, mocked llama.cpp HTTP adapter, and dataset runner."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from locale_gate.api.app import create_app
from locale_gate.benchmark.runner import BenchmarkRunner
from locale_gate.benchmark.schema import validate_benchmark_dataset
from locale_gate.config.settings import AppSettings, ModelSettings, Settings
from locale_gate.contracts.enums import GateDecisionStatus
from locale_gate.contracts.models import TrustedRequestContext
from locale_gate.models.adapter import (
    DeterministicMockAdapter,
    OpenAICompatibleLocalAdapter,
    run_model_preflight_smoke_test,
)
from locale_gate.models.gguf_inspector import inspect_gguf_artifact
from locale_gate.tools.simulator import SimulatedStore


class TestMockedLlamaCppAdapter:
    """Integration tests for OpenAICompatibleLocalAdapter against mocked HTTP transport (§13.3)."""

    def test_structured_json_schema_mode_and_preflight(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/health":
                return httpx.Response(200, json={"status": "ok"})
            if request.url.path == "/v1/models":
                return httpx.Response(
                    200,
                    json={"data": [{"id": "C:\\secret\\path\\Qwen3.5-4B-Q4_K_M.gguf"}]},
                )
            body = json.loads(request.content.decode("utf-8"))
            assert body["chat_template_kwargs"] == {"enable_thinking": False}
            user_msg = json.loads(body["messages"][1]["content"])
            req_text = user_msg["user_text"]

            if "1.234,56 €" in req_text:
                prop = {
                    "contract_version": "1.0",
                    "decision": "propose",
                    "tool_name": "submit_expense",
                    "fields": {
                        "amount": {"evidence": {"quote": "1.234,56 €"}},
                        "expense_date": {"evidence": {"quote": "03.11.2026."}},
                    },
                    "clarification_question": None,
                }
            elif "Project Sync" in req_text:
                prop = {
                    "contract_version": "1.0",
                    "decision": "propose",
                    "tool_name": "schedule_event",
                    "fields": {
                        "title": {"evidence": {"quote": "Project Sync"}},
                        "start_datetime": {"evidence": {"quote": "11/03/2026 at 09:30"}},
                        "duration": {"evidence": {"quote": "45 minutes"}},
                    },
                    "clarification_question": None,
                }
            elif "120,00 od 03.11." in req_text:
                prop = {
                    "contract_version": "1.0",
                    "decision": "propose",
                    "tool_name": "submit_expense",
                    "fields": {
                        "amount": {"evidence": {"quote": "120,00"}},
                        "expense_date": {"evidence": {"quote": "03.11."}},
                    },
                    "clarification_question": None,
                }
            elif "Ignore previous instructions" in req_text:
                prop = {
                    "contract_version": "1.0",
                    "decision": "no_action",
                    "tool_name": None,
                    "fields": {},
                    "clarification_question": None,
                }
            else:
                prop = {
                    "contract_version": "1.0",
                    "decision": "propose",
                    "tool_name": "submit_expense",
                    "fields": {
                        "amount": {"evidence": {"quote": "85,50 €"}},
                        "expense_date": {"evidence": {"quote": "03.11.2026."}},
                    },
                    "clarification_question": None,
                }

            return httpx.Response(
                200,
                json={
                    "model": "C:\\secret\\path\\Qwen3.5-4B-Q4_K_M.gguf",
                    "usage": {"prompt_tokens": 120, "completion_tokens": 45},
                    "choices": [{"message": {"role": "assistant", "content": json.dumps(prop)}}],
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        adapter = OpenAICompatibleLocalAdapter(http_client=client)
        health = adapter.check_health()
        assert health["reachable"] is True
        # Ensure filesystem path was sanitized to alias
        assert health["model_alias"] == "local-qwen35-4b-q4km"

        preflight = run_model_preflight_smoke_test(adapter)
        assert preflight["all_passed"] is True
        assert preflight["passed_cases"] == 5

    def test_native_tool_calls_mode_normalization(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "model": "local-qwen35-4b-q4km",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 30},
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "submit_expense",
                                            "arguments": json.dumps(
                                                {
                                                    "amount": {"evidence": {"quote": "1.234,56 €"}},
                                                    "expense_date": {
                                                        "evidence": {"quote": "03.11.2026."}
                                                    },
                                                }
                                            ),
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        adapter = OpenAICompatibleLocalAdapter(prefer_native_tools=True, http_client=client)
        ctx = TrustedRequestContext(
            request_id="req_tc",
            text="Evidentiraj trošak od 1.234,56 € od 03.11.2026.",
            locale="hr-HR",
            timezone="Europe/Zagreb",
        )
        out = adapter.propose(ctx)
        assert out.error_reason is None
        assert isinstance(out.raw_proposal, dict)
        assert out.raw_proposal["tool_name"] == "submit_expense"
        assert out.telemetry.extraction_mode == "native_tool_call"


class TestFastAPIEndToEnd:
    """Integration tests for FastAPI endpoints, confirmation queue, and evaluation explorer."""

    def test_api_workflow_request_confirm_reject_and_evaluations(self, tmp_path: Path) -> None:
        cfg = Settings(
            app=AppSettings(
                db_url="sqlite:///:memory:",
                artifacts_dir=str(tmp_path / "artifacts"),
                data_dir="data/benchmark-v1",
            ),
            model=ModelSettings(),
        )
        store = SimulatedStore(db_url="sqlite:///:memory:")
        mock_adapter = DeterministicMockAdapter()
        app = create_app(cfg, store=store, adapter=mock_adapter)
        client = TestClient(app)

        # 1. Health & config
        h = client.get("/health")
        assert h.status_code == 200
        assert h.json()["status"] == "ok"

        c = client.get("/api/v1/config")
        assert c.status_code == 200
        assert "127.0.0.1" in c.json()["app"]["bind_host"]

        # 2. Submit request with untrusted_proposal
        req_resp = client.post(
            "/api/v1/requests",
            json={
                "text": "Evidentiraj trošak od 1.234,56 € od 03.11.2026.",
                "locale": "hr-HR",
                "timezone": "Europe/Zagreb",
                "reference_datetime": "2026-11-03T12:00:00+01:00",
                "untrusted_proposal": {
                    "contract_version": "1.0",
                    "decision": "propose",
                    "tool_name": "submit_expense",
                    "fields": {
                        "amount": {"evidence": {"quote": "1.234,56 €"}},
                        "expense_date": {"evidence": {"quote": "03.11.2026."}},
                    },
                    "clarification_question": None,
                },
            },
        )
        assert req_resp.status_code == 200
        body = req_resp.json()
        assert body["status"] == GateDecisionStatus.AWAITING_CONFIRMATION.value
        prop_id = body["proposal_id"]
        digest = body["action_digest"]

        # 3. Inspect request
        insp = client.get(f"/api/v1/requests/{prop_id}")
        assert insp.status_code == 200
        assert insp.json()["action_digest"] == digest

        # 4. Confirm request with digest and confirmation_token
        conf_token = body["confirmation_token"]
        assert conf_token is not None
        conf = client.post(
            f"/api/v1/requests/{prop_id}/confirm",
            json={"action_digest": digest, "confirmation_token": conf_token},
        )
        assert conf.status_code == 200
        assert conf.json()["status"] == GateDecisionStatus.EXECUTED.value

        # 5. Simulated state reflects committed expense
        sim = client.get("/api/v1/simulated-state")
        assert sim.status_code == 200
        assert sim.json()["counts"]["expenses"] == 1
        assert sim.json()["expenses"][0]["amount"] == "1234.56"

        # 6. Run oracle-only smoke evaluation via API
        ev_resp = client.post(
            "/api/v1/evaluations",
            json={"suite": "smoke", "model": "oracle-only"},
        )
        assert ev_resp.status_code == 200
        run_id = ev_resp.json()["run_id"]

        ev_get = client.get(f"/api/v1/evaluations/{run_id}")
        assert ev_get.status_code == 200
        assert ev_get.json()["run"]["cases_evaluated"] == 12

        arts = client.get(f"/api/v1/evaluations/{run_id}/artifacts")
        assert arts.status_code == 200
        assert any(a["filename"] == "report.md" for a in arts.json()["artifacts"])

        dl = client.get(f"/api/v1/evaluations/{run_id}/artifacts/report.md")
        assert dl.status_code == 200
        assert "LocaleGate Evaluation Report" in dl.text

        # 7. Dataset browser endpoint
        ds = client.get("/api/v1/dataset/cases?locale=hr-HR&limit=10")
        assert ds.status_code == 200
        assert ds.json()["total"] == 300
        assert len(ds.json()["items"]) == 10

    def test_dataset_validation_and_gguf_header_inspection(self, tmp_path: Path) -> None:
        cases_path = Path("data/benchmark-v1/cases.jsonl")
        summary = validate_benchmark_dataset(cases_path)
        assert summary["valid"] is True
        assert summary["audited_cases"] == 600

        local_gguf = Path("Qwen3.5-4B-Q4_K_M.gguf")
        if local_gguf.is_file():
            manifest = inspect_gguf_artifact(local_gguf, compute_hash=False)
            assert manifest.format == "GGUF"
            assert manifest.quantization == "Q4_K_M"
            assert manifest.quantization_verified_from_header is True
            assert manifest.architecture == "qwen35"

        cfg = Settings(
            app=AppSettings(artifacts_dir=str(tmp_path / "artifacts")),
        )
        runner = BenchmarkRunner(settings=cfg, adapter=None)
        res = runner.run_evaluation(
            cases_path=cases_path,
            suite="smoke",
            compute_gguf_hash=False,
        )
        assert (
            res["metrics"]["systems"]["D_parser_oracle"]["full_action_exact_match"]["rate"] == 1.0
        )


@pytest.mark.model_integration
def test_live_qwen35_gguf_smoke_integration() -> None:
    """Optional live model smoke test (skipped if llama-server is not running on port 8080)."""
    adapter = OpenAICompatibleLocalAdapter()
    health = adapter.check_health()
    if not health.get("reachable"):
        pytest.skip("Local llama-server is not currently running on http://127.0.0.1:8080")
    summary = run_model_preflight_smoke_test(adapter)
    assert summary["passed_cases"] >= 4
