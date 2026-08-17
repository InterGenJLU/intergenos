# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""forge's normalized installer-hooks fingerprint (item-8 completion, 2026-07-08).

forge fingerprints its INPUTS, not its generated tarball's bytes. Its dynamic
input is the per-package installer-hooks tree that scripts/build-forge-tarball.sh
stages (every non-toolchain package's build.sh + release/content_hash-stripped
package.yml). content_hash.installer_hooks_fingerprint mirrors that staging loop.

The load-bearing risk is the two loops DRIFTING silently — a change to the
generator's staging that the fingerprint doesn't track would let a stale-hooks
forge ship unbumped again. This test is the anti-drift guard the ledger row
requires: it runs the REAL generator, extracts the installer-hooks tree it
produced, and asserts the fingerprint loop hashes EXACTLY the same
(tier/pkg -> build.sh raw, package.yml stripped) set — byte-for-byte — modulo the
one sanctioned self-exclusion (forge's own recipe, which template_hash covers).
"""
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

from content_hash import (  # noqa: E402
    installer_hooks_fingerprint,
    _strip_hook_bump_lines,
)


def _forge_version() -> str:
    for line in (REPO_ROOT / "packages/desktop/forge/package.yml").read_text().splitlines():
        if line.startswith("version:"):
            return line.split('"')[1]
    raise AssertionError("forge version not found")


class ForgeHooksFingerprintParity(unittest.TestCase):
    def setUp(self):
        # Run the REAL generator so we compare against what actually ships, not a
        # re-implementation of the staging loop.
        r = subprocess.run(
            [str(REPO_ROOT / "scripts/build-forge-tarball.sh")],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, f"generator failed: {r.stderr}")
        self.version = _forge_version()
        self.tarball = REPO_ROOT / "build/sources" / f"forge-{self.version}.tar.xz"
        self.assertTrue(self.tarball.exists(), "generator did not produce the tarball")

    def _staged_hooks(self):
        """{(tier,pkg): (build.sh bytes, package.yml bytes or None)} from the tarball
        the generator actually produced. Excludes forge's own recipe (the
        fingerprint self-excludes it — template_hash covers it)."""
        out = {}
        with tempfile.TemporaryDirectory() as td:
            with tarfile.open(self.tarball) as tf:
                tf.extractall(td)
            hooks = Path(td) / f"forge-{self.version}" / "installer-hooks"
            self.assertTrue(hooks.is_dir(), "no installer-hooks/ in the tarball")
            for tier in sorted(hooks.iterdir()):
                if not tier.is_dir():
                    continue
                for pkg in sorted(tier.iterdir()):
                    bs = pkg / "build.sh"
                    if not bs.is_file():
                        continue
                    yml = pkg / "package.yml"
                    out[(tier.name, pkg.name)] = (
                        bs.read_bytes(),
                        yml.read_bytes() if yml.is_file() else None,
                    )
        out.pop(("desktop", "forge"), None)   # sanctioned self-exclusion
        return out

    def _fingerprint_hooks(self):
        """The same map, built the way installer_hooks_fingerprint iterates the
        repo (build.sh raw + package.yml stripped via _strip_hook_bump_lines),
        with forge self-excluded."""
        out = {}
        packages = REPO_ROOT / "packages"
        for tier in sorted(p for p in packages.iterdir() if p.is_dir()):
            if tier.name == "toolchain":
                continue
            for pkg in sorted(p for p in tier.iterdir() if p.is_dir()):
                bs = pkg / "build.sh"
                if not bs.is_file():
                    continue
                if (tier.name, pkg.name) == ("desktop", "forge"):
                    continue
                yml = pkg / "package.yml"
                out[(tier.name, pkg.name)] = (
                    bs.read_bytes(),
                    _strip_hook_bump_lines(yml.read_bytes()) if yml.is_file() else None,
                )
        return out

    def test_hook_package_set_matches_generator(self):
        self.assertEqual(set(self._staged_hooks()), set(self._fingerprint_hooks()),
                         "the fingerprint's package set drifted from the generator's")

    def test_hook_bytes_match_generator_byte_for_byte(self):
        staged = self._staged_hooks()
        fp = self._fingerprint_hooks()
        for key in fp:
            s_bs, s_yml = staged[key]
            f_bs, f_yml = fp[key]
            self.assertEqual(f_bs, s_bs, f"build.sh drift at {key}")
            # The generator strips release/content_hash from staged ymls via sed;
            # the fingerprint strips via _strip_hook_bump_lines. These MUST agree
            # byte-for-byte or a release bump could leak into forge's fingerprint.
            self.assertEqual(f_yml, s_yml, f"stripped package.yml drift at {key}")

    def test_strip_is_scoped_to_the_two_machine_lines(self):
        # A defensive unit check on the strip itself: it removes release/content_hash
        # top-level lines and NOTHING else (so a real hook-yml edit still drifts forge).
        src = (b"name: demo\nrelease: 7  # why\ncontent_hash: abcdef0123456789\n"
               b"description: keep me\n")
        self.assertEqual(
            _strip_hook_bump_lines(src),
            b"name: demo\ndescription: keep me\n",
        )

    def test_missing_packages_dir_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileNotFoundError):
                installer_hooks_fingerprint(td)  # no packages/ under an empty root


if __name__ == "__main__":
    unittest.main()
