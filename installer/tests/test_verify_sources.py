#!/usr/bin/env python3
"""Tests for verify-sources build phase (design doc §5.1).

Fixtures exercise four conditions:
  1. Pinned-correctly — valid sha256 matches tarball → PASS
  2. Unpinned — source entry with no sha256 → HARD FAIL
  3. Mismatched — sha256 doesn't match tarball → HARD FAIL
  4. Build_artifacts-only — no source key, only build_artifacts → PASS (skipped)
"""

import hashlib
import os
import sys
import tempfile
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def fixture_dir():
    d = Path(tempfile.mkdtemp(prefix="verify-sources-test-"))
    pkgs = d / "packages" / "test"
    sources = d / "sources"
    sources.mkdir(parents=True)

    # 1. Pinned-correctly
    (pkgs / "pinned").mkdir(parents=True)
    content = b"hello intergenos\n" * 100
    sha = hashlib.sha256(content).hexdigest()
    (sources / "pinned-1.0.tar.gz").write_bytes(content)
    (pkgs / "pinned" / "package.yml").write_text(
        "name: pinned\nversion: '1.0'\n"
        "source:\n  - url: https://example.com/pinned-1.0.tar.gz\n"
        f"    sha256: {sha}\n"
    )

    # 2. Unpinned — url but no sha256
    (pkgs / "unpinned").mkdir(parents=True)
    (pkgs / "unpinned" / "package.yml").write_text(
        "name: unpinned\nversion: '1.0'\n"
        "source:\n  - url: https://example.com/unpinned-1.0.tar.gz\n"
    )

    # 3. Mismatched — sha256 doesn't match
    (pkgs / "mismatched").mkdir(parents=True)
    (sources / "mismatched-1.0.tar.gz").write_bytes(content)
    fake_sha = "a" * 64
    (pkgs / "mismatched" / "package.yml").write_text(
        "name: mismatched\nversion: '1.0'\n"
        "source:\n  - url: https://example.com/mismatched-1.0.tar.gz\n"
        f"    sha256: {fake_sha}\n"
    )

    # 4. Build_artifacts only — no source key
    (pkgs / "build-artifacts-only").mkdir(parents=True)
    (pkgs / "build-artifacts-only" / "package.yml").write_text(
        "name: ba-only\nversion: '1.0'\n"
        "build_artifacts:\n"
        "  - name: vendor.tar.xz\n"
        "    generated_by: cargo-vendor\n"
    )

    return d


# ---------------------------------------------------------------------------
# Inline the verify-sources logic (same as the phase_verify_sources function
# in scripts/build-intergenos.sh)
# ---------------------------------------------------------------------------

def run_verify_sources(packages_dir: Path, sources_dir: Path):
    unpinned = []
    mismatches = []

    for yml_path in sorted(packages_dir.rglob("package.yml")):
        with yml_path.open() as f:
            data = yaml.safe_load(f)

        name = data.get("name", yml_path.parent.name)
        src = data.get("source")

        if not src or not isinstance(src, list):
            continue

        for item in src:
            if not isinstance(item, dict):
                unpinned.append(f"{name}: malformed source entry")
                continue
            sha = item.get("sha256")
            if not sha or not isinstance(sha, str) or len(sha) != 64:
                unpinned.append(f"{name}: missing/invalid sha256")
                continue
            filename = item.get("filename") or item["url"].rsplit("/", 1)[-1].split("?")[0]
            tarball = sources_dir / filename
            if not tarball.exists():
                mismatches.append(f"{name}: {filename} not found")
                continue
            actual = hashlib.sha256(tarball.read_bytes()).hexdigest()
            if actual != sha:
                mismatches.append(f"{name}: {filename} sha mismatch")

    return unpinned, mismatches


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestVerifySources:
    def test_pinned_correctly_passes(self, fixture_dir):
        unpinned, mismatches = run_verify_sources(
            fixture_dir / "packages", fixture_dir / "sources"
        )
        # pinned should NOT appear in either list
        pinned_entries = [u for u in unpinned if u.startswith("pinned:")] + \
                         [m for m in mismatches if m.startswith("pinned:")]
        assert pinned_entries == []

    def test_unpinned_flagged(self, fixture_dir):
        unpinned, mismatches = run_verify_sources(
            fixture_dir / "packages", fixture_dir / "sources"
        )
        assert any("unpinned" in u for u in unpinned)

    def test_mismatched_flagged(self, fixture_dir):
        unpinned, mismatches = run_verify_sources(
            fixture_dir / "packages", fixture_dir / "sources"
        )
        assert any("mismatched" in m for m in mismatches)

    def test_build_artifacts_only_skipped(self, fixture_dir):
        unpinned, mismatches = run_verify_sources(
            fixture_dir / "packages", fixture_dir / "sources"
        )
        assert not any("ba-only" in u for u in unpinned)
        assert not any("ba-only" in m for m in mismatches)

    def test_hard_fail_if_any_unpinned_or_mismatched(self, fixture_dir):
        unpinned, mismatches = run_verify_sources(
            fixture_dir / "packages", fixture_dir / "sources"
        )
        # At least the unpinned + mismatched fixtures should fire
        assert len(unpinned) >= 1
        assert len(mismatches) >= 1
