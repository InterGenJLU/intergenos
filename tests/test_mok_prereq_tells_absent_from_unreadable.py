# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The post-install skip gate says whether it looked, not just what it saw.

WHY THIS EXISTS. Nine post-install integration cases skip with

    MOK cert /var/lib/intergen/mok/mok.crt not found
    (not a Forge-installed target)

on machines that ARE Forge-installed targets. The cert is there. Its directory
is mode 0700 root, so an unprivileged process cannot traverse it - and
Path.exists() swallows the resulting EACCES and returns False exactly as if the
file were missing. Measured on the R001.1 installed system, 2026-08-24: the
suite reported "not a Forge-installed target" while
/var/lib/intergen/mok/mok.crt existed at 1168 bytes.

An instrument that cannot tell "it is not there" from "I was not allowed to
look" and then reports the first is the failure this project refuses: it prints
the reassuring reading of the two. Nine cases that exist to validate an
installed target were silent on the only machines that are one, and the reason
they gave was false rather than merely unhelpful.

WHAT THIS CHANGES, AND WHAT IT DOES NOT. The skip outcome is unchanged - an
unprivileged run still cannot read the cert and still cannot do the work, so it
still skips. Coverage is identical. What changes is that the reason now names
which of the two happened. Whether these cases should instead RUN with
privilege on an installed target is a coverage decision for the installer lane
and is deliberately not taken here.

WHAT THIS MEASURES. The real shared classifier, against real directories, with
the unreadable case built rather than borrowed: a directory this test creates
and sets to mode 0 is one the test's own user cannot traverse, so the case is
reproduced on any machine instead of depending on the host having a 0700
directory to hand. Every case asserts it is running unprivileged first, because
root traverses regardless and would turn the whole file green for the wrong
reason.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from installer.tests._target_state import (  # noqa: E402
    MOK_ABSENT, MOK_PRESENT, MOK_UNREADABLE, mok_cert_state,
)

FALSE_REASON = "not a Forge-installed target"


class MokCertState(unittest.TestCase):

    def setUp(self):
        self.assertNotEqual(
            os.geteuid(), 0,
            "this file measures what an UNPRIVILEGED process can see; run as "
            "root every case passes for a reason that has nothing to do with "
            "the classifier")
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._cleanup)
        self.tmp = Path(self._tmp.name)

    def _cleanup(self):
        # A mode-0 directory cannot be removed by the cleanup walk until it is
        # readable again, and leaving it behind would fail the next run.
        for p in self.tmp.rglob("*"):
            if p.is_dir():
                try:
                    p.chmod(0o755)
                except OSError:
                    pass
        self._tmp.cleanup()

    def _cert(self, *, exists, traversable):
        d = self.tmp / "var/lib/intergen/mok"
        d.mkdir(parents=True, exist_ok=True)
        cert = d / "mok.crt"
        if exists:
            cert.write_text("not a real certificate\n")
        if not traversable:
            d.chmod(0o000)
        return cert

    # ---------- the harness ----------

    def test_a_mode_zero_directory_really_does_block_this_user(self):
        """Without this, the unreadable case below could be vacuous."""
        cert = self._cert(exists=True, traversable=False)
        with self.assertRaises(PermissionError):
            os.stat(cert)

    # ---------- the three states ----------

    def test_a_readable_cert_reads_as_present(self):
        state, reason = mok_cert_state(self._cert(exists=True, traversable=True))
        self.assertEqual(state, MOK_PRESENT, reason)

    def test_a_missing_cert_reads_as_absent(self):
        state, reason = mok_cert_state(self._cert(exists=False, traversable=True))
        self.assertEqual(state, MOK_ABSENT, reason)
        self.assertIn("not", reason.lower())

    def test_a_cert_behind_a_closed_directory_reads_as_unreadable(self):
        cert = self._cert(exists=True, traversable=False)
        state, reason = mok_cert_state(cert)
        self.assertEqual(
            state, MOK_UNREADABLE,
            "a cert that is present but cannot be examined is being reported "
            f"as {state!r}; that is the defect this file exists for")
        self.assertNotIn(
            FALSE_REASON, reason,
            "the reason still says the target is not Forge-installed. This run "
            "did not look at the cert and cannot say anything about the target")
        for token in ("permission", "unprivileged"):
            with self.subTest(token=token):
                self.assertIn(token, reason.lower(),
                              "the reason does not say that the check was "
                              "refused, or why")

    def test_a_missing_cert_behind_a_closed_directory_is_still_unreadable(self):
        """Not knowing is not the same as knowing it is gone."""
        cert = self._cert(exists=False, traversable=False)
        state, _ = mok_cert_state(cert)
        self.assertEqual(state, MOK_UNREADABLE)

    def test_the_reason_names_the_path_it_could_not_read(self):
        cert = self._cert(exists=True, traversable=False)
        _, reason = mok_cert_state(cert)
        self.assertIn(str(cert), reason)

    # ---------- both callers use it ----------

    def test_both_suites_classify_through_the_shared_helper(self):
        for rel in ("installer/tests/test_post_install_integration.py",
                    "installer/tests/test_class1_integration.py"):
            text = (REPO_ROOT / rel).read_text()
            with self.subTest(module=rel):
                self.assertIn("mok_cert_state", text,
                              "this suite still classifies the cert itself; the "
                              "two copies are what drifted in the first place")
                self.assertNotIn(
                    f'"MOK cert {{mok_cert}} not found ({FALSE_REASON})"', text,
                    "the unconditional false reason is still spelled here")

    def test_neither_suite_decides_with_a_swallowing_exists_call(self):
        for rel in ("installer/tests/test_post_install_integration.py",
                    "installer/tests/test_class1_integration.py"):
            text = (REPO_ROOT / rel).read_text()
            for line in text.splitlines():
                if "mok" not in line.lower() or "cert" not in line.lower():
                    continue
                with self.subTest(module=rel, line=line.strip()):
                    self.assertNotIn(
                        ".exists()", line,
                        "Path.exists() swallows EACCES and returns False, which "
                        "is how a present cert came to be reported as missing")


if __name__ == "__main__":
    unittest.main()
