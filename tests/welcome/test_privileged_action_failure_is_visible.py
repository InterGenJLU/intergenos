# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A privileged action that fails or is dismissed must be shown, not swallowed.

WHAT THIS FIXES, from this machine's own journal on a first boot::

    polkitd[934]: Operator of unix-session:2 FAILED to authenticate to gain
        authorization for action org.freedesktop.policykit.exec for
        unix-process:3286 [python3 /usr/libexec/intergen-welcome/intergen-welcome.py]
    pkexec[6752]: christopher: Error executing command as another user:
        Request dismissed [COMMAND=/usr/bin/intergen setup --yes --tier=2]

The polkit prompt was DISMISSED — the user closed it. Thirteen minutes later the
same command was authenticated and ran. Nothing was misconfigured; what was
wrong is that neither the person nor the record was told what had happened.

Two places swallowed it.

  * The service toggles: on a non-zero pkexec exit the row is silently set back
    to where it was. A user who dismisses the prompt, or whose helper errors,
    watches the switch flick back with no sentence anywhere. A toggle that
    silently returns to off after a failed authentication is indistinguishable
    from a toggle that does not work.
  * The one-click InterGen setup: every failure collapses into "Setup didn't
    finish. You can try again", which is also what a download error says. The
    dismissal case is the one the user can act on immediately, and it read like
    a malfunction.

WHAT IS ASSERTED HERE. pkexec's own exit codes are the evidence: 126 is "the
authentication dialog was dismissed", 127 is "not authorized / authentication
failed", and anything else came from the command itself. The Welcomer must turn
each into a distinct sentence, and must write the same fact to stderr so it
lands in the journal beside the polkit line that recorded the refusal.

