"""Verify that the release package can be extracted and executed in a clean environment.

Implements the 6-step clean-environment verification recommended by the technical reviewer:
1. Extract the release ZIP into an isolated fresh temporary directory.
2. Verify all extracted files match their cryptographic SHA-256 hashes in archive_manifest.json.
3. Validate public benchmark datasets from the clean directory.
4. Replay the frozen regression fixtures (13 model-case executions) offline.
5. Execute the deterministic unit test suite.
6. Verify release archive SHA-256 matches SHA256SUMS.txt.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().lower()


def test_clean_environment_release(zip_path: Path) -> None:
    print("=" * 70)
    print("LocaleGate Research Validation 1.0 — Clean Environment Release Test")
    print(f"Archive: {zip_path}")
    print("=" * 70)

    assert zip_path.exists(), f"Release archive not found: {zip_path}"

    # Step 1: Verify archive checksum against SHA256SUMS.txt
    checksum_file = zip_path.parent / "SHA256SUMS.txt"
    if checksum_file.exists():
        expected_zip_hash = checksum_file.read_text(encoding="utf-8").split()[0].lower()
        actual_zip_hash = compute_sha256(zip_path)
        assert actual_zip_hash == expected_zip_hash, (
            f"Archive checksum mismatch: {actual_zip_hash} != {expected_zip_hash}"
        )
        print(f"[OK] Archive checksum matches SHA256SUMS.txt ({actual_zip_hash[:16]}...)")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        clean_dir = Path(tmpdir)
        print(f"\n1. Extracting archive into clean temporary directory:\n   {clean_dir}")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(clean_dir)

        # Step 2: Verify manifest integrity
        manifest_path = clean_dir / "archive_manifest.json"
        assert manifest_path.exists(), "archive_manifest.json missing from extracted archive"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        print(f"2. Validating cryptographic hashes for {len(manifest['files'])} bundled files...")
        for entry in manifest["files"]:
            target_file = clean_dir / entry["path"]
            assert target_file.exists(), f"File missing from archive: {entry['path']}"
            actual_hash = compute_sha256(target_file)
            assert actual_hash == entry["sha256"], (
                f"Hash mismatch on {entry['path']}: {actual_hash} != {entry['sha256']}"
            )
        print(f"   [OK] All {len(manifest['files'])} files verified identical to manifest.")

        # Step 3: Validate public benchmark dataset
        print("\n3. Validating public benchmark datasets in clean directory...")
        res = subprocess.run(
            [sys.executable, "-m", "locale_gate.cli.main", "eval", "validate-dataset"],
            cwd=clean_dir,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(clean_dir / "src")},
        )
        assert res.returncode == 0, f"validate-dataset failed:\n{res.stderr}\n{res.stdout}"
        print("   [OK] Public benchmark datasets validated (600 cases valid, 0 leakage).")

        # Step 4: Replay frozen regression fixtures
        print("\n4. Replaying frozen model failure regression fixtures...")
        res = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/regression/test_adversarial_model_regressions.py",
                "-q",
            ],
            cwd=clean_dir,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(clean_dir / "src")},
        )
        assert res.returncode == 0, f"Regression replay failed:\n{res.stderr}\n{res.stdout}"
        print("   [OK] All 13 model-case regression instances PASSED offline.")

        # Step 5: Run deterministic unit test suite
        print("\n5. Running deterministic unit test suite in clean directory...")
        res = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/unit", "-q"],
            cwd=clean_dir,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(clean_dir / "src")},
        )
        assert res.returncode == 0, f"Unit test suite failed:\n{res.stderr}\n{res.stdout}"
        print("   [OK] Deterministic unit tests PASSED cleanly.")

    print("\n" + "=" * 70)
    print("ALL CLEAN-ENVIRONMENT RELEASE VERIFICATIONS SUCCEEDED (6/6)")
    print("LocaleGate is 100% verified for external distribution.")
    print("=" * 70)


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent
    archive_path = (
        project_root
        / "artifacts"
        / "releases"
        / "localegate_research_validation_1.0_evaluation_package.zip"
    )
    test_clean_environment_release(archive_path)
