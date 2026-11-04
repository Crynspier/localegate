"""Unit tests for dataset split isolation, zero paraphrase group leakage, and Strong Baseline E."""

from __future__ import annotations

from pathlib import Path

from locale_gate.benchmark.schema import (
    load_benchmark_cases,
    validate_benchmark_dataset,
)
from locale_gate.contracts.models import TrustedRequestContext
from locale_gate.gate.baselines import (
    RawModelBaselinePipeline,
    StrongConventionalBaselinePipeline,
)


def test_benchmark_v1_split_isolation_and_zero_leakage() -> None:
    cases_path = Path("data/benchmark-v1/cases.jsonl")
    assert cases_path.is_file()
    res = validate_benchmark_dataset(cases_path)
    assert res["valid"] is True
    assert len(res["leaking_paraphrase_groups"]) == 0
    assert len(res["oracle_mismatches"]) == 0

    all_cases = load_benchmark_cases(cases_path, suite="full", only_audited=True)
    dev_texts = {c.request_text.strip() for c in all_cases if c.split == "dev"}
    test_texts = {c.request_text.strip() for c in all_cases if c.split == "test"}
    # Verify zero exact text overlap between dev and test
    overlap = dev_texts & test_texts
    assert len(overlap) == 0, f"Found exact text overlap across dev/test splits: {overlap}"


def test_adversarial_v1_and_v2_manifest_validation() -> None:
    # Public adversarial-v1 must always be present and valid
    p_v1 = Path("data/adversarial-v1/cases.jsonl")
    assert p_v1.is_file(), "Public adversarial-v1 cases.jsonl must be present"
    res_v1 = validate_benchmark_dataset(p_v1)
    assert res_v1["valid"] is True, f"adversarial-v1 validation failed: {res_v1}"
    assert len(res_v1["leaking_paraphrase_groups"]) == 0
    assert len(res_v1["oracle_mismatches"]) == 0

    # Sealed adversarial-v2: manifest and data-card must always exist
    v2_dir = Path("data/adversarial-v2")
    assert (v2_dir / "manifest.json").is_file(), "adversarial-v2 manifest.json must be present"
    assert (v2_dir / "data-card.md").is_file(), "adversarial-v2 data-card.md must be present"

    # If cases.jsonl is present (development environment), validate it fully.
    # In clean release packages, cases.jsonl is intentionally omitted to protect the held-out benchmark.
    p_v2 = v2_dir / "cases.jsonl"
    if p_v2.is_file():
        res_v2 = validate_benchmark_dataset(p_v2)
        assert res_v2["valid"] is True, f"adversarial-v2 validation failed: {res_v2}"
        assert len(res_v2["leaking_paraphrase_groups"]) == 0
        assert len(res_v2["oracle_mismatches"]) == 0


def test_baseline_e_babel_cldr_parsing() -> None:
    pipeline_e = StrongConventionalBaselinePipeline()
    ctx_hr = TrustedRequestContext(
        request_id="req_e_hr",
        text="Račun od 1.234,56 € od 03.11.2026.",
        locale="hr-HR",
        timezone="Europe/Zagreb",
    )
    payload_hr = {
        "contract_version": "1.0",
        "decision": "propose",
        "tool_name": "submit_expense",
        "fields": {
            "amount": {"evidence": {"quote": "1.234,56 €"}},
            "expense_date": {"evidence": {"quote": "03.11.2026."}},
        },
    }
    res_hr = pipeline_e.evaluate_raw_json(ctx_hr, payload_hr)
    # Babel CLDR successfully parses Croatian number 1.234,56 € as 1234.56
    assert res_hr.canonical_arguments["amount"] == "1234.56"
    assert res_hr.canonical_arguments["currency"] == "EUR"

    # Contrast with raw baseline A which corrupts it to 1.23
    res_a = RawModelBaselinePipeline().evaluate_raw_json(ctx_hr, payload_hr)
    assert res_a.canonical_arguments["amount"] == "1.23"
