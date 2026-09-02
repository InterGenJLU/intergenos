#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The Welcomer on an NVIDIA machine AFTER the driver reboot.

Measured on the reference laptop on 2026-09-02, the first time this path met
real NVIDIA hardware. The driver leg worked; everything the page then had to
do failed, and each failure left the person believing something untrue.

(1) THE PAGE CRASHED BEFORE IT HAD A WINDOW. The model-size choice is offered
    only once the driver is installed. Its loop rebound the name `title`, which
    is also the page heading, and the driver-done branch then handed a string
    to reorder_child_after as the sibling widget. Every launch after the
    reboot ended in a TypeError and no window.

(2) THE CRASHED RUN WAS RECORDED AS FINISHED. The GTK bindings print an
    exception raised in do_activate and swallow it; the process exited 0, and
    the launcher — which marks the Welcomer done on a clean exit — hid it from
    every later login. A question never answered was recorded as answered.

(3) THE PAGE SAID THE TERMINAL HAD CLOSED WHILE THE INSTALL WAS RUNNING. The
    gnome-terminal process it started was a client that exits at once; the
    handle "closed" two seconds after the window opened, and the page reported
    nothing installed while the packages were still downloading.

(4) THE LAST LINE IN THE TERMINAL DID NOT SAY THE WELCOMER COMES BACK. The
    promise was made in the notice and in the success verdict — which (3)
    prevented from ever showing — and not on the line the person actually
    carried away.

(5) THE CUDA TOOLKIT WAS NEVER DOWNLOADED. It came in as a dependency of the
    engine, its small installer package was recorded as installed, and the
    download it exists to perform never ran. The page's installed-state check
    then read the install record and would have called it installed.

