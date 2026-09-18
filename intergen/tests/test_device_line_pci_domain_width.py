# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A PCI domain wider than four hex digits must not drop the whole device line.

The in-tree list-devices patch prints each device's ``[PCI <id>]`` tail from
ggml's ``ggml_backend_dev_props.device_id``. The CUDA backend builds that
string with ``%04x:%02x:%02x.0`` — and printf's ``%04x`` is a MINIMUM field
width, not a maximum. A device in domain 0 prints ``0000:01:00.0``; a device in
domain 0x10000 prints ``10000:e1:00.0``, five digits.

The two patterns that read those lines required EXACTLY four hex digits for the
domain. The consequence is worse than losing the address: the optional tail
then fails to match while the rest of the line is followed by text, so the
WHOLE line fails, and the device disappears from the list the serving-card
selection reads. A card the machine has would be reported as a card it does
not have.

Domains above 0xffff are what Linux gives a device behind a Thunderbolt or VMD
host bridge — the shape a card in an external enclosure arrives in. The machine
these tests were written on carries four such devices (``10000:e0:06.0``,
``10000:e0:06.2``, ``10000:e1:00.0``, ``10000:e2:00.0``); none is a GPU there,
which is exactly why this was never seen from the output of one machine.

WHAT THESE TESTS ASSERT: one to eight hex digits of domain are accepted, the
four-digit form still parses to the same address it always did, a line with no
tail still parses with no address, and both readers agree — they are the same
pattern and the comment in serving_device.py requires them to move together.
"""

from __future__ import annotations

import unittest

from intergen import hardware as hw
from intergen import serving_device as sd


# An enclosure card at 10000:e1:00.0 beside the built-in one at 0000:01:00.0.
THUNDERBOLT_LIST = (
    "Available devices:\n"
    "  Vulkan0: NVIDIA GeForce RTX 3070 Ti Laptop GPU "
    "(8192 MiB, 469 MiB free) [PCI 0000:01:00.0]\n"
    "  Vulkan1: NVIDIA GeForce RTX 4090 "
    "(24564 MiB, 24000 MiB free) [PCI 10000:e1:00.0]\n"
    "  Vulkan2: Intel(R) Iris(R) Xe Graphics (ADL GT2) "
    "(17791 MiB, 12703 MiB free) [PCI 0000:00:02.0]\n"
)

FOUR_DIGIT_LIST = (
    "Available devices:\n"
    "  CUDA0: NVIDIA GeForce RTX 3070 Ti Laptop GPU "
    "(7840 MiB, 310 MiB free) [PCI 0000:01:00.0]\n"
)

NO_TAIL_LIST = (
    "Available devices:\n"
    "  Vulkan0: NVIDIA GeForce RTX 3070 Ti Laptop GPU "
    "(8192 MiB, 469 MiB free)\n"
)


class TheDeviceLinePatternTests(unittest.TestCase):

    def test_a_five_digit_domain_still_matches_the_whole_line(self):
        got = [(m.group("name"), m.group("pci"))
               for m in sd._DEVICE_LINE_RE.finditer(THUNDERBOLT_LIST)]
        self.assertEqual(got, [("Vulkan0", "0000:01:00.0"),
                               ("Vulkan1", "10000:e1:00.0"),
                               ("Vulkan2", "0000:00:02.0")])

    def test_the_four_digit_form_parses_exactly_as_before(self):
        got = [(m.group("name"), m.group("pci"))
               for m in sd._DEVICE_LINE_RE.finditer(FOUR_DIGIT_LIST)]
        self.assertEqual(got, [("CUDA0", "0000:01:00.0")])

    def test_a_line_with_no_tail_still_parses_with_no_address(self):
        got = [(m.group("name"), m.group("pci"))
               for m in sd._DEVICE_LINE_RE.finditer(NO_TAIL_LIST)]
        self.assertEqual(got, [("Vulkan0", None)])

    def test_the_two_patterns_agree_on_which_lines_they_see(self):
        """hardware's pattern is the same one minus the name group."""
        for text in (THUNDERBOLT_LIST, FOUR_DIGIT_LIST, NO_TAIL_LIST):
            with self.subTest(text=text.splitlines()[1][:24]):
                self.assertEqual(
                    len(sd._DEVICE_LINE_RE.findall(text)),
                    len(hw._LIST_DEVICES_RE.findall(text)))

    def test_the_widest_domain_the_kernel_can_give_is_accepted(self):
        line = ("  Vulkan0: A Card (1024 MiB, 512 MiB free) "
                "[PCI ffffffff:e1:00.7]\n")
        m = sd._DEVICE_LINE_RE.search(line)
        self.assertIsNotNone(m)
        self.assertEqual(m.group("pci"), "ffffffff:e1:00.7")

    def test_a_malformed_tail_is_still_refused(self):
        """Widening the domain must not turn the tail into 'anything'."""
        for bad in ("[PCI 0000:01:00]", "[PCI 0000:01:00.8]",
                    "[PCI zzzz:01:00.0]", "[PCI 123456789:01:00.0]"):
            with self.subTest(bad=bad):
                line = f"  Vulkan0: A Card (1024 MiB, 512 MiB free) {bad}\n"
                self.assertIsNone(sd._DEVICE_LINE_RE.search(line),
                                  f"{bad} should not parse as a PCI tail")


class TheSelectionReadsTheEnclosureCardTests(unittest.TestCase):
    """The consumers, not just the pattern: the card must reach the list."""

    def test_the_enclosure_card_can_be_selected_and_its_address_returned(self):
        name, pci = sd.select_serving_device_and_pci(
            list_output=THUNDERBOLT_LIST, discrete_vram_mb=24564,
            sysfs_root="/nonexistent")
        self.assertEqual((name, pci), ("Vulkan1", "10000:e1:00.0"))

    def test_the_name_to_address_lookup_answers_for_it(self):
        self.assertEqual(
            sd.pci_for_device_name("Vulkan1", list_output=THUNDERBOLT_LIST),
            "10000:e1:00.0")
        self.assertEqual(
            sd.pci_for_device_name("Vulkan0", list_output=THUNDERBOLT_LIST),
            "0000:01:00.0")

    def test_the_open_driver_vram_reader_sees_the_enclosure_card_too(self):
        self.assertEqual(
            hw.open_driver_vram_mb("geforce rtx 4090",
                                   list_output=THUNDERBOLT_LIST),
            24564)


if __name__ == "__main__":
    unittest.main()
