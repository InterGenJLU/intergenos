# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""RED/GREEN tests for scripts/preflight-license-identifiers.py.

Each test builds a synthetic mini-repo in a tempdir (packages/<tier>/<pkg>/
package.yml, a symlink to the REAL igos-build/ so the gate resolves iso_include
with the build's own parser, and a symlink to the REAL config/ so it validates
against the identifier sets the build actually ships) and invokes the gate as a
subprocess via --root, asserting the exit code and the emitted text.

Everything runs headless and offline: no network, no chroot, no prompt. The
gate reaching the network would itself be a defect, and one test asserts it
does not by running with the identifier file removed and requiring a SETUP
ERROR rather than a fetch or a pass.

Covered:
  RED  — an identifier that is not on the SPDX licence list
  RED  — two licences separated by a bare space ("Public Domain")
  RED  — hyphens where AND operators belong (the NVIDIA shape)
  RED  — unbalanced parenthesis
  RED  — a WITH whose right operand is a licence, not an exception
  RED  — an empty license: value          (fail-closed, not skipped)
  RED  — a missing license: key           (fail-closed, not skipped)
  RED  — an unparseable package.yml       (fail-closed, not skipped)
  RED  — every finding reported in ONE run, not just the first
  GREEN— plain identifier, expression with AND/OR/parens, WITH + real
         exception, LicenseRef-, DocumentRef-qualified LicenseRef-, trailing '+'
  WARN — a deprecated-but-listed identifier passes and is still reported
  scope— --shipped-only leaves a mirror-only offender unchecked
  setup— a missing identifier data file exits 2, never 0
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "preflight-license-identifiers.py"
REAL_IGOS_BUILD = REPO_ROOT / "igos-build"
REAL_CONFIG = REPO_ROOT / "config"


def _pkg_yml(name: str, tier: str, license_line: str | None) -> str:
    """A minimal-but-complete template satisfying the parser's REQUIRED_FIELDS.

    license_line is written verbatim so a test can express a malformed value,
    or omitted entirely so the missing-key path is exercised for real.
    """
    lines = [
        f"name: {name}",
        "version: 1.0.0",
        "release: 1",
        f"description: {name} test fixture",
        "source: []",
        "build_style: custom",
        f"tier: {tier}",
    ]
    if license_line is not None:
        lines.append(license_line)
    return "\n".join(lines) + "\n"


def _make_repo(tmp: Path, packages: dict[str, dict], *,
               config: Path | None = REAL_CONFIG) -> Path:
    """packages = {name: {"tier": ..., "license": "license: MIT" | None,
                          "raw": "<verbatim package.yml text>"}}"""
    (tmp / "packages").mkdir()
    for name, meta in packages.items():
        pkg_dir = tmp / "packages" / meta["tier"] / name
        pkg_dir.mkdir(parents=True)
        body = meta.get("raw")
        if body is None:
            body = _pkg_yml(name, meta["tier"], meta.get("license", "license: MIT"))
        (pkg_dir / "package.yml").write_text(body)
    (tmp / "igos-build").symlink_to(REAL_IGOS_BUILD)
    if config is not None:
        (tmp / "config").symlink_to(config)
    return tmp


def _run(repo: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(repo), *extra],
        capture_output=True, text=True,
    )


