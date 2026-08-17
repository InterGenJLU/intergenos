# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Runtime semantic-health detector (intergen.semantic_health) — G12 corpus.

The regression corpus is the development machine session's ACTUAL responses (the Intel ANV
Vulkan-F16 degradation). Entries 1, 2, 4 are corrupt and MUST flag (prompt-echo /
charset+repetition / foreign-script). Entry 3 is the router's own CANNED fallback
— coherent English that MUST pass (a detector that flagged our own fallback would
loop the reaction ladder). The legit-twin cases pin the exemptions: fenced
foreign/verbatim content, a short identity phrase, directory output, a repeated-
line code block, and a user who is themselves writing in a non-Latin script.
"""
from __future__ import annotations

import unittest

from intergen.semantic_health import (
    FLAG_CHARSET, FLAG_FOREIGN_SCRIPT, FLAG_REPETITION, FLAG_SYSTEM_PROMPT_ECHO,
    assess_semantic_health,
)

# A representative live system prompt (the development machine turn leaked its tool-instruction
# text — the echo check screens against the LIVE prompt, which we own).
_SYS = (
    "You are InterGen. If the user's request involves usernames, blocklist, "
    "or permissions, handle it gracefully and respond with a clear message. "
    "Clear the blocklist or request a username change to run this command. "
    "Today is Saturday."
)

# The development machine corpus, verbatim from the dispatch.
_ENTRY1 = (
    'usernames, blocklist, or permissions, handle it gracefully and respond '
    'with: "IClear the blocklist or request a username change to run this '
    'command: [command2]\n7. If the user asked Inter, respond today is  \n\n'
    'Today is Saturday line.'
)
_ENTRY2 = ("— TOPMk${conskomland ofconstant characteristic by  classic:    "
           "allback of of 含 Jens of by Austin栾")
_ENTRY3 = "Sorry — I didn't quite catch that. Could you rephrase it for me?"
_ENTRY4 = "作战"


class Dot241CorpusTests(unittest.TestCase):
    def test_entry1_flags_system_prompt_echo(self) -> None:
        r = assess_semantic_health(_ENTRY1, system_prompt=_SYS, conversation_texts=[])
        self.assertIn(FLAG_SYSTEM_PROMPT_ECHO, r.flags, r.detail)

    def test_entry2_flags_charset_or_repetition(self) -> None:
        r = assess_semantic_health(_ENTRY2, system_prompt=_SYS, conversation_texts=[])
        self.assertTrue(
            {FLAG_CHARSET, FLAG_REPETITION} & set(r.flags),
            f"entry2 should flag charset/repetition, got {r.flags} {r.detail}")

    def test_entry3_canned_fallback_passes(self) -> None:
        r = assess_semantic_health(_ENTRY3, system_prompt=_SYS, conversation_texts=[])
        self.assertTrue(r.ok, f"canned fallback must not flag: {r.flags} {r.detail}")

    def test_entry4_flags_foreign_script(self) -> None:
        r = assess_semantic_health(_ENTRY4, system_prompt=_SYS, conversation_texts=[])
        self.assertIn(FLAG_FOREIGN_SCRIPT, r.flags, r.detail)


class LegitTwinTests(unittest.TestCase):
    """Content that superficially resembles corruption but is correct — MUST pass."""

    def test_fenced_foreign_script_passes(self) -> None:
        r = assess_semantic_health("Here is the translation:\n```\n中文答案：你好世界\n```",
                                   system_prompt=_SYS, conversation_texts=[])
        self.assertTrue(r.ok, r.flags)

    def test_short_identity_phrase_passes(self) -> None:
        # Reuses a couple of system-prompt words but far under the 8-token run.
        r = assess_semantic_health("I'm InterGen, here to help.",
                                   system_prompt=_SYS, conversation_texts=[])
        self.assertTrue(r.ok, r.flags)

    def test_directory_listing_passes(self) -> None:
        r = assess_semantic_health("a.txt  b.txt  notes.md  src  build  README.md",
                                   system_prompt=_SYS, conversation_texts=[])
        self.assertTrue(r.ok, r.flags)

    def test_fenced_repeated_lines_pass(self) -> None:
        code = "```\nprint(1)\nprint(1)\nprint(1)\nprint(1)\nprint(1)\n```"
        r = assess_semantic_health(code, system_prompt=_SYS, conversation_texts=[])
        self.assertTrue(r.ok, r.flags)

    def test_user_writing_cjk_gets_cjk_back(self) -> None:
        # The user's own turn is Chinese, so a Chinese reply is not a flood.
        r = assess_semantic_health("你好！很高兴为你服务。", system_prompt=_SYS,
                                   conversation_texts=["你能帮我做个计划吗？"])
        self.assertTrue(r.ok, r.flags)


class CheckIsolationTests(unittest.TestCase):
    def test_no_in_band_echo_exemption(self) -> None:
        # An "intentional quote" marker must NOT exempt an echo (decided:
        # an in-band marker would be a prompt-injection bypass of the screen).
        echoed = ("(quote) If the user's request involves usernames, blocklist, "
                  "or permissions, handle it gracefully and respond")
        r = assess_semantic_health(echoed, system_prompt=_SYS, conversation_texts=[])
        self.assertIn(FLAG_SYSTEM_PROMPT_ECHO, r.flags)

    def test_control_chars_flag_charset(self) -> None:
        r = assess_semantic_health("normal text \x00\x07 with control bytes",
                                   system_prompt="", conversation_texts=[])
        self.assertIn(FLAG_CHARSET, r.flags)

    def test_clean_english_no_flags(self) -> None:
        r = assess_semantic_health(
            "Your disk is about 60% full. Want me to show the largest folders?",
            system_prompt=_SYS, conversation_texts=["how full is my disk?"])
        self.assertEqual(r.flags, [])


if __name__ == "__main__":
    unittest.main()
