# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The CUDA gate counts compiled code the way NVIDIA's binary-compatibility
rule does.

THE RULE (CUDA Programming Guide, binary compatibility). Compiled code (a
cubin) built for compute capability X.y executes on every card of capability
X.z with z >= y; never on a lower minor and never across a major. An
architecture-specific target (the 'a' suffix) executes on exactly its own
capability. A -virtual target is PTX, not compiled code.

THE DEFECT THIS PINS. The gate tested exact membership: a build carrying
80-real was taken to carry no compiled code for a capability-8.6 card, so on a
driver older than the build's toolkit the CUDA engine was declined with the
sentence that the driver would have to translate its PTX, where the
documented rule says the compiled code loads. The same narrow reading kept the
architecture gate from counting 80-real for an 8.6 card, and from counting the
PTX half of a bare number (cmake's default, compiled code and PTX both) for a
newer card, as it already did for a -virtual entry.

The cells below the minor-forward ones hold where the rule stops: across a
major, below a minor, an 'a' target on any other capability, PTX counted as
compiled code. The control, an exact compiled target, is the shape this
machine's card has in every build at hand.
"""
from __future__ import annotations

import unittest
from unittest import mock

from intergen import serving_device

CARD = "0000:01:00.0"


class _NewerMinorToolkit(unittest.TestCase):
    """A build from CUDA 12.8 on a driver at CUDA 12.0: the case where only
    compiled code for the card loads, since the driver cannot translate PTX
    from a newer toolkit."""

    def support(self, targets: set, capability: int):
        with mock.patch.object(serving_device, "cuda_build_toolkit_release",
                               return_value=(12, 8)), \
                mock.patch.object(serving_device, "cuda_driver_release",
                                  return_value=(12, 0)), \
                mock.patch.object(serving_device, "cuda_build_gpu_targets",
                                  return_value=set(targets)):
            return serving_device.cuda_driver_support(
                caps={CARD: capability}, server_path="/stand-in/llama-server")


class CompiledCodeRunsOnLaterMinorsOfItsMajor(_NewerMinorToolkit):

    def test_a_compiled_target_covers_a_later_minor_of_its_major(self):
        support = self.support({"80-real", "80-virtual"}, 86)
        self.assertIs(support.loadable, True, support.reason)
        self.assertNotIn("translate its PTX", support.reason)

    def test_a_compiled_target_covers_the_last_minor_of_its_major(self):
        self.assertIs(self.support({"86-real"}, 89).loadable, True)

    def test_a_bare_number_counts_as_compiled_code_for_later_minors(self):
        self.assertIs(self.support({"120"}, 121).loadable, True)


class TheRuleStopsWhereNvidiaSaysItStops(_NewerMinorToolkit):

    def test_compiled_code_never_crosses_a_major(self):
        support = self.support({"80-real"}, 90)
        self.assertIs(support.loadable, False)
        self.assertIn("translate its PTX", support.reason)

    def test_compiled_code_never_runs_on_a_lower_minor(self):
        self.assertIs(self.support({"86-real"}, 80).loadable, False)

    def test_an_architecture_specific_target_covers_its_own_capability(self):
        self.assertIs(self.support({"90a-real"}, 90).loadable, True)

    def test_an_architecture_specific_target_covers_nothing_else(self):
        self.assertIs(self.support({"90a-real"}, 100).loadable, False)

    def test_ptx_alone_is_not_compiled_code(self):
        support = self.support({"80-virtual"}, 80)
        self.assertIs(support.loadable, False)
        self.assertIn("translate its PTX", support.reason)

    def test_the_control_an_exact_compiled_target(self):
        self.assertIs(self.support({"75-real"}, 75).loadable, True)


class TheArchitectureGateCountsTheSameCode(unittest.TestCase):

    def test_a_compiled_target_covers_a_later_minor_of_its_major(self):
        self.assertIs(serving_device._cuda_targets_cover(86, {"80-real"}), True)

    def test_a_bare_numbers_ptx_carries_forward_as_a_virtual_entrys_does(self):
        self.assertIs(serving_device._cuda_targets_cover(90, {"80"}), True)

    def test_compiled_code_alone_never_crosses_a_major(self):
        self.assertIs(serving_device._cuda_targets_cover(90, {"80-real"}), False)

    def test_an_architecture_specific_bare_number_does_not_carry_forward(self):
        self.assertIs(serving_device._cuda_targets_cover(130, {"120a"}), False)


if __name__ == "__main__":
    unittest.main()
