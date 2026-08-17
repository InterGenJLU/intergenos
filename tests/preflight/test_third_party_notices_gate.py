"""The THIRD-PARTY-NOTICES regeneration gate refuses and passes what it should.

Pins the generator's --check mode (decided 2026-08-16): the committed
THIRD-PARTY-NOTICES.md must byte-match what the generator produces from the
tree, because two independent correct recipe fixes once left the published
attribution stale for eleven days with nothing to catch it. The generator
derives its repository root from its own location, so the fixture tests copy
the REAL script into a throwaway tree; one test also runs --check against the
actual repository, which pins the live in-sync state the gate's adoption
depends on.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
GENERATOR = REPO / "scripts" / "generate-third-party-notices.py"

PKG_YML = """\
name: demo-lib
version: "1.0"
release: 1
description: Demonstration library
license: MIT
homepage: https://example.invalid/demo-lib
tier: core
"""


def _fixture_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(GENERATOR, root / "scripts" / GENERATOR.name)
    pkg = root / "packages" / "core" / "demo-lib"
    pkg.mkdir(parents=True)
    (pkg / "package.yml").write_text(PKG_YML)
    return root


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / GENERATOR.name), *args],
        capture_output=True, text=True, cwd=root)


class TestCheckMode:
    def test_freshly_generated_tree_is_in_sync(self, tmp_path):
        root = _fixture_repo(tmp_path)
        assert _run(root).returncode == 0
        r = _run(root, "--check")
        assert r.returncode == 0
        assert "in sync" in r.stdout

    def test_recipe_license_change_without_regeneration_refused(self, tmp_path):
        root = _fixture_repo(tmp_path)
        assert _run(root).returncode == 0
        yml = root / "packages" / "core" / "demo-lib" / "package.yml"
        yml.write_text(PKG_YML.replace("license: MIT", "license: MIT-0"))
        r = _run(root, "--check")
        assert r.returncode == 2
        assert "DRIFT" in r.stderr
        assert "generate-third-party-notices.py" in r.stderr

    def test_hand_edited_notices_refused(self, tmp_path):
        root = _fixture_repo(tmp_path)
        assert _run(root).returncode == 0
        notices = root / "THIRD-PARTY-NOTICES.md"
        notices.write_text(notices.read_text().replace("MIT", "BSD-3-Clause"))
        r = _run(root, "--check")
        assert r.returncode == 2

    def test_missing_notices_file_is_an_error_not_a_pass(self, tmp_path):
        root = _fixture_repo(tmp_path)
        r = _run(root, "--check")
        assert r.returncode == 1

    def test_regeneration_is_the_stated_remedy_and_repairs(self, tmp_path):
        root = _fixture_repo(tmp_path)
        assert _run(root).returncode == 0
        yml = root / "packages" / "core" / "demo-lib" / "package.yml"
        yml.write_text(PKG_YML.replace('version: "1.0"', 'version: "1.1"'))
        assert _run(root, "--check").returncode == 2
        assert _run(root).returncode == 0
        assert _run(root, "--check").returncode == 0


class TestRealTree:
    def test_the_repository_is_in_sync_at_adoption(self):
        r = subprocess.run(
            [sys.executable, str(GENERATOR), "--check"],
            capture_output=True, text=True, cwd=REPO)
        assert r.returncode == 0, r.stderr
        assert "in sync" in r.stdout
