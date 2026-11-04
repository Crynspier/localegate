"""Benchmark dataset, metrics, and evaluation runner package."""

from locale_gate.benchmark.dataset_builder import build_and_write_benchmark_v1
from locale_gate.benchmark.metrics import (
    CaseEvaluationOutcome,
    compute_system_metrics,
    grade_single_case,
    paired_cluster_bootstrap_diff,
    wilson_confidence_interval,
)
from locale_gate.benchmark.runner import (
    BenchmarkRunner,
    compare_two_runs,
    generate_markdown_report,
)
from locale_gate.benchmark.schema import (
    DATASET_SCHEMA_VERSION,
    BenchmarkCase,
    compute_dataset_sha256,
    load_benchmark_cases,
    validate_benchmark_dataset,
)

__all__ = [
    "DATASET_SCHEMA_VERSION",
    "BenchmarkCase",
    "BenchmarkRunner",
    "CaseEvaluationOutcome",
    "build_and_write_benchmark_v1",
    "compare_two_runs",
    "compute_dataset_sha256",
    "compute_system_metrics",
    "generate_markdown_report",
    "grade_single_case",
    "load_benchmark_cases",
    "paired_cluster_bootstrap_diff",
    "validate_benchmark_dataset",
    "wilson_confidence_interval",
]