The polkit ACTION is asserted here too. Every privileged path in the Welcomer
runs a bare `pkexec <program>`, so polkit falls back to its generic
org.freedesktop.policykit.exec action and the prompt tells the user only that
something wants to "run a program as another user". The package now ships an
action for its own helper, so the prompt names what is being authorized.
"""

import importlib.util
import re
import subprocess
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio, GLib  # noqa: E402,F401

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WELCOME_PY = REPO_ROOT / "assets" / "intergen-welcome" / "intergen-welcome.py"
POLICY = REPO_ROOT / "assets" / "intergen-welcome" / "org.intergenos.welcome.policy"
BUILD_SH = REPO_ROOT / "packages" / "desktop" / "intergen-welcome" / "build.sh"
HELPER_INSTALLED_PATH = "/usr/libexec/intergen-welcome/intergen-welcome-privhelper"

_spec = importlib.util.spec_from_file_location("intergen_welcome", WELCOME_PY)
welcome = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(welcome)

PKEXEC_DISMISSED = 126
PKEXEC_NOT_AUTHORIZED = 127


class ServiceToggleFailureIsExplained(unittest.TestCase):
    """_apply_service must report WHY, not just False."""

    def _apply_with_rc(self, rc):
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            return SimpleNamespace(returncode=rc, stdout="", stderr="")

        real = welcome.subprocess.run
        welcome.subprocess.run = fake_run
        try:
            return welcome._apply_service("printing", True)
        finally:
            welcome.subprocess.run = real

    def test_success_reports_ok_and_no_reason(self):
        result = self._apply_with_rc(0)
        self.assertTrue(result.ok)
        self.assertIsNone(result.reason)

    def test_a_dismissed_prompt_is_named_as_dismissed(self):
        result = self._apply_with_rc(PKEXEC_DISMISSED)
        self.assertFalse(result.ok)
        self.assertIsNotNone(
            result.reason,
            "a dismissed authentication produced no sentence for the user")
        low = result.reason.lower()
        self.assertTrue(
            "cancel" in low or "dismiss" in low or "closed" in low,
            "the sentence for a dismissed prompt does not say the prompt was "
            "dismissed: %r" % (result.reason,))

    def test_a_failed_authentication_is_told_apart_from_a_dismissal(self):
        dismissed = self._apply_with_rc(PKEXEC_DISMISSED)
        refused = self._apply_with_rc(PKEXEC_NOT_AUTHORIZED)
        self.assertFalse(refused.ok)
        self.assertIsNotNone(refused.reason)
        self.assertNotEqual(
            dismissed.reason, refused.reason,
            "a dismissed prompt and a refused authentication produce the same "
            "sentence, so the user cannot tell which happened")

    def test_a_helper_error_is_told_apart_from_both(self):
        helper = self._apply_with_rc(4)
        self.assertFalse(helper.ok)
        self.assertIsNotNone(helper.reason)
        self.assertNotIn(
            helper.reason,
            (self._apply_with_rc(PKEXEC_DISMISSED).reason,
             self._apply_with_rc(PKEXEC_NOT_AUTHORIZED).reason),
            "an error from the helper itself reads as an authentication problem")


class FailureIsRecorded(unittest.TestCase):
    """The same fact must reach the journal, not only the window."""

    def test_a_refused_action_writes_a_line_to_stderr(self):
        script = (
            "import importlib.util, sys\n"
            "from types import SimpleNamespace\n"
            "spec = importlib.util.spec_from_file_location('w', %r)\n"
            "w = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(w)\n"
            "w.subprocess.run = lambda a, **k: SimpleNamespace(returncode=126)\n"
            "w._apply_service('printing', True)\n" % str(WELCOME_PY)
        )
        r = subprocess.run([ "python3", "-c", script], capture_output=True,
                           text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(
            r.stderr.strip(),
            "a refused privileged action wrote nothing to stderr, so the "
            "journal carries polkit's refusal and no word from the Welcomer "
            "about which action it was")
        self.assertIn("printing", r.stderr,
                      "the recorded line does not name the action that failed")


class SetupFailureIsExplained(unittest.TestCase):
    """The one-click setup must not report a dismissal as a generic failure."""

    def test_the_module_maps_pkexec_codes_for_the_setup_path(self):
        self.assertTrue(
            hasattr(welcome, "_pkexec_failure_reason"),
            "there is no shared mapping from a pkexec exit code to a sentence, "
            "so the setup path cannot tell the user what happened")
        dismissed = welcome._pkexec_failure_reason(PKEXEC_DISMISSED)
        refused = welcome._pkexec_failure_reason(PKEXEC_NOT_AUTHORIZED)
        other = welcome._pkexec_failure_reason(1)
        self.assertTrue(dismissed and refused)
        self.assertNotEqual(dismissed, refused)
        self.assertNotEqual(dismissed, other)

    def test_the_setup_launcher_passes_the_reason_on(self):
        src = WELCOME_PY.read_text(encoding="utf-8")
        m = re.search(r"def _launch_intergen_setup\(.*?\n(?=\ndef )", src,
                      re.DOTALL)
        self.assertIsNotNone(m, "the setup launcher moved; update this test")
        body = m.group(0)
        self.assertIn(
            "_pkexec_failure_reason", body,
            "the setup launcher still reports every non-zero exit the same way, "
            "so a dismissed authentication reads as a failed download")


class PolkitActionIsRegistered(unittest.TestCase):
    """The helper is authorized under its own named action."""

    def test_the_policy_file_exists_and_is_well_formed(self):
        self.assertTrue(
            POLICY.exists(),
            "no polkit policy ships for the Welcomer's privileged helper, so "
            "every prompt falls back to org.freedesktop.policykit.exec and "
            "tells the user only that a program wants to run as another user")
        ET.parse(POLICY)   # raises if malformed

    def test_the_action_points_at_the_installed_helper(self):
        root = ET.parse(POLICY).getroot()
        actions = root.findall("action")
        self.assertTrue(actions, "the policy file declares no action")
        paths = [a.get("key") and a.text for a in root.iter("annotate")
                 if a.get("key") == "org.freedesktop.policykit.exec.path"]
        self.assertIn(
            HELPER_INSTALLED_PATH, paths,
            "the action does not annotate the helper's installed path, so "
            "polkit will not match it")

    def test_the_action_requires_administrator_authentication(self):
        root = ET.parse(POLICY).getroot()
        for action in root.findall("action"):
            defaults = action.find("defaults")
            self.assertIsNotNone(defaults, "an action ships with no defaults")
            for tag in ("allow_any", "allow_inactive", "allow_active"):
                el = defaults.find(tag)
                self.assertIsNotNone(el, "%s is unset" % tag)
                self.assertTrue(
                    el.text.startswith("auth_admin"),
                    "%s is %r — this action must not be weaker than the "
                    "generic exec action it replaces" % (tag, el.text))

    def test_the_recipe_installs_the_policy(self):
        text = BUILD_SH.read_text(encoding="utf-8")
        self.assertIn(
            "polkit-1/actions", text,
            "build.sh does not install the policy file, so it would exist in "
            "the tree and never reach a machine")
        self.assertIn("org.intergenos.welcome.policy", text)


if __name__ == "__main__":
    unittest.main()
