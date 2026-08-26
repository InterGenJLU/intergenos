# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The one-click model setup must not run the whole of `intergen setup` as root.

WHAT THIS FIXES. The Welcomer launched the model setup as
``pkexec intergen setup --yes [--tier=N]``, which escalates the ENTIRE run —
hardware detection, the license gate, and a download of up to about 21 GB — to
root, under polkit's generic exec action because /usr/bin/intergen has no action
of its own and must never be given one.

The tree already says that is not the design. The shipped policy file's own
comment reads "`intergen setup` runs as the unprivileged user; the model store
at /var/lib/intergen/models is system-wide root-owned read-only by design", and
intergen/model_manager.py implements exactly that: provision_model downloads and
pin-verifies into a user-writable staging directory AS THE USER, then escalates
ONCE through /usr/bin/intergen-model-setup-runner under the registered action
org.intergenos.intergen.provision-model-storage, whose runner re-verifies the
staged file's checksum before any root-owned write.

So the outer escalation bought nothing and cost the difference between one
prompt that says which model is being installed and one that says a program
wants to run as another user. It is removed here: the Welcomer runs
`intergen setup` unprivileged and the registered prompt inside it is the only
prompt raised.

WHAT REPLACES THE OLD FAILURE MAPPING. With no outer pkexec there is no outer
126 or 127 to read, so the sentences the user sees can no longer come from the
launcher's own exit code. They come from what `intergen setup` reports, which is
what the second half of this file pins.
"""

import importlib.util
import re
import unittest
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio, GLib  # noqa: E402,F401

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WELCOME_PY = REPO_ROOT / "assets" / "intergen-welcome" / "intergen-welcome.py"

_spec = importlib.util.spec_from_file_location("intergen_welcome", WELCOME_PY)
welcome = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(welcome)


class SetupArgvIsUnprivileged(unittest.TestCase):

    def test_the_module_exposes_the_argv_it_will_run(self):
        self.assertTrue(
            hasattr(welcome, "_intergen_setup_argv"),
            "the setup command is built inside a closure, so nothing can assert "
            "what the Welcomer actually runs")

    def test_the_argv_does_not_begin_with_pkexec(self):
        argv = welcome._intergen_setup_argv(None)
        self.assertNotEqual(
            argv[0], "pkexec",
            "the Welcomer still escalates the whole of `intergen setup` to root; "
            "only the model-store write is supposed to cross that boundary")
        self.assertNotIn(
            "pkexec", argv,
            "pkexec appears in the setup command: %r" % (argv,))

    def test_the_argv_still_runs_setup_non_interactively(self):
        argv = welcome._intergen_setup_argv(None)
        self.assertEqual(argv[:3], ["intergen", "setup", "--yes"])

    def test_a_chosen_tier_is_still_passed_through(self):
        self.assertEqual(welcome._intergen_setup_argv(2)[-1], "--tier=2")
        self.assertEqual(welcome._intergen_setup_argv(1)[-1], "--tier=1")

    def test_no_executable_line_of_the_launcher_names_pkexec(self):
        """Comments may explain the absence; code may not reintroduce it.

        The launcher's own comment says why there is no outer escalation any
        more, and that explanation is worth keeping — so this reads only the
        lines that RUN.
        """
        src = WELCOME_PY.read_text(encoding="utf-8")
        m = re.search(r"def _launch_intergen_setup\(.*?\n(?=\ndef )", src,
                      re.DOTALL)
        self.assertIsNotNone(m, "the setup launcher moved; update this test")
        code = []
        in_doc = False
        for line in m.group(0).splitlines():
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.endswith('"""'):
                # The single docstring: one line opens it, one closes it.
                in_doc = not in_doc
                continue
            if in_doc or stripped.startswith("#"):
                continue
            code.append(line)
        offenders = [l for l in code if "pkexec" in l]
        self.assertEqual(
            offenders, [],
            "an executable line of the setup launcher names pkexec: %r"
            % (offenders,))


class RefusedProvisioningReachesTheUser(unittest.TestCase):
    """A refused model install is not a failed download, and must not read as one."""

    def test_the_module_maps_what_setup_reports(self):
        self.assertTrue(
            hasattr(welcome, "_setup_failure_reason"),
            "nothing maps `intergen setup`'s own report of a refused install "
            "into a sentence, so the user gets the generic 'didn't finish' text "
            "for an authentication they dismissed")

    def test_a_refused_install_is_named_as_refused(self):
        reason = welcome._setup_failure_reason(
            1, ["intergen-setup: result=provisioning-refused"])
        self.assertIsNotNone(reason)
        low = reason.lower()
        self.assertTrue(
            "install" in low or "authoris" in low or "authoriz" in low
            or "password" in low,
            "the sentence does not say the install step was refused: %r" % reason)
        self.assertNotIn(
            "download did not finish", low,
            "a refused install is still being reported as a failed download")

    def test_an_ordinary_failure_keeps_the_general_sentence(self):
        refused = welcome._setup_failure_reason(
            1, ["intergen-setup: result=provisioning-refused"])
        other = welcome._setup_failure_reason(1, ["something else entirely"])
        self.assertIsNotNone(other)
        self.assertNotEqual(refused, other)

    def test_success_has_no_reason(self):
        self.assertIsNone(welcome._setup_failure_reason(0, []))


if __name__ == "__main__":
    unittest.main()
