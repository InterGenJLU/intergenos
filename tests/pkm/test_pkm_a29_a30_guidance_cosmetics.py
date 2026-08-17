#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PKM-A29/A30 regression: next-step guidance consistency + display cosmetics.

A29 — inconsistent next-step guidance + WARN/WARNING casing + summary wording.
The WARN->WARNING unification rode the A27 emit_warn conversion (every former
"WARN:" raw print now goes through emit_warn, prefix "WARNING:"). The residual
was command-hint quoting: some hints used single quotes ('pkm update') and the
check-updates install hint omitted sudo while cmd_update included it. Unified:
inline commands are backticked, and the upgrade install hint is `sudo pkm
upgrade --all` everywhere (upgrade mutates the system → needs root).

A30 — `pkm info` showed version without release (a same-version mirror republish
only advances release, so version alone hides a real difference, per A06), used
a fixed 50-column rule regardless of the title width, and printed reverse-deps
without release. Fixed: full version-release identity, a rule sized to the title,
and get_reverse_depends now carries release.
"""

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from pkm import cli
from pkm.database import PackageDB


class DisplayCosmeticsTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.tmp / "root"))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def _info(self, name):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.cmd_info(self.db, argparse.Namespace(package=name))
        return buf.getvalue()

    def test_info_shows_version_release_and_title_sized_rule(self):
        self.db.add_installed("libfoo", "1.2.0", release=3, tier="core")
        out = self._info("libfoo")
        # Full version-release identity in the title (not bare "1.2.0").
        self.assertIn("libfoo 1.2.0-3", out)
        # The rule line is sized to the title, NOT a fixed 50 '=' columns.
        lines = [l.strip() for l in out.splitlines()]
        rules = [l for l in lines if set(l) == {"="}]
        self.assertTrue(rules, "expected an '=' rule line")
        self.assertEqual(len(rules[0]), len("libfoo 1.2.0-3"))
        self.assertNotEqual(len(rules[0]), 50)

    def test_reverse_deps_show_version_release(self):
        self.db.add_installed("libfoo", "1.2.0", release=3, tier="core")
        appbar_id = self.db.add_installed("appbar", "2.0.0", release=5, tier="extra")
        self.db.add_depends(appbar_id, [("libfoo", "runtime")])
        out = self._info("libfoo")
        self.assertIn("Required by", out)
        self.assertIn("appbar 2.0.0-5", out)

    def test_get_reverse_depends_carries_release(self):
        self.db.add_installed("libfoo", "1.0", release=1, tier="core")
        appbar_id = self.db.add_installed("appbar", "2.0", release=7, tier="extra")
        self.db.add_depends(appbar_id, [("libfoo", "runtime")])
        rdeps = self.db.get_reverse_depends("libfoo")
        self.assertEqual(len(rdeps), 1)
        self.assertEqual(rdeps[0]["release"], 7)
        # Additive: existing keys still present.
        self.assertEqual(rdeps[0]["name"], "appbar")
        self.assertEqual(rdeps[0]["version"], "2.0")


class GuidanceConsistencyTest(unittest.TestCase):
    """Source-level guard: inline command hints are backticked (not single-
    quoted) and the upgrade install hint includes sudo. Prevents reintroducing
    the inconsistent guidance A29 unified."""

    @classmethod
    def setUpClass(cls):
        cls.src = (Path(__file__).resolve().parents[2] / "pkm" / "cli.py").read_text()

    def _code_lines(self):
        # Skip comment-only lines + docstring-ish lines so we test emitted
        # guidance, not prose in comments.
        for ln in self.src.splitlines():
            s = ln.strip()
            if s.startswith("#"):
                continue
            yield ln

    def test_no_single_quoted_pkm_command_hints(self):
        offenders = [ln for ln in self._code_lines() if "'pkm " in ln]
        self.assertEqual(offenders, [], f"single-quoted pkm hints remain: {offenders}")

    def test_upgrade_install_hint_includes_sudo(self):
        # The check-updates "to install" hint must point at `sudo pkm upgrade`.
        self.assertIn("sudo pkm upgrade --all` to install", self.src)
        # And the bare un-sudo'd "pkm upgrade --all` to install" must be gone.
        self.assertNotIn("`pkm upgrade --all` to install", self.src)

    def test_upgradeable_spelling_alias_accepted(self):
        # `pkm list upgradeable` is accepted as a spelling alias of upgradable.
        self.assertIn('"upgradeable"', self.src)        # in the choices
        self.assertIn('("upgradable", "upgradeable")', self.src)  # handled in cmd_list


if __name__ == "__main__":
    unittest.main()
