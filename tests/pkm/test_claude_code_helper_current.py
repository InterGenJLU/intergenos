#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Claude Code helper version selection and installed-version checks.

The download helper normally installs a reviewed version.  A person may ask it
to resolve the registry's current stable release explicitly.  These tests run
the real planning path with a stand-in npm executable, so current-mode coverage
never uses the network.  They also drive the installed-version comparison that
must refuse a package whose executable reports a different version.
"""

import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
PACKAGE_DIR = REPO / "packages" / "extra" / "claude-code"
BUILD_SH = PACKAGE_DIR / "build.sh"
FUNCTIONS = PACKAGE_DIR / "claude-code-helper-functions.sh"
PACKAGE_YML = PACKAGE_DIR / "package.yml"

PINNED_VERSION = "2.1.270"
VSIX_VERSION = "2.1.270"
VSIX_SHA256 = "e33b1b88312302953857b0a09fa65dc13f72d58730e71107385a26a30ddd8640"


def _helper_body() -> str:
    source = BUILD_SH.read_text(encoding="utf-8")
    match = re.search(
        r"cat > \"\$\{DESTDIR\}/usr/bin/igos-install-claude-code\" "
        r"<< 'HELPEREOF'\n(?P<body>.*?)\nHELPEREOF",
        source,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError("build.sh does not contain the installer heredoc")
    return match.group("body") + "\n"


class _PlanHarness:
    def __init__(self, root: Path, latest: str = "9.8.7"):
        self.root = root
        self.bin_dir = root / "bin"
        self.bin_dir.mkdir()
        self.npm_log = root / "npm.log"
        npm = self.bin_dir / "npm"
        npm.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> {self.npm_log}\n"
            "if [ \"$1\" = view ] && "
            "[ \"$2\" = @anthropic-ai/claude-code ] && "
            "[ \"$3\" = dist-tags.latest ]; then\n"
            f"    printf '%s\\n' '{latest}'\n"
            "    exit 0\n"
            "fi\n"
            "printf '%s\\n' 'unexpected npm invocation' >&2\n"
            "exit 91\n",
            encoding="utf-8",
        )
        npm.chmod(npm.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        helper = _helper_body()
        helper = helper.replace(
            "source /usr/libexec/igos-install-claude-code-functions.sh",
            f"source {FUNCTIONS}",
        )
        self.helper = root / "igos-install-claude-code"
        self.helper.write_text(helper, encoding="utf-8")
        self.helper.chmod(0o755)

    def run(self, mode=None):
        env = dict(os.environ)
        env["PATH"] = f"{self.bin_dir}:/usr/bin:/bin"
        env.pop("CLAUDE_CODE_INSTALL_CURRENT", None)
        if mode is not None:
            env["CLAUDE_CODE_INSTALL_CURRENT"] = mode
        return subprocess.run(
            ["/bin/bash", str(self.helper), "--print-plan"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )


class VersionPlanTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_pinned_mode_is_the_default_and_does_not_query_the_registry(self):
        harness = _PlanHarness(self.root)
        result = harness.run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("mode=pinned", result.stdout)
        self.assertIn(f"cli_version={PINNED_VERSION}", result.stdout)
        self.assertIn(f"extension_version={VSIX_VERSION}", result.stdout)
        self.assertIn(f"extension_sha256={VSIX_SHA256}", result.stdout)
        self.assertIn("reviewed version", result.stdout)
        self.assertFalse(harness.npm_log.exists(), "pinned mode queried npm")

    def test_current_mode_uses_the_registry_answer_without_network_in_the_test(self):
        harness = _PlanHarness(self.root, latest="9.8.7")
        result = harness.run("1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("mode=current", result.stdout)
        self.assertIn("cli_version=9.8.7", result.stdout)
        self.assertIn("explicit current-registry request", result.stdout)
        self.assertEqual(
            harness.npm_log.read_text(encoding="utf-8").splitlines(),
            ["view @anthropic-ai/claude-code dist-tags.latest"],
        )

    def test_current_mode_refuses_a_non_version_registry_answer(self):
        harness = _PlanHarness(self.root, latest="latest")
        result = harness.run("1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid current release", result.stderr)

    def test_unrecognised_mode_value_is_not_silently_treated_as_pinned(self):
        harness = _PlanHarness(self.root)
        result = harness.run("yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be absent, 0, or 1", result.stderr)


class InstalledVersionTests(unittest.TestCase):
    def _run_check(self, requested: str, reported: str):
        script = (
            f"source {FUNCTIONS}; "
            'claude_code_verify_installed_version "$1" "$2"'
        )
        return subprocess.run(
            ["/bin/bash", "-c", script, "version-check", requested, reported],
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_matching_readback_returns_the_installed_version(self):
        result = self._run_check(PINNED_VERSION, f"{PINNED_VERSION} (Claude Code)")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), PINNED_VERSION)

    def test_readback_mismatch_is_a_refusal(self):
        result = self._run_check(PINNED_VERSION, "2.1.269 (Claude Code)")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("version mismatch", result.stderr)
        self.assertIn(f"requested {PINNED_VERSION}", result.stderr)
        self.assertIn("reported 2.1.269", result.stderr)

    def test_unparseable_readback_is_a_refusal(self):
        result = self._run_check(PINNED_VERSION, "Claude Code version unknown")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not parse", result.stderr)

    def test_multiline_readback_is_a_refusal(self):
        result = self._run_check(PINNED_VERSION, f"{PINNED_VERSION}\nunexpected")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("more than one line", result.stderr)


class RecipeContractTests(unittest.TestCase):
    def test_selected_version_drives_audit_install_and_manifest_recording(self):
        source = BUILD_SH.read_text(encoding="utf-8")
        functions = FUNCTIONS.read_text(encoding="utf-8")
        self.assertIn('"@anthropic-ai/claude-code": "${selected_version}"', functions)
        self.assertIn("claude_code_install_selected_cli", source)
        self.assertIn('npm audit signatures', functions)
        self.assertIn('npm install -g "@anthropic-ai/claude-code@${selected_version}"', functions)
        self.assertIn('igos_helper_set_version "${CLAUDE_VERSION}"', source)
        self.assertIn('"$CLAUDE_CODE_INSTALLED_BIN" --version', functions)

    def test_helper_function_source_is_packaged_and_verified(self):
        package = PACKAGE_YML.read_text(encoding="utf-8")
        relative = "packages/extra/claude-code/claude-code-helper-functions.sh"
        self.assertGreaterEqual(package.count(relative), 2)
        self.assertIn("/usr/libexec/igos-install-claude-code-functions.sh", package)


if __name__ == "__main__":
    unittest.main()
