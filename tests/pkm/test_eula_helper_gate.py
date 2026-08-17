#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the EULA install-helper pre-install gate.

Covers the hybrid-model wiring landed 2026-05-28:
  * `eula_helper` field flows package.yml -> parser -> tracker ->
    .PKGINFO -> pkm._parse_pkginfo -> pkm.installer pre-install gate.
  * `_find_eula_helper` resolves a name to /usr/lib/intergen/eula-helpers/<name>.
  * `_run_eula_helper` maps every documented exit code (0/1/2/3/4)
    to the right (ok_bool, message) tuple.
  * The pre-install gate skips when eula_helper is absent (no
    regression for the 700-ish packages that have no EULA).
  * Subprocess env is stripped to HELPER_ENV_ALLOWLIST (H-024).
"""

import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pkm.installer import (
    EULA_HELPER_DIR,
    HELPER_ENV_ALLOWLIST,
    PackageInstaller,
)
from pkm.repo import _parse_pkginfo


class ParsePkginfoEulaHelperTest(unittest.TestCase):
    """`_parse_pkginfo` surfaces eula_helper key into the meta dict."""

    def test_parses_eula_helper(self):
        meta = _parse_pkginfo(
            "pkgname=nvidia\npkgver=580.95.05\neula_helper=nvidia-eula\n"
        )
        self.assertEqual(meta["eula_helper"], "nvidia-eula")

    def test_absent_eula_helper_yields_no_key(self):
        meta = _parse_pkginfo("pkgname=bash\npkgver=5.2.37\n")
        self.assertNotIn("eula_helper", meta)


class FindEulaHelperTest(unittest.TestCase):
    """`_find_eula_helper` requires file + executable bit."""

    def test_returns_none_when_helper_missing(self):
        installer = PackageInstaller(db=MagicMock())
        with patch("pkm.installer.EULA_HELPER_DIR",
                   Path("/nonexistent/eula-helpers")):
            self.assertIsNone(installer._find_eula_helper("nvidia-eula"))

    def test_returns_none_when_not_executable(self):
        installer = PackageInstaller(db=MagicMock())
        with tempfile.TemporaryDirectory() as tmp:
            helper_dir = Path(tmp)
            helper = helper_dir / "nvidia-eula"
            helper.write_text("#!/bin/sh\nexit 0\n")
            helper.chmod(0o644)  # not executable
            with patch("pkm.installer.EULA_HELPER_DIR", helper_dir):
                self.assertIsNone(installer._find_eula_helper("nvidia-eula"))

    def test_returns_path_when_present_and_executable(self):
        installer = PackageInstaller(db=MagicMock())
        with tempfile.TemporaryDirectory() as tmp:
            helper_dir = Path(tmp)
            helper = helper_dir / "nvidia-eula"
            helper.write_text("#!/bin/sh\nexit 0\n")
            helper.chmod(0o755)
            with patch("pkm.installer.EULA_HELPER_DIR", helper_dir):
                resolved = installer._find_eula_helper("nvidia-eula")
                self.assertEqual(resolved, helper)


class RunEulaHelperExitCodesTest(unittest.TestCase):
    """`_run_eula_helper` maps every documented helper exit code."""

    def _make_helper(self, tmpdir, exit_code):
        helper_dir = Path(tmpdir)
        helper = helper_dir / "test-eula"
        helper.write_text(f"#!/bin/sh\nexit {exit_code}\n")
        helper.chmod(0o755)
        return helper_dir, helper

    def test_exit_0_returns_ok(self):
        installer = PackageInstaller(db=MagicMock())
        with tempfile.TemporaryDirectory() as tmp:
            helper_dir, _ = self._make_helper(tmp, 0)
            with patch("pkm.installer.EULA_HELPER_DIR", helper_dir):
                ok, msg = installer._run_eula_helper("nvidia", "test-eula")
                self.assertTrue(ok)
                self.assertEqual(msg, "")

    def test_exit_1_decline_returns_clear_message(self):
        installer = PackageInstaller(db=MagicMock())
        with tempfile.TemporaryDirectory() as tmp:
            helper_dir, _ = self._make_helper(tmp, 1)
            with patch("pkm.installer.EULA_HELPER_DIR", helper_dir):
                ok, msg = installer._run_eula_helper("nvidia", "test-eula")
                self.assertFalse(ok)
                self.assertIn("declined", msg.lower())
                self.assertIn("nvidia", msg)

    def test_exit_2_eula_text_unavailable(self):
        # PI-Z15: rc=2 means the BUNDLED EULA text could not be read
        # (missing/empty sidecar — corrupted install media), not a
        # network-fetch failure; the message must say so and must not
        # misdirect the user toward their network.
        installer = PackageInstaller(db=MagicMock())
        with tempfile.TemporaryDirectory() as tmp:
            helper_dir, _ = self._make_helper(tmp, 2)
            with patch("pkm.installer.EULA_HELPER_DIR", helper_dir):
                ok, msg = installer._run_eula_helper("nvidia", "test-eula")
                self.assertFalse(ok)
                self.assertIn("bundled", msg.lower())
                self.assertNotIn("network", msg.lower())

    def test_exit_3_filesystem_error(self):
        installer = PackageInstaller(db=MagicMock())
        with tempfile.TemporaryDirectory() as tmp:
            helper_dir, _ = self._make_helper(tmp, 3)
            with patch("pkm.installer.EULA_HELPER_DIR", helper_dir):
                ok, msg = installer._run_eula_helper("nvidia", "test-eula")
                self.assertFalse(ok)
                self.assertIn("marker", msg.lower())

    def test_exit_4_non_tty(self):
        installer = PackageInstaller(db=MagicMock())
        with tempfile.TemporaryDirectory() as tmp:
            helper_dir, _ = self._make_helper(tmp, 4)
            with patch("pkm.installer.EULA_HELPER_DIR", helper_dir):
                ok, msg = installer._run_eula_helper("nvidia", "test-eula")
                self.assertFalse(ok)
                self.assertIn("TTY", msg)

    def test_unexpected_exit_code(self):
        installer = PackageInstaller(db=MagicMock())
        with tempfile.TemporaryDirectory() as tmp:
            helper_dir, _ = self._make_helper(tmp, 77)
            with patch("pkm.installer.EULA_HELPER_DIR", helper_dir):
                ok, msg = installer._run_eula_helper("nvidia", "test-eula")
                self.assertFalse(ok)
                self.assertIn("77", msg)
                self.assertIn("unexpected", msg.lower())

    def test_helper_missing_returns_clear_message(self):
        installer = PackageInstaller(db=MagicMock())
        with patch("pkm.installer.EULA_HELPER_DIR",
                   Path("/nonexistent/eula-helpers")):
            ok, msg = installer._run_eula_helper("nvidia", "nvidia-eula")
            self.assertFalse(ok)
            self.assertIn("nvidia", msg)
            self.assertIn("nvidia-eula", msg)


class HelperEnvAllowlistTest(unittest.TestCase):
    """H-024: subprocess env is stripped to HELPER_ENV_ALLOWLIST."""

    def test_env_is_stripped_to_allowlist(self):
        installer = PackageInstaller(db=MagicMock())
        with tempfile.TemporaryDirectory() as tmp:
            helper_dir = Path(tmp)
            helper = helper_dir / "test-eula"
            # Helper writes its env to a sidecar file so the test can
            # inspect what was inherited.
            env_dump = helper_dir / "env.dump"
            helper.write_text(textwrap.dedent(f"""\
                #!/bin/sh
                env > {env_dump}
                exit 0
                """))
            helper.chmod(0o755)

            # Set a forbidden env var; verify it does NOT reach the
            # subprocess.
            with patch.dict(os.environ, {
                "LD_PRELOAD": "/tmp/evil.so",
                "PATH": "/usr/bin:/bin",  # allowed
                "HTTP_PROXY": "http://attacker.invalid",
                "PYTHONPATH": "/tmp/evil",
            }):
                with patch("pkm.installer.EULA_HELPER_DIR", helper_dir):
                    ok, _ = installer._run_eula_helper("nvidia", "test-eula")
                    self.assertTrue(ok)

            inherited = env_dump.read_text()
            # Forbidden vars MUST NOT be inherited.
            self.assertNotIn("LD_PRELOAD", inherited)
            self.assertNotIn("HTTP_PROXY", inherited)
            self.assertNotIn("PYTHONPATH", inherited)
            # Pkm-set vars MUST be present.
            self.assertIn("PKM_PACKAGE_NAME=nvidia", inherited)
            self.assertIn("PKM_EULA_HELPER_NAME=test-eula", inherited)

    def test_sudo_user_passes_while_injection_vars_dropped(self):
        """H-024: SUDO_USER (inert identity metadata) MUST reach the helper so
        it can drop from root to the invoking user for per-user installs (the
        claude-code VS Code extension), while LD_PRELOAD-class injection vars
        stay stripped. RED without SUDO_USER in HELPER_ENV_ALLOWLIST; GREEN with
        it. SUDO_UID is deliberately NOT allowlisted (no consumer today)."""
        installer = PackageInstaller(db=MagicMock())
        with tempfile.TemporaryDirectory() as tmp:
            helper_dir = Path(tmp)
            helper = helper_dir / "test-eula"
            env_dump = helper_dir / "env.dump"
            helper.write_text(textwrap.dedent(f"""\
                #!/bin/sh
                env > {env_dump}
                exit 0
                """))
            helper.chmod(0o755)

            with patch.dict(os.environ, {
                "SUDO_USER": "christopher",      # identity metadata — MUST pass
                "SUDO_UID": "1000",              # no consumer — MUST drop
                "LD_PRELOAD": "/tmp/evil.so",    # injection — MUST drop
                "LD_LIBRARY_PATH": "/tmp/evil",  # injection — MUST drop
                "PATH": "/usr/bin:/bin",         # allowed
            }):
                with patch("pkm.installer.EULA_HELPER_DIR", helper_dir):
                    ok, _ = installer._run_eula_helper("nvidia", "test-eula")
                    self.assertTrue(ok)

            inherited = env_dump.read_text()
            # SUDO_USER passes through (the WI1 fix).
            self.assertIn("SUDO_USER=christopher", inherited)
            # Injection-class + non-allowlisted identity vars stay dropped.
            self.assertNotIn("LD_PRELOAD", inherited)
            self.assertNotIn("LD_LIBRARY_PATH", inherited)
            self.assertNotIn("SUDO_UID", inherited)


class InstallGateIntegrationTest(unittest.TestCase):
    """End-to-end: PackageInstaller.install skips gate when eula_helper
    is absent + invokes gate when present.

    We mock the underlying install machinery (extract / db / deploy /
    hooks) and verify only that the EULA gate fires at the right time
    and aborts the install on decline.
    """

    def test_install_aborts_when_helper_declines(self):
        """When .PKGINFO has eula_helper=X and the helper exits 1
        (decline), install() returns (False, decline-message) WITHOUT
        any deploy / DB / hook activity."""
        db = MagicMock()
        db.get_installed.return_value = None  # not already installed
        installer = PackageInstaller(db=db)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Fabricate an archive that has a .PKGINFO with eula_helper.
            archive = tmp_path / "nvidia-580.95.05.igos.tar.gz"
            archive.touch()  # existence check passes
            helper_dir = tmp_path / "eula-helpers"
            helper_dir.mkdir()
            helper = helper_dir / "nvidia-eula"
            helper.write_text("#!/bin/sh\nexit 1\n")  # decline
            helper.chmod(0o755)

            # Stub the .PKGINFO read so we don't need a real tarball.
            with patch("pkm.installer._read_package_meta",
                       return_value={"eula_helper": "nvidia-eula"}), \
                 patch("pkm.installer.EULA_HELPER_DIR", helper_dir):
                ok, msg = installer.install("nvidia", archive_path=archive)
                self.assertFalse(ok)
                self.assertIn("declined", msg.lower())
                self.assertIn("nvidia", msg)

    def test_install_skips_gate_when_no_eula_helper(self):
        """The 700-ish packages with no EULA declaration should
        proceed straight to staging extract without touching the
        EULA gate."""
        db = MagicMock()
        db.get_installed.return_value = None
        installer = PackageInstaller(db=db)

        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "bash-5.2.37.igos.tar.gz"
            archive.touch()

            # Stub _read_package_meta to return no eula_helper. Stub
            # the extract / DB / etc. so install() returns early at a
            # synthetic mid-flow point. We just want to confirm the
            # EULA gate did NOT fire by patching _run_eula_helper as a
            # mock and asserting not_called.
            with patch("pkm.installer._read_package_meta",
                       return_value={"depends": []}), \
                 patch.object(installer, "_run_eula_helper") as eula_mock, \
                 patch("pkm.installer._safe_extract_tar",
                       return_value=(False, "stub failure")):
                ok, _ = installer.install("bash", archive_path=archive)
                # The install will fail on the synthetic extract stub —
                # but the EULA gate must NOT have fired.
                self.assertFalse(ok)
                eula_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
