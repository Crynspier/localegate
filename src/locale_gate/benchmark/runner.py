"""Resumable benchmark evaluation runner, artifact writer, and Markdown report generator."""

from __future__ import annotations

import json
import platform
import sys
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from locale_gate import __version__
from locale_gate.benchmark.metrics import (
    CaseEvaluationOutcome,
    compute_system_metrics,
    grade_single_case,
    paired_cluster_bootstrap_diff,
)
from locale_gate.benchmark.schema import (
    compute_dataset_sha256,
    load_benchmark_cases,
    validate_benchmark_dataset,
)
from locale_gate.config.settings import LocalGGUFSpec, ModelProfileConfig, Settings
from locale_gate.contracts.enums import ReasonCode
from locale_gate.contracts.models import GateResult, ModelErrorGateResult
from locale_gate.gate.baselines import (
    EvidenceOnlyNaiveParserAblationPipeline,
    LocaleParserNoEvidenceAblationPipeline,
    NoRoleBindingAblationPipeline,
    ParserOraclePipeline,
    RawModelBaselinePipeline,
    SchemaOnlyBaselinePipeline,
    StrongConventionalBaselinePipeline,
)
from locale_gate.gate.pipeline import LocaleGatePipeline
from locale_gate.models.adapter import ModelAdapter, load_system_prompt
from locale_gate.models.gguf_inspector import inspect_gguf_artifact, query_llama_server_version


