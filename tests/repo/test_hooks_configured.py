"""The repo's git gates must actually be armed in the working clone.

A clone (or a working copy restored from a fresh install) starts with core.hooksPath
unset, so every in-repo gate under .githooks/ is silently NOT running — and
nothing announces that. A hook cannot announce its own absence, so the test
suite is the announce-point: it runs on every review cycle, turning a
gate-silent clone into a loud failure instead of a silent one (decided
2026-07-24, after a working clone was found pushing with no gates armed).

Fix on failure:  git config core.hooksPath .githooks
"""

import subprocess
import unittest
from pathlib import Path


def _repo_root():
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return Path(out.stdout.strip())


class TestHooksConfigured(unittest.TestCase):
    def test_hooks_path_points_at_githooks(self):
        out = subprocess.run(
            ["git", "config", "core.hooksPath"],
            capture_output=True, text=True,
        )
        configured = out.stdout.strip()
        self.assertEqual(
            configured, ".githooks",
            "core.hooksPath is %r — the in-repo gates are NOT running in "
            "this clone. Arm them: git config core.hooksPath .githooks"
            % (configured or "<unset>"),
        )

    def test_gate_scripts_present_and_executable(self):
        hooks_dir = _repo_root() / ".githooks"
        for name in ("pre-push", "pre-commit"):
            hook = hooks_dir / name
            self.assertTrue(hook.is_file(), f"{hook} missing")
            self.assertTrue(
                hook.stat().st_mode & 0o111,
                f"{hook} is not executable — gates configured but inert",
            )


if __name__ == "__main__":
    unittest.main()
