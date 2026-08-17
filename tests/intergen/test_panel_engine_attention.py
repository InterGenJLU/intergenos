# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An engine failure must not make InterGen invisible.

THE DEFECT. The panel icon was gated on one boolean: components.llama_server.
False hid the icon, and Super+I then said

    "InterGen isn't set up yet — open the Welcome app to finish installing
     the assistant."

On a fresh machine that is correct and useful. On an ONBOARDED machine whose
engine failed to start it is false twice over: the assistant IS set up, and the
message sends the user to redo work they already did while saying nothing about
the actual failure. The one state a user most needs to see — it is installed and
it is broken — was the state rendered as absence.

THE FIX. Three states instead of two. Serving shows the icon normally. Set up
but not serving shows the icon in an ATTENTION colour and reports the recorded
reason. Never set up keeps the old behaviour, because there the icon really
would be a dead end.

HOW THIS IS TESTED. The extension runs inside the compositor, where no Python
test can execute it, but the two decisions that carry the fix are pure functions
over the status payload. They are EXTRACTED FROM THE SHIPPED FILE and executed
under node, so what is exercised is the code that ships rather than a
description of it. The parts that can only be checked structurally — which
branch calls which — are checked structurally, and this file says which is
which rather than presenting both as the same strength of evidence.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXTENSION_JS = REPO / "intergen" / "panel" / "extension" / "extension.js"


def _extract_function(text, name):
    """The full source of a top-level `function name(...) { ... }`.

    Brace-matched from the opening brace, so a function containing braces comes
    out whole. Returns None when the function is not declared, and the tests
    treat that as a failure rather than skipping — a missing function is the
    regression, not a reason to pass.
    """
    m = re.search(r"^function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{",
                  text, re.MULTILINE)
    if not m:
        return None
    start = m.start()
    i = text.index("{", m.start())
    depth = 0
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return None


@unittest.skipUnless(shutil.which("node"), "node is required to execute the "
                                           "shipped helper functions")
class HelperExecutionTest(unittest.TestCase):
    """Run the real functions from the real file."""

    @classmethod
    def setUpClass(cls):
        cls.text = EXTENSION_JS.read_text(encoding="utf-8")

    def _run(self, funcs, script):
        with tempfile.TemporaryDirectory(prefix="panel-attention-") as tmp:
            p = Path(tmp) / "probe.js"
            p.write_text("\n".join(funcs) + "\n" + script)
            r = subprocess.run(["node", str(p)], capture_output=True,
                               text=True, timeout=60)
            self.assertEqual(r.returncode, 0,
                             f"probe failed: {r.stderr}")
            return r.stdout.strip()

    def _fn(self, name):
        src = _extract_function(self.text, name)
        self.assertIsNotNone(
            src, f"{name}() is not declared in the shipped extension — the "
                 f"icon gate has lost the decision this test covers")
        return src

    def test_a_selected_model_counts_as_onboarded(self):
        out = self._run(
            [self._fn("_isOnboarded")],
            "console.log(JSON.stringify(["
            "_isOnboarded({model: 'gemma-9b'}),"
            "_isOnboarded({components: {model_manager: true}}),"
            "_isOnboarded({components: {model_manager: false}}),"
            "_isOnboarded({}),"
            "_isOnboarded(null)"
            "]));")
        self.assertEqual(json.loads(out),
                         [True, True, False, False, False])

    def test_the_recorded_error_is_what_gets_reported(self):
        out = self._run(
            [self._fn("_engineFailureReason")],
            "console.log(_engineFailureReason("
            "{last_error: 'UNHEALTHY: never became healthy'}));")
        self.assertIn("never became healthy", out)

    def test_an_integrity_failure_outranks_the_generic_error(self):
        """The conspicuous one must not be hidden behind the ordinary one."""
        out = self._run(
            [self._fn("_engineFailureReason")],
            "console.log(_engineFailureReason({"
            "last_error: 'something ordinary',"
            "model_server_integrity_failure: 'TOOLS_NOT_ADVERTISED: drift'"
            "}));")
        self.assertIn("TOOLS_NOT_ADVERTISED", out)
        self.assertNotIn("something ordinary", out)

    def test_no_recorded_reason_says_so_rather_than_inventing_one(self):
        out = self._run(
            [self._fn("_engineFailureReason")],
            "console.log(_engineFailureReason({}));")
        self.assertIn("no reason was recorded", out)
        self.assertIn("journalctl", out,
                      "it does not tell the user where to look")

    def test_the_reason_is_never_empty(self):
        """An empty string would render as a sentence that trails off."""
        for payload in ("{}", "null", "{last_error: null}",
                        "{last_error: ''}"):
            with self.subTest(payload=payload):
                out = self._run([self._fn("_engineFailureReason")],
                                f"console.log(_engineFailureReason({payload}));")
                self.assertTrue(out.strip())


