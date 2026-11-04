"""Comprehensive branch-coverage and CLI/audit/benchmark tests for LocaleGate."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from typer.testing import CliRunner

from locale_gate.agent.orchestrator import LocalAgentOrchestrator
from locale_gate.audit.logger import JsonlAuditLogger
from locale_gate.benchmark.runner import BenchmarkRunner, compare_two_runs
from locale_gate.cli.main import app
from locale_gate.config.settings import AppSettings, Settings
from locale_gate.contracts.enums import (
    ExpenseCategory,
    GateDecisionStatus,
    ParseStatus,
    ReasonCode,
)
from locale_gate.contracts.models import EvidenceQuote, TrustedRequestContext
from locale_gate.evidence.resolver import resolve_evidence_quote
from locale_gate.gate.baselines import (
    ParserOraclePipeline,
    RawModelBaselinePipeline,
    SchemaOnlyBaselinePipeline,
)
from locale_gate.gate.pipeline import LocaleGatePipeline
from locale_gate.models.adapter import DeterministicMockAdapter
from locale_gate.models.gguf_inspector import inspect_gguf_artifact
from locale_gate.parsers.date_time import parse_date, parse_datetime, parse_duration
from locale_gate.parsers.money import parse_currency, parse_money
from locale_gate.policy.rules import build_clarification_prompt, get_policy_profile
from locale_gate.tools.registry import parse_expense_category
from locale_gate.tools.simulator import SimulatedStore

REF_DT = datetime.fromisoformat("2026-11-03T12:00:00+01:00")


class TestAdditionalParserAndPolicyBranches:
    def test_currency_and_money_edge_branches(self) -> None:
        # Empty currency
        assert parse_currency("", "hr-HR").status == ParseStatus.MISSING
        # Lookalike in currency
        assert parse_currency("USD\uff0e", "en-US").reason_code == ReasonCode.CURRENCY_UNSUPPORTED
        # Ambiguous dollar symbol CA$
        assert parse_currency("CA$", "en-US").status == ParseStatus.AMBIGUOUS
        # Unambiguous £ and ¥
        assert parse_currency("£", "hr-HR").value == "GBP"
        assert parse_currency("¥", "en-US").value == "JPY"
        assert parse_currency("US$", "en-GB").value == "USD"
        # Lowercase ISO 4217 code rejected
        assert parse_currency("eur", "hr-HR").status == ParseStatus.INVALID
        # Unrecognized currency code
        assert parse_currency("XYZ", "en-US").reason_code == ReasonCode.CURRENCY_UNSUPPORTED

        # Money edge branches: empty, unsupported locale, multiple signs, suffix symbols
        assert parse_money("  ", "hr-HR").status == ParseStatus.MISSING
        assert parse_money("10 €", "de-DE").reason_code == ReasonCode.LOCALE_UNSUPPORTED
        assert (
            parse_money("€ -10,00", "hr-HR").is_ok
            and parse_money("€ -10,00", "hr-HR").value is not None
        )
        assert parse_money("+-10,00 €", "hr-HR").reason_code == ReasonCode.MONEY_INVALID_FORMAT
        assert parse_money("100 £", "en-GB").value is not None
        assert parse_money("100 ¥", "en-US").value is not None
        assert parse_money("100 US$", "hr-HR").value is not None
        assert parse_money("100 $", "en-US").value is not None
        assert parse_money("100 $", "hr-HR").reason_code == ReasonCode.CURRENCY_MISSING
        assert parse_money("EUR 250,50", "hr-HR").value is not None
        assert parse_money("XYZ 250,50", "hr-HR").reason_code == ReasonCode.CURRENCY_UNSUPPORTED
        # Embedded + explicit currency conflict
        assert (
            parse_money("100,00 €", "hr-HR", explicit_currency_quote="USD").reason_code
            == ReasonCode.CURRENCY_CONFLICT
        )
        # Explicit currency invalid
        assert (
            parse_money("100,00", "hr-HR", explicit_currency_quote="DEM").reason_code
            == ReasonCode.CURRENCY_UNSUPPORTED
        )
        # Unlocalized plain integer with default currency
        res_plain = parse_money("100", None, trusted_default_currency="EUR")
        assert res_plain.is_ok and res_plain.value is not None
        assert res_plain.value.to_dict()["amount"] == "100.00"
        # Unlocalized decimal fails closed
        assert (
            parse_money("100.50", None, trusted_default_currency="EUR").reason_code
            == ReasonCode.MONEY_AMBIGUOUS_LOCALE
        )
        # Leading zeros rejected
        assert parse_money("012,50 €", "hr-HR").reason_code == ReasonCode.MONEY_INVALID_FORMAT
        assert parse_money("012 €", "hr-HR").reason_code == ReasonCode.MONEY_INVALID_FORMAT
        # Incomplete decimal
        assert parse_money(",50 €", "hr-HR").reason_code == ReasonCode.MONEY_INVALID_FORMAT
        # Mixed grouping separators
        assert (
            parse_money("1.234\u00a0567,89 €", "hr-HR").reason_code
            == ReasonCode.MONEY_INVALID_GROUPING
        )

    def test_date_time_and_duration_edge_branches(self) -> None:
        # Empty / unsupported locale / lookalikes
        assert parse_date("", "hr-HR").status == ParseStatus.MISSING
        assert parse_date("2026-11-03", "de-DE").reason_code == ReasonCode.LOCALE_UNSUPPORTED
        assert (
            parse_date("2026\u221211\u221203", "hr-HR").reason_code
            == ReasonCode.DATE_INVALID_FORMAT
        )

        # Weekday prefix matching and mismatching
        res_wd_ok = parse_date("utorak, 03.11.2026.", "hr-HR", reference_datetime=REF_DT)
        assert res_wd_ok.is_ok and res_wd_ok.value is not None
        assert res_wd_ok.value.to_dict()["iso_date"] == "2026-11-03"

        res_wd_bad = parse_date("srijeda, 03.11.2026.", "hr-HR", reference_datetime=REF_DT)
        assert res_wd_bad.reason_code == ReasonCode.DATE_WEEKDAY_MISMATCH

        # Relative weekday disabled by default, enabled when allow_relative_weekday=True
        assert (
            parse_date("sljedeći petak", "hr-HR", reference_datetime=REF_DT).status
            == ParseStatus.AMBIGUOUS
        )
        res_rel_wd = parse_date(
            "sljedeći petak",
            "hr-HR",
            reference_datetime=REF_DT,
            allow_relative_weekday=True,
        )
        assert res_rel_wd.is_ok and res_rel_wd.value is not None
        assert res_rel_wd.value.iso_date == "2026-11-06"

        # Croatian genitive month and English Month Day, Year / Day Month Year
        assert parse_date("3. studenoga 2026.", "hr-HR").is_ok
        assert parse_date("November 3, 2026", "en-US").is_ok
        assert parse_date("3 November 2026", "en-GB").is_ok
        assert parse_date("3 November", "en-GB").reason_code == ReasonCode.DATE_YEAR_MISSING
        assert parse_date("November 3", "en-US").reason_code == ReasonCode.DATE_YEAR_MISSING

        # Datetime edge cases: missing date (time only), ISO Z, explicit offset, 12h AM/PM
        assert (
            parse_datetime("09:30", "hr-HR", trusted_timezone="Europe/Zagreb").status
            == ParseStatus.AMBIGUOUS
        )
        dt_conflict = parse_datetime(
            "2026-11-03T08:30:00Z", "en-GB", trusted_timezone="Europe/Zagreb"
        )
        assert dt_conflict.reason_code == ReasonCode.TIMEZONE_CONFLICT
        dt_z = parse_datetime("2026-11-03T08:30:00Z", "en-GB", trusted_timezone="UTC")
        assert dt_z.is_ok and dt_z.value is not None
        assert dt_z.value.to_dict()["rfc3339"].endswith("+00:00")

        # Croatian 'sati' and 'h' suffix, English 'o'clock' missing AM/PM
        assert parse_datetime(
            "03.11.2026. u 9 sati", "hr-HR", trusted_timezone="Europe/Zagreb"
        ).is_ok
        assert parse_datetime(
            "03.11.2026. u 14:30h", "hr-HR", trusted_timezone="Europe/Zagreb"
        ).is_ok
        assert (
            parse_datetime(
                "11/03/2026 at 5 o'clock", "en-US", trusted_timezone="Europe/Zagreb"
            ).reason_code
            == ReasonCode.DATETIME_AMPM_MISSING
        )
        assert (
            parse_datetime(
                "03.11.2026. u 25:00", "hr-HR", trusted_timezone="Europe/Zagreb"
            ).reason_code
            == ReasonCode.DATETIME_INVALID_TIME
        )
        assert (
            parse_datetime(
                "11/03/2026 at 13:00 pm", "en-US", trusted_timezone="Europe/Zagreb"
            ).reason_code
            == ReasonCode.DATETIME_INVALID_TIME
        )

        # Duration edge cases
        assert parse_duration("", "hr-HR").status == ParseStatus.MISSING
        assert parse_duration("0 min", "en-US").reason_code == ReasonCode.DURATION_INVALID
        assert parse_duration("1h 30m", "en-GB").value is not None
        assert parse_duration("2 sata i 15 minuta", "hr-HR").value is not None
        assert parse_duration("abc hours", "en-US").reason_code == ReasonCode.DURATION_INVALID
        d_ok = parse_duration("45 min", "en-US")
        assert d_ok.value is not None and d_ok.value.to_dict()["minutes"] == 45

    def test_evidence_and_policy_helpers(self) -> None:
        assert (
            resolve_evidence_quote(
                "hello world", EvidenceQuote(quote="hello", context_excerpt="missing")
            ).reason_code
            == ReasonCode.EVIDENCE_CONTEXT_NOT_FOUND
        )
        assert (
            resolve_evidence_quote(
                "hello world and hello world",
                EvidenceQuote(quote="hello", context_excerpt="hello world"),
            ).reason_code
            == ReasonCode.EVIDENCE_CONTEXT_AMBIGUOUS
        )
        assert (
            resolve_evidence_quote(
                "alpha beta gamma",
                EvidenceQuote(quote="gamma", context_excerpt="alpha beta"),
            ).reason_code
            == ReasonCode.EVIDENCE_QUOTE_OUTSIDE_CONTEXT
        )
        prof = get_policy_profile("research_simulation_v1")
        assert prof.require_user_confirmation_for_writes is True
        q_hr = build_clarification_prompt(ReasonCode.CURRENCY_MISSING, "hr-HR")
        q_en = build_clarification_prompt(ReasonCode.CURRENCY_MISSING, "en-US")
        assert "valutu" in q_hr and "currency" in q_en
        cat, err = parse_expense_category("smještaj")
        assert cat == ExpenseCategory.LODGING and err is None
        cat_bad, err_bad = parse_expense_category("invalid_cat")
        assert cat_bad is None and err_bad == ReasonCode.CATEGORY_INVALID

    def test_pipeline_schedule_event_and_expense_validation_branches(self) -> None:
        store = SimulatedStore(db_url="sqlite:///:memory:")
        gate = LocaleGatePipeline(store=store)

        # 1. Invalid JSON and non-dict JSON
        ctx = TrustedRequestContext(
            request_id="pipe_1",
            text="Evidentiraj trošak od 150000,00 € od 03.11.2026. za putovanje (opis: Konferencija).",
            locale="hr-HR",
            timezone="Europe/Zagreb",
            reference_datetime=REF_DT,
        )
        assert gate.evaluate_raw_json(ctx, "{not_json").status == GateDecisionStatus.MODEL_ERROR
        assert gate.evaluate_raw_json(ctx, "[1, 2, 3]").status == GateDecisionStatus.BLOCKED

        # 2. Exceeds max policy limit
        res_max = gate.evaluate_raw_json(
            ctx,
            {
                "contract_version": "1.0",
                "decision": "propose",
                "tool_name": "submit_expense",
                "fields": {
                    "amount": {"evidence": {"quote": "150000,00 €"}},
                    "expense_date": {"evidence": {"quote": "03.11.2026."}},
                },
                "clarification_question": None,
            },
        )
        assert res_max.reason_code == ReasonCode.MONEY_EXCEEDS_MAX_LIMIT

        # 3. Valid expense with explicit currency, category, description, and require_confirmation=False
        ctx_imm = TrustedRequestContext(
            request_id="pipe_imm",
            text="Evidentiraj trošak od 250,00 u valuti EUR od 03.11.2026. za putovanje - Konferencija.",
            locale="hr-HR",
            timezone="Europe/Zagreb",
            reference_datetime=REF_DT,
            require_confirmation=False,
        )
        res_imm = gate.evaluate_raw_json(
            ctx_imm,
            {
                "contract_version": "1.0",
                "decision": "propose",
                "tool_name": "submit_expense",
                "fields": {
                    "amount": {"evidence": {"quote": "250,00"}},
                    "currency": {"evidence": {"quote": "EUR"}},
                    "expense_date": {"evidence": {"quote": "03.11.2026."}},
                    "category": {"evidence": {"quote": "putovanje"}},
                    "description": {"evidence": {"quote": "Konferencija"}},
                },
                "clarification_question": None,
            },
        )
        assert res_imm.status == GateDecisionStatus.EXECUTED
        assert res_imm.simulated_effect is not None

        # 4. Schedule event with end_datetime before start_datetime, and conflicting end_datetime + duration
        ctx_evt_before = TrustedRequestContext(
            request_id="pipe_evt_before",
            text="Zakaži 'Pregled' od 03.11.2026. u 10:00 do 03.11.2026. u 09:00.",
            locale="hr-HR",
            timezone="Europe/Zagreb",
            reference_datetime=REF_DT,
        )
        res_before = gate.evaluate_raw_json(
            ctx_evt_before,
            {
                "contract_version": "1.0",
                "decision": "propose",
                "tool_name": "schedule_event",
                "fields": {
                    "title": {"evidence": {"quote": "Pregled"}},
                    "start_datetime": {"evidence": {"quote": "03.11.2026. u 10:00"}},
                    "end_datetime": {"evidence": {"quote": "03.11.2026. u 09:00"}},
                },
                "clarification_question": None,
            },
        )
        assert res_before.reason_code == ReasonCode.EVENT_END_BEFORE_START

        ctx_evt_conflict = TrustedRequestContext(
            request_id="pipe_evt_conflict",
            text="Zakaži 'Pregled' od 03.11.2026. u 10:00 do 03.11.2026. u 11:00 uz trajanje od 30 minuta.",
            locale="hr-HR",
            timezone="Europe/Zagreb",
            reference_datetime=REF_DT,
        )
        res_conflict = gate.evaluate_raw_json(
            ctx_evt_conflict,
            {
                "contract_version": "1.0",
                "decision": "propose",
                "tool_name": "schedule_event",
                "fields": {
                    "title": {"evidence": {"quote": "Pregled"}},
                    "start_datetime": {"evidence": {"quote": "03.11.2026. u 10:00"}},
                    "end_datetime": {"evidence": {"quote": "03.11.2026. u 11:00"}},
                    "duration": {"evidence": {"quote": "30 minuta"}},
                },
                "clarification_question": None,
            },
        )
        assert res_conflict.reason_code == ReasonCode.EVENT_END_AND_DURATION_CONFLICT


class TestBaselinesAuditAndOrchestrator:
    def test_baselines_and_oracle_on_schedule_event_and_errors(self) -> None:
        ctx = TrustedRequestContext(
            request_id="base_evt_1",
            text="Schedule event 'Architecture Review' on 11/03/2026 at 09:30 for 45 minutes.",
            locale="en-US",
            timezone="Europe/Zagreb",
            reference_datetime=REF_DT,
        )
        prop = {
            "contract_version": "1.0",
            "decision": "propose",
            "tool_name": "schedule_event",
            "fields": {
                "title": {"evidence": {"quote": "Architecture Review"}},
                "start_datetime": {"evidence": {"quote": "11/03/2026 at 09:30"}},
                "duration": {"evidence": {"quote": "45 minutes"}},
            },
            "clarification_question": None,
        }
        raw_p = RawModelBaselinePipeline()
        sch_p = SchemaOnlyBaselinePipeline()
        ora_p = ParserOraclePipeline()

        assert raw_p.evaluate_raw_json(ctx, "{bad").status == GateDecisionStatus.MODEL_ERROR
        assert sch_p.evaluate_raw_json(ctx, "{bad").status == GateDecisionStatus.MODEL_ERROR
        assert raw_p.evaluate_raw_json(ctx, prop).status == GateDecisionStatus.EXECUTED
        assert sch_p.evaluate_raw_json(ctx, prop).status == GateDecisionStatus.EXECUTED

        ora_res = ora_p.evaluate_gold_evidence(ctx, prop)
        assert ora_res.status == GateDecisionStatus.AWAITING_CONFIRMATION

    def test_audit_logger_and_orchestrator(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        logger = JsonlAuditLogger(log_path=log_path, store_raw_user_text=False)
        store = SimulatedStore(db_url="sqlite:///:memory:")
        gate = LocaleGatePipeline(store=store)
        mock_adapter = DeterministicMockAdapter()
        ctx = TrustedRequestContext(
            request_id="orch_1",
            text="Evidentiraj trošak od 1.234,56 € od 03.11.2026.",
            locale="hr-HR",
            timezone="Europe/Zagreb",
            reference_datetime=REF_DT,
        )
        mock_adapter.register(
            "orch_1",
            {
                "contract_version": "1.0",
                "decision": "propose",
                "tool_name": "submit_expense",
                "fields": {
                    "amount": {"evidence": {"quote": "1.234,56 €"}},
                    "expense_date": {"evidence": {"quote": "03.11.2026."}},
                },
                "clarification_question": None,
            },
        )
        orch = LocalAgentOrchestrator(adapter=mock_adapter, gate=gate)
        outcome = orch.run_turn(ctx)
        assert outcome.gate_result.status == GateDecisionStatus.AWAITING_CONFIRMATION

        logger.append_decision(
            outcome.gate_result,
            locale=ctx.locale,
            timezone_name=ctx.timezone,
            request_text=ctx.text,
            extra_metadata={"test": True},
        )
        assert log_path.exists()
        logged_line = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert "request_text" not in logged_line


class TestBenchmarkRunnerAndCLI:
    def test_benchmark_runner_and_compare_with_mock_adapter(self, tmp_path: Path) -> None:
        settings = Settings(
            app=AppSettings(
                artifacts_dir=str(tmp_path / "artifacts"),
                data_dir="data/benchmark-v1",
            )
        )
        mock_adapter = DeterministicMockAdapter()
        runner = BenchmarkRunner(settings=settings, adapter=mock_adapter)
        cases_path = Path("data/benchmark-v1/cases.jsonl")
        res1 = runner.run_evaluation(
            cases_path=cases_path,
            suite="smoke",
            profile_name="qwen35_q4km_primary",
            run_id="test_run_1",
            compute_gguf_hash=False,
        )
        runner.run_evaluation(
            cases_path=cases_path,
            suite="smoke",
            profile_name="qwen35_q4km_primary",
            run_id="test_run_2",
            compute_gguf_hash=False,
        )
        assert res1["run"]["suite"] == "smoke"
        assert (tmp_path / "artifacts" / "evaluations" / "test_run_1" / "report.md").exists()

        comp = compare_two_runs(
            tmp_path / "artifacts" / "evaluations" / "test_run_1",
            tmp_path / "artifacts" / "evaluations" / "test_run_2",
        )
        assert comp["run_a"] == "test_run_1"

    def test_cli_commands_smoke(self, tmp_path: Path) -> None:
        cli = CliRunner()
        res_doc = cli.invoke(app, ["doctor", "--skip-hash"])
        assert res_doc.exit_code == 0
        assert "0.1.0" in res_doc.stdout

        res_parse = cli.invoke(
            app,
            ["parse", "money", "1.234,56 €", "--locale", "hr-HR"],
        )
        assert res_parse.exit_code == 0
        assert "1234.56" in res_parse.stdout

        res_date = cli.invoke(
            app,
            ["parse", "date", "03.11.2026.", "--locale", "hr-HR"],
        )
        assert res_date.exit_code == 0
        assert "2026-11-03" in res_date.stdout

        res_dt = cli.invoke(
            app,
            [
                "parse",
                "datetime",
                "03.11.2026. u 09:30",
                "--locale",
                "hr-HR",
                "--timezone",
                "Europe/Zagreb",
            ],
        )
        assert res_dt.exit_code == 0
        assert "2026-11-03T09:30:00+01:00" in res_dt.stdout

        res_val = cli.invoke(app, ["eval", "validate-dataset"])
        assert res_val.exit_code == 0
        assert '"valid": true' in res_val.stdout

        # Test GGUF header inspection on a synthetic miniature GGUF header
        fake_gguf = tmp_path / "mini.gguf"
        fake_gguf.write_bytes(
            b"GGUF"
            + (3).to_bytes(4, "little")
            + (0).to_bytes(8, "little")
            + (0).to_bytes(8, "little")
        )
        meta = inspect_gguf_artifact(fake_gguf, compute_hash=True)
        assert meta.gguf_version == 3
        assert meta.file_size_bytes == 24
