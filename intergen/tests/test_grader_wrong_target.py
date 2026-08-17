# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A coherent, well-formed reply aimed at the WRONG target must not pass.

MEASURED RESIDUE (the "item 16" class): the judge re-calibration closed three
findings and left one standing — a reply that is fluent, correctly shaped and
about something else entirely still grades as a pass. Asked about one service,
the model answers confidently about another; every quality dimension the judge
reads is satisfied, because the reply IS coherent. Nothing in the instrument
binds the answer to the thing the turn was about.

This is an ASSERTION-SIDE check, not judge-prompt text: it is a structural
Gate-A fact about the turn, derived from data the run already carries, and a
judge cannot be prompted into reliably noticing it.

WHAT BINDS THE ANSWER TO THE TURN — the dispatch's own arguments. When a turn
dispatched a tool with a concrete target (a service name, a package, a path, the
subject of a shell command), the answer must NAME that target. The check derives
its targets per turn from `tool_calls`, so it carries no subject list of its own
and needs no corpus authoring: a cell that dispatches against a new service is
covered the day it is written.

The assertion requires the RIGHT target to be present rather than trying to
identify the wrong one. Naming some other subject is what makes these replies
feel coherent, but "the answer never mentions what the tool acted on" is the
sharp, vocabulary-free edge of the same failure.

Deliberately NOT produced (each would be a false failure):
  * turns with no dispatch, or a dispatch whose arguments carry no subject
    (`df -h` names no target — the command IS the subject);
  * argument keys that carry a verb rather than a subject (action / operation /
    mode / format), which name what was done, not what it was done to;
  * the code-owned honest fallbacks, which deliberately name nothing;
  * a reply that names the target by a stem the tool spelled longer
    ("sshd" answered as "SSH"), which is correct English about the right thing.
