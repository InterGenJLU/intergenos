# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Launch-monitor extension — the game-launch setting and the daemon calls.

The extension is the half of the game-launch pause that runs inside the desktop
compositor, where no Python test can execute it. What CAN be checked without a
compositor is the contract it has to hold, and each check here stands for a
failure that would reach a user:

  * the declared setting must exist, be one of exactly three values, and default
    to pausing — the decided behaviour;
  * the settings schema must actually compile, or the extension has no settings
    at all at runtime;
  * the extension must call the daemon ASYNCHRONOUSLY — a synchronous call would
    freeze the whole desktop for as long as a model takes to stop or load;
  * it must not auto-start InterGen, or a machine that never runs the assistant
    would launch it purely to tell it to stop;
  * it must call BOTH edges, on the same interface the daemon exports;
  * it must release its pause when the extension is disabled, or the assistant
    stays stopped with nothing left watching to release it;
  * the preferences window must expose the setting, or "declared once by the
    user" is not true.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXT_DIR = (REPO / "assets" / "intergenos-launch-monitor"
           / "intergenos-launch-monitor@intergenos.org")
SCHEMA = (EXT_DIR / "schemas"
          / "org.gnome.shell.extensions.intergenos-launch-monitor.gschema.xml")
EXTENSION_JS = EXT_DIR / "extension.js"
PREFS_JS = EXT_DIR / "prefs.js"

SETTING = "game-launch-intergen"
CHOICES = {"pause", "keep", "ask"}


class TestGameLaunchSetting(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCHEMA.exists(), f"schema missing at {SCHEMA}")
        self.root = ET.parse(SCHEMA).getroot()
        self.key = None
        for key in self.root.iter("key"):
            if key.get("name") == SETTING:
                self.key = key
        self.assertIsNotNone(self.key, f"{SETTING} is not declared in the schema")

    def test_it_is_a_string_key(self):
        self.assertEqual(self.key.get("type"), "s")

    def test_it_offers_exactly_the_three_declared_values(self):
        got = {c.get("value") for c in self.key.iter("choice")}
        self.assertEqual(got, CHOICES)

    def test_it_defaults_to_pausing(self):
        default = self.key.find("default")
        self.assertIsNotNone(default)
        self.assertEqual((default.text or "").strip(), "'pause'")

    def test_it_explains_itself_to_the_user(self):
        for tag in ("summary", "description"):
            node = self.key.find(tag)
            self.assertIsNotNone(node, f"{SETTING} has no <{tag}>")
            self.assertTrue((node.text or "").strip(),
                            f"{SETTING} has an empty <{tag}>")

    def test_the_monitor_picker_is_still_declared(self):
        """The pause setting was added beside the placement setting, not over
        it — this is the guard against replacing one behaviour with the other."""
        names = {k.get("name") for k in self.root.iter("key")}
        self.assertIn("launch-monitor", names)
        self.assertIn("game-wm-class-prefixes", names)

    @unittest.skipIf(shutil.which("glib-compile-schemas") is None,
                     "glib-compile-schemas not installed")
    def test_the_schema_compiles_strictly(self):
        """The package build compiles this schema with --strict and fails the
        build if the compiled file is not produced; this is the same check,
        early enough to catch it while editing."""
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(SCHEMA, tmp)
            proc = subprocess.run(
                ["glib-compile-schemas", "--strict", tmp],
                capture_output=True, text=True, check=False)
            self.assertEqual(proc.returncode, 0,
                             f"schema did not compile: {proc.stderr}")
            self.assertTrue(Path(tmp, "gschemas.compiled").exists())


def _code_only(source: str) -> str:
    """The source with whole-line // comments removed.

    Every check below is about what the extension DOES, and this file's comments
    explain the same mechanisms in the same words — so searching the raw text
    would pass on the prose alone. That is not hypothetical: the first version of
    the auto-start check read the comment that explains the flag and kept passing
    after the flag itself was changed. Dropping comment lines is what makes these
    tests able to fail.
    """
    return "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("//"))


class TestExtensionDaemonCall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = _code_only(EXTENSION_JS.read_text(encoding="utf-8"))

    def test_the_comment_stripper_actually_strips(self):
        """Guard on the guard: if _code_only ever stopped removing comments,
        every check in this class would quietly go back to reading prose."""
        stripped = _code_only("// gone\nconst kept = 1; // trailing kept\n")
        self.assertNotIn("gone", stripped)
        self.assertIn("const kept = 1;", stripped)

    def test_it_calls_both_edges(self):
        self.assertIn("'PauseForGame'", self.code)
        self.assertIn("'ResumeAfterGame'", self.code)

    def test_it_targets_the_interface_the_daemon_exports(self):
        self.assertIn("com.intergenos.InterGen", self.code)
        self.assertIn("/com/intergenos/InterGen", self.code)

    def test_it_never_calls_the_daemon_synchronously(self):
        """A synchronous call from the compositor blocks the entire desktop for
        as long as the daemon takes to stop or load a model — seconds to
        minutes. The asynchronous form is the only acceptable one here."""
        self.assertNotIn("call_sync", self.code)
        self.assertIn("call_finish", self.code)

    def test_it_does_not_auto_start_intergen(self):
        """InterGen ships a D-Bus activation file, so a plain call would START
        the assistant in order to tell it to stop. Checked on the flag the call
        actually passes, not on any mention of it."""
        flags = set(re.findall(r"Gio\.DBusCallFlags\.(\w+)", self.code))
        self.assertEqual(flags, {"DO_NOT_AUTO_START"},
                         f"the daemon call passes {flags or 'no'} flags")

    def test_it_reads_the_declared_setting(self):
        self.assertIn(SETTING, self.code)
        for value in CHOICES:
            self.assertIn(f"'{value}'", self.code,
                          f"the extension never handles the {value!r} choice")

    def test_it_watches_the_exit_edge(self):
        self.assertIn("unmanaging", self.code)

    def test_disable_releases_what_it_placed(self):
        """A disabled extension must not leave InterGen paused: nothing would be
        left watching the game windows to release it."""
        body = self.code.split("disable()", 1)
        self.assertEqual(len(body), 2, "no disable() in the extension")
        # Up to the next method at class-body indentation.
        after = body[1]
        end = re.search(r"\n    _?[A-Za-z]+\(", after)
        disable_body = after[:end.start()] if end else after
        self.assertIn("ResumeAfterGame", disable_body,
                      "disable() does not release the pauses it placed")


class TestPreferencesExposeTheSetting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = _code_only(PREFS_JS.read_text(encoding="utf-8"))

    def test_the_setting_is_readable_and_writable_from_preferences(self):
        self.assertIn(f"get_string('{SETTING}')", self.code)
        self.assertIn(f"set_string('{SETTING}'", self.code)

    def test_all_three_choices_are_offered(self):
        for value in CHOICES:
            self.assertIn(f"'{value}'", self.code,
                          f"preferences never offer the {value!r} choice")

    def test_the_monitor_picker_is_still_there(self):
        self.assertIn("launch-monitor", self.code)


if __name__ == "__main__":
    unittest.main()
