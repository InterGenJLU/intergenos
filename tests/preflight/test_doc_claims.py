# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Publication-time tests for scripts/check-doc-claims.py.

The fixture is a miniature release tree plus the two external publication
inputs: the image checksum record and the mirror index.  Each named claim
class is then made stale by itself, so a green result cannot come from one
earlier failure hiding the remaining checks.
"""

from __future__ import annotations

import gzip
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "check-doc-claims.py"
DIGEST = "a" * 64


class ReleaseFixture:
    def __init__(self, root: Path):
        self.root = root
        self.repo = root / "repo"
        self.wiki = root / "switching.md"
        self.index = root / "InterGenOS.db"
        self.iso_sum = root / "intergenos-r001.2.iso.sha256"
        self._write()

    def _put(self, relative: str, text: str) -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _write(self) -> None:
        etc = "packages/core/intergenos-base-files/files/etc"
        self._put(f"{etc}/igos-release", "R001.2\n")
        self._put(
            f"{etc}/os-release",
            'NAME="InterGenOS"\nVERSION="R001.2 (Revival)"\nID=intergenos\n'
            'VERSION_ID=r001.2\nVERSION_CODENAME=revival\n'
            'PRETTY_NAME="InterGenOS R001.2 (Revival)"\n',
        )
        self._put(
            f"{etc}/lsb-release",
            'DISTRIB_ID="InterGenOS"\nDISTRIB_RELEASE="R001.2"\n'
            'DISTRIB_CODENAME="revival"\n'
            'DISTRIB_DESCRIPTION="InterGenOS R001.2 (Revival)"\n',
        )
        self._put(
            f"{etc}/issue",
            "\n  InterGenOS R001.2 (Revival)\n  Kernel \\r on \\m (\\l)\n",
        )
        self._put("packages/extra/demo/package.yml", "name: demo\nversion: 2.0\n")
        self._put(
            "README.md",
            "# InterGenOS\n\n"
            "> ### Download InterGenOS R001.2 "
            "(https://repo.example/iso/intergenos-r001.2.iso)\n"
            f"> x86_64 live image · sha256 `{DIGEST}` — "
            "[checksum](https://repo.example/iso/intergenos-r001.2.iso.sha256) "
            "[signature](https://repo.example/iso/intergenos-r001.2.iso.sha256.asc)\n\n"
            "## Features\n\n- **Already shipped** — present now.\n\n"
            "## Upcoming\n\n"
            "Items planned after R001.2:\n\n"
            "- **Future tool** — planned for a later release.\n\n"
            "## History\n",
        )
        self._put(
            "SECURITY.md",
            "# Security Policy\n\n"
            "> **Project status:** the current release is the newest point "
            "release of the **R001.x** line, always stated at "
            "https://intergenos.org/news.html.\n",
        )
        self._put(
            "docs/release-policy.md",
            "# Release Policy\n\n## Release types\n\n"
            "- **Major releases — `R001`, `R002`, …** rebuild everything.\n"
            "- **Point releases — `R001.1`, `R001.2`, …** rebuild the delta.\n",
        )
        self._put(
            "CHANGELOG.md",
            "# Changelog\n\n## [Unreleased]\n\nNothing yet.\n\n"
            "## [R001.2] — 2026-09-03\n\n### Added\n- Already shipped.\n",
        )
        self.wiki.write_text(
            "# Switching\n\n```bash\nsudo pkm install demo\n```\n",
            encoding="utf-8",
        )
        self.iso_sum.write_text(
            f"{DIGEST}  intergenos-r001.2.iso\n", encoding="utf-8")
        with gzip.open(self.index, "wt", encoding="utf-8") as handle:
            json.dump({
                "version": 1,
                "package_count": 1,
                "packages": {"demo": {"version": "2.0", "release": 1}},
            }, handle)

    def replace(self, relative: str, old: str, new: str) -> None:
        path = self.repo / relative
        text = path.read_text(encoding="utf-8")
        if old not in text:
            raise AssertionError(f"fixture text {old!r} not found in {relative}")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def run(self) -> tuple[int, str]:
        result = subprocess.run(
            [
                sys.executable, str(GATE),
                "--tree", str(self.repo),
                "--iso-sha256", str(self.iso_sum),
                "--mirror-index", str(self.index),
                "--wiki-switching-page", str(self.wiki),
            ],
            capture_output=True, text=True,
        )
        return result.returncode, result.stdout + result.stderr


class DocClaimsGateTest(unittest.TestCase):
    def test_current_release_fixture_passes(self):
        with tempfile.TemporaryDirectory(prefix="doc-claims-current-") as tmp:
            fixture = ReleaseFixture(Path(tmp))
            rc, out = fixture.run()
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS", out)
        self.assertIn("R001.2", out)
        self.assertIn("does not cover", out.lower())

    def test_each_stale_claim_class_is_refused_independently(self):
        mutations = {
            "readme release": (
                lambda f: f.replace("README.md", "Download InterGenOS R001.2",
                                    "Download InterGenOS R001.1"),
                "README.md:3", "release",
            ),
            "readme iso name": (
                lambda f: f.replace("README.md", "intergenos-r001.2.iso)",
                                    "intergenos-r001.1.iso)"),
                "README.md:3", "ISO name",
            ),
            "readme digest": (
                lambda f: f.replace("README.md", DIGEST, "b" * 64),
                "README.md:4", "sha256",
            ),
            "upcoming package": (
                lambda f: (
                    f._put("packages/extra/future-tool/package.yml",
                           "name: future-tool\nversion: 1.0\n"),
                    f.replace("README.md", "**Future tool**",
                              "**future-tool**"),
                ),
                "README.md:14", "already has package",
            ),
            "upcoming released feature": (
                lambda f: f.replace("README.md", "**Future tool**",
                                    "**Already shipped**"),
                "README.md:14", "released changelog",
            ),
            "security status": (
                lambda f: f.replace("SECURITY.md", "R001.x", "R001.1"),
                "SECURITY.md:3", "release-invariant",
            ),
            "release policy version row": (
                lambda f: f.replace("docs/release-policy.md",
                                    "`R001.1`, `R001.2`, …", "`R001.1` only"),
                "docs/release-policy.md:6", "Point releases",
            ),
            "changelog head": (
                lambda f: f.replace("CHANGELOG.md", "[R001.2]", "[R001.1]"),
                "CHANGELOG.md:7", "top release",
            ),
            "wiki mirror package": (
                lambda f: f.wiki.write_text(
                    "# Switching\n\n`sudo pkm install absent-package`\n",
                    encoding="utf-8"),
                "switching.md:3", "absent-package",
            ),
        }
        for name, (mutate, location, message) in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory(
                    prefix="doc-claims-stale-") as tmp:
                fixture = ReleaseFixture(Path(tmp))
                mutate(fixture)
                rc, out = fixture.run()
                self.assertEqual(rc, 2, out)
                self.assertIn(location, out)
                self.assertIn(message, out)

    def test_all_stale_classes_are_reported_in_one_run(self):
        with tempfile.TemporaryDirectory(prefix="doc-claims-all-stale-") as tmp:
            fixture = ReleaseFixture(Path(tmp))
            fixture.replace("README.md", DIGEST, "b" * 64)
            fixture.replace("README.md", "**Future tool**", "**Already shipped**")
            fixture.replace("SECURITY.md", "R001.x", "R001.1")
            fixture.replace("docs/release-policy.md", "`R001.1`, `R001.2`, …",
                            "`R001.1` only")
            fixture.replace("CHANGELOG.md", "[R001.2]", "[R001.1]")
            fixture.wiki.write_text("`pkm install absent-package`\n")
            rc, out = fixture.run()
        self.assertEqual(rc, 2, out)
        for path in ("README.md:", "SECURITY.md:", "docs/release-policy.md:",
                     "CHANGELOG.md:", "switching.md:"):
            self.assertIn(path, out)
        self.assertIn("REFUSED", out)


if __name__ == "__main__":
    unittest.main()
