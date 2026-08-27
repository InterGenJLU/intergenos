# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""While a model is held on a discrete card, that card stays powered on.

THE DEFECT, and what it costs the person using the machine. A discrete GPU
with nothing plugged into it is left at `power/control = auto`, so the kernel
runtime-suspends it whenever it is idle. Starting or stopping a served model
touches that card, the kernel runtime-RESUMES it, and the resume announces a
hotplug on every connector — which the compositor reads as "the device
changed", frees its planes, CRTCs and connectors, and rebuilds the desktop.
The person sees their windows move. It fired again on this workstation at
18:24:05 on 2026-08-26 and the operator saw it happen.

The upstream half of that chain was fixed in an earlier lane as two declared
kernel/compositor patches. THIS lane closes the other half, in the product: a
model server never has to wake a suspended card in the first place, because
the daemon holds the card ON for exactly as long as it holds a model on it,
and puts the setting back the way it found it when it lets go.

WHAT IS MEASURED ON THIS WORKSTATION, before a line was written (read-only,
sealed in the lane's evidence):

  card  pci             vendor/device   power/control   runtime_status
  card0 0000:07:00.0    0x1002/0x7551   on              active
  card1 0000:03:00.0    0x1002/0x7551   auto            active
  card2 0000:13:00.0    0x1002/0x13c0   on              active

Two identical discrete cards, and they are ASYMMETRIC: one is already held on,
the other sits at `auto` — the state that lets it suspend and fire the chain.

THREE THINGS THE MEASUREMENT SETTLED, each of which shapes this file:

1. THE PATH IS THE PCI DEVICE NODE, NOT THE DRM CARD NODE. `runtime_status`
   reads "unsupported" on every `drm/cardN` node and "active" on the PCI
   device node, so `<sysfs>/bus/pci/devices/<id>/power/control` is the file
   that means anything.

2. THE DAEMON CANNOT WRITE IT AS ITSELF. Every card's `power/control` is mode
   0644 owned by root, and the daemon runs as an ordinary user. So the lane
   owes a udev rule that grants the write — not a sudo call — and the code
   must treat "cannot write" as an ordinary, quiet outcome rather than an
   error, because that is the state of every box until the rule is installed.

3. THE CARD MUST BE THE ONE ACTUALLY SERVED, never a fixed index. The two
   cards here are indistinguishable by description and the selector already
   resolves which one serves through its PCI id; the same resolution decides
   which card gets held.

EVERY TEST HERE RUNS AGAINST A FAKE SYSFS TREE built in a temporary directory,
which is what the existing `sysfs_root` parameter on this module's display
check exists for. No real card is touched, no real power state is changed, and
the tests give the same answer on a box with no GPU at all.
"""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from intergen import serving_device


# ── a fake sysfs tree ────────────────────────────────────────────────────────
def build_sysfs(root: Path, cards: dict[str, dict]) -> None:
    """Write a sysfs tree the module's own readers can walk.

    `cards` maps a PCI id to a dict with:
        control   — the initial contents of power/control (omit for no file)
        drm       — the DRM card name, e.g. "card1" (omit for no DRM node)
        connected — list of connector suffixes reporting "connected"
        readonly  — True to make power/control unwritable by this user
    """
    for pci, spec in cards.items():
        dev = root / "bus" / "pci" / "devices" / pci
        (dev / "power").mkdir(parents=True, exist_ok=True)
        if "control" in spec:
            ctl = dev / "power" / "control"
            ctl.write_text(spec["control"] + "\n", encoding="utf-8")
            if spec.get("readonly"):
                ctl.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        drm = spec.get("drm")
        if drm:
            (dev / "drm" / drm).mkdir(parents=True, exist_ok=True)
            for suffix in spec.get("connected", []):
                c = root / "class" / "drm" / f"{drm}-{suffix}"
                c.mkdir(parents=True, exist_ok=True)
                (c / "status").write_text("connected\n", encoding="utf-8")
            for suffix in spec.get("disconnected", []):
                c = root / "class" / "drm" / f"{drm}-{suffix}"
                c.mkdir(parents=True, exist_ok=True)
                (c / "status").write_text("disconnected\n", encoding="utf-8")


# The two-identical-cards shape this workstation actually has: card1 paints the
# desktop, card0 has nothing plugged into it and is the one that serves.
TWIN_CARDS = {
    "0000:03:00.0": {"control": "auto", "drm": "card1", "connected": ["DP-1"]},
    "0000:07:00.0": {"control": "auto", "drm": "card0", "disconnected": ["DP-5"]},
}

LIST_DEVICES = (
    "ggml_vulkan: Found 2 Vulkan devices:\n"
    "  ROCm0: AMD Radeon AI PRO R9700 (32752 MiB, 32000 MiB free) "
    "[PCI 0000:03:00.0]\n"
    "  ROCm1: AMD Radeon AI PRO R9700 (32752 MiB, 32000 MiB free) "
    "[PCI 0000:07:00.0]\n"
)


class TheServedCardIsResolvedByItsPciId(unittest.TestCase):
    """RED at base: the selector returns only the ggml NAME and throws the PCI
    id away, so nothing downstream can say which card to hold."""

    def test_the_pci_id_of_the_served_card_is_available(self) -> None:
        pci = serving_device.select_serving_device_pci(
            list_output=LIST_DEVICES, discrete_vram_mb=32752)
        self.assertEqual(
            pci, "0000:07:00.0",
            "the card that serves is the one NOT painting the desktop, and its "
            "PCI id is what the power hold needs")

    def test_the_name_and_the_pci_id_describe_the_SAME_card(self) -> None:
        """One definition, both answers — a name from one card and an id from
        another would hold the wrong card's power on."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_sysfs(root, TWIN_CARDS)
            name = serving_device.select_serving_device(
                list_output=LIST_DEVICES, discrete_vram_mb=32752)
            pci = serving_device.select_serving_device_pci(
                list_output=LIST_DEVICES, discrete_vram_mb=32752)
        self.assertEqual(name, "ROCm1")
        self.assertEqual(pci, "0000:07:00.0")

    def test_no_pci_suffix_yields_no_id_rather_than_a_guess(self) -> None:
        plain = ("  ROCm0: AMD Radeon AI PRO R9700 "
                 "(32752 MiB, 32000 MiB free)\n")
        self.assertIsNone(
            serving_device.select_serving_device_pci(
                list_output=plain, discrete_vram_mb=32752),
            "an engine build without the PCI suffix must yield None, not a "
            "guess at which card is which")


class TheCardIsHeldOnWhileAModelIsOnIt(unittest.TestCase):
    """RED at base: none of these functions exists."""

    def _root(self, tmp: str, cards=None) -> Path:
        root = Path(tmp)
        build_sysfs(root, cards if cards is not None else dict(TWIN_CARDS))
        return root

    def test_the_prior_value_is_read_before_anything_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            self.assertEqual(
                serving_device.read_runtime_pm("0000:07:00.0",
                                               sysfs_root=str(root)),
                "auto")

    def test_holding_writes_on_and_returns_what_was_there(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            prior = serving_device.hold_runtime_pm_on("0000:07:00.0",
                                                      sysfs_root=str(root))
            self.assertEqual(prior, "auto", "the caller must be handed what to "
                                            "put back")
            ctl = (root / "bus" / "pci" / "devices" / "0000:07:00.0"
                   / "power" / "control")
            self.assertEqual(ctl.read_text().strip(), "on")

    def test_releasing_puts_back_exactly_what_was_there(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            prior = serving_device.hold_runtime_pm_on("0000:07:00.0",
                                                      sysfs_root=str(root))
            self.assertTrue(
                serving_device.release_runtime_pm("0000:07:00.0", prior,
                                                  sysfs_root=str(root)))
            ctl = (root / "bus" / "pci" / "devices" / "0000:07:00.0"
                   / "power" / "control")
            self.assertEqual(ctl.read_text().strip(), "auto",
                             "the setting is RESTORED, not left on")

    def test_a_card_already_on_is_left_on_when_released(self) -> None:
        """Holding a card that was ALREADY "on" must not turn it to "auto" on
        release — that would hand the machine a worse state than it started in.
        One of the two cards on this workstation is in exactly that state."""
        cards = {"0000:07:00.0": {"control": "on", "drm": "card0",
                                  "disconnected": ["DP-5"]}}
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp, cards)
            prior = serving_device.hold_runtime_pm_on("0000:07:00.0",
                                                      sysfs_root=str(root))
            self.assertEqual(prior, "on")
            serving_device.release_runtime_pm("0000:07:00.0", prior,
                                              sysfs_root=str(root))
            ctl = (root / "bus" / "pci" / "devices" / "0000:07:00.0"
                   / "power" / "control")
            self.assertEqual(ctl.read_text().strip(), "on")

    def test_an_unwritable_control_is_a_quiet_no_not_an_error(self) -> None:
        """Every box is in this state until the udev rule is installed, so it
        must be ordinary: hold returns None and nothing raises."""
        cards = {"0000:07:00.0": {"control": "auto", "readonly": True,
                                  "drm": "card0", "disconnected": ["DP-5"]}}
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp, cards)
            if os.geteuid() == 0:
                self.skipTest("running as root: a read-only mode does not "
                              "refuse the write, so this control cannot fire")
            self.assertIsNone(
                serving_device.hold_runtime_pm_on("0000:07:00.0",
                                                  sysfs_root=str(root)))
            ctl = (root / "bus" / "pci" / "devices" / "0000:07:00.0"
                   / "power" / "control")
            self.assertEqual(ctl.read_text().strip(), "auto",
                             "nothing may be changed when the write is refused")

    def test_an_absent_card_is_a_quiet_no(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            self.assertIsNone(
                serving_device.read_runtime_pm("0000:99:00.0",
                                               sysfs_root=str(root)))
            self.assertIsNone(
                serving_device.hold_runtime_pm_on("0000:99:00.0",
                                                  sysfs_root=str(root)))

    def test_releasing_a_value_that_was_never_held_does_nothing(self) -> None:
        """hold() returning None means nothing was changed, so release(None)
        must not write a value the code invented."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            self.assertFalse(
                serving_device.release_runtime_pm("0000:07:00.0", None,
                                                  sysfs_root=str(root)))
            ctl = (root / "bus" / "pci" / "devices" / "0000:07:00.0"
                   / "power" / "control")
            self.assertEqual(ctl.read_text().strip(), "auto")

    def test_an_unknown_prior_value_is_never_invented(self) -> None:
        """A control carrying something this code does not recognise is put
        back verbatim, not normalised to one of the two values it knows."""
        cards = {"0000:07:00.0": {"control": "something-else", "drm": "card0",
                                  "disconnected": ["DP-5"]}}
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp, cards)
            prior = serving_device.hold_runtime_pm_on("0000:07:00.0",
                                                      sysfs_root=str(root))
            self.assertEqual(prior, "something-else")
            serving_device.release_runtime_pm("0000:07:00.0", prior,
                                              sysfs_root=str(root))
            ctl = (root / "bus" / "pci" / "devices" / "0000:07:00.0"
                   / "power" / "control")
            self.assertEqual(ctl.read_text().strip(), "something-else")


class TheManagerHoldsAndReleasesAroundTheModel(unittest.TestCase):
    """RED at base: the server manager has no notion of a power hold."""

    def test_a_started_server_holds_its_card(self) -> None:
        from intergen.llama_manager import LlamaManager
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_sysfs(root, dict(TWIN_CARDS))
            m = LlamaManager()
            m._sysfs_root = str(root)
            m._hold_serving_card("0000:07:00.0")
            ctl = (root / "bus" / "pci" / "devices" / "0000:07:00.0"
                   / "power" / "control")
            self.assertEqual(ctl.read_text().strip(), "on")
            m._release_serving_card()
            self.assertEqual(ctl.read_text().strip(), "auto")

    def test_releasing_twice_is_harmless(self) -> None:
        """stop() can be reached more than once; the second release must not
        write anything."""
        from intergen.llama_manager import LlamaManager
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_sysfs(root, dict(TWIN_CARDS))
            m = LlamaManager()
            m._sysfs_root = str(root)
            m._hold_serving_card("0000:07:00.0")
            m._release_serving_card()
            m._release_serving_card()
            ctl = (root / "bus" / "pci" / "devices" / "0000:07:00.0"
                   / "power" / "control")
            self.assertEqual(ctl.read_text().strip(), "auto")

    def test_no_card_named_means_nothing_is_touched(self) -> None:
        from intergen.llama_manager import LlamaManager
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_sysfs(root, dict(TWIN_CARDS))
            m = LlamaManager()
            m._sysfs_root = str(root)
            m._hold_serving_card(None)
            m._release_serving_card()
            for pci in TWIN_CARDS:
                ctl = (root / "bus" / "pci" / "devices" / pci
                       / "power" / "control")
                self.assertEqual(ctl.read_text().strip(), "auto")


class TheUdevRuleIsShipped(unittest.TestCase):
    """The daemon cannot write power/control as itself — measured 0644 root on
    every card of this workstation — so the lane owes the rule that grants it.
    A code path that can never run on a real box is not a fix."""

    RULE = Path(__file__).resolve().parents[2] / "packages" / "ai" / "intergen"

    def _rule_text(self) -> str:
        hits = list(self.RULE.rglob("*gpu-runtime-pm*"))
        self.assertTrue(
            hits, f"no udev/tmpfiles rule granting power/control was found "
                  f"under {self.RULE}")
        return "\n".join(h.read_text(encoding="utf-8") for h in hits)

    def test_the_rule_exists_and_targets_display_controllers_only(self) -> None:
        text = self._rule_text()
        self.assertIn("power/control", text)
        self.assertIn(
            "0x0300", text,
            "the rule must be scoped to the display-controller PCI class, "
            "never to every PCI device on the machine")

    def test_the_rule_grants_a_group_and_never_world_write(self) -> None:
        text = self._rule_text()
        self.assertNotIn("0666", text, "power/control may not be world-writable")
        self.assertNotIn("o+w", text, "power/control may not be world-writable")
        self.assertIn("video", text,
                      "the daemon's own group is the one that gets the write")

    def test_no_sudo_anywhere_in_the_lane(self) -> None:
        """The cut is explicit: deliver the rule, not a privilege escalation."""
        src = (Path(__file__).resolve().parents[1] / "serving_device.py"
               ).read_text(encoding="utf-8")
        self.assertNotIn("sudo", src)
        self.assertNotIn("pkexec", src)


if __name__ == "__main__":
    unittest.main()
