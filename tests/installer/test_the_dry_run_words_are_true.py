# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""What forge says about a dry run is what a dry run does.

A dry run is the TUI's alone and previews the disk phase only: that phase
logs its destructive commands without running them and the run ends there,
so no later phase runs or is previewed. The privileged installer service runs
no dry run and refuses one. The words said otherwise: the global help promised
every destructive command logged; the TUI reported a completed dry run as
"Install failed at phase partition" (and, given a success status, would have
offered a reboot); the GUI sent the flag to the service and showed its refusal
as a failure to start; the manpage promised partition, package, account and
bootloader commands logged, an exit status of 0 and a GUI example; and the
integrity smoke check counted a dry run among the ways a completed install
skips its verify phase.
"""

import contextlib
import io
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import installer.__main__ as forge_main
from installer.backend import _trace
from installer.backend import bootloader as backend_bootloader
from installer.backend import disks as backend_disks
from installer.backend import install as backend_install
from installer.frontend import tui

REPO = Path(__file__).resolve().parents[2]
MANPAGE = REPO / "man" / "forge.1"
SIGNING_CHECKS = REPO / "installer" / "smoke" / "checks" / "signing.sh"
YAML = ("version: 1\nlocale: en_US.UTF-8\ntimezone: UTC\nhostname: intergenos\n"
        "package_groups: [core]\n")
COMPLETE = ("Dry run complete: the disk phase's destructive commands were logged and "
            "nothing was run; no later phase is previewed")


class _Popen:
    """Records a command and starts nothing."""

    started = []

    def __init__(self, args, *a, **kw):
        _Popen.started.append(args)
        self.args, self.returncode, self.pid = args, 0, 4242
        self.stdin = self.stdout = self.stderr = None

    def communicate(self, input=None, timeout=None):
        return "", ""

    def wait(self, timeout=None):
        return 0

    poll = wait

    def kill(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TheHelpSaysWhatADryRunIs(unittest.TestCase):

    def test_the_option_names_the_tui_and_the_disk_phase_only(self):
        parser = forge_main.build_parser()
        action = next(a for a in parser._actions if "--dry-run" in a.option_strings)
        text = action.help
        self.assertIn("TUI only", text)
        self.assertIn("disk phase", text)
        self.assertIn("no later phase", text)
        self.assertIn("GUI has no dry run", text)
        self.assertNotIn("all destructive commands", text)


class TheTuiReportsACompletedDryRunAsOne(unittest.TestCase):
    """The TUI's real run_declarative and the real pipeline, dry: the host
    reads that would stop a dry run off the medium are stood in, and every
    process start is recorded and none is started."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.yaml_path = self.root / "install.yaml"
        self.yaml_path.write_text(YAML)
        _Popen.started = []
        for p in (mock.patch.object(subprocess, "Popen", _Popen),
                  mock.patch.object(_trace.module, "_VERBOSE", False),
                  mock.patch.object(backend_install, "preflight_check_binaries", lambda *a, **kw: None),
                  mock.patch.object(backend_bootloader, "verify_shim_assets_present", lambda *a, **kw: None),
                  mock.patch.object(backend_disks, "is_efi", lambda: True),
                  mock.patch.object(backend_disks, "_disk_size_bytes", lambda path: 10 ** 12)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(backend_disks.set_dry_run, False)

    def test_the_note_states_the_boundary_and_the_result_is_complete_with_status_0(self):
        install_io = {"disk": "/dev/vdz", "username": "person", "user_password": "pw1234",
                      "root_password": "root1234", "mok_key_passphrase": "a-signing-passphrase"}
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = tui.run_declarative(str(self.yaml_path), install_io, None, None, True)
        text = out.getvalue()
        self.assertEqual(_Popen.started, [], "a dry run started a process")
        self.assertIn("the disk phase's destructive commands will be logged, not run", text)
        self.assertIn(COMPLETE, text)
        self.assertNotIn("Install failed", text)
        self.assertEqual(rc, 0, text)

    def test_after_a_completed_dry_run_no_reboot_is_offered(self):
        asked = []
        stand_ins = {
            "walking": lambda: {"answers": True},
            "emit_yaml": lambda answers: self.yaml_path,
            "prompt_install_io": lambda: {"disk": "/dev/vdz"},
            "_show_confirm_summary": lambda answers, install_io, **kw: True,
            "run_declarative": lambda *a, **kw: 0,
            "_ask_yesno": lambda title, prompt, default_no=False: asked.append(title) or True,
        }
        with contextlib.ExitStack() as stack:
            for name, value in stand_ins.items():
                stack.enter_context(mock.patch.object(tui, name, value))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            rc = tui.run_installer(str(self.root), None, dry_run=True)
        self.assertEqual(rc, 0)
        self.assertEqual(asked, [], "a reboot was offered after a dry run")
        self.assertEqual(_Popen.started, [], "a process started after a dry run")


class TheGuiSendsNoDryRunAndSaysWhy(unittest.TestCase):
    """The progress page's own on_load, on a stand-in page: the GUI with
    --dry-run starts nothing and sends nothing to the privileged service."""

    def test_nothing_is_sent_and_the_page_says_the_gui_has_no_dry_run(self):
        import gi
        # As the GUI's window does before it imports its pages.
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from installer.frontend.gui import dbus_client
        from installer.frontend.gui.screens import progress

        made, failed, tails = [], [], []

        class _Client:
            def __init__(self):
                made.append(self)

            def subscribe(self, **kw):
                pass

            def start_install(self, yaml_content, install_io, dry_run=False):
                made.append(("start_install", dry_run))
                return "an-install"

        page = SimpleNamespace(
            _install_started=False, _window=SimpleNamespace(dry_run=True),
            _start_backend_log_tail=lambda: tails.append(True),
            _serialize_state_yaml=lambda state: YAML,
            _on_install_failed=lambda state, message, *a: failed.append(message),
            _on_bus_progress=None, _on_bus_integrity_warning=None, _on_bus_complete=None)
        state = SimpleNamespace(install_started=False, to_install_io=lambda: {"disk": "/dev/vdz"})
        with mock.patch.object(dbus_client, "ForgeInstallerClient", _Client):
            progress.ProgressPage.on_load(page, state)
        self.assertEqual(made, [], "the GUI reached the privileged service with a dry run")
        self.assertEqual(len(failed), 1, failed)
        self.assertIn("no dry run", failed[0])
        self.assertIn("disk phase", failed[0])
        self.assertEqual(tails, [], "the backend's log was tailed for an install that never starts")


class TheManpageSaysWhatADryRunIs(unittest.TestCase):

    def setUp(self):
        self.text = MANPAGE.read_text()

    def _paragraph(self, tag):
        # The .TP paragraph that opens with the option or status `tag`, its
        # source lines joined: a line break in roff source is a space.
        match = re.search(rf"\.TP\n\.B {re.escape(tag)}\n(.*?)(?=\n\.TP|\n\.SH)", self.text, re.S)
        self.assertIsNotNone(match, f"no paragraph for {tag}")
        return " ".join(match.group(1).split())

    def test_the_option_is_the_tuis_and_previews_the_disk_phase_only(self):
        text = self._paragraph(r"\-\-dry-run")
        for word in ("TUI", "disk phase", "no later phase", "graphical installer has no dry run"):
            self.assertIn(word, text)
        for claim in ("package install", "account", "bootloader install"):
            self.assertNotIn(claim, text, f"the option still promises {claim}")

    def test_the_exit_statuses_are_the_ones_forge_returns(self):
        section = " ".join(self.text.split(".SH EXIT STATUS", 1)[1].split(".SH ", 1)[0].split())
        self.assertIn("dry run completed", section)
        self.assertIn("130", section)
        self.assertIn("install failed", section)

    def test_no_example_runs_the_gui_with_a_dry_run(self):
        examples = self.text.split(".SH EXAMPLES", 1)[1].split(".SH ", 1)[0]
        for line in examples.splitlines():
            if "gui" in line and "dry" in line:
                self.fail(f"an example runs the GUI with a dry run: {line}")


class TheSmokeCheckCountsNoDryRunInstall(unittest.TestCase):

    def test_no_check_message_names_a_dry_run_as_how_an_install_skipped_its_verify(self):
        for number, line in enumerate(SIGNING_CHECKS.read_text().splitlines(), 1):
            if re.search(r"check_(pass|skip|warn|fail) ", line) and "dry" in line:
                self.fail(f"signing.sh:{number}: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
