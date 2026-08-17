# SPDX-License-Identifier: GPL-3.0-or-later
"""The serving floor must not hand the user output that is not language.

Measured 2026-08-07 on the 2B tier of the baseline battery: asked "get me htop",
the model emitted punctuation clusters and whitespace, and the daemon SERVED it
— the citation layer even appended a source footer to it. check_quality() passed
the reply because its four checks are empty / repetitive (unique-word ratio) /
echo / template-artifact markers, and degenerate punctuation defeats the
repetition check: every punctuation cluster is a distinct "word".

The two reply strings below are verbatim from that run's sealed results
(traces c215bca41ac7 and 4d7d7e9716c1), with the router's citation footer
removed because it is appended after chat() returns — check_quality never sees
it. Thresholds were calibrated against all 441 replies of the three sealed
baseline runs; the calibration is in that round's evidence.

Covered here:
  1. both measured garbage replies are flagged "degenerate";
  2. real replies — prose, a shell command, a code-fenced table, a short answer
     — are NOT flagged (the false-positive direction is the load-bearing one);
  3. the detector feeds the EXISTING retry ladder: garbage retries, and a good
     second attempt is what reaches the user;
  4. when both attempts are degenerate the honest fallback is served — the
     measured garbage never reaches the user;
  5. the four pre-existing checks (empty / repetitive / echo / artifacts) still
     return their own reasons.
"""
from __future__ import annotations

import unittest
import unittest.mock as mock

from intergen.llm import LLMRouter
from intergen.interfaces.types import EscalationMode, Message, MessageRole

# Verbatim from the sealed baseline run of 2026-08-07, 2B tier
# (results.json sha256 45e2b7174757468535b771d630e39d4342663f4858f2d18396db73797ce7007f),
# trace c215bca41ac7, user turn "get me htop".
HTOP_GARBAGE = '"""""，""""##""\n\n"-" \n\n\n\n\n<\nn"\n\n\n\n<\n"\n\n"\n"\n"\n####\n\n""##\n、"\n""\n\n"、 \n\n"， \n""\n"\n\n""\n，##  \n  \n\n\n \n####\n\n\n\n\n \n\n\n\n\n\n\n\n，\n\n\n\n\n"\n\n"##\n  \n  \n\n\n\n\n\n"\n"'

# Same run, trace 4d7d7e9716c1, user turn "Is sshd enabled?" — the
# whitespace-and-fragment shape the repetition check also misses.
SSHD_GARBAGE = 'The \n\n  \n\n   \n\n    \n\n# \n\n##"# " \n\n \n\n \n\n" The   # All# message All The \n\n \n\n All These % \n\n!% ""$"# \n\n$#!!    #  \n\n$ \n\n" \n\n"    The \n\n \n\n  $ \n\n All   \n\n# #"# \n\n  \n\n All The   \n\n The The \n\n  The  # \n\n \n\n  The \n\n \n\n These  Themessage The    \n\n \n\n %  \n  \n\n The \n\n All  \n\n  \n\n  \n\nmessage \n\n The   \n\n The  The \n\n  All \n\n  \n\n ... \n\n \n\n The   \n \n\n \n\n \n\n   The   \n\n \n\n...%   echo  \n\n% \n\n \n\nmessage  \n\n  \n\n   \n\n \n\n \n\n \n\n \n\n \n\n   \n\nechoecho  All... \n\n \n\n \n\n The \n\n  The  \n\n $ \n\n These     The "  \n\n The \n\n"" " \n\n The" All " " The%  "" The All"  "" \n\n""\n\n但是\nButfully; and...List says ...\n\nAspects ... Will message ... which ... But reply for ...\n\n# ...\n\nWaitfully ...\n\nBy and; orings, ...But ...But ...For message ...  ...Asfully ...\n\nButAsButButAsBut$AsAsAsButButButAsAsAsAsAsAsThenAsButAsButAsAsButAsAsButAsButButAsAsButButButAsAsAsAsAsAsButAsButAsButButButButButAsWaitAsButAsButAsButAsWaitButButAsButAsButButButAsAsButButAsAsAsButAsButButAsAsButAsButAsAsButButAsAsAsButButAsAsAsButThenButButButButButButAsAsAsButAsAsButAsAsAsAsButAsButButButButButUserAsAsAsAsAsButAsWaitAsAsButButAsButAsAsAsAsButButButAsAsAsAsAsAsAsButButAsButButButAsWaitAsAsButAsButAsButAsAsButThenButAsAsButAsAsAsAsButButWaitButButAsButButAsAsAsAsAsAsAsAsAsButAsButButAsAsWaitAsButButWaitButWaitAsButButAsButButAsButButAsButAsButAsButButAsAsButAsAsAsAsAsAsButWaitAsButAsButAsAsAsAsButAsButWaitButButThenButButAsWaitAsButButButButAsAsWaitButAsWaitAsAsAsAsAsButAsButButAsAsAsButAsButAsAsButAsAs$AsAsButAsButAsButAsButButAsButButAsButAsAsAsButAsAsWaitAsButAsButAsButButButButButButButAsAsAsAsButButAsAsButAsAsButButButAsButAsButAsButButThenAsButAsButButBut“'


