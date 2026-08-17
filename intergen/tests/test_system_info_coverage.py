# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""FACE coverage — Bucket A grounded system-state selector + formatters.

The system_info intent routes these to run_command; _natural_language_to_command
picks the AUTO command and _template_synthesis renders a terse answer. These are
deterministic unit tests of the selector + formatters (no embedder). The intent-
recall side (that these phrasings match system_info and the shopping/comparison
false-friends do NOT) is measured by the coverage classifier + the precision/
recall harness with the live embedder.
"""

from __future__ import annotations

import unittest

from intergen.router import ConversationRouter as R


class SelectorResolution(unittest.TestCase):
    """Every Bucket-A phrasing must resolve to a (non-None) AUTO command."""

    CASES = {
        "what version am I running": "cat /etc/os-release",
        "what OS is this": "cat /etc/os-release",
        "is my system 32 bit or 64 bit": "uname -m",
        "what CPU do I have": "lscpu | head -20",
        "how many cores do I have": "nproc",
        "show me my hardware": "hostnamectl",
        "what are my computer's specs": "hostnamectl",
        "how much free space is left": "df -h",
        "how do I check what graphics card I have": "lspci | grep -i vga",
    }

    def test_resolves_expected_command(self):
        for q, cmd in self.CASES.items():
            self.assertEqual(R._natural_language_to_command(q), cmd, q)

    def test_taking_up_space_is_du_not_df(self):
        # "what's taking up my space" wants the du breakdown (where it went),
        # NOT df (how much is free) — the du phrase must beat the generic df keys.
        got = R._natural_language_to_command("what's taking up all my disk space")
        self.assertIn("du ", got)
        self.assertNotEqual(got, "df -h")

    def test_cpu_load_is_top_consumers_not_lscpu(self):
        # "why is my cpu usage so high" wants top consumers, not the cpu model.
        got = R._natural_language_to_command("why is my cpu usage so high")
        self.assertIn("--sort=-pcpu", got)
        self.assertNotIn("lscpu", got)

    def test_plain_cpu_model_still_lscpu(self):
        self.assertEqual(R._natural_language_to_command("what CPU do I have"),
                         "lscpu | head -20")

    def test_using_all_ram_is_memory_not_disk(self):
        # F3 correctness fix (2026-07-02): the generic "using all"/"taking up" du
        # keys were first-substring-wins and SHADOWED the RAM and CPU frames, so a
        # memory question wrongly resolved to a disk du. The du keys are now scoped
        # to a disk/space/storage object.
        for q in ("what's using all my ram", "what is using all my memory"):
            got = R._natural_language_to_command(q)
            self.assertEqual(got, "free -h", q)

    def test_using_all_cpu_is_top_consumers_not_disk(self):
        got = R._natural_language_to_command("what's using all my cpu")
        self.assertIn("--sort=-pcpu", got)
        self.assertNotIn("du ", got)

    def test_disk_frames_still_du(self):
        # The disk-scoped du keys must still win for genuine disk questions.
        for q in ("what's taking up all my disk space",
                  "what's using all my disk space"):
            got = R._natural_language_to_command(q)
            self.assertIn("du ", got)


class ShoppingComparisonBackstop(unittest.TestCase):
    """FACE defense-in-depth (WC backstop): the command picker refuses a
    shopping/comparison frame even if the recall gate over-matched — a second
    layer below the embedder-anchored system_info recall."""

    def test_shopping_and_comparison_return_none(self):
        for q in ["what cpu should i buy", "how much does more ram cost",
                  "how many cores does an apple have", "how do i overclock",
                  "what graphics card should i buy", "which cpu is better",
                  "how much is a new gpu", "is amd or intel cheaper",
                  "what ram should i get", "gpu vs cpu",
                  "how does my cpu compare to a ryzen", "compare my cpu to an i9"]:
            self.assertIsNone(R._natural_language_to_command(q), q)

    def test_self_referential_does_my_x_have_still_resolves(self):
        # WC's main worry: "does MY cpu have" (self-ref) must still resolve while
        # "does AN apple have" (comparison) rejects.
        for q in ["how many cores does my cpu have", "does my system have 64 bit",
                  "what cpu does my machine have", "how much ram does this have"]:
            self.assertIsNotNone(R._natural_language_to_command(q), q)

    def test_real_state_queries_still_resolve(self):
        for q in ["what CPU do I have", "how many cores do I have",
                  "how much RAM do I have", "how much disk space do i have",
                  "is my system 32 bit or 64 bit", "show me my hardware",
                  "how much free space is left", "what version am I running"]:
            self.assertIsNotNone(R._natural_language_to_command(q), q)


class Formatters(unittest.TestCase):
    def test_arch_64bit(self):
        self.assertEqual(
            R._template_synthesis("is my system 32 bit or 64 bit", "x86_64"),
            "This is a 64-bit system (x86_64).")

    def test_arch_32bit(self):
        self.assertEqual(
            R._template_synthesis("is my system 32 bit or 64 bit", "i686"),
            "This is a 32-bit system (i686).")

    def test_cores_plural_and_singular(self):
        self.assertEqual(
            R._template_synthesis("how many cores do I have", "8"),
            "This machine has 8 CPU cores.")
        self.assertEqual(
            R._template_synthesis("how many cores do I have", "1"),
            "This machine has 1 CPU core.")

    def test_version_routes_to_os_summary(self):
        out = 'PRETTY_NAME="InterGenOS 1.0-dev (Revival)"\nID=intergenos'
        self.assertEqual(
            R._template_synthesis("what version am I running", out),
            "OS: InterGenOS 1.0-dev (Revival).")


if __name__ == "__main__":
    unittest.main()
