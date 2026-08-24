# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The shipped cron directories say, on the machine, that nothing reads them.

WHY THIS EXISTS. The fcron package ships /etc/cron.hourly, /etc/cron.daily,
/etc/cron.weekly and /etc/cron.monthly, and a system fcrontab that runs
run-parts over all four. The unit that would run that fcrontab ships DISABLED,
by a written decision in the preset file, and it has no socket or D-Bus
activation - so a script dropped into any of those four directories never
runs, and nothing anywhere on the installed system says so.

That is a silent no-op, which is the failure class this project refuses: four
directories that look exactly like the place where scheduled work goes, on a
system where scheduled work put there does not happen. The decision to ship
the scheduler off is not in question and is not changed here. What is fixed is
that the machine now tells the person the consequence, next to the thing it is
a consequence of, with the one command that changes it.

WHAT THIS MEASURES, AND WHY IT CANNOT GO STALE. The README's claim is checked
against the tree that makes it true, not against itself:

  * the four directories and the system fcrontab that references them are
    still shipped by the recipe - otherwise the README describes nothing;
  * the preset file still resolves the scheduler to DISABLED - if someone ever
    ships it enabled, the README becomes false and this test fails, which is
    the point of deriving the claim rather than pinning a sentence;
  * the README names that same unit, so the two cannot drift apart.
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FCRON_DIR = REPO_ROOT / "packages" / "base" / "fcron"
FCRON_BUILD = FCRON_DIR / "build.sh"
FCRON_YML = FCRON_DIR / "package.yml"
README_REL = "etc/cron.README"
README = FCRON_DIR / "files" / README_REL

PRESET_DIR = (REPO_ROOT / "packages" / "core" / "intergenos-base-files"
              / "files" / "usr" / "lib" / "systemd" / "system-preset")

CRON_DIRS = ("hourly", "daily", "weekly", "monthly")
UNIT = "fcron.service"


def _preset_verb(unit):
    """What the shipped preset policy resolves a unit to.

    Read the way systemd reads it: files in lexical order, first matching line
    wins, and a catch-all counts. Returning None would make every assertion
    below vacuous, so the caller treats None as a failure rather than a pass.
    """
    for path in sorted(PRESET_DIR.glob("*.preset")):
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 2:
                continue
            verb, pattern = parts
            if verb not in ("enable", "disable"):
                continue
            if pattern == unit or pattern == "*":
                return verb
    return None


