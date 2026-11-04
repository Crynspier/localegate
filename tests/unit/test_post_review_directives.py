"""Comprehensive tests for P0/P1/P2 Post-README Review & Engineering Directives."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from locale_gate.api.app import create_app
from locale_gate.benchmark.schema import BenchmarkCase
from locale_gate.canonical.types import get_tzdb_provenance
from locale_gate.config.settings import (
    KNOWN_LOCAL_MODELS,
    AppSettings,
    Settings,
    resolve_known_model_spec,
)
from locale_gate.contracts.enums import (
    GateDecisionStatus,
    ParseStatus,
    ReasonCode,
    RoleBindingStatus,
)
from locale_gate.contracts.models import (
    DuplicateJSONKeyError,
    EvidenceQuote,
    TrustedRequestContext,
    UntrustedJSONBoundsError,
    parse_strict_untrusted_json,
)
from locale_gate.evidence.resolver import (
    code_point_to_utf16_offset,
    find_all_nfc_equivalent_occurrences,
    is_valid_grapheme_boundary,
    resolve_evidence_quote,
    scan_unicode_threats,
    utf16_to_code_point_offset,
)
from locale_gate.gate.baselines import (
    EvidenceOnlyNaiveParserAblationPipeline,
    LocaleParserNoEvidenceAblationPipeline,
    NoRoleBindingAblationPipeline,
    StrongConventionalBaselinePipeline,
)
from locale_gate.gate.pipeline import LocaleGatePipeline
from locale_gate.locales.profiles import (
    ISO_4217_TABLE_VERSION,
    validate_currency_for_date,
)
from locale_gate.models.adapter import (
    DeterministicMockAdapter,
    _strip_thinking_and_markdown_fences,
)
from locale_gate.parsers.money import parse_money
from locale_gate.tools.registry import (
    generate_confirmation_capability_token,
    verify_confirmation_capability_token,
)
from locale_gate.tools.simulator import SimulatedStore

REF_DT = datetime.fromisoformat("2026-11-03T12:00:00+01:00")


class TestP0SemanticRoleBindingAndCompetingCandidates:
    """Tests for §2.1 & §3.2 Semantic Role-Binding and Competing Candidates."""

    def test_wrong_span_decoy_budget_amount_blocked(self) -> None:
        gate = LocaleGatePipeline()
        ctx = TrustedRequestContext(
            request_id="rb_decoy_1",
            text=(
                "Preostali budžet projekta je 1.500,00 €, a danas evidentiraj "
                "trošak taksija od 45,00 € od 03.11.2026."
            ),
            locale="hr-HR",
            timezone="Europe/Zagreb",
            reference_datetime=REF_DT,
        )
        res = gate.evaluate_raw_json(
            ctx,
            {
                "contract_version": "1.0",
                "decision": "propose",
                "tool_name": "submit_expense",
                "fields": {
                    "amount": {"evidence": {"quote": "1.500,00 €"}},
                    "expense_date": {"evidence": {"quote": "03.11.2026."}},
                },
                "clarification_question": None,
            },
        )
        assert res.status == GateDecisionStatus.BLOCKED
        assert res.reason_code == ReasonCode.FIELD_ROLE_MISMATCH
        amt_diag = next(d for d in res.field_diagnostics if d.field_name == "amount")
        assert amt_diag.role_binding_status == RoleBindingStatus.MISMATCH
        assert amt_diag.candidate_count == 2
        assert "45,00 €" in amt_diag.competing_candidates

    def test_correct_span_with_decoy_succeeds(self) -> None:
        gate = LocaleGatePipeline()
        ctx = TrustedRequestContext(
            request_id="rb_decoy_ok",
            text=(
                "Preostali budžet projekta je 1.500,00 €, a danas evidentiraj "
                "trošak taksija od 45,00 € od 03.11.2026."
            ),
            locale="hr-HR",
            timezone="Europe/Zagreb",
            reference_datetime=REF_DT,
        )
        res = gate.evaluate_raw_json(
            ctx,
            {
                "contract_version": "1.0",
                "decision": "propose",
                "tool_name": "submit_expense",
                "fields": {
                    "amount": {"evidence": {"quote": "45,00 €"}},
                    "expense_date": {"evidence": {"quote": "03.11.2026."}},
                },
                "clarification_question": None,
            },
        )
        assert res.status == GateDecisionStatus.AWAITING_CONFIRMATION
        assert res.canonical_arguments["amount"] == "45.00"
        amt_diag = next(d for d in res.field_diagnostics if d.field_name == "amount")
        assert amt_diag.role_binding_status == RoleBindingStatus.VERIFIED
        assert amt_diag.candidate_count == 2

    def test_ambiguous_competing_amounts_trigger_clarification(self) -> None:
        gate = LocaleGatePipeline()
        ctx = TrustedRequestContext(
            request_id="rb_ambig_1",
            text="Imamo račune od 45,00 € i 60,00 € od 03.11.2026. za obradu.",
            locale="hr-HR",
            timezone="Europe/Zagreb",
            reference_datetime=REF_DT,
        )
        res = gate.evaluate_raw_json(
            ctx,
            {
                "contract_version": "1.0",
                "decision": "propose",
                "tool_name": "submit_expense",
                "fields": {
                    "amount": {"evidence": {"quote": "45,00 €"}},
                    "expense_date": {"evidence": {"quote": "03.11.2026."}},
                },
                "clarification_question": None,
            },
        )
        assert res.status == GateDecisionStatus.NEEDS_CLARIFICATION
        assert res.reason_code == ReasonCode.FIELD_ROLE_AMBIGUOUS

    def test_wrong_date_span_blocked_in_schedule_event(self) -> None:
        gate = LocaleGatePipeline()
        ctx = TrustedRequestContext(
            request_id="rb_date_1",
            text="Zakaži sastanak 'Pregled' od 03.11.2026. u 09:00 do 03.11.2026. u 10:00.",
            locale="hr-HR",
            timezone="Europe/Zagreb",
            reference_datetime=REF_DT,
        )
        res = gate.evaluate_raw_json(
            ctx,
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
        assert res.status == GateDecisionStatus.BLOCKED
        assert res.reason_code == ReasonCode.FIELD_ROLE_MISMATCH


class TestP1UnicodeAndEvidenceHardening:
    """Tests for §3.3 Evidence Resolution & Unicode Security Hardening."""

    def test_unicode_zero_width_and_bidi_override_blocked(self) -> None:
        assert scan_unicode_threats("45\u200b,00 €") is not None
        assert scan_unicode_threats("45,00 \u202e€") is not None
        # Mixed Cyrillic 'е' (\u0435) + Latin 'u' + 'r'
        assert scan_unicode_threats("45,00 \u0435ur") is not None

        res = resolve_evidence_quote(
            "Evidentiraj trošak od 45\u200b,00 € od 03.11.2026.",
            EvidenceQuote(quote="45\u200b,00 €"),
        )
        assert res.status != ParseStatus.PARSED
        assert res.reason_code == ReasonCode.EVIDENCE_SUSPICIOUS_UNICODE

    def test_nfd_to_nfc_normalization_span_mapping(self) -> None:
        # Source text has NFD decomposed 'š' ('s' + '\u030c') before the quote
        source_nfd = "Evidentiraj tros\u030cak od 120,00 €."
        quote_nfc = "120,00 €"
        matches = find_all_nfc_equivalent_occurrences(source_nfd, quote_nfc)
        assert len(matches) == 1
        start, end = matches[0]
        assert source_nfd[start:end] == "120,00 €"

        # Quote in NFC matching NFD source substring
        res = resolve_evidence_quote(source_nfd, EvidenceQuote(quote="tro\u0161ak"))
        assert res.status == ParseStatus.PARSED
        assert res.resolved is not None
        assert source_nfd[res.resolved.start : res.resolved.end] == "tros\u030cak"
        assert res.resolved.normalization_form_matched == "NFC"

    def test_grapheme_boundary_rejects_split_combining_mark(self) -> None:
        text = "tros\u030cak"
        # Index 4 splits 's' (index 3) and '\u030c' (index 4)
        assert not is_valid_grapheme_boundary(text, 4)
        assert is_valid_grapheme_boundary(text, 0)
        assert is_valid_grapheme_boundary(text, 5)

    def test_utf16_offset_conversion_with_astral_codepoints(self) -> None:
        # '🧾' (U+1F9FE) takes 1 Unicode code point in Python, 2 UTF-16 code units in JS
        text = "Račun 🧾 iznosi 45,00 €"
        cp_start = text.index("45,00 €")
        cp_end = cp_start + len("45,00 €")
        u16_start = code_point_to_utf16_offset(text, cp_start)
        u16_end = code_point_to_utf16_offset(text, cp_end)
        assert u16_start == cp_start + 1
        assert u16_end == cp_end + 1
        assert utf16_to_code_point_offset(text, u16_start) == cp_start
        assert utf16_to_code_point_offset(text, u16_end) == cp_end


class TestP1CurrencyAndLocaleProvenance:
    """Tests for current EUR parsing and generic unsupported currency rejection (§5)."""

    def test_current_eur_and_generic_unsupported_rejection(self) -> None:
        assert ISO_4217_TABLE_VERSION.startswith("SIX-ISO4217")

        # 1. Active currencies: EUR is valid unconditionally
        ok_eur, reason_eur, _ = validate_currency_for_date("EUR", date(2026, 11, 3))
        assert ok_eur
        assert reason_eur is None

        # 2. Unsupported currencies follow generic CURRENCY_UNSUPPORTED
        ok_unsupported, reason_unsupp, _ = validate_currency_for_date("DEM", date(2026, 11, 3))
        assert not ok_unsupported
        assert reason_unsupp == ReasonCode.CURRENCY_UNSUPPORTED

        ok_xyz, reason_xyz, _ = validate_currency_for_date("XYZ", date(2026, 11, 3))
        assert not ok_xyz
        assert reason_xyz == ReasonCode.CURRENCY_UNSUPPORTED

        # 3. Parse money tests for modern EUR in hr-HR
        res_eur = parse_money("350,00 €", "hr-HR", transaction_date=date(2026, 11, 3))
        assert res_eur.status == ParseStatus.PARSED
        assert res_eur.value is not None
        assert res_eur.value.amount == Decimal("350.00")
        assert res_eur.value.currency == "EUR"

        res_eur_code = parse_money("200,00 EUR", "hr-HR", transaction_date=date(2026, 11, 3))
        assert res_eur_code.status == ParseStatus.PARSED
        assert res_eur_code.value is not None
        assert res_eur_code.value.amount == Decimal("200.00")
        assert res_eur_code.value.currency == "EUR"

        # 4. Unsupported currencies rejected through generic path
        res_dem = parse_money("200,00 DEM", "hr-HR", transaction_date=date(2026, 11, 3))
        assert res_dem.status == ParseStatus.INVALID
        assert res_dem.reason_code == ReasonCode.CURRENCY_UNSUPPORTED

        res_xyz = parse_money("350,00 XYZ", "hr-HR", transaction_date=date(2026, 11, 3))
        assert res_xyz.status == ParseStatus.INVALID
        assert res_xyz.reason_code == ReasonCode.CURRENCY_UNSUPPORTED

    def test_tzdb_provenance_recorded(self) -> None:
        prov = get_tzdb_provenance()
        assert "tzdata_package_version" in prov
        assert "source" in prov


class TestP1StrictJSONAndSecurityHardening:
    """Tests for §3.6 Duplicate JSON Keys, Capability Tokens, Audit Hash Chain, and Host/Origin."""

    def test_duplicate_json_keys_blocked(self) -> None:
        raw_dup = (
            '{"contract_version": "1.0", "decision": "propose", "tool_name": "submit_expense", '
            '"fields": {"amount": {"evidence": {"quote": "45,00 €"}}, '
            '"amount": {"evidence": {"quote": "999,00 €"}}}, "clarification_question": null}'
        )
        try:
            parse_strict_untrusted_json(raw_dup)
            raise AssertionError("Expected DuplicateJSONKeyError")
        except DuplicateJSONKeyError as exc:
            assert exc.key == "amount"

        gate = LocaleGatePipeline()
        ctx = TrustedRequestContext(
            request_id="dup_1",
            text="Evidentiraj trošak od 45,00 € od 03.11.2026.",
            locale="hr-HR",
            timezone="Europe/Zagreb",
            reference_datetime=REF_DT,
        )
        res = gate.evaluate_raw_json(ctx, raw_dup)
        assert res.status == GateDecisionStatus.BLOCKED
        assert res.reason_code == ReasonCode.PROPOSAL_DUPLICATE_JSON_KEY

    def test_oversized_untrusted_json_blocked(self) -> None:
        raw_huge = '{"a": "' + ("x" * 40000) + '"}'
        try:
            parse_strict_untrusted_json(raw_huge)
            raise AssertionError("Expected UntrustedJSONBoundsError")
        except UntrustedJSONBoundsError:
            pass

    def test_single_use_capability_token_and_tamper_evident_audit_chain(self) -> None:
        store = SimulatedStore(db_url="sqlite:///:memory:")
        gate = LocaleGatePipeline(store=store)
        ctx = TrustedRequestContext(
            request_id="cap_1",
            text="Evidentiraj trošak od 120,00 € od 03.11.2026.",
            locale="hr-HR",
            timezone="Europe/Zagreb",
            reference_datetime=REF_DT,
            require_confirmation=True,
        )
        res = gate.evaluate_raw_json(
            ctx,
            {
                "contract_version": "1.0",
                "decision": "propose",
                "tool_name": "submit_expense",
                "fields": {
                    "amount": {"evidence": {"quote": "120,00 €"}},
                    "expense_date": {"evidence": {"quote": "03.11.2026."}},
                },
                "clarification_question": None,
            },
        )
        assert res.status == GateDecisionStatus.AWAITING_CONFIRMATION
        assert res.confirmation_token is not None
        assert res.action_digest is not None

        # Verify raw token is redacted in SQLite stored gate_result
        stored = store.get_request_by_id_or_proposal("cap_1")
        assert stored is not None
        assert stored["gate_result"].confirmation_token is None

        # Wrong capability token must fail
        try:
            store.confirm_and_execute(
                "cap_1",
                expected_action_digest=res.action_digest,
                confirmation_token="lgcap_v1_wrong_token",
            )
            raise AssertionError("Expected ValueError on wrong token")
        except ValueError as exc:
            assert "Invalid confirmation capability token" in str(exc)

        # Valid capability token succeeds
        exec_res = store.confirm_and_execute(
            "cap_1",
            expected_action_digest=res.action_digest,
            confirmation_token=res.confirmation_token,
        )
        assert exec_res.status == GateDecisionStatus.EXECUTED

        # Audit hash chain must verify cleanly
        intact, count = store.verify_audit_chain()
        assert intact
        assert count >= 2

        # Direct helper test for capability token
        raw_tok, tok_hash = generate_confirmation_capability_token()
        assert verify_confirmation_capability_token(raw_tok, tok_hash)
        assert not verify_confirmation_capability_token("wrong_tok", tok_hash)

    def test_api_host_header_and_cross_origin_post_rejected(self, tmp_path: Path) -> None:
        cfg = Settings(
            app=AppSettings(
                db_url="sqlite:///:memory:",
                artifacts_dir=str(tmp_path / "artifacts"),
            ),
        )
        app = create_app(
            cfg,
            store=SimulatedStore(db_url="sqlite:///:memory:"),
            adapter=DeterministicMockAdapter(),
        )
        client = TestClient(app)

        # DNS rebinding Host header rejected with 403
        resp_host = client.get("/health", headers={"Host": "evil-rebinding.attacker.com"})
        assert resp_host.status_code == 403

        # Cross-origin POST rejected with 403
        resp_origin = client.post(
            "/api/v1/requests",
            headers={"Origin": "https://evil.attacker.com"},
            json={"text": "Evidentiraj trošak od 45,00 € od 03.11.2026."},
        )
        assert resp_origin.status_code == 403

        # Audit chain verification endpoint works
        resp_audit = client.get("/api/v1/audit/verify")
        assert resp_audit.status_code == 200
        assert resp_audit.json()["intact"] is True


class TestP0BaselinesAblationsAndMultiModelSupport:
    """Tests for Strong Conventional Baseline E, Ablations Ab1..Ab3, and Multi-Model Support."""

    def test_known_local_models_and_gemma4_thought_stripping(self) -> None:
        assert "qwen35-q4km" in KNOWN_LOCAL_MODELS
        assert "gemma4-e2b-q4km" in KNOWN_LOCAL_MODELS
        gemma_spec = resolve_known_model_spec("gemma4-e2b-q4km")
        assert gemma_spec is not None
        assert gemma_spec.filename == "gemma-4-E2B-it-Q4_K_M.gguf"

        raw_gemma = (
            "<|channel>thought\nLet me extract the exact quote.\n<channel|>"
            '{"contract_version":"1.0","decision":"no_action","tool_name":null,'
            '"fields":{},"clarification_question":null}'
        )
        extracted = _strip_thinking_and_markdown_fences(raw_gemma)
        assert extracted.startswith("{")
        assert '"decision":"no_action"' in extracted

    def test_strong_conventional_baseline_and_ablations(self) -> None:
        case = BenchmarkCase(
            case_id="test_case_0001",
            split="test",
            suites=("pilot",),
            locale="hr-HR",
            timezone="Europe/Zagreb",
            reference_datetime="2026-11-03T09:30:00+01:00",
            task_family="expense_submission",
            request_text=(
                "Preostali budžet je 1.500,00 €, a danas evidentiraj trošak od "
                "45,00 € od 03.11.2026."
            ),
            tool_expected="submit_expense",
            expected_decision="awaiting_confirmation",
            expected_arguments={
                "amount": "45.00",
                "currency": "EUR",
                "expense_date": "2026-11-03",
                "business_approval_status": "pending_manager_approval",
            },
            expected_reason_code="OK_AWAITING_CONFIRMATION",
            paraphrase_group="pg_test_1",
            gold_proposal={
                "contract_version": "1.0",
                "decision": "propose",
                "tool_name": "submit_expense",
                "fields": {
                    "amount": {"evidence": {"quote": "45,00 €"}},
                    "expense_date": {"evidence": {"quote": "03.11.2026."}},
                },
                "clarification_question": None,
            },
        )
        ctx = TrustedRequestContext(
            request_id="ab_1",
            text=case.request_text,
            locale=case.locale,
            timezone=case.timezone,
            reference_datetime=REF_DT,
        )
        decoy_proposal = {
            "contract_version": "1.0",
            "decision": "propose",
            "tool_name": "submit_expense",
            "fields": {
                "amount": {"evidence": {"quote": "1.500,00 €"}},
                "expense_date": {"evidence": {"quote": "03.11.2026."}},
            },
            "clarification_question": None,
        }

        # Strong conventional baseline E (no span/role verification) accepts the decoy 1500.00!
        conv_e = StrongConventionalBaselinePipeline()
        res_e = conv_e.evaluate_raw_json(ctx, decoy_proposal)
        assert (
            res_e.status == GateDecisionStatus.EXECUTED
            or res_e.status == GateDecisionStatus.AWAITING_CONFIRMATION
        )
        assert res_e.canonical_arguments["amount"] == "1500.00"

        # Ab1 (locale parser, no evidence) also accepts decoy 1500.00
        ab1 = LocaleParserNoEvidenceAblationPipeline()
        res_ab1 = ab1.evaluate_raw_json(ctx, decoy_proposal)
        assert res_ab1.status == GateDecisionStatus.AWAITING_CONFIRMATION
        assert res_ab1.canonical_arguments["amount"] == "1500.00"

        # Ab2 (evidence only, naive parser) misparses 1.500,00 as 1.50!
        ab2 = EvidenceOnlyNaiveParserAblationPipeline()
        res_ab2 = ab2.evaluate_raw_json(ctx, decoy_proposal)
        assert res_ab2.canonical_arguments["amount"] == "1.50"

        # Ab3 (no role binding) accepts the in-text decoy quote 1.500,00 €
        ab3 = NoRoleBindingAblationPipeline()
        res_ab3 = ab3.evaluate_raw_json(ctx, decoy_proposal)
        assert res_ab3.status == GateDecisionStatus.AWAITING_CONFIRMATION
        assert res_ab3.canonical_arguments["amount"] == "1500.00"

        # Full LocaleGate C blocks the wrong-span decoy quote!
        full_c = LocaleGatePipeline(enable_role_binding=True)
        res_c = full_c.evaluate_raw_json(ctx, decoy_proposal)
        assert res_c.status == GateDecisionStatus.BLOCKED
        assert res_c.reason_code == ReasonCode.FIELD_ROLE_MISMATCH
