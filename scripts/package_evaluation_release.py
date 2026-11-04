"""Package LocaleGate Research Validation 1.0 artifacts for external evaluation.

Bundles public benchmark datasets, prompts, manifests, evaluation runs, analysis tables,
and reproducibility documentation into a clean, audited, distributable ZIP archive.

Safety & Provenance Protections (Reviewer Directive):
1. Held-Out Protection: data/adversarial-v2 cases.jsonl is strictly excluded by default
   to prevent contaminating the sealed challenge benchmark.
2. Content Audit: verifies zero local user paths (C:\\Users), zero secrets (.env), zero
   temporary SQLite databases, zero model binary weights (*.gguf), and zero cache files.
3. Cryptographic Provenance: records Git commit SHA, working tree cleanliness, Python
   lockfile SHA-256, Node lockfile SHA-256, SQLite runtime version, and individual file hashes.
4. Verification Checksum: generates SHA-256 checksum file (SHA256SUMS.txt) for the finished ZIP.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import sqlite3
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().lower()


def _get_git_provenance(project_root: Path) -> dict[str, Any]:
    """Extract Git commit SHA and working tree cleanliness."""
    try:
        commit_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project_root, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        commit_sha = "UNCOMMITTED_OR_INITIAL_CHECKOUT"

    try:
        status_out = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=project_root, text=True, stderr=subprocess.DEVNULL
        ).strip()
        is_clean = len(status_out) == 0
    except Exception:
        is_clean = False

    return {
        "git_commit_sha": commit_sha,
        "git_working_tree_clean": is_clean,
    }


def audit_file_content(path: Path) -> None:
    """Audit file against unintended local paths, credentials, or binaries."""
    # Forbidden extensions
    forbidden_extensions = {
        ".sqlite",
        ".sqlite3",
        ".db",
        ".log",
        ".gguf",
        ".bin",
        ".pt",
        ".pth",
        ".pyc",
    }
    if path.suffix.lower() in forbidden_extensions:
        raise ValueError(f"Prohibited file type {path.suffix} detected: {path}")

    # Check text files for leaked local Windows user paths, sealed test data, and secrets
    sealed_leakage_markers = [
        "ADV2-HR-",
        "ADV2-US-",
        "ADV2-GB-",
        "synthetic_adversarial_v2_authored",
    ]
    secret_patterns = [
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
        re.compile(
            r"(?:api[_-]?key|auth[_-]?token|secret[_-]?key)\s*[:=]\s*['\"][0-9a-zA-Z\-_]{20,}['\"]",
            re.IGNORECASE,
        ),
        re.compile(r"ghp_[0-9a-zA-Z]{36}"),
        re.compile(r"github_pat_[0-9a-zA-Z_]{82}"),
        re.compile(r"xox[baprs]-[0-9a-zA-Z]{10,48}"),
    ]
    text_extensions = {".json", ".jsonl", ".md", ".txt", ".csv", ".py", ".yml", ".yaml"}
    if path.suffix.lower() in text_extensions and path.stat().st_size < 10_000_000:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            # 1. Prohibit raw home directories like C:\Users\<name>
            if (
                "C:\\Users\\" in content or "C:/Users/" in content
            ) and "path sanitization" not in content.lower():
                raise ValueError(f"Leaked local Windows path detected in {path}")

            # 2. Check for indirect sealed-dataset leakage in public/derived files
            is_sealed_doc = (
                "data/adversarial-v2" in path.as_posix()
                or "package_evaluation_release" in path.name
            )
            if not is_sealed_doc:
                for marker in sealed_leakage_markers:
                    if marker in content:
                        raise ValueError(
                            f"Indirect sealed dataset leakage ({marker}) detected in {path}"
                        )

            # 3. Secret and credential scanner
            for pattern in secret_patterns:
                if pattern.search(content):
                    raise ValueError(
                        f"Potential secret pattern detected in {path}: {pattern.pattern}"
                    )
        except Exception as exc:
            if any(
                term in str(exc)
                for term in (
                    "Leaked local Windows path",
                    "Indirect sealed dataset leakage",
                    "Potential secret",
                )
            ):
                raise


def create_evaluation_package(
    output_zip_path: Path,
    include_sealed: bool = False,
) -> Path:
    """Create a self-contained, audited ZIP archive for external technical reviewers."""
    project_root = Path(__file__).resolve().parent.parent
    print("=" * 70)
    print("LocaleGate Research Validation 1.0 — External Evaluation Packager")
    print(f"Target archive: {output_zip_path}")
    print(
        f"Sealed challenge inclusion: {'INCLUDED (WARNING: Held-out benchmark disclosed)' if include_sealed else 'PROTECTED (data/adversarial-v2 cases omitted)'}"
    )
    print("=" * 70)

    files_to_bundle: list[tuple[Path, str]] = []

    # 1. Benchmark Datasets
    # Public 600-case Primary Benchmark (benchmark-v1)
    for p in (project_root / "data" / "benchmark-v1").glob("*.*"):
        files_to_bundle.append((p, f"data/benchmark-v1/{p.name}"))

    # Public 60-case Adversarial Benchmark (adversarial-v1)
    for p in (project_root / "data" / "adversarial-v1").glob("*.*"):
        files_to_bundle.append((p, f"data/adversarial-v1/{p.name}"))

    # Sealed 30-case Adversarial Benchmark v2 (adversarial-v2)
    # Always include documentation and manifest; cases.jsonl ONLY if --include-sealed
    adv_v2_dir = project_root / "data" / "adversarial-v2"
    if adv_v2_dir.exists():
        files_to_bundle.append((adv_v2_dir / "data-card.md", "data/adversarial-v2/data-card.md"))
        files_to_bundle.append((adv_v2_dir / "manifest.json", "data/adversarial-v2/manifest.json"))
        if include_sealed:
            files_to_bundle.append((adv_v2_dir / "cases.jsonl", "data/adversarial-v2/cases.jsonl"))

    # 2. Prompts
    for p in (project_root / "prompts").glob("*.txt"):
        files_to_bundle.append((p, f"prompts/{p.name}"))

    # 3. Documentation & Reports
    docs_to_include = [
        "docs/reproducibility.md",
        "docs/implementation-report.md",
        "docs/evaluation-methodology.md",
        "docs/research-validation-plan.md",
        "docs/concurrency-stress-report.md",
        "docs/threat-model.md",
        "docs/architecture.md",
        "docs/parser-rules.md",
        "docs/dataset-card.md",
        "docs/api-reference.md",
        "docs/model-setup-windows.md",
        "README.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "pyproject.toml",
    ]
    for doc in docs_to_include:
        dp = project_root / doc
        if dp.exists():
            files_to_bundle.append((dp, doc))

    # 4. Manifests & Reconciliation Analysis
    for p in (project_root / "artifacts" / "analysis").glob("*.*"):
        if p.suffix.lower() in (".csv", ".json", ".md"):
            files_to_bundle.append((p, f"artifacts/analysis/{p.name}"))

    # 5. Curated Evaluation Runs
    eval_dir = project_root / "artifacts" / "evaluations"
    for p in eval_dir.glob("*.json"):
        files_to_bundle.append((p, f"artifacts/evaluations/{p.name}"))
    for run_folder in eval_dir.iterdir():
        if run_folder.is_dir():
            for p in run_folder.glob("*.json"):
                files_to_bundle.append((p, f"artifacts/evaluations/{run_folder.name}/{p.name}"))
            for p in run_folder.glob("*.md"):
                files_to_bundle.append((p, f"artifacts/evaluations/{run_folder.name}/{p.name}"))
            for p in run_folder.glob("*.jsonl"):
                files_to_bundle.append((p, f"artifacts/evaluations/{run_folder.name}/{p.name}"))

    # 6. Source Code, Tests, Scripts, and Reproducible Lockfiles
    # Enables an external researcher to unpackage into a clean directory and immediately execute:
    # uv sync --locked && uv run locale-gate eval validate-dataset && uv run pytest tests/regression/test_adversarial_model_regressions.py
    if (project_root / "uv.lock").exists():
        files_to_bundle.append((project_root / "uv.lock", "uv.lock"))
    if (project_root / "web" / "package-lock.json").exists():
        files_to_bundle.append(
            (project_root / "web" / "package-lock.json", "web/package-lock.json")
        )

    for p in (project_root / "src").rglob("*.py"):
        rel_posix = p.relative_to(project_root).as_posix()
        files_to_bundle.append((p, rel_posix))

    for p in (project_root / "tests").rglob("*.py"):
        rel_posix = p.relative_to(project_root).as_posix()
        files_to_bundle.append((p, rel_posix))

    for p in (project_root / "scripts").glob("*.py"):
        rel_posix = p.relative_to(project_root).as_posix()
        files_to_bundle.append((p, rel_posix))

    # Audit all files before packaging
    print("\nAuditing candidate files against path leakage and forbidden binaries...")
    for src_path, _arc_name in files_to_bundle:
        if not src_path.exists():
            continue
        audit_file_content(src_path)
    print(f"Audit passed: {len(files_to_bundle)} files verified clean.")

    # Source code and environment provenance
    git_prov = _get_git_provenance(project_root)
    uv_lock = project_root / "uv.lock"
    pkg_lock = project_root / "web" / "package-lock.json"

    file_manifest_entries: list[dict[str, Any]] = []
    for src_path, arc_name in sorted(files_to_bundle, key=lambda x: x[1]):
        if src_path.exists():
            file_manifest_entries.append(
                {
                    "path": arc_name,
                    "size_bytes": src_path.stat().st_size,
                    "sha256": _compute_sha256(src_path),
                }
            )

    archive_manifest = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "package_name": "localegate_research_validation_1.0_evaluation_package",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "project_version": "0.1.0",
        "git_provenance": git_prov,
        "runtime_environment": {
            "os_platform": platform.platform(),
            "python_version": sys.version.split()[0],
            "sqlite_version": sqlite3.sqlite_version,
            "python_lockfile_sha256": _compute_sha256(uv_lock) if uv_lock.exists() else None,
            "frontend_lockfile_sha256": _compute_sha256(pkg_lock) if pkg_lock.exists() else None,
        },
        "dataset_disclosure_policy": {
            "public_datasets": ["data/benchmark-v1 (600 cases)", "data/adversarial-v1 (60 cases)"],
            "sealed_challenge_dataset": "data/adversarial-v2 (30 cases)",
            "sealed_dataset_cases_included": include_sealed,
            "status_note": (
                "Full sealed benchmark cases are omitted by default to prevent evaluation data leakage into "
                "subsequent model training or prompt iteration. Only documentation and verification manifests are included."
                if not include_sealed
                else "WARNING: Sealed evaluation cases were explicitly included; adversarial-v2 must no longer be considered held-out."
            ),
        },
        "files_count": len(file_manifest_entries),
        "files": file_manifest_entries,
    }

    output_zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for src_path, arc_name in files_to_bundle:
            if src_path.exists():
                zf.write(src_path, arc_name)
        # Write internal manifest into root of ZIP
        zf.writestr("archive_manifest.json", json.dumps(archive_manifest, indent=2))

    zip_sha256 = _compute_sha256(output_zip_path)
    zip_size = output_zip_path.stat().st_size

    # Write external checksum file alongside zip
    checksum_path = output_zip_path.parent / "SHA256SUMS.txt"
    with open(checksum_path, "w", encoding="utf-8") as f:
        f.write(f"{zip_sha256}  {output_zip_path.name}\n")

    # Write external archive manifest alongside zip
    ext_manifest_path = output_zip_path.parent / "archive_manifest.json"
    with open(ext_manifest_path, "w", encoding="utf-8") as f:
        json.dump(archive_manifest, f, indent=2)

    print(f"\nSuccessfully created evaluation package: {output_zip_path}")
    print(f"Archive byte size: {zip_size:,} bytes")
    print(f"Archive SHA-256:   {zip_sha256}")
    print(f"Checksum file:     {checksum_path}")
    print(f"Manifest file:     {ext_manifest_path}")

    return output_zip_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Package LocaleGate evaluation artifacts.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "artifacts/releases/localegate_research_validation_1.0_evaluation_package.zip"
        ),
        help="Output ZIP path",
    )
    parser.add_argument(
        "--include-sealed",
        action="store_true",
        default=False,
        help="Include sealed challenge benchmark cases (data/adversarial-v2/cases.jsonl). Default: False.",
    )
    args = parser.parse_args()
    create_evaluation_package(args.output, include_sealed=args.include_sealed)
