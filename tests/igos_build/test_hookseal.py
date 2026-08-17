#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""igos-build/hookseal.py — sealing recipe lifecycle functions into archives.

A recipe's post_install() ran at build time, in the build chroot, and a target
installed from archives never received it. pkm has always been able to fire
.scripts/<event>.sh out of an extracted archive; nothing was putting those
scripts in. This is that seam, and these are the properties that make it
trustworthy rather than merely present.

Extraction is TEXTUAL — the module never executes a recipe. That is not a
stylistic choice: the builder runs post_install AFTER the archive is sealed, so
observing what the hook did would observe build-chroot side effects belonging to
a filesystem that is not the target's.

Extraction is FAIL-CLOSED. A function that opens and never closes, or whose
extracted body does not parse, raises rather than sealing what was found so far.
A truncated hook is worse than an absent one because it reports success.

And the seal must not become payload: `.scripts/` is archive metadata in exactly
the way `.PKGINFO` is, so a sealed package must not deploy `/.scripts/` onto the
target root. That regression would be this seam creating the unowned-file class
it exists to close, which is why it is pinned here and again in the installer
tests.
"""
import importlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(REPO_ROOT), str(REPO_ROOT / "igos-build")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

hookseal = importlib.import_module("igos-build.hookseal")
_hooks = importlib.import_module("pkm.hooks")


class ExtractFunctionTest(unittest.TestCase):
    def test_extracts_a_simple_body(self):
        text = ("configure() {\n    :\n}\n\n"
                "post_install() {\n    set -e\n    ldconfig\n}\n")
        self.assertEqual(hookseal.extract_function(text, "post_install"),
                         "    set -e\n    ldconfig")

    def test_absent_function_is_none_not_an_error(self):
        """~99% of recipes declare none; that must be a silent no-op."""
        self.assertIsNone(
            hookseal.extract_function("build() {\n    make\n}\n", "post_install"))

    def test_picks_the_right_function_among_several(self):
        text = ("post_install() {\n    echo POST\n}\n"
                "pre_remove() {\n    echo PRE_REMOVE\n}\n")
        self.assertIn("PRE_REMOVE",
                      hookseal.extract_function(text, "pre_remove"))
        self.assertNotIn("PRE_REMOVE",
                         hookseal.extract_function(text, "post_install"))

    def test_nested_braces_survive(self):
        """Indented closers are body, not the end of the function."""
        text = ("post_install() {\n"
                "    if [ -x /usr/bin/x ]; then\n"
                "        /usr/bin/x\n"
                "    fi\n"
                "    for f in a b; do\n"
                "        echo ${f}\n"
                "    done\n"
                "}\n")
        body = hookseal.extract_function(text, "post_install")
        self.assertIn("done", body)
        self.assertIn("fi", body)

    def test_heredoc_body_survives(self):
        """The python3 <<'PY' … PY shape several recipes use."""
        text = ("post_install() {\n"
                "    set -e\n"
                "    python3 - <<'PY'\n"
                "import sys\n"
                "print('ok')\n"
                "PY\n"
                "}\n")
        body = hookseal.extract_function(text, "post_install")
        self.assertIn("import sys", body)
        self.assertIn("PY", body)

    def test_unterminated_function_raises(self):
        """Fail closed: a truncated hook reports success and does half a job."""
        text = "post_install() {\n    set -e\n    ldconfig\n"
        with self.assertRaises(hookseal.SealError):
            hookseal.extract_function(text, "post_install")

    def test_a_wrong_closer_is_caught_by_the_parse_check(self):
        """A column-0 '}' inside a heredoc would cut the body short."""
        text = ("post_install() {\n"
                "    cat > /tmp/x <<'EOF'\n"
                "}\n"                     # looks like the closer, is not
                "    still_in_the_body\n"
                "EOF\n"
                "}\n")
        with self.assertRaises(hookseal.SealError):
            hookseal.extract_function(text, "post_install")

    def test_unknown_event_is_rejected(self):
        with self.assertRaises(ValueError):
            hookseal.extract_function("x() {\n:\n}\n", "post_frobnicate")

    def test_event_list_matches_pkm(self):
        """The seal must not write a script pkm will never fire."""
        self.assertEqual(tuple(hookseal.LIFECYCLE_EVENTS),
                         tuple(_hooks.LIFECYCLE_EVENTS))


class SealIntoStagingTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.staging = self.tmp / "staging"
        self.staging.mkdir()
        self.build_sh = self.tmp / "build.sh"

    def tearDown(self):
        self._td.cleanup()

    def test_seals_and_the_script_is_executable_and_parses(self):
        self.build_sh.write_text(
            "build() {\n    make\n}\n"
            "post_install() {\n    set -e\n    ldconfig\n}\n")
        sealed = hookseal.seal_into_staging(self.staging, self.build_sh,
                                            "demo", "1.0")
        self.assertEqual(sealed, ["post_install"])
        script = self.staging / ".scripts" / "post_install.sh"
        self.assertTrue(script.is_file())
        self.assertTrue(script.stat().st_mode & 0o111, "hook must be executable")
        text = script.read_text()
        self.assertTrue(text.startswith("#!/bin/bash"))
        self.assertIn("ldconfig", text)
        self.assertIn("demo-1.0", text, "the seal states its provenance")
        r = subprocess.run(["bash", "-n", str(script)], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_seals_every_declared_event(self):
        self.build_sh.write_text(
            "pre_install() {\n    echo A\n}\n"
            "post_install() {\n    echo B\n}\n"
            "post_remove() {\n    echo C\n}\n")
        sealed = hookseal.seal_into_staging(self.staging, self.build_sh,
                                            "demo", "1.0")
        self.assertEqual(sorted(sealed),
                         ["post_install", "post_remove", "pre_install"])

    def test_no_lifecycle_functions_writes_nothing(self):
        self.build_sh.write_text("build() {\n    make\n}\n")
        self.assertEqual(
            hookseal.seal_into_staging(self.staging, self.build_sh, "d", "1"), [])
        self.assertFalse((self.staging / ".scripts").exists(),
                         "a package with no hooks must not gain an empty dir")

    def test_a_hand_written_script_is_not_overwritten(self):
        """A recipe that hand-rolled its own hook said something more specific."""
        target = self.staging / ".scripts" / "post_install.sh"
        target.parent.mkdir(parents=True)
        target.write_text("#!/bin/bash\n# bespoke\nexit 0\n")
        self.build_sh.write_text("post_install() {\n    generic\n}\n")
        sealed = hookseal.seal_into_staging(self.staging, self.build_sh,
                                            "demo", "1.0")
        self.assertEqual(sealed, ["post_install"])
        self.assertIn("bespoke", target.read_text(),
                      "the seam must not silently rewrite a package's behaviour")

    def test_missing_build_sh_is_a_no_op(self):
        self.assertEqual(
            hookseal.seal_into_staging(self.staging, self.tmp / "nope",
                                       "d", "1"), [])

    def test_refusal_propagates(self):
        self.build_sh.write_text("post_install() {\n    set -e\n")
        with self.assertRaises(hookseal.SealError):
            hookseal.seal_into_staging(self.staging, self.build_sh, "d", "1")


class SealRoundTripTest(unittest.TestCase):
    """recipe function -> sealed script -> pkm fires it -> it did the work.

    This is the end-to-end the whole seam exists for: the executed script is
    the one the seal produced, and what the recipe's function said to do
    actually happens on a root that is not the build chroot.
    """

    def test_sealed_hook_runs_under_pkm_and_takes_effect(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            staging = tmp / "staging"
            staging.mkdir()
            root = tmp / "root"
            (root / "var" / "lib").mkdir(parents=True)
            marker = root / "var" / "lib" / "demo.state"

            build_sh = tmp / "build.sh"
            build_sh.write_text(
                "post_install() {\n"
                "    set -e\n"
                f"    printf 'generated' > '{marker}'\n"
                "}\n")

            self.assertEqual(
                hookseal.seal_into_staging(staging, build_sh, "demo", "1.0"),
                ["post_install"])

            result = _hooks.run_archive_lifecycle_hook(
                staging, "post_install", "demo", "1.0", str(root))

            self.assertEqual(result.critical_failures, [], result.messages)
            self.assertTrue(marker.is_file(),
                            "pkm fired the sealed hook but its work did not land")
            self.assertEqual(marker.read_text(), "generated")

    def test_a_failing_sealed_hook_is_reported_critical(self):
        """The seam must not swallow a hook failure into a silent success."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            staging = tmp / "staging"
            staging.mkdir()
            build_sh = tmp / "build.sh"
            build_sh.write_text("post_install() {\n    set -e\n    exit 7\n}\n")
            hookseal.seal_into_staging(staging, build_sh, "demo", "1.0")

            result = _hooks.run_archive_lifecycle_hook(
                staging, "post_install", "demo", "1.0", str(tmp))

            self.assertTrue(result.critical_failures,
                            "a non-zero hook must surface, not pass quietly")


if __name__ == "__main__":
    unittest.main()
