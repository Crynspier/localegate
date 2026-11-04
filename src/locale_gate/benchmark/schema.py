"""Benchmark dataset schema, validation, and split integrity checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from locale_gate.contracts.models import TrustedRequestContext
from locale_gate.gate.baselines import ParserOraclePipeline

DATASET_SCHEMA_VERSION = "1.1.0"

ExpectedDecisionLiteral = Literal[
    "awaiting_confirmation",
    "needs_clarification",
    "blocked",
    "no_action",
]


class BenchmarkCase(BaseModel):
    """Single versioned diagnostic benchmark case with human-auditable gold annotations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=4, max_length=64)
    dataset_version: str = Field(default=DATASET_SCHEMA_VERSION)
    status: Literal["audited", "draft"] = Field(default="audited")
    split: Literal["dev", "test"] = Field(default="test")
    suites: tuple[Literal["smoke", "pilot", "full", "adversarial"], ...] = Field(default=("full",))
    locale: Literal["hr-HR", "en-US", "en-GB"]
    timezone: str | None = Field(default="Europe/Zagreb")
    reference_datetime: str = Field(default="2026-11-03T12:00:00+01:00")
    request_text: str = Field(min_length=1, max_length=2048)
    task_family: str = Field(min_length=1, max_length=64)
    tool_expected: Literal["submit_expense", "schedule_event"] | None = None
    expected_decision: ExpectedDecisionLiteral
    expected_arguments: dict[str, Any] = Field(default_factory=dict)
    expected_reason_code: str | None = None
    ambiguity_class: str | None = None
    severity: Literal["normal", "high", "critical"] = Field(default="normal")
    paraphrase_group: str = Field(min_length=1, max_length=64)
    source: str = Field(default="synthetic_authored")
    review_status: str = Field(default="two_pass_review")
    gold_proposal: dict[str, Any]

    @field_validator("expected_arguments")
    @classmethod
    def disallow_float_values(cls, v: dict[str, Any]) -> dict[str, Any]:
        for k, val in v.items():
            if isinstance(val, float):
                raise ValueError(
                    f"Gold expected_arguments[{k!r}] must not use JSON float; use string."
                )
        return v

    @model_validator(mode="after")
    def validate_decision_fields(self) -> BenchmarkCase:
        if self.expected_decision == "awaiting_confirmation":
            if self.tool_expected is None:
                raise ValueError(
                    "tool_expected is required when expected_decision is awaiting_confirmation"
                )
            if not self.expected_arguments:
                raise ValueError("expected_arguments cannot be empty when awaiting_confirmation")
        return self

    def to_trusted_context(self, *, require_confirmation: bool = True) -> TrustedRequestContext:
        from datetime import datetime

        return TrustedRequestContext(
            request_id=self.case_id,
            text=self.request_text,
            locale=self.locale,
            timezone=self.timezone,
            reference_datetime=datetime.fromisoformat(self.reference_datetime),
            require_confirmation=require_confirmation,
            dry_run=True,
        )


