# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The pre-squashfs audit asks the parser what ships; it keeps no rule of its own.

THE DRIFT THIS PINS. `scripts/pre-squashfs-audit.py` carried a hand-written copy
of the parser's iso_include tier rule. The copy said ('extra', 'compute'); the
authority (igos-build/parser.py) said ("extra", "compute", "toolchain"). That is
the SECOND time the two diverged — `compute` did the same thing in 2026-07-18 and
surfaced as a mint Step-4.5 halt, and the comment above the copy recorded that
incident while the copy drifted again underneath it.

WHAT THE DIVERGENCE COSTS. A toolchain-tier recipe that declares verify_paths and
no explicit iso_include is not shipped by the build, so its files are not in the
chroot at squashfs time — but the audit's copy called it an ISO package, checked
its declared paths, found them absent, and failed the gate. That is a burn halt on
a recipe that is behaving correctly.

WHY THE REPO HAD NOT FELT IT YET. Measured on the tree at 707f1da83: 28 recipes
declare `tier: toolchain`, none of them declares verify_paths, and no recipe
anywhere declares a tier that differs from its directory. The divergence was live
and latent at the same time.

THE SECOND COPY, ALSO REMOVED. The audit additionally skipped the whole
`packages/toolchain/` DIRECTORY, which stated "toolchain does not ship" a second
time, keyed on where a recipe sits instead of what it declares. With the tier rule
sourced from the parser that skip is redundant, and keying on the directory would
hide a mis-filed recipe. It is gone, so a toolchain recipe is now READ and
reported as exempt rather than never looked at — which is what the last test here
pins.
"""

import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "pre-squashfs-audit.py"

_spec = importlib.util.spec_from_file_location("pre_squashfs_audit", SCRIPT)
_psa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_psa)

sys.path.insert(0, str(REPO_ROOT / "igos-build"))
import parser as igos_parser  # noqa: E402  (path manipulation precedes import)

RECIPE = ("name: {name}\nversion: '1.0'\nrelease: 1\n"
          "description: d\nlicense: MIT\nbuild_style: custom\n"
          "tier: {tier}\n{extra}")


def _write_pkg(pkgs_dir, tier_dir, name, tier, extra=""):
    d = pkgs_dir / tier_dir / name
    d.mkdir(parents=True)
    (d / "package.yml").write_text(
        RECIPE.format(name=name, tier=tier, extra=extra))
    return d


def _run_audit(pkgs_dir, chroot):
    """Run the audit's main() over a temp package tree, capturing its report.

    assert_root_traversal is stubbed: it refuses a non-root run because an
    unprivileged /proc-style traversal misreports files as missing, which is a
    real property of the chroot and not what these tests measure. Nothing else
    is patched — the walk, the exemptions and the path checks are the real ones.
    """
    argv = ["pre-squashfs-audit.py", "--packages-dir", str(pkgs_dir),
            "--chroot", str(chroot)]
    out = io.StringIO()
    with mock.patch.object(_psa, "assert_root_traversal", lambda _c: None), \
            mock.patch.object(sys, "argv", argv), redirect_stdout(out):
        try:
            rc = _psa.main()
        except SystemExit as e:            # main() exits on argument errors
            rc = e.code
    return rc, out.getvalue()


class TheAuditKeepsNoCopyOfTheRule(unittest.TestCase):
    """The audit's answer IS the parser's answer, for every tier."""

    def test_toolchain_default_is_not_shipped(self):
        # RED before the fix: the audit's own tuple omitted toolchain, so this
        # returned True — "this ships, audit its paths".
        self.assertFalse(_psa.effective_iso_include({"tier": "toolchain"}))

    def test_audit_agrees_with_the_parser_on_every_tier(self):
        for tier in ("core", "base", "desktop", "ai", "extra", "compute",
                     "toolchain", "a_tier_that_does_not_exist_yet"):
            with self.subTest(tier=tier):
                self.assertEqual(
                    igos_parser.effective_iso_include(None, tier),
                    _psa.effective_iso_include({"tier": tier}),
                    f"the audit and the parser disagree about tier {tier!r}")

    def test_the_audit_holds_no_tier_tuple_of_its_own(self):
        """A copy of the rule is what drifted twice; there must not be one."""
        source = SCRIPT.read_text()
        code = "\n".join(
            line for line in source.splitlines()
            if not line.lstrip().startswith("#"))
        for tier in ("extra", "compute", "toolchain"):
            self.assertNotIn(
                f"'{tier}'", code,
                f"pre-squashfs-audit.py names the tier {tier!r} in code — the "
                f"rule belongs to igos-build/parser.py alone")
            self.assertNotIn(f'"{tier}"', code, f"same, double-quoted: {tier}")

    def test_explicit_override_still_wins_both_ways(self):
        self.assertTrue(
            _psa.effective_iso_include({"tier": "toolchain",
                                        "iso_include": True}))
        self.assertFalse(
            _psa.effective_iso_include({"tier": "core", "iso_include": False}))

    def test_a_non_boolean_iso_include_is_refused_not_coerced(self):
        """Coercion could exempt a package silently — the one unacceptable answer.

        The parser refuses a non-boolean at parse time; the audit now refuses it
        too, instead of the old bool() coercion that made a quoted "false" mean
        True (and a 0 mean "skip this package without saying so").
        """
        with self.assertRaises(ValueError):
            _psa.effective_iso_include({"tier": "core", "iso_include": "false"})
        with self.assertRaises(ValueError):
            _psa.effective_iso_include({"tier": "core", "iso_include": 0})


class TheGe9b05HaltShape(unittest.TestCase):
    """End to end, through the audit's real main(), on a real temp tree."""

    def test_toolchain_recipe_with_verify_paths_is_exempt_not_failed(self):
        # The shape that halted the mint at Step-4.5: a recipe whose declared
        # paths are genuinely absent from the chroot because the build never
        # ships it. The audit must exempt it, not report a missing path.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            pkgs, chroot = tmp / "packages", tmp / "chroot"
            chroot.mkdir()
            _write_pkg(pkgs, "toolchain", "gcc-pass1", "toolchain",
                       extra="verify_paths:\n  - /usr/bin/never-shipped\n")
            rc, report = _run_audit(pkgs, chroot)
        self.assertEqual(0, rc, f"audit failed a correct recipe:\n{report}")
        # Read and classified, not invisible: the whole packages/toolchain/
        # directory used to be skipped before any recipe in it was opened, so
        # the totals stayed at zero and nothing said why.
        self.assertIn("Total packages read:            1", report,
                      f"the recipe was never read:\n{report}")
        self.assertIn("or not shipped): 1", report,
                      f"the recipe was read but not counted as exempt:\n{report}")

    def test_a_toolchain_recipe_outside_its_directory_is_still_exempt(self):
        # The declared tier decides, not the directory the recipe sits in.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            pkgs, chroot = tmp / "packages", tmp / "chroot"
            chroot.mkdir()
            _write_pkg(pkgs, "core", "misfiled-tmp", "toolchain",
                       extra="verify_paths:\n  - /usr/bin/never-shipped\n")
            rc, report = _run_audit(pkgs, chroot)
        self.assertEqual(0, rc, f"audit failed a correct recipe:\n{report}")

    def test_a_shipping_recipe_with_a_missing_path_still_fails(self):
        """The negative control: the exemption must not have blunted the gate."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            pkgs, chroot = tmp / "packages", tmp / "chroot"
            chroot.mkdir()
            _write_pkg(pkgs, "core", "coreutils", "core",
                       extra="verify_paths:\n  - /usr/bin/never-installed\n")
            rc, report = _run_audit(pkgs, chroot)
        self.assertEqual(1, rc,
                         f"a genuinely missing path must fail the gate:\n{report}")
        self.assertIn("never-installed", report)


if __name__ == "__main__":
    unittest.main()
