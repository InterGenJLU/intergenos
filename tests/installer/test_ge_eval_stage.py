"""Wedge tests for the GE mirror-install eval stage (RT-11).

Drives the REAL ge-eval-stage.sh with stubbed pkm + smoke-test binaries —
the stage must be fail-closed at every step (sync, install, verify, strict
smoke) and green only when every step genuinely succeeds.
"""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STAGE = REPO_ROOT / "installer" / "smoke" / "ge-eval-stage.sh"


class TestGeEvalStage(unittest.TestCase):
    def _run(self, tmp, *, sync_rc=0, install_rc=0, verify_rc=0,
             smoke_rc=0):
        t = Path(tmp)
        bindir = t / "bin"
        bindir.mkdir()
        pkm = bindir / "pkm"
        pkm.write_text(f"""#!/bin/sh
case "$1" in
  sync) exit {sync_rc};;
  install) exit {install_rc};;
  verify) exit {verify_rc};;
  info) echo "Depends: lib32-glibc"; exit 0;;
esac
exit 0
""")
        pkm.chmod(pkm.stat().st_mode | stat.S_IEXEC)
        # The stage self-locates smoke-test.sh beside itself: stage a copy
        # of the script with a stub smoke-test.sh in a scratch dir.
        stage_dir = t / "lib"
        stage_dir.mkdir()
        stage = stage_dir / "ge-eval-stage.sh"
        stage.write_text(STAGE.read_text())
        stage.chmod(stage.stat().st_mode | stat.S_IEXEC)
        smoke = stage_dir / "smoke-test.sh"
        smoke.write_text(f"#!/bin/sh\necho smoke-ran STRICT=$SMOKE_STRICT\nexit {smoke_rc}\n")
        smoke.chmod(smoke.stat().st_mode | stat.S_IEXEC)
        env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")
        return subprocess.run(["bash", str(stage)], env=env,
                              capture_output=True, text=True)

    def test_green_all_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run(tmp)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("smoke-ran STRICT=1", r.stdout)  # strict enforced
            self.assertIn("GREEN", r.stdout)

    def test_red_sync_failure_aborts_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run(tmp, sync_rc=1)
            self.assertEqual(r.returncode, 1)
            self.assertIn("pkm sync failed", r.stderr)
            self.assertNotIn("smoke-ran", r.stdout)  # never reached

    def test_red_install_failure_aborts_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run(tmp, install_rc=1)
            self.assertEqual(r.returncode, 1)
            self.assertIn("did not install", r.stderr)

    def test_red_verify_failure_aborts_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run(tmp, verify_rc=1)
            self.assertEqual(r.returncode, 1)
            self.assertIn("not intact", r.stderr)
            self.assertNotIn("smoke-ran", r.stdout)

    def test_red_smoke_failure_is_stage_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run(tmp, smoke_rc=1)
            self.assertEqual(r.returncode, 1)
            self.assertIn("strict smoke battery reported failures", r.stderr)


if __name__ == "__main__":
    unittest.main()