"""
from __future__ import annotations

import unittest

from intergen.tests.grader import grade_turn_trace


def _turn(text, tool_calls, trace=None):
    return {
        "response_text": text,
        "tool_calls": tool_calls,
        "trace": trace or [],
        "assertions": [],
    }


def _result(turn):
    for r in grade_turn_trace(turn):
        if r.type == "no_wrong_target":
            return r
    return None


class WrongTargetIsCaughtTests(unittest.TestCase):
    def test_the_measured_class_fails(self):
        # Asked about sshd; the answer is a fluent report about cups.
        t = _turn("The cups service is active and running normally.",
                  [{"name": "manage_services",
                    "arguments": {"action": "status", "name": "sshd"}}])
        r = _result(t)
        self.assertIsNotNone(r, "a dispatch with a target must be checked")
        self.assertFalse(r.passed)
        self.assertEqual(r.gate, "A", "this is a structural fact, not a nit")
        self.assertIn("sshd", r.value)

    def test_naming_the_target_passes(self):
        t = _turn("sshd is active and enabled at boot.",
                  [{"name": "manage_services",
                    "arguments": {"action": "status", "name": "sshd"}}])
        self.assertTrue(_result(t).passed)

    def test_a_stem_of_the_target_counts_as_naming_it(self):
        # The tool spells it sshd; English calls it SSH. Same subject.
        t = _turn("SSH is running and set to start at boot.",
                  [{"name": "manage_services",
                    "arguments": {"action": "status", "name": "sshd"}}])
        self.assertTrue(_result(t).passed)

    def test_an_ordinary_word_sharing_three_letters_does_not_name_the_target(self):
        # Measured by cross-review: "con" matched *confirm*, so a reply that
        # answers nothing passed the check written to catch exactly that.
        t = _turn("I could not confirm that from here.",
                  [{"name": "manage_services",
                    "arguments": {"action": "status", "name": "containerd"}}])
        self.assertFalse(_result(t).passed)

    def test_a_short_prefix_of_a_long_name_does_not_name_it(self):
        # "system" is a third of "systemd-resolved" and means something else.
        t = _turn("Your system clock is synchronised.",
                  [{"name": "manage_services",
                    "arguments": {"action": "status",
                                  "name": "systemd-resolved"}}])
        self.assertFalse(_result(t).passed)

    def test_a_stem_buried_inside_a_longer_word_does_not_name_the_target(self):
        # "net" inside "internet" — a substring, never a mention.
        t = _turn("Your internet connection looks fine.",
                  [{"name": "manage_services",
                    "arguments": {"action": "status",
                                  "name": "network-manager"}}])
        self.assertFalse(_result(t).passed)

    def test_a_longer_spelling_of_the_target_still_names_it(self):
        # The reverse direction stays accepted: tool said ssh, reply says sshd.
        t = _turn("sshd is running.",
                  [{"name": "manage_services",
                    "arguments": {"action": "status", "name": "ssh"}}])
        self.assertTrue(_result(t).passed)

    def test_the_shell_command_subject_is_the_target(self):
        t = _turn("Everything looks fine on the printer side.",
                  [{"name": "run_command",
                    "arguments": {"command": "systemctl status sshd"}}])
        r = _result(t)
        self.assertIsNotNone(r)
        self.assertFalse(r.passed)

    def test_an_option_value_is_not_mistaken_for_the_subject(self):
        # Measured by cross-review: dropping the flag but keeping its argument
        # made "today" the subject of `journalctl -u sshd --since today`, so a
        # correct answer about sshd was graded as a correctness failure.
        t = _turn("sshd logged three failed password attempts.",
                  [{"name": "run_command",
                    "arguments": {"command":
                                  "journalctl -u sshd --since today"}}])
        self.assertTrue(_result(t).passed)

    def test_an_attached_option_value_is_consumed_with_its_option(self):
        t = _turn("sshd logged three failed password attempts.",
                  [{"name": "run_command",
                    "arguments": {"command":
                                  "journalctl -u sshd --since=today"}}])
        self.assertTrue(_result(t).passed)

    def test_a_plain_trailing_token_still_wins_over_an_option_value(self):
        # The dominant shape must not regress: the plain token is the subject.
        t = _turn("Everything looks fine on the printer side.",
                  [{"name": "run_command",
                    "arguments": {"command": "systemctl --no-pager status sshd"}}])
        r = _result(t)
        self.assertIsNotNone(r)
        self.assertFalse(r.passed)

    def test_a_package_target_is_bound_too(self):
        t = _turn("vim is already installed at version 9.1.",
                  [{"name": "manage_packages",
                    "arguments": {"action": "check", "package": "htop"}}])
        self.assertFalse(_result(t).passed)

    def test_a_path_target_is_bound(self):
        t = _turn("Here are the contents of your hosts file.",
                  [{"name": "read_file",
                    "arguments": {"path": "/etc/fstab"}}])
        self.assertFalse(_result(t).passed)

    def test_naming_the_path_passes(self):
        t = _turn("/etc/fstab lists two mounts: root and home.",
                  [{"name": "read_file",
                    "arguments": {"path": "/etc/fstab"}}])
        self.assertTrue(_result(t).passed)


class NotProducedWhereItWouldBeWrongTests(unittest.TestCase):
    def test_no_dispatch_no_assertion(self):
        self.assertIsNone(_result(_turn("Some freeform answer.", [])))

    def test_a_command_with_no_subject_produces_no_assertion(self):
        # `df -h` names no target: the command is the subject. Requiring the
        # reply to say "df" would fail every correct disk answer.
        self.assertIsNone(_result(_turn(
            "You have 120 GB free on the root filesystem.",
            [{"name": "run_command", "arguments": {"command": "df -h"}}])))

    def test_verb_arguments_are_not_targets(self):
        # action/operation/mode/format say what was DONE, not what it was done
        # to. A reply need not contain the word "status".
        self.assertIsNone(_result(_turn(
            "Everything is up to date.",
            [{"name": "manage_packages",
              "arguments": {"action": "list", "format": "short"}}])))

    def test_the_honest_fallback_is_not_a_wrong_target(self):
        # The code-owned fallbacks name nothing BY DESIGN. Failing them here
        # would double-punish a turn the serving floor already handled.
        t = _turn("I didn't manage to put together a response that time. "
                  "Could you rephrase or give me a bit more detail about what "
                  "you need?",
                  [{"name": "manage_services",
                    "arguments": {"action": "status", "name": "sshd"}}])
        r = _result(t)
        self.assertTrue(r is None or r.passed)

    def test_an_empty_reply_is_not_graded_here(self):
        # Empty is its own failure elsewhere; this check would add noise.
        t = _turn("", [{"name": "read_file",
                        "arguments": {"path": "/etc/fstab"}}])
        r = _result(t)
        self.assertTrue(r is None or r.passed)

    def test_a_short_generic_argument_is_not_a_target(self):
        # Two-character values are not subjects; requiring them would fire on
        # flags and abbreviations.
        self.assertIsNone(_result(_turn(
            "Your network is up.",
            [{"name": "run_command", "arguments": {"command": "ip a"}}])))


class TargetExtractionIsDataDrivenTests(unittest.TestCase):
    """No subject list anywhere — the targets come from the turn's own data."""

    def test_a_service_never_seen_before_is_still_bound(self):
        t = _turn("The database is fine.",
                  [{"name": "manage_services",
                    "arguments": {"action": "status",
                                  "name": "quux-invented-daemon"}}])
        r = _result(t)
        self.assertIsNotNone(r)
        self.assertFalse(r.passed)
        self.assertIn("quux-invented-daemon", r.value)

    def test_multiple_targets_pass_when_any_is_named(self):
        t = _turn("nginx is running.",
                  [{"name": "manage_services",
                    "arguments": {"action": "status", "name": "nginx"}},
                   {"name": "manage_services",
                    "arguments": {"action": "status", "name": "sshd"}}])
        self.assertTrue(_result(t).passed)


if __name__ == "__main__":
    unittest.main()
