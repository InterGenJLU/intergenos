# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A gated save offer must name the file the USER named.

Reported turn: "Write a bash script to monitor CPU usage … and save it as
~/scripts/monitor.sh" was said to surface no offer. Fired against a live daemon
the offer IS staged — but it reads "Save the draft to /home/<user>/script.sh".
The explicitly named target was dropped and a generated filename in the home
directory put in its place, with `default_applied` left unset so nothing in the
trace showed a substitution had happened.

The save-the-draft resolver only ever recognised a bare `as|called|named
<name.ext>`; a path (`~/scripts/monitor.sh`) and the far more common `save it to
<path>` matched nothing at all, so every one of those turns silently defaulted.

That is worse than a missing offer. The write still lands through the consent
gate — but a gate can only confirm the path the offer put in front of the user,
so a substituted target converts an informed confirmation into a blind one.
These fixtures pin the target end of the offer in both directions: a named
target is honoured verbatim, and a clause that names none still defaults — now
declaring that it did.
"""
import os
import unittest

from intergen.router import detect_file_lifecycle_intent as detect

HOME = "/home/testuser"
DRAFT = "```bash\n#!/bin/bash\necho hi\n```"


def _spec(user_input, draft=DRAFT):
    return detect(user_input, prior_draft=draft, home=HOME)


class NamedTargetIsHonouredTests(unittest.TestCase):

    def test_the_reported_turn_saves_where_the_user_said(self):
        spec = _spec("Write a bash script to monitor CPU usage and alert me if "
                     "it goes above 90 percent, and save it as "
                     "~/scripts/monitor.sh")
        self.assertEqual(spec["args"]["path"],
                         os.path.join(HOME, "scripts/monitor.sh"))
        self.assertIsNone(spec["default_applied"],
                          "the user named the target — nothing was defaulted")

    def test_the_offer_line_shows_the_named_target(self):
        """The label is what the user reads before confirming, so it is the
        surface the substitution was actually hidden on."""
        spec = _spec("write a note and save it to ~/Documents/note.md")
        self.assertEqual(spec["label"],
                         f"Save the draft to {HOME}/Documents/note.md")
        self.assertEqual(spec["display"], f"{HOME}/Documents/note.md")

    def test_every_preposition_a_save_clause_uses(self):
        for clause in ("save it to ~/out/x.txt", "save it as ~/out/x.txt",
                       "save it in ~/out/x.txt", "save it into ~/out/x.txt",
                       "save it at ~/out/x.txt", "save it under ~/out/x.txt"):
            with self.subTest(clause=clause):
                self.assertEqual(_spec(f"write a note and {clause}")
                                 ["args"]["path"], f"{HOME}/out/x.txt")

    def test_absolute_paths_are_kept_verbatim(self):
        """A privileged destination is still the destination the user named;
        whether the write is allowed is the consent gate's call, not the
        resolver's, and silently rewriting the path would take that decision
        away from both."""
        spec = _spec("Write a systemd unit file for a python monitoring "
                     "service, and save it to /etc/systemd/system/monitor.service")
        self.assertEqual(spec["args"]["path"],
                         "/etc/systemd/system/monitor.service")

    def test_dotfile_and_nested_targets_resolve(self):
        for named, expected in (
            ("~/.git-commit-template.txt", f"{HOME}/.git-commit-template.txt"),
            ("~/scripts/email_regex.txt", f"{HOME}/scripts/email_regex.txt"),
            ("notes.md", f"{HOME}/notes.md"),
            ("reports/q3.csv", f"{HOME}/reports/q3.csv"),
        ):
            with self.subTest(named=named):
                self.assertEqual(
                    _spec(f"write it up and save it to {named}")
                    ["args"]["path"], expected)

    def test_a_traversal_segment_is_resolved_before_it_is_shown(self):
        """The offer line is the only thing the consent gate can confirm, so it
        must show where the write actually lands, not an unresolved string."""
        spec = _spec("write a note and save it to ../shared/x.txt")
        self.assertEqual(spec["args"]["path"], "/home/shared/x.txt")
        self.assertEqual(spec["display"], spec["args"]["path"])

    def test_the_draft_content_travels_with_the_named_target(self):
        spec = _spec("write a script and save it to ~/bin/go.sh")
        self.assertEqual(spec["args"]["content"], DRAFT)
        self.assertEqual(spec["tool"], "write_file")


class DefaultIsDeclaredTests(unittest.TestCase):
    """A default is fine. A default that presents itself as the user's choice
    is not — `default_applied` is the field that keeps them distinguishable."""

    def test_no_named_target_still_defaults(self):
        spec = _spec("write a hello-world python script and save it")
        self.assertEqual(spec["args"]["path"], os.path.join(HOME, "script.py"))

    def test_the_default_is_declared_on_the_spec(self):
        for query in ("write a hello-world python script and save it",
                      "hey can you whip up a bash script and save it",
                      "compose a poem, then save it"):
            with self.subTest(query=query):
                self.assertEqual(
                    _spec(query)["default_applied"], "home",
                    "a substituted location must be visible on the trace, "
                    "not indistinguishable from a target the user chose")

    def test_a_phrase_naming_no_file_does_not_become_a_target(self):
        """"save it to my crontab" names a destination that is not a file
        path — it must not be captured as one."""
        spec = _spec("Draft a cron entry that runs my backup script every "
                     "night at 2am, and save it to my crontab")
        self.assertEqual(spec["default_applied"], "home")
        self.assertTrue(spec["args"]["path"].startswith(HOME + "/"))


class TwoTurnSaveRegressionTests(unittest.TestCase):
    """The pre-existing two-turn save forms must resolve exactly as before."""

    def test_save_it_as_name_in_my_home_folder(self):
        spec = detect("save it as temp.py in my home folder",
                      prior_draft="SCRIPT", home=HOME)
        self.assertEqual(spec["args"]["path"], os.path.join(HOME, "temp.py"))
        self.assertEqual(spec["args"]["content"], "SCRIPT")

    def test_save_this_text_to_a_file_called_notes(self):
        spec = detect("save this text to a file called notes.txt",
                      prior_draft="THE TEXT", home=HOME)
        self.assertEqual(spec["args"]["path"], os.path.join(HOME, "notes.txt"))
        self.assertEqual(spec["args"]["content"], "THE TEXT")

    def test_no_draft_stages_nothing(self):
        self.assertIsNone(detect("save it to ~/x.txt", prior_draft=None,
                                 home=HOME))


if __name__ == "__main__":
    unittest.main()
