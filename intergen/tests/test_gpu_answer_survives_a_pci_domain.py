# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The "what graphics card do I have" answer survives a printed PCI domain.

`lspci` prints the PCI domain on every line as soon as ANY device on the
machine has a domain that is not zero — a Thunderbolt controller in domain
10000 is enough, and it need not be a display adapter. The identity reader
now reproduces that (intergen.state_cache.read_display_adapters), so the
`gpu_info` cache value on such a machine reads "0000:01:00.0 VGA compatible
controller: …" rather than "01:00.0 …".

The summariser that turns that value into the sentence a person reads took
the description off by counting two colons from the front of the line. On a
line carrying a domain that counted the domain and the bus as description,
and the assistant answered "GPU: 02.0 Iris Xe Graphics." The same reply is
produced by a raw `lspci | grep -i vga` run, so this was already wrong on
any machine with a domain, whichever path fed it.

These tests pin BOTH forms, because both are real: a machine with no
domain anywhere prints neither, and a machine with one prints it on every
line, including the lines whose own domain is zero.
"""

from __future__ import annotations

import unittest

from intergen.router import ConversationRouter
from intergen import state_cache as sc


NO_DOMAIN = ("00:02.0 VGA compatible controller: Intel Corporation "
             "Alder Lake-P GT2 [Iris Xe Graphics] (rev 0c)")
WITH_DOMAIN = ("0000:00:02.0 VGA compatible controller: Intel Corporation "
               "Alder Lake-P GT2 [Iris Xe Graphics] (rev 0c)")
WIDE_DOMAIN = ("10000:e1:00.0 VGA compatible controller: NVIDIA Corporation "
               "GA104 [GeForce RTX 3070 Ti Laptop GPU] (rev a1)")
NO_BRACKET = ("0000:01:00.0 VGA compatible controller: Example Graphics G1 "
              "(rev a1)")


class TheGpuSentenceTests(unittest.TestCase):

    def test_a_line_without_a_domain_answers_with_the_card(self):
        self.assertEqual(ConversationRouter._summarize_gpu(NO_DOMAIN),
                         "GPU: Intel Iris Xe Graphics.")

    def test_a_line_with_a_zero_domain_answers_with_the_same_card(self):
        """The line this was found on: a printed 0000 domain changes nothing."""
        self.assertEqual(ConversationRouter._summarize_gpu(WITH_DOMAIN),
                         "GPU: Intel Iris Xe Graphics.")

    def test_a_domain_wider_than_four_digits_answers_with_the_card(self):
        self.assertEqual(ConversationRouter._summarize_gpu(WIDE_DOMAIN),
                         "GPU: NVIDIA GeForce RTX 3070 Ti Laptop GPU.")

    def test_a_card_with_no_bracketed_name_keeps_its_plain_name(self):
        self.assertEqual(ConversationRouter._summarize_gpu(NO_BRACKET),
                         "GPU: Example Graphics G1.")

    def test_no_adapter_line_still_answers_nothing(self):
        """The NOTHING-PARSED contract is unchanged."""
        self.assertIsNone(ConversationRouter._summarize_gpu(""))
        self.assertIsNone(ConversationRouter._summarize_gpu("   \n"))

    def test_the_reader_and_the_summariser_agree_on_this_machine(self):
        """The whole chain, on whatever this machine really is.

        The reader produces the machine's own line and the summariser must
        name the card, not a bus number. Skipped where no display adapter is
        readable, so the test never passes by finding nothing.
        """
        value = sc.read_display_adapters()
        if not value.strip():
            self.skipTest("this machine exposes no display adapter in sysfs")
        first = value.splitlines()[0]
        sentence = ConversationRouter._summarize_gpu(value)
        self.assertIsNotNone(sentence)
        slot = first.split(None, 1)[0]
        self.assertNotIn(slot.split(":")[-1], sentence,
                         f"the answer {sentence!r} carries part of the PCI "
                         f"slot {slot!r} instead of the card's name")


if __name__ == "__main__":
    unittest.main()
