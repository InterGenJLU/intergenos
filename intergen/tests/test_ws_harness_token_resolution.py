# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The live-gate opt-in can actually be taken.

Four cells in test_ws_gate_lifecycle.py are advertised as "opt-in: set
INTERGEN_WS_HARNESS=1 with a running daemon+model to enable". Taking that
opt-in produced four FileNotFoundError failures instead of four runs, at
/tmp/igos-pytest-xdg-*/home/.config/intergen/web-token — measured 2026-09-16 on
intergenos-192-r001-2 with the daemon active and the model serving, and
identically at a51039c4e, so it had never been reachable under pytest.

The cause is two correct things colliding. conftest.py redirects HOME and every
XDG base to a throwaway directory before any intergen import, so a suite run
cannot touch the invoking user's own files. The live cells, by design, drive the
REAL daemon, whose token is in the real home — which the redirection has moved.

A gate that cannot be enabled is a verification that looks available and is not.
The resolution order restores the opt-in and touches nothing else: the isolation
exists to stop a test run WRITING to the real home, and every path here is read.
"""

from __future__ import annotations

import os
import pwd
import shutil
import tempfile
import unittest
from pathlib import Path

from intergen.tests import ws_harness as wh


class TokenResolutionTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._env = os.environ.get(wh.TOKEN_FILE_ENV)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._env is None:
            os.environ.pop(wh.TOKEN_FILE_ENV, None)
        else:
            os.environ[wh.TOKEN_FILE_ENV] = self._env

    def _write(self, path: str, token: str) -> str:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(token + "\n")
        return str(p)

    def test_an_explicit_path_wins(self):
        p = self._write(os.path.join(self.tmp, "explicit", "web-token"), "tok-x")
        os.environ[wh.TOKEN_FILE_ENV] = p
        self.assertEqual(wh.default_token(), "tok-x")

    def test_the_passwd_home_is_consulted_even_when_HOME_is_redirected(self):
        """The exact case the suite creates: HOME points at a throwaway dir."""
        real_home = pwd.getpwuid(os.getuid()).pw_dir
        token_path = Path(real_home) / ".config" / "intergen" / "web-token"
        if not token_path.is_file():
            self.skipTest("this user has no panel token; the daemon writes one "
                          "when it starts")
        os.environ.pop(wh.TOKEN_FILE_ENV, None)
        prior = os.environ.get("HOME")
        os.environ["HOME"] = os.path.join(self.tmp, "redirected-home")
        try:
            self.assertEqual(wh.default_token(),
                             token_path.read_text().strip())
        finally:
            if prior is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = prior

    def test_an_explicit_config_dir_still_behaves_exactly_as_before(self):
        d = os.path.join(self.tmp, "given")
        self._write(os.path.join(d, "web-token"), "tok-given")
        self.assertEqual(wh.default_token(Path(d)), "tok-given")

    def test_the_candidate_list_is_ordered_and_carries_no_duplicates(self):
        os.environ[wh.TOKEN_FILE_ENV] = os.path.join(self.tmp, "first",
                                                     "web-token")
        paths = wh._token_candidates()
        self.assertEqual(str(paths[0]),
                         os.path.join(self.tmp, "first", "web-token"))
        self.assertEqual(len(paths), len(set(str(p) for p in paths)))
        self.assertTrue(all(p.name == "web-token" for p in paths[1:]))

    def test_nothing_readable_names_every_path_it_tried(self):
        """The bare FileNotFoundError pointed at a throwaway pytest directory
        and said nothing about why the token was not there."""
        missing = os.path.join(self.tmp, "absent", "web-token")
        os.environ[wh.TOKEN_FILE_ENV] = missing
        real_candidates = wh._token_candidates()
        original = wh._token_candidates
        wh._token_candidates = lambda: [Path(missing)]
        self.addCleanup(setattr, wh, "_token_candidates", original)
        with self.assertRaises(FileNotFoundError) as cm:
            wh.default_token()
        message = str(cm.exception)
        self.assertIn(missing, message)
        self.assertIn(wh.TOKEN_FILE_ENV, message)
        self.assertTrue(real_candidates)

    def test_an_empty_token_file_is_not_accepted_as_a_token(self):
        p = self._write(os.path.join(self.tmp, "empty", "web-token"), "")
        os.environ[wh.TOKEN_FILE_ENV] = p
        original = wh._token_candidates
        wh._token_candidates = lambda: [Path(p)]
        self.addCleanup(setattr, wh, "_token_candidates", original)
        with self.assertRaises(FileNotFoundError):
            wh.default_token()


class NoLiveCellMayPromptAPersonTests(unittest.TestCase):
    """No cell reachable from the ordinary live opt-in may escalate.

    Answering a gate with ALLOW routes an allowed privileged built-in through
    _dispatch_via_pkexec, pkexec asks polkit, and polkit raises an INTERACTIVE
    authentication dialog on the desktop of whoever is at the machine. Measured
    2026-09-16 on intergenos-192-r001-2: taking the INTERGEN_WS_HARNESS opt-in
    put a prompt in front of the person using the box, who had to answer it.

    A test must never be able to ask a human anything — nobody reads a test
    runner's terminal, and an unattended run has no one to answer. This is
    pinned here, in the suite, rather than left to each author of a future cell
    to remember.
    """

    def test_the_escalating_cell_carries_its_own_opt_in(self):
        from intergen.tests import test_ws_gate_lifecycle as wsg
        cell = wsg.WSGateLifecycleLiveTests.test_allow_resolves_and_terminates
        self.assertTrue(
            getattr(cell, "__unittest_skip__", False)
            or not wsg._ALLOW_OPT_IN,
            "the allow cell must not run without INTERGEN_WS_ALLOW_PRIVILEGED")
        self.assertNotEqual(wsg._ALLOW_SKIP_REASON, wsg._SKIP_REASON)
        # The reason a reader sees must NAME the consequence, not just the flag.
        for phrase in ("authentication", "polkit", "NOT verified"):
            self.assertIn(phrase, wsg._ALLOW_SKIP_REASON)

    def test_every_other_live_cell_answers_gates_with_deny(self):
        """Deny never escalates. Any cell that sends something else must carry
        its own opt-in, like the allow cell does."""
        import inspect
        from intergen.tests import test_ws_gate_lifecycle as wsg
        source = inspect.getsource(wsg.WSGateLifecycleLiveTests)
        for name, member in vars(wsg.WSGateLifecycleLiveTests).items():
            if not name.startswith("test_"):
                continue
            body = inspect.getsource(member)
            escalating = ('"allow"' in body or "'allow'" in body
                          or "allow_conversation" in body)
            if escalating and name != "test_allow_resolves_and_terminates":
                self.fail(
                    f"{name} answers a gate with allow but carries no opt-in "
                    "of its own; it can raise an authentication prompt")
        self.assertIn('gate_decision="deny"', source)


if __name__ == "__main__":
    unittest.main()
