#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The CUDA toolkit helper takes its license answer from a terminal, or stops.

The helper asks the person to type "I ACCEPT" before it downloads four
gigabytes from NVIDIA. It read that answer from standard input, which is the
package manager's when the package manager runs it — and inside a transaction
that is not always a terminal. Read from a pipe, the "answer" is whatever the
pipe holds; read at end-of-file, silence becomes a refusal. Neither is a person
reading NVIDIA's terms.

The helper now reads from the terminal it is attached to (/dev/tty) whenever
standard input is not one, and when there is no terminal at all it says so,
prints the command to run, and exits with a status the package manager reports
as a failure rather than as a declined license.

These cases run the REAL helper script up to and including that decision. They
cannot reach the download: a root check is satisfied by a stand-in `id`, the
download tool on the PATH is a stand-in that refuses, and the run is detached
from any controlling terminal (setsid) so /dev/tty is genuinely absent. A
machine that already holds an acceptance record for this toolkit version would
skip the prompt — the cases skip on such a machine rather than run past it.
"""

import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "packages" / "compute" / "cuda-toolkit" / "helper" / "igos-install-cuda-toolkit"
HELPER_LIB = REPO / "packages" / "core" / "intergenos-helper-lib" / "helper-lib.sh"
SYSTEM_HELPER_LIB = Path("/usr/share/igos/helpers/helper-lib.sh")


def _acceptance_recorded():
    m = re.search(r'^CUDA_VERSION="([^"]+)"', HELPER.read_text(), re.M)
    version = m.group(1)
    return Path(f"/var/lib/intergen/legal/cuda-toolkit-{version}-accepted.json").exists()


class _Harness:
    def __init__(self, tmp):
        self.tmp = Path(tmp)
        bindir = self.tmp / "bin"
        bindir.mkdir()
        # The helper insists on root; the stand-in answers as root would. It
        # is the only way to reach the consent step without privilege, and
        # nothing after the consent step can run (the download tool refuses).
        self._stub(bindir / "id", "#!/bin/sh\necho 0\n")
        self._stub(bindir / "wget", "#!/bin/sh\necho 'stand-in wget: refusing' >&2\nexit 1\n")
        self.bindir = bindir
        # The helper sources the shared helper library from its installed
        # path. On a machine that is not an InterGenOS install that file is
        # absent; the copy in this tree is used instead, by rewriting that one
        # line in a private copy of the script.
        script = HELPER.read_text()
        if not SYSTEM_HELPER_LIB.exists():
            script = script.replace(f"source {SYSTEM_HELPER_LIB}",
                                    f"source {HELPER_LIB}")
        self.script = self.tmp / "igos-install-cuda-toolkit"
        self.script.write_text(script)
        self.script.chmod(0o755)
        self.workroot = self.tmp / "work"
        self.workroot.mkdir()

    @staticmethod
    def _stub(path, body):
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def run(self, stdin_text=None, detach=True):
        env = dict(os.environ)
        env["PATH"] = f"{self.bindir}:{env.get('PATH', '')}"
        env["IGOS_CUDA_WORKROOT"] = str(self.workroot)
        env["IGOS_HELPER_MANIFEST_DIR"] = str(self.tmp / "manifests")
        argv = ["bash", str(self.script)]
        if detach:
            argv = ["setsid", "-w"] + argv
        return subprocess.run(argv, env=env, input=stdin_text or "",
                              capture_output=True, text=True, timeout=120)


@unittest.skipUnless(shutil.which("setsid"), "setsid is needed to detach from a terminal")
class NoTerminalMeansNoAcceptance(unittest.TestCase):

    def setUp(self):
        if _acceptance_recorded():
            self.skipTest("this machine already holds an acceptance record "
                          "for this toolkit version; the prompt would be skipped")
        self._td = tempfile.TemporaryDirectory()
        self.h = _Harness(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _run(self, stdin_text):
        r = self.h.run(stdin_text)
        if "MiB free" in r.stdout:
            self.skipTest("not enough free disk on this machine to reach the "
                          f"consent step: {r.stdout.strip().splitlines()[-3:]}")
        return r

    def test_a_piped_acceptance_is_not_taken(self):
        """The phrase arriving on a pipe is not a person reading the terms."""
        r = self._run("I ACCEPT\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotEqual(r.returncode, 10,
                            "reported as a declined license; nobody declined")
        self.assertEqual(r.returncode, 4, r.stdout[-1500:] + r.stderr[-500:])
        self.assertIn("no terminal is attached", r.stdout)
        self.assertIn("sudo pkm install cuda-toolkit", r.stdout)
        self.assertIn("Nothing was downloaded", r.stdout)

    def test_end_of_file_is_not_a_refusal(self):
        r = self._run("")
        self.assertEqual(r.returncode, 4, r.stdout[-1500:] + r.stderr[-500:])
        self.assertNotIn("Acceptance not given", r.stdout)

    def test_nothing_was_fetched(self):
        r = self._run("I ACCEPT\n")
        self.assertNotIn("stand-in wget", r.stderr)
        self.assertEqual(sorted(p.name for p in self.h.workroot.iterdir()), [])


class TheDecisionIsInTheScript(unittest.TestCase):
    """Pinned in the source so the behaviour cannot regress to reading stdin
    on a machine where the executed cases skip."""

    def test_the_answer_is_read_from_the_terminal_when_stdin_is_not_one(self):
        src = HELPER.read_text()
        block = src[src.index("Type 'I ACCEPT'"):src.index('if [ "$REPLY" != "I ACCEPT" ]')]
        self.assertIn("read -r REPLY < /dev/tty", block)
        self.assertIn("exit 4", block)
        self.assertIn("sudo pkm install cuda-toolkit", block)


if __name__ == "__main__":
    unittest.main()