class BenchmarkRunner:
    """Orchestrates resumable multi-system evaluations (A, B, E, Ab1, Ab2, Ab3, C, D)."""

    def __init__(
        self,
        settings: Settings,
        adapter: ModelAdapter | None = None,
        *,
        model_spec: LocalGGUFSpec | None = None,
    ) -> None:
        self.settings = settings
        self.adapter = adapter
        self.model_spec = model_spec
        self.gate_c = LocaleGatePipeline()
        self.baseline_a = RawModelBaselinePipeline()
        self.baseline_b = SchemaOnlyBaselinePipeline()
        self.baseline_e = StrongConventionalBaselinePipeline()
        self.ablation_ab1 = LocaleParserNoEvidenceAblationPipeline()
        self.ablation_ab2 = EvidenceOnlyNaiveParserAblationPipeline()
        self.ablation_ab3 = NoRoleBindingAblationPipeline()
        self.oracle_d = ParserOraclePipeline()

    def run_evaluation(
        self,
        *,
        cases_path: Path,
        suite: str = "pilot",
        run_id: str | None = None,
        profile_name: str | None = None,
        compute_gguf_hash: bool = True,
    ) -> dict[str, Any]:
        """Run evaluation suite (`smoke`, `pilot`, `test`, `dev`, `full`, or `adversarial`) and save all §12.4 artifacts."""
        effective_run_id = (
            run_id
            or f"eval_{suite}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:6]}"
        )
        out_dir = Path(self.settings.app.artifacts_dir) / "evaluations" / effective_run_id
        out_dir.mkdir(parents=True, exist_ok=True)

        cases = load_benchmark_cases(cases_path, suite=suite, only_audited=True)
        prof_key = (
            profile_name
            or (self.model_spec.default_profile if self.model_spec else None)
            or self.settings.model.active_profile
        )
        profile = self.settings.model.profiles.get(
            prof_key,
            ModelProfileConfig(name=prof_key),
        )
        _, prompt_ver, prompt_hash = load_system_prompt(Path(self.settings.app.prompts_dir))

        # Load checkpointed per_case.jsonl if resuming
        per_case_file = out_dir / "per_case.jsonl"
        completed_outcomes: dict[tuple[str, str], CaseEvaluationOutcome] = {}
        if per_case_file.is_file():
            with per_case_file.open("r", encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        row = json.loads(line)
                        out_obj = CaseEvaluationOutcome(**row)
                        completed_outcomes[(out_obj.case_id, out_obj.system)] = out_obj

        model_systems = (
            "A_raw_model_baseline",
            "B_schema_only_baseline",
            "E_strong_conventional_baseline",
            "Ab1_locale_parser_no_evidence",
            "Ab2_evidence_only_naive_parser",
            "Ab3_no_role_binding",
            "C_locale_gate",
        )
        systems = ["D_parser_oracle"]
        if self.adapter is not None:
            systems = [*model_systems, "D_parser_oracle"]

        all_outcomes: list[CaseEvaluationOutcome] = []

        with per_case_file.open("a", encoding="utf-8") as per_case_fh:
            for case in cases:
                ctx = case.to_trusted_context(require_confirmation=True)

                # System D: Parser-only diagnostic oracle on gold evidence
                if (case.case_id, "D_parser_oracle") in completed_outcomes:
                    all_outcomes.append(completed_outcomes[(case.case_id, "D_parser_oracle")])
                else:
                    res_d = self.oracle_d.evaluate_gold_evidence(ctx, case.gold_proposal)
                    out_d = grade_single_case(
                        case, res_d, system_name="D_parser_oracle", latency_ms=0.0
                    )
                    all_outcomes.append(out_d)
                    per_case_fh.write(json.dumps(asdict(out_d), ensure_ascii=False) + "\n")
                    per_case_fh.flush()

                if self.adapter is None:
                    continue

                # All model-backed systems share the exact same model proposal for a fair paired comparison
                need_model = any(
                    (case.case_id, sys_name) not in completed_outcomes for sys_name in model_systems
                )
                if not need_model:
                    for sys_name in model_systems:
                        all_outcomes.append(completed_outcomes[(case.case_id, sys_name)])
                    continue

                mod_out = self.adapter.propose(ctx, profile_override=profile)
                latency_ms = mod_out.telemetry.latency_ms

                for sys_name in model_systems:
                    if (case.case_id, sys_name) in completed_outcomes:
                        all_outcomes.append(completed_outcomes[(case.case_id, sys_name)])
                        continue

                    if mod_out.error_reason is not None or mod_out.raw_proposal is None:
                        gate_res: GateResult = ModelErrorGateResult(
                            request_id=ctx.request_id,
                            proposal_id=f"err_{case.case_id}",
                            audit_correlation_id=f"corr_{case.case_id}",
                            reason_code=mod_out.error_reason or ReasonCode.MODEL_UNAVAILABLE,
                            message=mod_out.error_message or "Model error.",
                        )
                    elif sys_name == "A_raw_model_baseline":
                        gate_res = self.baseline_a.evaluate_raw_json(ctx, mod_out.raw_proposal)
                    elif sys_name == "B_schema_only_baseline":
                        gate_res = self.baseline_b.evaluate_raw_json(ctx, mod_out.raw_proposal)
                    elif sys_name == "E_strong_conventional_baseline":
                        gate_res = self.baseline_e.evaluate_raw_json(ctx, mod_out.raw_proposal)
                    elif sys_name == "Ab1_locale_parser_no_evidence":
                        gate_res = self.ablation_ab1.evaluate_raw_json(ctx, mod_out.raw_proposal)
                    elif sys_name == "Ab2_evidence_only_naive_parser":
                        gate_res = self.ablation_ab2.evaluate_raw_json(ctx, mod_out.raw_proposal)
                    elif sys_name == "Ab3_no_role_binding":
                        gate_res = self.ablation_ab3.evaluate_raw_json(ctx, mod_out.raw_proposal)
                    else:
                        gate_res = self.gate_c.evaluate_raw_json(ctx, mod_out.raw_proposal)

                    graded = grade_single_case(
                        case,
                        gate_res,
                        system_name=sys_name,
                        latency_ms=latency_ms,
                    )
                    all_outcomes.append(graded)
                    per_case_fh.write(json.dumps(asdict(graded), ensure_ascii=False) + "\n")
                    per_case_fh.flush()

        # Group outcomes by system
        by_system: dict[str, list[CaseEvaluationOutcome]] = {}
        for o in all_outcomes:
            by_system.setdefault(o.system, []).append(o)

        system_metrics: dict[str, Any] = {
            sys_name: compute_system_metrics(sys_outcomes)
            for sys_name, sys_outcomes in by_system.items()
        }

        # Paired cluster bootstrap comparisons (C vs A, C vs B, C vs E, C vs Ab1, C vs Ab2, C vs Ab3)
        comparisons: dict[str, Any] = {}
        if "C_locale_gate" in by_system:
            for comp_sys, comp_label in (
                ("A_raw_model_baseline", "C_vs_A"),
                ("B_schema_only_baseline", "C_vs_B"),
                ("E_strong_conventional_baseline", "C_vs_E"),
                ("Ab1_locale_parser_no_evidence", "C_vs_Ab1"),
                ("Ab2_evidence_only_naive_parser", "C_vs_Ab2"),
                ("Ab3_no_role_binding", "C_vs_Ab3"),
            ):
                if comp_sys in by_system:
                    comparisons[comp_label] = _compute_paired_comparisons(
                        by_system[comp_sys],
                        by_system["C_locale_gate"],
                        seed=profile.seed,
                    )

        # Write errors.jsonl
        errors_file = out_dir / "errors.jsonl"
        with errors_file.open("w", encoding="utf-8") as err_fh:
            for o in all_outcomes:
                if not o.full_action_exact_match:
                    err_fh.write(json.dumps(asdict(o), ensure_ascii=False) + "\n")

        # Write confusion_matrix.json
        confusion_file = out_dir / "confusion_matrix.json"
        confusion_payload = {
            sys_name: m["confusion_matrix"] for sys_name, m in system_metrics.items()
        }
        confusion_file.write_text(
            json.dumps(confusion_payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Write environment.json
        env_payload = {
            "os": platform.platform(),
            "python_version": sys.version,
            "locale_gate_version": __version__,
            "llama_cpp_version": query_llama_server_version(
                Path(self.settings.model.llama_server_bin)
                if self.settings.model.llama_server_bin
                else None
            ),
            "timestamp_utc": datetime.now(UTC).isoformat(),
        }
        (out_dir / "environment.json").write_text(
            json.dumps(env_payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Write model_manifest.json
        selector = self.model_spec.key if self.model_spec else None
        alias = self.model_spec.path_alias if self.model_spec else self.settings.model.path_alias
        model_path = self.settings.model.resolve_model_path(model_selector=selector)
        if model_path and model_path.is_file():
            manifest = inspect_gguf_artifact(
                model_path,
                path_alias=alias,
                context_size=self.settings.model.context_size,
                gpu_layers=self.settings.model.gpu_layers,
                thinking_enabled=profile.enable_thinking,
                compute_hash=compute_gguf_hash,
            )
            model_manifest_dict = manifest.to_public_dict()
            if not compute_gguf_hash and self.model_spec is not None:
                model_manifest_dict["sha256"] = self.model_spec.expected_sha256
        else:
            model_manifest_dict = {
                "model_family": self.model_spec.model_family if self.model_spec else "Qwen3.5-4B",
                "format": "GGUF",
                "quantization": self.model_spec.quantization if self.model_spec else "Q4_K_M",
                "path_alias": alias,
                "status": "not_configured_or_core_only",
            }
        (out_dir / "model_manifest.json").write_text(
            json.dumps(model_manifest_dict, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Write dataset_manifest.json
        ds_summary = validate_benchmark_dataset(cases_path)
        (out_dir / "dataset_manifest.json").write_text(
            json.dumps(ds_summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Write run.json
        run_payload = {
            "run_id": effective_run_id,
            "suite": suite,
            "cases_evaluated": len(cases),
            "systems_evaluated": systems,
            "model_evaluated": self.adapter is not None,
            "model_alias": alias if self.adapter is not None else "oracle-only",
            "model_family": model_manifest_dict.get("model_family"),
            "prompt_version": prompt_ver,
            "prompt_sha256": prompt_hash,
            "dataset_version": ds_summary["dataset_version"],
            "dataset_sha256": compute_dataset_sha256(cases_path),
            "sampling_profile": profile.model_dump(),
            "context_size": self.settings.model.context_size,
            "gpu_layers": self.settings.model.gpu_layers,
            "model_sha256": model_manifest_dict.get("sha256"),
            "llama_cpp_version": env_payload["llama_cpp_version"],
            "timestamp_utc": datetime.now(UTC).isoformat(),
        }
        (out_dir / "run.json").write_text(
            json.dumps(run_payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Write metrics.json
        metrics_payload = {
            "run_id": effective_run_id,
            "suite": suite,
            "cases_evaluated": len(cases),
            "systems": system_metrics,
            "paired_comparisons": comparisons,
        }
        (out_dir / "metrics.json").write_text(
            json.dumps(metrics_payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Write report.md
        report_md = generate_markdown_report(
            run_payload=run_payload,
            metrics_payload=metrics_payload,
            model_manifest=model_manifest_dict,
            env_payload=env_payload,
        )
        (out_dir / "report.md").write_text(report_md, encoding="utf-8")

        return {
            "run_id": effective_run_id,
            "artifact_dir": str(out_dir),
            "run": run_payload,
            "metrics": metrics_payload,
        }


def _compute_paired_comparisons(
    outcomes_base: list[CaseEvaluationOutcome],
    outcomes_guarded: list[CaseEvaluationOutcome],
    *,
    seed: int,
) -> dict[str, Any]:
    clusters_exact_base: dict[str, list[float]] = {}
    clusters_exact_guard: dict[str, list[float]] = {}
    clusters_unsafe_base: dict[str, list[float]] = {}
    clusters_unsafe_guard: dict[str, list[float]] = {}

    for o in outcomes_base:
        clusters_exact_base.setdefault(o.paraphrase_group, []).append(
            1.0 if o.full_action_exact_match else 0.0
        )
        clusters_unsafe_base.setdefault(o.paraphrase_group, []).append(
            1.0 if o.unsafe_execution else 0.0
        )
    for o in outcomes_guarded:
        clusters_exact_guard.setdefault(o.paraphrase_group, []).append(
            1.0 if o.full_action_exact_match else 0.0
        )
        clusters_unsafe_guard.setdefault(o.paraphrase_group, []).append(
            1.0 if o.unsafe_execution else 0.0
        )

    return {
        "full_action_exact_match_diff": paired_cluster_bootstrap_diff(
            clusters_exact_base, clusters_exact_guard, seed=seed
        ),
        "unsafe_execution_rate_diff": paired_cluster_bootstrap_diff(
            clusters_unsafe_base, clusters_unsafe_guard, seed=seed
        ),
    }


def generate_markdown_report(
    *,
    run_payload: dict[str, Any],
    metrics_payload: dict[str, Any],
    model_manifest: dict[str, Any],
    env_payload: dict[str, Any],
) -> str:
    """Generate a factual, machine-derived research report from evaluation artifacts."""
    lines: list[str] = [
        f"# LocaleGate Evaluation Report — `{run_payload['run_id']}`",
        "",
        "## 1. Run Configuration & Provenance",
        "",
        f"- **Suite:** `{run_payload['suite']}` ({run_payload['cases_evaluated']} audited cases)",
        f"- **Timestamp (UTC):** `{run_payload['timestamp_utc']}`",
        f"- **Model Artifact Alias:** `{model_manifest.get('path_alias', 'n/a')}`",
        f"- **Model Family / Quantization:** `{model_manifest.get('model_family', 'n/a')}` / `{model_manifest.get('quantization', 'n/a')}`",
        f"- **Model SHA-256:** `{run_payload.get('model_sha256', 'n/a')}`",
        f"- **Runtime:** `{env_payload.get('llama_cpp_version', 'n/a')}`",
        f"- **Context Size / GPU Layers:** `{run_payload['context_size']}` / `{run_payload['gpu_layers']}`",
        f"- **Sampling Profile:** `{json.dumps(run_payload['sampling_profile'])}`",
        f"- **Dataset SHA-256:** `{run_payload['dataset_sha256']}`",
        f"- **Prompt Version / SHA-256:** `{run_payload['prompt_version']}` (`{run_payload['prompt_sha256'][:16]}...`)",
        "",
        "## 2. System & Ablation Comparison Summary (95% Wilson Confidence Intervals)",
        "",
        "| System | Full Action Exact Match | Clear Safe Execution Recall | Unsafe Execution Rate (Overall) | False Block Rate (Valid Cases) | Latency p50 / p95 (ms) |",
        "|---|---:|---:|---:|---:|---:|",
    ]

    systems = metrics_payload.get("systems", {})
    for sys_name, sm in systems.items():
        fa = sm["full_action_exact_match"]
        rec = sm["clear_safe_action_execution_recall"]
        uns = sm["unsafe_execution_rate_overall"]
        fb = sm["false_block_rate_on_clear_valid"]
        lat = sm["latency_ms"]
        lines.append(
            f"| `{sys_name}` | "
            f"{fa['rate'] * 100:.1f}% [{fa['ci_lower'] * 100:.1f}%, {fa['ci_upper'] * 100:.1f}%] ({fa['numerator']}/{fa['denominator']}) | "
            f"{rec['rate'] * 100:.1f}% [{rec['ci_lower'] * 100:.1f}%, {rec['ci_upper'] * 100:.1f}%] ({rec['numerator']}/{rec['denominator']}) | "
            f"{uns['rate'] * 100:.1f}% [{uns['ci_lower'] * 100:.1f}%, {uns['ci_upper'] * 100:.1f}%] ({uns['numerator']}/{uns['denominator']}) | "
            f"{fb['rate'] * 100:.1f}% [{fb['ci_lower'] * 100:.1f}%, {fb['ci_upper'] * 100:.1f}%] ({fb['numerator']}/{fb['denominator']}) | "
            f"{lat['p50']:.1f} / {lat['p95']:.1f} |"
        )

    lines.extend(["", "## 3. Per-Locale Breakdown", ""])
    for sys_name, sm in systems.items():
        lines.append(f"### `{sys_name}`")
        lines.append("")
        lines.append(
            "| Locale | Total | Full Action Exact Match | Unsafe Execution Rate | Clear Safe Recall | False Block Rate |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|")
        for loc, lm in sm.get("by_locale", {}).items():
            l_fa = lm["full_action_exact_match"]
            l_un = lm["unsafe_execution_rate"]
            l_rc = lm["clear_safe_execution_recall"]
            l_fb = lm["false_block_rate_on_valid"]
            lines.append(
                f"| `{loc}` | {lm['total']} | "
                f"{l_fa['rate'] * 100:.1f}% ({l_fa['numerator']}/{l_fa['denominator']}) | "
                f"{l_un['rate'] * 100:.1f}% ({l_un['numerator']}/{l_un['denominator']}) | "
                f"{l_rc['rate'] * 100:.1f}% ({l_rc['numerator']}/{l_rc['denominator']}) | "
                f"{l_fb['rate'] * 100:.1f}% ({l_fb['numerator']}/{l_fb['denominator']}) |"
            )
        lines.append("")

    comparisons = metrics_payload.get("paired_comparisons", {})
    if comparisons:
        lines.extend(["## 4. Paired Cluster Bootstrap Comparisons (2,000 Resamples)", ""])
        for comp_key, comp_val in comparisons.items():
            fa_d = comp_val["full_action_exact_match_diff"]
            un_d = comp_val["unsafe_execution_rate_diff"]
            lines.append(
                f"- **`{comp_key}` (`full_action_exact_match` Δ):** "
                f"`{fa_d['mean_diff'] * 100:+.2f}%` (95% CI: `[{fa_d['ci_lower'] * 100:+.2f}%, {fa_d['ci_upper'] * 100:+.2f}%]`, {fa_d['clusters']} paraphrase groups)"
            )
            lines.append(
                f"- **`{comp_key}` (`unsafe_execution_rate` Δ):** "
                f"`{un_d['mean_diff'] * 100:+.2f}%` (95% CI: `[{un_d['ci_lower'] * 100:+.2f}%, {un_d['ci_upper'] * 100:+.2f}%]`, {un_d['clusters']} paraphrase groups)"
            )
        lines.append("")

    lines.extend(
        [
            "## 5. Error Taxonomy & Limitations",
            "",
            "- Note: `D_parser_oracle` evaluates deterministic parser coverage on gold field evidence and is an upper-bound diagnostic, not an autonomous agent system.",
            "- Stage 3b deterministic semantic role-binding verification detects invoice/reference numbers misbound as amounts, retracted/negated figures, subtotal/VAT components when a total is present, and unresolved disjunctive choices, while reporting residual limitations when multiple identical-role figures lack lexical markers.",
            "",
        ]
    )
    return "\n".join(lines)


def compare_two_runs(run_dir_a: Path, run_dir_b: Path) -> dict[str, Any]:
    """Compare two saved evaluation artifact folders."""
    m_a = json.loads((run_dir_a / "metrics.json").read_text(encoding="utf-8"))
    m_b = json.loads((run_dir_b / "metrics.json").read_text(encoding="utf-8"))
    r_a = json.loads((run_dir_a / "run.json").read_text(encoding="utf-8"))
    r_b = json.loads((run_dir_b / "run.json").read_text(encoding="utf-8"))

    sys_a = m_a.get("systems", {}).get("C_locale_gate", {})
    sys_b = m_b.get("systems", {}).get("C_locale_gate", {})

    return {
        "run_a": r_a["run_id"],
        "run_b": r_b["run_id"],
        "suite_a": r_a["suite"],
        "suite_b": r_b["suite"],
        "model_a": r_a.get("model_alias", "unknown"),
        "model_b": r_b.get("model_alias", "unknown"),
        "profile_a": r_a["sampling_profile"]["name"],
        "profile_b": r_b["sampling_profile"]["name"],
        "locale_gate_a": {
            "full_action_exact_match": sys_a.get("full_action_exact_match"),
            "unsafe_execution_rate_overall": sys_a.get("unsafe_execution_rate_overall"),
            "false_block_rate_on_clear_valid": sys_a.get("false_block_rate_on_clear_valid"),
        },
        "locale_gate_b": {
            "full_action_exact_match": sys_b.get("full_action_exact_match"),
            "unsafe_execution_rate_overall": sys_b.get("unsafe_execution_rate_overall"),
            "false_block_rate_on_clear_valid": sys_b.get("false_block_rate_on_clear_valid"),
        },
    }