class DegenerateOutputDetectionTests(unittest.TestCase):
    def setUp(self):
        self.llm = LLMRouter()

    def test_measured_htop_garbage_is_flagged(self):
        self.assertEqual(
            self.llm.check_quality(HTOP_GARBAGE, "get me htop"), "degenerate")

    def test_measured_sshd_garbage_is_flagged(self):
        self.assertEqual(
            self.llm.check_quality(SSHD_GARBAGE, "Is sshd enabled?"), "degenerate")

    def test_punctuation_only_reply_is_flagged_at_any_length(self):
        # Served on the 2B tier as the entire reply (trace 4cc34dfc8947, before
        # the citation footer was appended): one quote character.
        self.assertEqual(self.llm.check_quality('"', "What about Nigeria?"),
                         "degenerate")

    def test_ordinary_prose_reply_passes(self):
        reply = ("You're running kernel 6.18.10-igos-10. The machine has been "
                 "up for 3 days, and nothing in the journal looks unusual.")
        self.assertEqual(self.llm.check_quality(reply, "what kernel am I on?"), "")

    def test_command_and_path_heavy_reply_passes(self):
        # Symbol-dense but correct: paths, a flag, markdown emphasis. This is
        # the shape a naive character-class threshold would wrongly reject.
        reply = ("Run `pkm install htop` to install it. The binary lands in "
                 "**/usr/bin/htop**, and its config is at ~/.config/htop/htoprc "
                 "(see /etc/pkm/pkm.conf for the mirror it pulls from).")
        self.assertEqual(self.llm.check_quality(reply, "how do I get htop?"), "")

    def test_code_fenced_table_reply_passes(self):
        # A df table is legitimately non-linguistic; it is inside a fence, and
        # the fence is excluded before the character mix is measured.
        reply = ("Here is the `df -h` output:\n\n```\n"
                 "Filesystem      Size  Used Avail Use% Mounted on\n"
                 "/dev/mapper/root 982G   64G  868G   7% /\n"
                 "/dev/nvme0n1p1   511M   19M  493M   4% /boot/efi\n"
                 "|---------------|------|-----|------|----|------|\n"
                 "```\n\nRoot is 7% used, so there is plenty of room.")
        self.assertEqual(self.llm.check_quality(reply, "df -h output please"), "")

    def test_short_symbol_dense_answer_passes(self):
        # "12." is 33% punctuation by character; below the length floor the
        # shape signals carry no information and the check abstains.
        self.assertEqual(
            self.llm.check_quality("12.", "What is the square root of 144?"), "")

    def test_existing_checks_keep_their_own_reasons(self):
        self.assertEqual(self.llm.check_quality("", "hi"), "empty")
        self.assertEqual(self.llm.check_quality("   \n ", "hi"), "empty")
        self.assertEqual(
            self.llm.check_quality("yes " * 40, "is ssh running?"), "repetitive")
        self.assertEqual(
            self.llm.check_quality("Is sshd enabled?", "Is sshd enabled?"), "echo")
        self.assertEqual(
            self.llm.check_quality(
                "<think>the user wants the kernel version</think> You are on "
                "6.18.10-igos-10, which is the current InterGenOS kernel.",
                "what kernel?"),
            "artifacts")


class DegenerateOutputLadderTests(unittest.TestCase):
    """The detector must drive the retry ladder that already exists."""

    def setUp(self):
        self.llm = LLMRouter()
        self.llm.set_escalation_mode(EscalationMode.NEVER)
        self.msgs = [Message(role=MessageRole.USER, content="get me htop")]

    def _stream_returning(self, *replies):
        calls = {"n": 0}

        def _stream(*a, **k):
            self.llm._last_reasoning = ""
            self.llm._last_finish_reason = "stop"
            reply = replies[min(calls["n"], len(replies) - 1)]
            calls["n"] += 1
            return iter([reply])

        return calls, _stream

    def test_degenerate_first_attempt_retries_and_serves_the_good_reply(self):
        good = ("I can install htop for you with `pkm install htop` — say the "
                "word and I'll ask for your approval.")
        calls, stream = self._stream_returning(HTOP_GARBAGE, good)
        with mock.patch.object(self.llm, "stream", stream):
            resp = self.llm.chat(self.msgs)
        self.assertEqual(calls["n"], 2, "degenerate output must trigger a retry")
        self.assertEqual(resp.text.strip(), good)
        self.assertTrue(resp.quality_passed)

    def test_degenerate_twice_serves_the_honest_fallback_not_the_garbage(self):
        calls, stream = self._stream_returning(HTOP_GARBAGE, SSHD_GARBAGE)
        with mock.patch.object(self.llm, "stream", stream):
            resp = self.llm.chat(self.msgs)
        self.assertEqual(calls["n"], 2)
        self.assertFalse(resp.quality_passed)
        self.assertEqual(resp.text, self.llm._EMPTY_RESPONSE_FALLBACK)
        self.assertNotIn('"""', resp.text)

    def test_good_first_attempt_is_not_retried(self):
        good = "htop is already installed — run it with `htop`."
        calls, stream = self._stream_returning(good)
        with mock.patch.object(self.llm, "stream", stream):
            resp = self.llm.chat(self.msgs)
        self.assertEqual(calls["n"], 1, "a good reply must not pay for a retry")
        self.assertEqual(resp.text.strip(), good)


if __name__ == "__main__":
    unittest.main()
