# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""No shipped autostart entry may carry a condition nothing will honour.

WHAT THIS GUARDS, and how it was measured.

`/etc/xdg/autostart/*.desktop` entries may carry `AutostartCondition=`, a key
that gates whether the entry runs — for example on a GSettings value the user
controls. GNOME's own session manager used to read it. GNOME 49 removed that
machinery: upstream's NEWS for 49.beta records that "gnome-session's builtin
service manager has been completely removed" along with "various .desktop and
.session file keys that were used only by the builtin service manager", and
gnome-session 49.2's source contains no autostart-condition helper at all.

On a systemd-managed session the entries are converted by
systemd-xdg-autostart-generator instead, and that generator delegates
`AutostartCondition=` to a separate binary, `gnome-systemd-autostart-condition`.
When that binary is absent the generator logs "ExecCondition executable
gnome-systemd-autostart-condition not found, unit will not be started
automatically" — and then emits the unit ANYWAY, with the condition demoted to
a comment and the unit symlinked into xdg-desktop-autostart.target.wants like
any other. Measured on systemd 259.1 with a constructed entry: the unit is
produced, it is wanted by the target, and its only remaining ExecCondition is
the desktop-list one, which exits 0. So the entry RUNS and its condition is
silently ignored. The generator's own message says the opposite of what its
output does.

The one thing that saves an entry is an unrelated key: an entry carrying
`X-GNOME-Autostart-Phase=` is handled separately by the generator ("GNOME
startup phases are handled separately"), which either skips it outright or
marks it NotShowIn=GNOME. Measured three ways in one controlled run — no phase
key produced a runnable unit, `Phase=Application` and `Phase=Initialization`
each produced none.

So the rule this gate enforces is exact: an entry that carries
`AutostartCondition=` and does NOT carry `X-GNOME-Autostart-Phase=` will run
with its condition silently ignored, and that is a setting the user believes
they control and do not.

The gate also refuses to keep enforcing a premise that has changed: if
`gnome-systemd-autostart-condition` is present under the scanned root, the
conditions ARE honoured, and the gate says so rather than continuing to fail
entries for a reason that no longer holds.
"""

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "check-autostart-condition-honoured.py"
ALLOWLIST = REPO_ROOT / "config" / "autostart-condition-allowlist.txt"
SQUASHFS = REPO_ROOT / "scripts" / "build-squashfs.sh"

ENTRY = """[Desktop Entry]
Type=Application
Name={name}
Exec=/bin/true
OnlyShowIn=GNOME;
NoDisplay=true
"""


def write_entry(root: Path, name: str, condition=None, phase=None):
    d = root / "etc/xdg/autostart"
    d.mkdir(parents=True, exist_ok=True)
    text = ENTRY.format(name=name)
    if condition:
        text += f"AutostartCondition={condition}\n"
    if phase:
        text += f"X-GNOME-Autostart-Phase={phase}\n"
    (d / f"{name}.desktop").write_text(text)


def run_gate(root: Path, allowlist: Path, *extra):
    return subprocess.run(
        [sys.executable, str(GATE), "--root", str(root),
         "--allowlist", str(allowlist), *extra],
        capture_output=True, text=True)


class GateBehaviour(unittest.TestCase):

    def setUp(self):
        self._td = TemporaryDirectory()
        self.base = Path(self._td.name)
        self.root = self.base / "root"
        (self.root / "etc/xdg/autostart").mkdir(parents=True)
        self.allowlist = self.base / "allow.txt"
        self.allowlist.write_text("# empty\n")

    def tearDown(self):
        self._td.cleanup()

    def test_condition_without_a_phase_key_is_a_violation(self):
        write_entry(self.root, "silently-ignored",
                    condition="GSettings org.gnome.desktop.sound event-sounds")
        r = run_gate(self.root, self.allowlist)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("silently-ignored", r.stdout)
        self.assertIn("FAIL", r.stdout)

    def test_condition_with_a_phase_key_passes_and_is_still_named(self):
        """The phase key is what stops the entry running, and it is unrelated
        to the condition. That the entry is safe by accident is worth saying
        out loud, not passing over."""
        write_entry(self.root, "phase-guarded",
                    condition="GSettings org.gnome.desktop.sound event-sounds",
                    phase="Application")
        r = run_gate(self.root, self.allowlist)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("phase-guarded", r.stdout)

    def test_entry_without_a_condition_is_not_the_gate_s_business(self):
        write_entry(self.root, "plain")
        r = run_gate(self.root, self.allowlist)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_reasoned_allowlist_entry_clears_a_violation(self):
        write_entry(self.root, "known-exception",
                    condition="GSettings org.gnome.desktop.sound event-sounds")
        self.allowlist.write_text(
            "known-exception.desktop\tdeliberate: reviewed on <date>, the "
            "condition is decorative here\n")
        r = run_gate(self.root, self.allowlist)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_an_allowlist_entry_without_a_reason_is_refused(self):
        self.allowlist.write_text("known-exception.desktop\n")
        write_entry(self.root, "known-exception",
                    condition="GSettings org.gnome.desktop.sound event-sounds")
        r = run_gate(self.root, self.allowlist)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_zero_entries_scanned_is_a_loud_failure_not_a_green(self):
        """A gate that validated nothing must never print green — it cannot
        tell an empty corpus from a wrong path."""
        r = run_gate(self.root, self.allowlist)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("no autostart entries", (r.stdout + r.stderr).lower())

    def test_missing_autostart_directory_is_a_setup_error(self):
        empty = self.base / "no-such-root"
        empty.mkdir()
        r = run_gate(empty, self.allowlist)
        self.assertEqual(r.returncode, 2)

    def test_the_premise_is_rechecked_not_assumed(self):
        """If the helper is present under the root, conditions ARE honoured
        and the class does not exist. The gate must say so rather than keep
        failing entries for a reason that stopped being true."""
        helper = self.root / "usr/libexec/gnome-systemd-autostart-condition"
        helper.parent.mkdir(parents=True, exist_ok=True)
        helper.write_text("#!/bin/sh\nexit 0\n")
        helper.chmod(0o755)
        write_entry(self.root, "would-have-failed",
                    condition="GSettings org.gnome.desktop.sound event-sounds")
        r = run_gate(self.root, self.allowlist)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("gnome-systemd-autostart-condition", r.stdout)


class ShippedAllowlist(unittest.TestCase):

    def test_the_allowlist_exists_and_every_entry_carries_a_reason(self):
        self.assertTrue(ALLOWLIST.is_file(), f"{ALLOWLIST} missing")
        import re
        for i, raw in enumerate(ALLOWLIST.read_text().splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = re.split(r"\t+| {2,}", line, maxsplit=1)
            self.assertEqual(len(parts), 2, f"line {i} has no reason: {raw!r}")
            self.assertTrue(parts[1].strip(), f"line {i} reason empty: {raw!r}")


class PipelineWiring(unittest.TestCase):

    def test_the_squashfs_build_runs_the_gate_and_dies_on_failure(self):
        text = SQUASHFS.read_text()
        self.assertIn("check-autostart-condition-honoured.py", text,
                      "the gate is not wired into the squashfs build, so "
                      "nothing runs it")
        idx = text.index("check-autostart-condition-honoured.py")
        window = text[max(0, idx - 400):idx + 1400]
        self.assertIn("die ", window,
                      "a failing autostart-condition gate must stop the build")


if __name__ == "__main__":
    unittest.main()