class CronDirectoriesAreHonest(unittest.TestCase):

    def setUp(self):
        self.build = FCRON_BUILD.read_text()

    # ---------- the premise, from the recipe and the preset ----------

    def test_the_recipe_still_ships_the_four_directories(self):
        self.assertRegex(
            self.build,
            r"install -dm\d+ \"\$\{DESTDIR\}\"/etc/cron\.\{hourly,daily,weekly,monthly\}",
            "the recipe no longer stages the four cron directories; if they are "
            "gone the README beside them describes nothing and should go too")

    def test_the_recipe_still_ships_the_system_fcrontab_over_them(self):
        for name in CRON_DIRS:
            with self.subTest(dir=name):
                self.assertIn(f"run-parts /etc/cron.{name}", self.build,
                              "the system fcrontab no longer runs this directory")

    def test_the_scheduler_still_ships_disabled(self):
        verb = _preset_verb(UNIT)
        self.assertIsNotNone(
            verb, f"no preset line resolves {UNIT} at all — this suite cannot "
                  "tell what the machine ships and must not report that it can")
        self.assertEqual(
            verb, "disable",
            f"{UNIT} now resolves to {verb!r}. The README says nothing reads the "
            "cron directories until it is enabled; if it ships enabled that "
            "sentence is false and the README has to change with it")

    # ---------- the README ----------

    def test_the_readme_ships(self):
        self.assertTrue(
            README.is_file(),
            f"no {README_REL} in the package: the four directories arrive on the "
            "machine with nothing saying that nothing reads them")

    def test_the_readme_names_the_unit_the_preset_names(self):
        self.assertIn(UNIT, README.read_text(),
                      "the README does not name the unit that has to be enabled, "
                      "so it states a problem without naming its cause")

    def test_the_readme_gives_the_command_that_changes_it(self):
        text = README.read_text()
        self.assertRegex(
            text, rf"systemctl enable --now {re.escape(UNIT)}",
            "the README does not carry the one command that makes the "
            "directories work; telling someone their scripts do not run without "
            "telling them how to change it is half a message")

    def test_the_readme_names_every_directory_it_speaks_for(self):
        text = README.read_text()
        for name in CRON_DIRS:
            with self.subTest(dir=name):
                self.assertIn(f"/etc/cron.{name}", text,
                              "the README does not name this directory, so a "
                              "reader cannot tell whether it is covered")

    def test_the_readme_schedule_matches_the_shipped_fcrontab(self):
        """The times in the README are read out of the recipe, not trusted.

        The README tells the reader when each directory runs once the unit is
        on. Those are four concrete times, and a hardcoded sentence about them
        is exactly the kind of claim that goes quietly false when the shipped
        fcrontab is edited - the class this whole cut is about. So each line of
        the system fcrontab is parsed and its time is required to appear in the
        README.
        """
        rows = re.findall(
            r"^&bootrun (\d+) (\S+) (\S+) (\S+) (\S+) root run-parts /etc/cron\.(\w+)$",
            self.build, re.MULTILINE)
        self.assertEqual(
            len(rows), len(CRON_DIRS),
            f"parsed {len(rows)} system-fcrontab rows, expected one per "
            f"directory - the parser, not the README, is what failed here")
        text = README.read_text()
        for minute, hour, _dom, _mon, _dow, name in rows:
            with self.subTest(dir=name):
                if hour == "*":
                    needle = f"{int(minute)} minute"
                else:
                    needle = f"{int(hour):02d}:{int(minute):02d}"
                self.assertIn(
                    needle, text,
                    f"the README does not state {needle} for /etc/cron.{name}; "
                    "the shipped fcrontab says it and the two have drifted")

    def test_the_readme_says_where_the_decision_is_written(self):
        """The user owns the machine; point at the file that decided this."""
        self.assertIn(
            "system-preset", README.read_text(),
            "the README does not say where the ship-disabled decision is "
            "recorded, so a reader cannot audit or change it at the source")

    # ---------- it has to arrive, and be checked on arrival ----------

    def test_the_readme_is_in_the_overlay_the_builder_deploys(self):
        """files/ is copied into DESTDIR by igos-build/builder.py, mode intact.

        Asserted against the builder rather than assumed: the same overlay is
        how this package already ships its sysusers.d entry, which nothing in
        build.sh installs either.
        """
        builder = (REPO_ROOT / "igos-build" / "builder.py").read_text()
        self.assertIn('files_dir = pkg.template_path.parent / "files"', builder,
                      "the builder no longer deploys a package's files/ tree, so "
                      "a README placed there would reach no installed system")
        self.assertTrue(
            README.is_file() and README.parent.parent.name == "files",
            f"{README_REL} is not under the package's files/ overlay")

    def test_the_readme_is_registered_for_verification(self):
        self.assertIn(f"/{README_REL}", FCRON_YML.read_text(),
                      "the README is not in verify_paths, so it can go missing "
                      "from an install without anything saying so")

    def test_the_readme_is_not_executable(self):
        """It sits beside run-parts targets, and run-parts runs what is +x."""
        mode = README.stat().st_mode & 0o777
        self.assertEqual(
            mode, 0o644,
            f"{README_REL} is mode {mode:o}; the overlay copies the mode through "
            "to the installed system, and anything executable near the cron "
            "directories is a script waiting to be run")


if __name__ == "__main__":
    unittest.main()