class GateStructureTest(unittest.TestCase):
    """Checked by reading the source — stated as the weaker evidence it is.

    These cover the wiring between the decisions above and the icon, which runs
    inside the compositor and cannot be executed here. They catch the
    regressions that matter: the attention branch disappearing, or the
    "isn't set up yet" message being shown to a machine that is set up.
    """

    @classmethod
    def setUpClass(cls):
        cls.text = EXTENSION_JS.read_text(encoding="utf-8")

    def test_the_extension_parses(self):
        if not shutil.which("node"):
            self.skipTest("node unavailable")
        r = subprocess.run(["node", "--check", str(EXTENSION_JS)],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)

    def _code(self):
        return "\n".join(ln for ln in self.text.splitlines()
                         if not ln.strip().startswith("//"))

    def test_a_not_serving_but_onboarded_machine_still_shows_the_icon(self):
        code = self._code()
        # The CALL SITE inside the readiness check, not the declaration. An
        # earlier version of this test matched the function definition and so
        # sliced the wrong region entirely.
        m = re.search(r"else\s+if\s*\(\s*_isOnboarded\(status\)\s*\)\s*\{",
                      code)
        self.assertIsNotNone(
            m, "the readiness check no longer branches on _isOnboarded, so an "
               "engine failure hides the icon again")
        branch = code[m.end():]
        end = branch.find("} else {")
        self.assertGreater(end, 0, "could not delimit the onboarded branch")
        branch = branch[:end]
        self.assertIn("_showIndicator", branch,
                      "the onboarded-but-not-serving branch does not show the "
                      "icon")
        self.assertIn("setAttention", branch,
                      "the icon is shown without marking it as needing "
                      "attention, so the failure is invisible again")
        self.assertNotIn("_hideIndicator", branch,
                         "the onboarded branch still hides the icon")

    def test_super_i_does_not_tell_an_onboarded_user_to_set_up(self):
        """Inside the keybinding callback, the attention check must come first.

        Otherwise a machine whose engine has failed opens a dead panel window,
        or is told to go and install what it already has.
        """
        code = self._code()
        start = code.index('"toggle-intergen"')
        callback = code[start:start + 1600]
        self.assertIn("attentionReason", callback,
                      "the keybinding cannot distinguish the attention state")
        self.assertIn("_launchPanel()", callback)
        self.assertIn("isn't set up yet", callback)
        self.assertLess(
            callback.index("attentionReason"),
            callback.index("_launchPanel()"),
            "the launch branch is reached before the attention check")
        self.assertLess(
            callback.index("attentionReason"),
            callback.index("isn't set up yet"),
            "the not-set-up message is reached before the attention check")

    def test_the_fresh_machine_message_is_still_there(self):
        """The old behaviour is correct for the case it was written for."""
        self.assertIn("isn't set up yet", self.text)

    def test_the_attention_state_is_visually_distinct(self):
        code = "\n".join(ln for ln in self.text.splitlines()
                         if not ln.strip().startswith("//"))
        self.assertIn("setAttention", code)
        self.assertIn("#FFA000", code,
                      "no distinct attention colour, so a failing engine looks "
                      "identical to a healthy one")
        self.assertIn("#0099FF", code, "the healthy colour is gone")

    def test_the_attention_state_is_cleared_when_serving_resumes(self):
        code = "\n".join(ln for ln in self.text.splitlines()
                         if not ln.strip().startswith("//"))
        self.assertIn("setAttention(null)", code,
                      "the attention state is never cleared, so an engine that "
                      "recovered would keep showing as broken")


if __name__ == "__main__":
    unittest.main()