class TestLicenseIdentifierGate(unittest.TestCase):

    # ---- RED: identifiers that are not on the list -----------------------

    def test_unlisted_identifier_is_red_and_names_the_token(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), {
                "docbook-xml": {"tier": "core", "license": "license: OASIS"},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("HALT", r.stderr)
            self.assertIn("docbook-xml", r.stderr)
            self.assertIn("'OASIS'", r.stderr)
            self.assertIn("not an identifier on the SPDX licence list", r.stderr)

    def test_bare_space_between_licences_is_red(self):
        # "Public Domain" is two operands with no operator. A shape check that
        # only looked at token characters would accept both words.
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), {
                "sqlite": {"tier": "core", "license": "license: Public Domain"},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("sqlite", r.stderr)

    def test_hyphenated_operators_are_red(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), {
                "nvidia": {
                    "tier": "extra",
                    "license": "license: MIT-AND-GPL-2.0-only-AND-LicenseRef-X",
                },
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("nvidia", r.stderr)

    def test_unbalanced_parenthesis_is_red(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), {
                "lopsided": {"tier": "core",
                             "license": "license: (MIT OR Apache-2.0"},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("unbalanced", r.stderr)

    def test_with_operand_must_be_an_exception_not_a_licence(self):
        # Exceptions live on their own SPDX list. A membership check that
        # consulted only the licence list would accept `MIT WITH MIT`.
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), {
                "wrong-with": {"tier": "core", "license": "license: MIT WITH MIT"},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("licence-exception list", r.stderr)

    def test_trailing_operator_is_red(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), {
                "dangling": {"tier": "core", "license": "license: MIT AND"},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("dangling", r.stderr)

    # ---- RED: fail-closed on anything unreadable -------------------------

    def test_empty_license_value_is_a_finding_not_a_skip(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), {
                "blank": {"tier": "core", "license": 'license: ""'},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("blank", r.stderr)

    def test_missing_license_key_is_a_finding_not_a_skip(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), {
                "nolicense": {"tier": "core", "license": None},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("nolicense", r.stderr)

    def test_unparseable_recipe_is_a_finding_not_a_skip(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), {
                "broken": {"tier": "core",
                           "raw": "name: broken\n  bad: [unclosed\n"},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("broken", r.stderr)
            self.assertIn("unreadable", r.stderr)

    def test_all_findings_are_reported_in_one_run(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), {
                "one": {"tier": "core", "license": "license: OASIS"},
                "two": {"tier": "core", "license": "license: MIT-style"},
                "three": {"tier": "core", "license": "license: Public Domain"},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("3 recipe(s)", r.stderr)
            for name in ("one", "two", "three"):
                self.assertIn(name, r.stderr)

    # ---- GREEN: valid expressions ----------------------------------------

    def test_valid_expressions_are_green(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), {
                "plain": {"tier": "core", "license": "license: MIT"},
                "conjunction": {"tier": "core",
                                "license": "license: MIT AND GPL-2.0-only"},
                "parenthesised": {
                    "tier": "core",
                    "license": "license: (Apache-2.0 OR MIT) AND BSD-3-Clause"},
                "with-exception": {
                    "tier": "core",
                    "license": "license: Apache-2.0 WITH LLVM-exception"},
                "licenseref": {"tier": "core",
                               "license": "license: LicenseRef-Public-Domain"},
                "documentref": {
                    "tier": "core",
                    "license": "license: DocumentRef-spdx-tool:LicenseRef-Vendor"},
                "orlater-plus": {"tier": "core", "license": "license: GPL-2.0+"},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("PASS", r.stdout)

    # ---- WARN: deprecated identifiers pass, and are still reported -------

    def test_deprecated_identifier_passes_and_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), {
                "liburing": {"tier": "core",
                             "license": "license: MIT AND LGPL-2.1 AND GPL-2.0"},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("DEPRECATED", r.stdout)
            self.assertIn("liburing", r.stdout)
            self.assertIn("GPL-2.0", r.stdout)

    def test_quiet_suppresses_the_warning_body_but_keeps_the_count(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), {
                "liburing": {"tier": "core", "license": "license: GPL-2.0"},
            })
            r = _run(repo, "--quiet")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("deprecated:", r.stdout)
            self.assertIn("deprecated-identifier warning", r.stdout)

    # ---- scope -----------------------------------------------------------

    def test_shipped_only_skips_a_mirror_only_offender(self):
        with tempfile.TemporaryDirectory() as td:
            # tier: extra defaults to mirror-only in the parser's resolution.
            repo = _make_repo(Path(td), {
                "mirror-thing": {"tier": "extra", "license": "license: OASIS"},
                "shipped-thing": {"tier": "core", "license": "license: MIT"},
            })
            self.assertEqual(_run(repo).returncode, 1)
            r = _run(repo, "--shipped-only")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("shipped packages only", r.stdout)

    # ---- setup errors are exit 2, never a quiet pass ---------------------

    def test_missing_identifier_data_file_is_a_setup_error(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), {
                "plain": {"tier": "core", "license": "license: MIT"},
            }, config=None)
            r = _run(repo)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("SETUP ERROR", r.stderr)

    def test_empty_identifier_data_file_is_a_setup_error(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fake_config = tmp / "fake-config"
            fake_config.mkdir()
            (fake_config / "spdx-license-list.json").write_text(json.dumps({
                "upstream": {"license_list_version": "0"},
                "licenses": [], "exceptions": [], "deprecated_licenses": [],
            }))
            repo = _make_repo(tmp, {
                "plain": {"tier": "core", "license": "license: MIT"},
            }, config=fake_config)
            r = _run(repo)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("SETUP ERROR", r.stderr)

    def test_empty_package_tree_is_refused_not_passed(self):
        # A gate certifies only what it positively scanned. Exit 0 here would
        # claim a clean corpus while having read nothing.
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), {})
            r = _run(repo)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("never read", r.stderr)

    def test_scope_that_filters_everything_out_is_refused(self):
        # --shipped-only against a tree with no shipped package checks nothing.
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), {
                "mirror-thing": {"tier": "extra", "license": "license: MIT"},
            })
            r = _run(repo, "--shipped-only")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("nothing to certify", r.stderr)

    # ---- the run always states which list it judged against --------------

    def test_run_states_the_licence_list_version(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), {
                "plain": {"tier": "core", "license": "license: MIT"},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("SPDX licence list", r.stdout)


class TestShippedTreeIsClean(unittest.TestCase):
    """The real tree must pass, or the gate has nothing to protect."""

    def test_repository_passes_the_gate(self):
        r = _run(REPO_ROOT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS", r.stdout)


if __name__ == "__main__":
    unittest.main()
