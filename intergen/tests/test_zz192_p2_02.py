# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""`intergen tier` answers a question it may have created, from a source it
does not name.

The rule is the assistant's own, written over `cmd_status` (cli.py:809-817):
the service is D-Bus activatable, "so on a machine with nothing running the
call itself starts the assistant — a model server, the hardware probe, the
lot. That is a legitimate mechanism and it is left alone. What is not
legitimate is doing it in silence: the user asked what the state was, and got
back the state their question created, with nothing saying so."

`cmd_status` keeps that rule: it reads `daemon_has_owner()` BEFORE the call and
renders an activation line when the call is what started the daemon.
`cmd_tier` issues the same kind of bus call — `try_dbus("GetTier")`,
cli.py:1244, with `Gio.DBusCallFlags.NONE`, which permits activation — with no
ownership read before it and no disclosure after it. A command whose whole
description is "Show hardware tier info" therefore starts hardware detection, a
model resolution, a chat and an embedding llama-server and a bind on
127.0.0.1:8089, and prints a tier block with nothing saying the reading was
created by the question.

The same block is also printed from the local `HardwareDetector` when the bus
call brings nothing back, with no line distinguishing the two — so the reader
cannot tell whether the running assistant or this process answered.

These cases assert the honest behaviour on both counts. They are hermetic: no
bus, no service, no probe — `daemon_has_owner`, `try_dbus` and
`HardwareDetector.detect` are all stood in for, exactly as the status cases in
test_cli_status_activation_and_model_choice.py do it.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

from intergen import cli
from intergen.interfaces.types import HardwareTier, HardwareTierLevel


def _tier() -> HardwareTier:
    return HardwareTier(
        ram_gb=64.0, gpu_vendor="nvidia", gpu_model="a discrete card",
        gpu_vram_mb=16384, tier=HardwareTierLevel.TIER_3,
        recommended_model="Big Serving Model", recommended_quant="Q4_K_M",
        estimated_model_size_gb=20.0)


def _get_tier_reply(tier: HardwareTier) -> str:
    """The GetTier reply for that hardware, in the daemon's JSON shape.

    Every field carries the value cmd_tier's own local fallback would build
    from the same HardwareTier, so the two answer paths differ in NOTHING but
    which of them answered.
    """
    return json.dumps({
        "level": tier.tier.value,
        "ram_gb": tier.ram_gb,
        "gpu_vendor": tier.gpu_vendor,
        "gpu_model": tier.gpu_model,
        "recommended_model": tier.recommended_model,
        "recommended_quant": tier.recommended_quant,
        "estimated_model_size_gb": tier.estimated_model_size_gb,
    })


def _run_tier() -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_tier()
    return buf.getvalue()


class TierActivationDisclosureTests(unittest.TestCase):
    """A tier call that started the assistant says so, once."""

    LINE = "the call started it"

    def test_a_tier_call_that_activated_the_daemon_says_so(self):
        """Nothing owned the name when the call began and the call was
        answered: the daemon that answered is the one this question started."""
        with mock.patch.object(cli, "daemon_has_owner", return_value=False), \
             mock.patch.object(cli, "try_dbus",
                               return_value=_get_tier_reply(_tier())):
            out = _run_tier()
        self.assertIn(
            self.LINE, out,
            "the tier call started the assistant — a model server, the "
            "hardware probe, the lot — and said nothing")
        self.assertEqual(out.count(self.LINE), 1, "one line, not a paragraph")

    def test_a_daemon_that_was_already_running_gets_no_such_line(self):
        """The disclosure is about a state change this call caused. No change,
        nothing to disclose — otherwise the line means nothing."""
        with mock.patch.object(cli, "daemon_has_owner", return_value=True), \
             mock.patch.object(cli, "try_dbus",
                               return_value=_get_tier_reply(_tier())):
            out = _run_tier()
        self.assertNotIn(self.LINE, out)


class TierAnswerSourceTests(unittest.TestCase):
    """The block says which instrument read the hardware."""

    def test_the_daemons_answer_and_the_local_probe_do_not_read_alike(self):
        """Same daemon on the bus, same hardware, same numbers — one reading
        taken by the assistant, one taken by this process when the bus call
        brought nothing back. A report that names its source renders those two
        differently; today they are the same characters, so the source is
        unrecoverable from the output.

        Asserted as a difference rather than a phrase so it pins the property,
        not one wording of it.
        """
        tier = _tier()
        with mock.patch.object(cli, "daemon_has_owner", return_value=True), \
             mock.patch.object(cli, "try_dbus",
                               return_value=_get_tier_reply(tier)):
            from_the_daemon = _run_tier()
        with mock.patch.object(cli, "daemon_has_owner", return_value=True), \
             mock.patch.object(cli, "try_dbus", return_value=None), \
             mock.patch("intergen.hardware.HardwareDetector.detect",
                        return_value=tier):
            from_the_local_probe = _run_tier()
        self.assertNotEqual(
            from_the_daemon, from_the_local_probe,
            "the daemon's reading and this process's own probe print the "
            "identical block, so nothing says which one answered")


if __name__ == "__main__":
    unittest.main()
