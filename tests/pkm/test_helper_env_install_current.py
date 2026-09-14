#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The claude-code helper's install-mode option reaches the helper through
the package manager, and only as "0" or "1".

The helper (packages/extra/claude-code) reads CLAUDE_CODE_INSTALL_CURRENT to
choose between its reviewed pin and the registry's current release. The
package manager rebuilds every helper's environment from the exact
HELPER_ENV_ALLOWLIST, so without an entry the documented invocation

    sudo CLAUDE_CODE_INSTALL_CURRENT=1 pkm install claude-code

silently installed the pin. The variable is inert (a one-character mode
flag, not a path, URL or module search path), so it joins the allowlist —
and the package manager refuses any value other than "0" or "1" BEFORE a
helper runs, so a helper never sees a value it would have to reject itself.

Every test here failed on the tree before the change (the variable was
stripped, and no value was refused).
"""

import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pkm.hooks import HOOK_ENV_ALLOWLIST  # noqa: E402
from pkm.installer import (  # noqa: E402
    CLAUDE_CODE_INSTALL_CURRENT_VAR,
    HELPER_ENV_ALLOWLIST,
    PackageInstaller,
)


def _dumping_helper(directory, name):
    """A helper that records its environment and a run marker, then exits 0."""
    helper = Path(directory) / name
    env_dump = Path(directory) / f"{name}.env"
    marker = Path(directory) / f"{name}.ran"
    helper.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        env > {env_dump}
        : > {marker}
        exit 0
        """))
    helper.chmod(0o755)
    return helper, env_dump, marker


class AllowlistCarriesTheModeVariableTest(unittest.TestCase):

    def test_variable_is_allowlisted_for_helpers_only(self):
        self.assertEqual(CLAUDE_CODE_INSTALL_CURRENT_VAR,
                         "CLAUDE_CODE_INSTALL_CURRENT")
        self.assertIn(CLAUDE_CODE_INSTALL_CURRENT_VAR, HELPER_ENV_ALLOWLIST)
        # Lifecycle hooks do not consume it; the hook allowlist stays minimal.
        self.assertNotIn(CLAUDE_CODE_INSTALL_CURRENT_VAR, HOOK_ENV_ALLOWLIST)


class ModeVariablePassesThroughTest(unittest.TestCase):
    """The EULA-helper path and the download-helper path build their
    environment from the same allowlist; both must pass "0" and "1"."""

    def _run_eula_with(self, value):
        installer = PackageInstaller(db=MagicMock())
        with tempfile.TemporaryDirectory() as tmp:
            _, env_dump, marker = _dumping_helper(tmp, "test-eula")
            with patch.dict(os.environ, {
                CLAUDE_CODE_INSTALL_CURRENT_VAR: value,
                "LD_PRELOAD": "/tmp/evil.so",
                "PATH": "/usr/bin:/bin",
            }):
                with patch("pkm.installer.EULA_HELPER_DIR", Path(tmp)):
                    ok, msg = installer._run_eula_helper("claude-code",
                                                         "test-eula")
            self.assertTrue(ok, msg)
            self.assertTrue(marker.exists())
            return env_dump.read_text()

    def test_one_reaches_the_eula_helper(self):
        inherited = self._run_eula_with("1")
        self.assertIn(f"{CLAUDE_CODE_INSTALL_CURRENT_VAR}=1\n", inherited)
        self.assertNotIn("LD_PRELOAD", inherited)

    def test_zero_reaches_the_eula_helper(self):
        inherited = self._run_eula_with("0")
        self.assertIn(f"{CLAUDE_CODE_INSTALL_CURRENT_VAR}=0\n", inherited)

    def test_one_reaches_the_download_helper(self):
        installer = PackageInstaller(db=MagicMock())
        with tempfile.TemporaryDirectory() as tmp:
            helper, env_dump, marker = _dumping_helper(
                tmp, "igos-install-claude-code")
            with patch.dict(os.environ, {
                CLAUDE_CODE_INSTALL_CURRENT_VAR: "1",
                "HTTP_PROXY": "http://attacker.invalid",
                "PATH": "/usr/bin:/bin",
            }), patch("pkm.installer._read_helper_manifest",
                      return_value=(None, "no manifest (test)")):
                ok, msg, declined = installer._run_helper("claude-code",
                                                          helper)
            self.assertTrue(marker.exists(), "the helper did not run")
            inherited = env_dump.read_text()
            self.assertIn(f"{CLAUDE_CODE_INSTALL_CURRENT_VAR}=1\n", inherited)
            self.assertIn("PKM_HELPER_INVOCATION=1\n", inherited)
            self.assertNotIn("HTTP_PROXY", inherited)


class InvalidModeValueIsRefusedBeforeTheHelperRunsTest(unittest.TestCase):

    def test_download_helper_never_starts_on_an_invalid_value(self):
        installer = PackageInstaller(db=MagicMock())
        with tempfile.TemporaryDirectory() as tmp:
            helper, _, marker = _dumping_helper(tmp, "igos-install-claude-code")
            with patch.dict(os.environ, {CLAUDE_CODE_INSTALL_CURRENT_VAR: "yes",
                                         "PATH": "/usr/bin:/bin"}):
                ok, msg, declined = installer._run_helper("claude-code",
                                                          helper)
            self.assertFalse(ok)
            self.assertFalse(declined)
            self.assertFalse(marker.exists(),
                             "the helper ran despite the refused value")
            self.assertIn(CLAUDE_CODE_INSTALL_CURRENT_VAR, msg)
            self.assertIn("yes", msg)
            self.assertIn('"0"', msg)
            self.assertIn('"1"', msg)

    def test_eula_helper_never_starts_on_an_invalid_value(self):
        installer = PackageInstaller(db=MagicMock())
        with tempfile.TemporaryDirectory() as tmp:
            _, _, marker = _dumping_helper(tmp, "test-eula")
            with patch.dict(os.environ, {CLAUDE_CODE_INSTALL_CURRENT_VAR: "",
                                         "PATH": "/usr/bin:/bin"}):
                with patch("pkm.installer.EULA_HELPER_DIR", Path(tmp)):
                    ok, msg = installer._run_eula_helper("claude-code",
                                                         "test-eula")
            self.assertFalse(ok)
            self.assertFalse(marker.exists())
            self.assertIn(CLAUDE_CODE_INSTALL_CURRENT_VAR, msg)

    def test_post_install_hook_is_skipped_loudly_on_an_invalid_value(self):
        """The per-package post-install hook runs after the deploy committed
        and is documented as a non-fatal side channel; an invalid mode value
        is reported and the hook is not run (never run with a value the
        helper contract refuses)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hook_dir = root / "var" / "lib" / "pkm" / "hooks" / "vim"
            hook_dir.mkdir(parents=True)
            hook, _, marker = _dumping_helper(hook_dir, "post-install")
            installer = PackageInstaller(db=MagicMock(), root=str(root))
            with patch.dict(os.environ, {CLAUDE_CODE_INSTALL_CURRENT_VAR: "2",
                                         "PATH": "/usr/bin:/bin"}):
                with patch("pkm.installer.sys.stderr") as err:
                    installer._run_post_install_hook("vim", "9.1")
            self.assertFalse(marker.exists(), "the hook ran")
            written = "".join(str(c) for c in err.write.call_args_list)
            self.assertIn(CLAUDE_CODE_INSTALL_CURRENT_VAR, written)


if __name__ == "__main__":
    unittest.main()