The page-building cases run the REAL page, under a headless display (the GTK
broadway backend, which needs no screen), in a subprocess so a crash cannot
take the test runner with it. A machine without gtk4-broadwayd skips those
two cases and says so; nothing here pretends a widget test ran when it did not.
"""

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "assets" / "intergen-welcome" / "intergen-welcome.py"

_spec = importlib.util.spec_from_file_location("welcome_after_driver", SCRIPT)
welcome = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(welcome)


# ---------------------------------------------------------------------------
# A headless display for the cases that build widgets
# ---------------------------------------------------------------------------

class _Broadway:
    """gtk4-broadwayd on a display number nothing else is using."""

    def __init__(self):
        self.proc = None
        self.display = None

    def __enter__(self):
        exe = shutil.which("gtk4-broadwayd")
        if exe is None:
            raise unittest.SkipTest(
                "gtk4-broadwayd is not installed here, so the page cannot be "
                "built headlessly on this machine")
        for number in range(60, 90):
            with socket.socket() as s:
                try:
                    s.bind(("127.0.0.1", 8080 + number))
                except OSError:
                    continue
            self.display = f":{number}"
            self.proc = subprocess.Popen(
                [exe, "--port", str(8080 + number), self.display],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.6)
            if self.proc.poll() is None:
                return self
        raise unittest.SkipTest("no free display number for gtk4-broadwayd")

    def __exit__(self, *exc):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def env(self):
        env = dict(os.environ)
        env.update({"GDK_BACKEND": "broadway",
                    "BROADWAY_DISPLAY": self.display,
                    "GSK_RENDERER": "cairo",
                    "IGOS_WELCOMER_SRC": str(SCRIPT)})
        # A display the operator may be logged into must never be reached.
        env.pop("WAYLAND_DISPLAY", None)
        env.pop("DISPLAY", None)
        return env


class _NeverExited:
    """What a run that neither showed a window nor exited looks like.

    This is the shape the laptop was in: the crashed instance stayed alive
    with an unshown window (a GtkWindow created before the crash holds the
    application open), and each app-menu click re-activated THAT instance and
    crashed it again — four tracebacks under one process id in the journal.
    """
    returncode = None
    stdout = ""

    def __init__(self, stderr):
        self.stderr = stderr


def _run_in_display(code, timeout=25):
    with _Broadway() as bw:
        try:
            return subprocess.run([sys.executable, "-c", code], env=bw.env(),
                                  capture_output=True, text=True,
                                  timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            err = exc.stderr.decode(errors="replace") if exc.stderr else ""
            return _NeverExited(err)


# The state the laptop was in: an NVIDIA record, the driver installed, the
# engine not yet, InterGen not set up, and an offer of TWO model sizes — which
# is what makes the model-choice loop run.
_DRIVER_DONE_STATE = r'''
import importlib.util, os, sys
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib
spec = importlib.util.spec_from_file_location("w", os.environ["IGOS_WELCOMER_SRC"])
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
w._gpu_detection_record = lambda *a, **k: {
    "version": w._GPU_RECORD_VERSION, "vendor": "nvidia",
    "upgrade_engine": "cuda", "upgrade_outranks_shipped": True}
w._package_is_installed = lambda name: name == "nvidia"
w._intergen_is_set_up = lambda *a, **k: False
w._model_offer = lambda *a, **k: {
    "tiers": [1, 2], "download_bytes": {"1": 2300000000, "2": 6100000000}}
w._qwen_attribution = lambda *a, **k: None
w._probe_download_sources = lambda *a, **k: type("P", (), {"cause": None})()
Adw.init()
'''


class ThePageBuildsAfterTheDriverLeg(unittest.TestCase):
    """(1) The page whose construction crashed on the laptop."""

    def test_the_meet_intergen_page_builds_with_the_driver_installed(self):
        code = _DRIVER_DONE_STATE + r'''
class A(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.intergenos.test.driverdone",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
    def do_activate(self):
        try:
            self._activate()
        except Exception:
            # This harness must not linger the way the old app did.
            import traceback
            traceback.print_exc()
            self.quit()
            sys.exit(1)
    def _activate(self):
        win = Adw.ApplicationWindow(application=self)
        win.set_default_size(760, 720)
        page = w.build_intergen_page()
        win.set_content(page)
        win.present()
        # The heading stays first, the driver-done note and the setup card
        # directly under it (the placement the reorder exists to produce).
        def find_page_box(widget):
            if isinstance(widget, Gtk.Box):
                first = widget.get_first_child()
                if isinstance(first, Gtk.Label) and first.get_text() == "Meet InterGen":
                    return widget
            child = widget.get_first_child()
            while child is not None:
                found = find_page_box(child)
                if found is not None:
                    return found
                child = child.get_next_sibling()
            return None
        box = find_page_box(page)
        labels = []
        child = box.get_first_child() if box is not None else None
        while child is not None and len(labels) < 3:
            if isinstance(child, Gtk.Label):
                labels.append(child.get_text())
            elif isinstance(child, Gtk.Box):
                inner = child.get_first_child()
                labels.append(inner.get_text() if isinstance(inner, Gtk.Label) else type(inner).__name__)
            else:
                labels.append(type(child).__name__)
            child = child.get_next_sibling()
        print("ORDER " + " | ".join(labels), flush=True)
        GLib.timeout_add(300, self.quit)
sys.exit(A().run([]))
'''
        r = _run_in_display(code)
        self.assertNotIn("TypeError", r.stderr,
                         f"the page crashed while being built:\n{r.stderr[-3000:]}")
        self.assertEqual(
            r.returncode, 0,
            "building the Meet InterGen page with the driver installed "
            f"failed (status {r.returncode}):\n{r.stderr[-3000:]}")
        order = next((ln for ln in r.stdout.splitlines()
                      if ln.startswith("ORDER ")), "")
        parts = [p.strip() for p in order[len("ORDER "):].split("|")]
        self.assertEqual(parts[0], "Meet InterGen", order)
        self.assertTrue(parts[1].startswith("Your graphics driver is installed"),
                        "the driver-done note is not directly under the "
                        f"heading: {order!r}")
        self.assertEqual(parts[2], "Ready to meet him? Set InterGen up now",
                         "the setup card is not directly under the note: "
                         f"{order!r}")

    def test_the_model_choice_loop_leaves_the_heading_name_alone(self):
        """The defect, named: the loop must not bind `title`."""
        src = SCRIPT.read_text(encoding="utf-8")
        body = src[src.index("def build_intergen_page"):]
        body = body[:body.index("\ndef ", 10)]
        self.assertNotIn("title, note = _tier_label", body,
                         "the model-choice loop still rebinds the heading's "
                         "name")
        self.assertIn("reorder_child_after(done_note, title)", body)


class ACrashIsNotACompletedRun(unittest.TestCase):
    """(2) The exit status tells the launcher the truth."""

    def test_a_page_that_cannot_be_built_exits_non_zero(self):
        code = r'''
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("w", os.environ["IGOS_WELCOMER_SRC"])
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
def boom():
    raise TypeError("argument sibling: Expected Gtk.Widget, but got str")
w.build_intergen_page = boom
w.main()
'''
        r = _run_in_display(code)
        self.assertIsNotNone(
            r.returncode,
            "the Welcomer neither showed a window nor exited: the crashed "
            "instance lingers, and every later launch re-activates it and "
            "crashes it again — the state the laptop's journal showed, four "
            f"tracebacks under one process id:\n{r.stderr[-2000:]}")
        self.assertNotEqual(
            r.returncode, 0,
            "the Welcomer exited 0 after failing to build its window — the "
            "launcher would record this run as completed")
        self.assertIn("TypeError", r.stderr,
                      "the cause no longer reaches the journal")

    def test_a_run_that_builds_its_window_still_exits_zero(self):
        """The fix must not turn every run into a failure."""
        code = _DRIVER_DONE_STATE + r'''
w.apply_theme = lambda *a, **k: None
w.apply_prompt = lambda *a, **k: None
w.get_enabled_extensions = lambda: set()
w.set_enabled_extensions = lambda *a, **k: None
real_present = Adw.ApplicationWindow.present
def present_then_quit(self):
    real_present(self)
    GLib.timeout_add(300, self.get_application().quit)
Adw.ApplicationWindow.present = present_then_quit
w.main()
'''
        r = _run_in_display(code)
        self.assertEqual(r.returncode, 0,
                         f"a clean run exits {r.returncode}:\n{r.stderr[-2000:]}")

    def test_the_launcher_keys_on_that_status(self):
        """The wrapper marks done only on exit 0 — already the contract; this
        pins it so the two halves cannot drift apart."""
        build_sh = (REPO / "packages" / "desktop" / "intergen-welcome"
                    / "build.sh").read_text(encoding="utf-8")
        self.assertIn('if [ "${rc}" -eq 0 ] ; then', build_sh)
        self.assertIn('touch "${done_marker}"', build_sh)


class TheTerminalHandleMeansTheCommand(unittest.TestCase):
    """(3) The process the page watches lives as long as the install."""

    def _terminal_argv(self):
        recorded = []
        real = welcome.subprocess.Popen

        class _P:
            def poll(self):
                return 0

        def fake_popen(argv, *a, **k):
            recorded.append(list(argv))
            return _P()

        welcome.subprocess.Popen = fake_popen
        try:
            welcome._open_terminal_running("true")
        finally:
            welcome.subprocess.Popen = real
        self.assertTrue(recorded, "no terminal was started")
        return recorded[0]

    def test_gnome_terminal_is_asked_to_wait_for_the_command(self):
        argv = self._terminal_argv()
        self.assertEqual(argv[0], "gnome-terminal")
        self.assertIn("--wait", argv)
        self.assertLess(argv.index("--wait"), argv.index("--"),
                        "--wait sits after the separator, where it is an "
                        "argument to the command instead of to the terminal")

    def test_the_shipped_terminal_supports_wait(self):
        """Asked of the real binary when one is present: the option this fix
        depends on exists in the gnome-terminal this product ships."""
        exe = shutil.which("gnome-terminal")
        if exe is None:
            self.skipTest("gnome-terminal is not installed on this machine")
        r = subprocess.run([exe, "--help-all"], capture_output=True,
                           text=True, timeout=30)
        self.assertIn("--wait", r.stdout,
                      "this gnome-terminal has no --wait option; the outcome "
                      "check would report the window closed at once")


class TheReturnPromiseIsMadeOnEverySurface(unittest.TestCase):
    """(4) One sentence, three places, including the terminal's last line."""

    def _nvidia_offers(self):
        return welcome._gpu_offers(
            {"version": welcome._GPU_RECORD_VERSION, "vendor": "nvidia",
             "upgrade_engine": "cuda", "upgrade_outranks_shipped": False},
            probe=lambda _n: False)

    def test_the_closing_line_says_the_welcomer_returns(self):
        note = welcome._closing_note(["nvidia_driver"], self._nvidia_offers())
        self.assertIn("REBOOT REQUIRED", note)
        self.assertIn("Welcomer again after the reboot", note)
        self.assertIn("sudo reboot", note)

    def test_all_three_surfaces_carry_the_same_sentence(self):
        offers = self._nvidia_offers()
        sentence = welcome._WELCOMER_RETURNS_AFTER_REBOOT
        self.assertIn(sentence, welcome._install_notice(["nvidia_driver"], offers))
        self.assertIn(sentence, welcome._install_outcome(
            ["nvidia_driver"], offers, probe=lambda _n: True)["message"])
        self.assertIn(sentence, welcome._closing_note(["nvidia_driver"], offers))

    def test_the_sentence_survives_the_terminal_script(self):
        """Executed through the same shell text the terminal runs — the
        sentence contains an apostrophe, and the script quotes it."""
        src = SCRIPT.read_text()
        start = src.index("    note = ''")
        end = src.index("for argv in (", start)
        literal = "\n".join(ln[4:] if ln.startswith("    ") else ln
                            for ln in src[start:end].splitlines())
        note = welcome._closing_note(["nvidia_driver"], self._nvidia_offers())
        ns = {"command": "true", "closing_note": note}
        exec(literal, ns)
        script = ns["script"].replace(
            'read -r -p "Press Enter to close this window."; ', "")
        r = subprocess.run(["bash", "-c", script], capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        last = [ln for ln in r.stdout.splitlines() if ln.strip()][-1]
        self.assertIn("Welcomer again after the reboot", last)
        self.assertIn("sudo reboot", last)

    def test_a_selection_needing_no_reboot_makes_no_such_promise(self):
        offers = self._nvidia_offers()
        self.assertNotIn("Welcomer again",
                         welcome._closing_note(["compute_engine"], offers))


class TheToolkitIsNamedAndAskedAbout(unittest.TestCase):
    """(5) The CUDA toolkit is part of what is installed AND what is checked."""

    def test_the_engine_offer_installs_the_toolkit_by_name_first(self):
        offers = welcome._gpu_offers(
            {"version": welcome._GPU_RECORD_VERSION, "vendor": "nvidia",
             "upgrade_engine": "cuda", "upgrade_outranks_shipped": False},
            probe=lambda _n: False)
        engine = next(o for o in offers if o["key"] == "compute_engine")
        self.assertEqual(engine["packages"][0], "cuda-toolkit")
        self.assertIn("llama-cpp-cuda", engine["packages"])
        command = welcome._gpu_install_command(["nvidia_driver", "compute_engine"], offers)
        self.assertLess(command.index("nvidia"), command.index("cuda-toolkit"))
        self.assertLess(command.index("cuda-toolkit"), command.index("llama-cpp-cuda"))
        self.assertEqual(command.count("pkm install"), 1)

    def _probe_with_output(self, stdout):
        real = welcome.subprocess.run

        class _R:
            returncode = 0
            stderr = ""

        _R.stdout = stdout
        welcome.subprocess.run = lambda *a, **k: _R()
        try:
            return welcome._package_is_installed("cuda-toolkit")
        finally:
            welcome.subprocess.run = real

    def test_an_installer_package_whose_download_has_not_run_is_not_installed(self):
        """The laptop's exact state: an install record, no payload."""
        self.assertIs(self._probe_with_output(
            "  cuda-toolkit 13.3.1-3\n"
            "  install_date        : 2026-09-02T21:17:52+00:00\n"
            "  install_method      : archive\n"
            "  payload             : not installed — this package is the "
            "installer for a vendor download that has not run yet. "
            "Run: sudo pkm install cuda-toolkit\n"), False)

    def test_a_downloaded_payload_still_reads_as_installed(self):
        self.assertIs(self._probe_with_output(
            "  cuda-toolkit 13.3.1-3\n"
            "  payload_version     : 13.3.1\n"
            "  install_date        : 2026-09-02T21:17:52+00:00\n"
            "  install_method      : helper\n"), True)

    def test_an_ordinary_package_is_unaffected(self):
        self.assertIs(self._probe_with_output(
            "  nvidia 580.159.04-1\n"
            "  install_date        : 2026-09-02T21:16:20+00:00\n"), True)


if __name__ == "__main__":
    unittest.main()