def load_benchmark_cases(
    cases_path: Path,
    *,
    suite: str = "full",
    only_audited: bool = True,
) -> list[BenchmarkCase]:
    """Load and validate JSONL benchmark cases filtered by suite/split and audit status."""
    if not cases_path.is_file():
        raise FileNotFoundError(f"Benchmark dataset not found at {cases_path}")

    cases: list[BenchmarkCase] = []
    seen_ids: set[str] = set()
    with cases_path.open("r", encoding="utf-8") as fh:
        for line_no, raw_line in enumerate(fh, start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            item = json.loads(stripped)
            case = BenchmarkCase.model_validate(item)
            if case.case_id in seen_ids:
                raise ValueError(f"Duplicate case_id {case.case_id} on line {line_no}")
            seen_ids.add(case.case_id)
            if only_audited and case.status != "audited":
                continue
            if suite in {"dev", "test"}:
                if case.split == suite:
                    cases.append(case)
            elif suite in case.suites or suite == "full":
                cases.append(case)
    return cases


def compute_dataset_sha256(cases_path: Path) -> str:
    """Compute SHA-256 of the raw cases.jsonl file."""
    return hashlib.sha256(cases_path.read_bytes()).hexdigest()


def validate_benchmark_dataset(cases_path: Path) -> dict[str, Any]:
    """Validate dataset schema, split leakage, locale distribution, and oracle consistency."""
    all_cases = load_benchmark_cases(cases_path, suite="full", only_audited=False)
    audited_cases = [c for c in all_cases if c.status == "audited"]
    oracle = ParserOraclePipeline()

    # 1. Verify no paraphrase_group leaks across dev and test splits
    group_splits: dict[str, set[str]] = {}
    for c in all_cases:
        group_splits.setdefault(c.paraphrase_group, set()).add(c.split)
    leaking_groups = [g for g, s in group_splits.items() if len(s) > 1]

    # 2. Verify every case's gold_proposal produces the exact expected_decision & expected_arguments
    oracle_mismatches: list[dict[str, Any]] = []
    for c in audited_cases:
        ctx = c.to_trusted_context(require_confirmation=True)
        res = oracle.evaluate_gold_evidence(ctx, c.gold_proposal)
        actual_dec = res.status.value.lower()
        if actual_dec != c.expected_decision:
            oracle_mismatches.append(
                {
                    "case_id": c.case_id,
                    "expected_decision": c.expected_decision,
                    "actual_decision": actual_dec,
                    "reason_code": res.reason_code.value,
                    "message": res.message,
                }
            )
            continue
        if c.expected_decision == "awaiting_confirmation":
            for k, exp_v in c.expected_arguments.items():
                act_v = res.canonical_arguments.get(k)
                if act_v != exp_v:
                    oracle_mismatches.append(
                        {
                            "case_id": c.case_id,
                            "field": k,
                            "expected": exp_v,
                            "actual": act_v,
                        }
                    )
        if c.expected_reason_code is not None and res.reason_code.value != c.expected_reason_code:
            oracle_mismatches.append(
                {
                    "case_id": c.case_id,
                    "expected_reason_code": c.expected_reason_code,
                    "actual_reason_code": res.reason_code.value,
                }
            )

    by_locale: dict[str, int] = {}
    by_decision: dict[str, int] = {}
    by_suite: dict[str, int] = {"smoke": 0, "pilot": 0, "full": 0, "adversarial": 0}
    by_split: dict[str, int] = {"dev": 0, "test": 0}

    for c in audited_cases:
        by_locale[c.locale] = by_locale.get(c.locale, 0) + 1
        by_decision[c.expected_decision] = by_decision.get(c.expected_decision, 0) + 1
        by_split[c.split] = by_split.get(c.split, 0) + 1
        for s_name in c.suites:
            by_suite[s_name] = by_suite.get(s_name, 0) + 1

    is_adversarial_pack = "adversarial" in str(cases_path).lower() or (
        by_suite.get("adversarial", 0) > 0 and len(audited_cases) < 600
    )
    if is_adversarial_pack:
        valid = (
            len(leaking_groups) == 0 and len(oracle_mismatches) == 0 and len(audited_cases) >= 30
        )
    else:
        valid = (
            len(leaking_groups) == 0
            and len(oracle_mismatches) == 0
            and len(audited_cases) >= 600
            and by_locale.get("hr-HR", 0) >= 300
            and by_locale.get("en-US", 0) >= 150
            and by_locale.get("en-GB", 0) >= 150
        )

    return {
        "valid": valid,
        "dataset_version": DATASET_SCHEMA_VERSION,
        "sha256": compute_dataset_sha256(cases_path),
        "total_cases": len(all_cases),
        "audited_cases": len(audited_cases),
        "draft_cases": len(all_cases) - len(audited_cases),
        "by_locale": by_locale,
        "by_decision": by_decision,
        "by_suite": by_suite,
        "by_split": by_split,
        "leaking_paraphrase_groups": leaking_groups,
        "oracle_mismatches": oracle_mismatches,
    }
