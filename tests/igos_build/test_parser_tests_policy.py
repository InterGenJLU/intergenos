"""Unit tests for the `tests:` policy block parsing in igos-build/parser.py.

The yml-lane counterpart of pkg_run_tests (scripts/pkg-functions.sh,
docs/test-allow-list.md): strict default, enabled=false skip, and
failure_policy=known_failures — with reason REQUIRED fail-closed on both
waiver forms (an unreasoned waiver refuses the template). Added with the
GE-01 launch-7 lib32-flac halt fix (the yml lane previously had no policy
layer at all, so a pure-yml package could not express the Rule-10 governed
environmental waiver its custom-style sibling routes through pkg_run_tests).
"""

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

from parser import (  # noqa: E402  (path manipulation must precede import)
    TemplateError,
    parse_template,
)


MINIMAL_YAML_TEMPLATE = """\
name: {name}
version: "1.0"
release: 1
description: "test package"
license: "MIT"
build_style: make
source:
  - url: "https://example.com/{name}-1.0.tar.gz"
    sha256: "0000000000000000000000000000000000000000000000000000000000000000"
{extra}
"""


def _write_template(tmp_dir: Path, name: str, extra_yaml: str = "") -> Path:
    path = tmp_dir / "package.yml"
    path.write_text(MINIMAL_YAML_TEMPLATE.format(name=name, extra=extra_yaml))
    return path


class TestsPolicyParsing(unittest.TestCase):

    def _parse(self, extra_yaml: str):
        with tempfile.TemporaryDirectory() as td:
            return parse_template(_write_template(Path(td), "tpol", extra_yaml))

    def test_no_tests_block_defaults_strict(self):
        pkg = self._parse("")
        self.assertTrue(pkg.tests_enabled)
        self.assertEqual(pkg.tests_failure_policy, "strict")
        self.assertIsNone(pkg.tests_reason)

    def test_known_failures_with_reason_parses(self):
        pkg = self._parse(
            "tests:\n"
            "  enabled: true\n"
            "  failure_policy: known_failures\n"
            "  reason: \"environmental root CAP_DAC_OVERRIDE bypass\"\n"
        )
        self.assertTrue(pkg.tests_enabled)
        self.assertEqual(pkg.tests_failure_policy, "known_failures")
        self.assertIn("CAP_DAC_OVERRIDE", pkg.tests_reason)

    def test_known_failures_without_reason_refused(self):
        with self.assertRaises(TemplateError):
            self._parse("tests:\n  failure_policy: known_failures\n")

    def test_enabled_false_without_reason_refused(self):
        with self.assertRaises(TemplateError):
            self._parse("tests:\n  enabled: false\n")

    def test_enabled_false_with_reason_parses(self):
        pkg = self._parse(
            "tests:\n"
            "  enabled: false\n"
            "  reason: \"suite leaves loop mounts behind\"\n"
        )
        self.assertFalse(pkg.tests_enabled)

    def test_invalid_policy_refused(self):
        with self.assertRaises(TemplateError):
            self._parse(
                "tests:\n"
                "  failure_policy: ignore\n"
                "  reason: \"nope\"\n"
            )

    def test_non_mapping_block_refused(self):
        with self.assertRaises(TemplateError):
            self._parse("tests: false\n")

    def test_non_bool_enabled_refused(self):
        with self.assertRaises(TemplateError):
            self._parse(
                "tests:\n"
                "  enabled: \"yes please\"\n"
                "  reason: \"typed wrong\"\n"
            )

    def test_strict_with_reason_is_allowed(self):
        # A reason on a strict block is informational, not an error.
        pkg = self._parse(
            "tests:\n"
            "  failure_policy: strict\n"
            "  reason: \"documented for the audit sheet\"\n"
        )
        self.assertEqual(pkg.tests_failure_policy, "strict")

    def test_jobs_with_reason_parses(self):
        pkg = self._parse(
            "tests:\n"
            "  jobs: 1\n"
            "  reason: \"BLFS's own command is make -j1 check\"\n"
        )
        self.assertEqual(pkg.tests_jobs, 1)
        self.assertEqual(pkg.tests_failure_policy, "strict")

    def test_jobs_without_reason_refused(self):
        with self.assertRaises(TemplateError):
            self._parse("tests:\n  jobs: 1\n")

    def test_jobs_zero_refused(self):
        with self.assertRaises(TemplateError):
            self._parse("tests:\n  jobs: 0\n  reason: \"nope\"\n")

    def test_jobs_bool_refused(self):
        # YAML `true` is a bool, not an int — refuse, don't coerce.
        with self.assertRaises(TemplateError):
            self._parse("tests:\n  jobs: true\n  reason: \"typed wrong\"\n")

    def test_no_jobs_defaults_none(self):
        pkg = self._parse("")
        self.assertIsNone(pkg.tests_jobs)


class TestsJobsStyleEmission(unittest.TestCase):
    """The make-driven styles must emit -jN on check when tests_jobs is set.

    Styles import via importlib under the 'igos-build.styles.*' package
    names (the test_styles_lib32 convention — the plain-path import breaks
    their package-relative imports).
    """

    @classmethod
    def setUpClass(cls):
        import importlib
        sys.path.insert(0, str(REPO_ROOT))
        cls.autotools = importlib.import_module(
            "igos-build.styles.autotools").AutotoolsStyle()
        cls.make = importlib.import_module(
            "igos-build.styles.make").MakeStyle()

    def _pkg(self, extra_yaml: str):
        with tempfile.TemporaryDirectory() as td:
            return parse_template(_write_template(Path(td), "tjob", extra_yaml))

    def test_autotools_check_serialized(self):
        pkg = self._pkg(
            "tests:\n  jobs: 1\n  reason: \"book says -j1\"\n")
        cmds = self.autotools.check(pkg).commands
        self.assertTrue(any("make -j1 check" in c for c in cmds), cmds)

    def test_autotools_check_default_unbounded(self):
        pkg = self._pkg("")
        cmds = self.autotools.check(pkg).commands
        self.assertTrue(any("make check" in c for c in cmds), cmds)
        self.assertFalse(any("-j1" in c for c in cmds), cmds)

    def test_make_style_check_serialized(self):
        pkg = self._pkg(
            "tests:\n  jobs: 1\n  reason: \"book says -j1\"\n")
        cmds = self.make.check(pkg).commands
        self.assertTrue(any("make -j1 check" in c for c in cmds), cmds)


if __name__ == "__main__":
    unittest.main()
