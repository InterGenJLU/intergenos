# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The HIP architecture gate must ask about the CARD, not about the machine.

THE DEFECT, measured on a two-card AMD workstation on 2026-09-18. The gate that
decides whether the HIP engine may serve compared the architectures the MACHINE
has against the architectures the installed HIP build carries device code for,
and said yes when ANY of them overlapped. The device pin is decided afterwards
and is PER-CARD: the serving model takes one card. So on a machine with a
gfx1100 card and a gfx1102 card, with a HIP build covering gfx1102 and not
gfx1100, the gate said "supported" on the strength of the gfx1102 card and the
daemon then pinned the gfx1100 card — for which that build has no device code.
That is exactly the case the gate exists to prevent: llama-server segfaults at
model load instead of refusing cleanly, and a machine that would have served
correctly on Vulkan serves nothing at all.

WHAT THE FIX HAS TO DO. The supported check takes the architecture of the card
the daemon WOULD pin, obtained from the same selection that produces the pin,
so the two can never describe different cards. The architecture of a specific
card is read from the amdgpu driver's own KFD topology, which publishes each
compute node's ``gfx_target_version`` beside the ``location_id`` that encodes
its PCI bus address — no ROCm userspace and no extra tool.

THE THREE-VALUED ANSWER SURVIVES, and it is load-bearing. An unresolvable pin,
an unreadable topology or an unreadable target list must leave today's behaviour
alone; only a MEASURED "this card is not covered" refuses. Refusing on "I could
not tell" would strand every machine whose driver state is unusual.

The tests build a fake KFD topology and hand in the engine's --list-devices
text, so the whole decision is exercised with no AMD hardware present. The
location_id decodings asserted below were read from a real two-card machine's
kernel records and cross-checked against that machine's PCI addresses.
"""
from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from intergen import serving_device


# The real two-card machine this defect was measured on, as the kernel and the
# engine each report it. Node 1 is the RX 7600 (gfx1102) at PCI 0000:06:00.0;
# node 2 is the RX 7900 XT (gfx1100) at PCI 0000:0e:00.0.
_RX7600_VRAM_MB = 8176
_RX7900XT_VRAM_MB = 20464
_TWO_CARD_LIST_OUTPUT = (
    "Available devices:\n"
    "  ROCm0: AMD Radeon RX 7600 (8176 MiB, 6842 MiB free) [PCI 0000:06:00.0]\n"
    "  ROCm1: AMD Radeon RX 7900 XT (20464 MiB, 13922 MiB free)"
    " [PCI 0000:0e:00.0]\n"
)


def _topology(tmp, nodes):
    """A KFD topology tree: ``nodes`` is [(gfx_target_version, location_id)].

    Shaped like the real thing — node 0 is the CPU node the driver always
    publishes, with a zero architecture and a zero location, so the reader has
    to skip it rather than mistake it for a GPU.
    """
    root = Path(tmp) / "nodes"
    entries = [(0, 0)] + list(nodes)
    for i, (version, location_id) in enumerate(entries):
        node = root / str(i)
        node.mkdir(parents=True)
        (node / "properties").write_text(
            f"cpu_cores_count {0 if i else 16}\n"
            f"simd_count {64 if i else 0}\n"
            f"gfx_target_version {version}\n"
            f"location_id {location_id}\n"
            "domain 0\n"
            "max_engine_clk_ccompute 3200\n")
    return str(root)


def _targets_file(tmp, text):
    p = Path(tmp) / "gpu-targets"
    p.write_text(text)
    return str(p)


def _fake_binary(directory, name):
    """An executable file standing in for an engine's server binary."""
    p = Path(directory) / name
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p)


class LocationIdDecodingTest(unittest.TestCase):
    """location_id packs bus/device/function; the domain is published apart."""

    def test_the_two_cards_measured_on_the_real_machine(self):
        self.assertEqual(
            serving_device._pci_address_from_location(0, 1536), "0000:06:00.0")
        self.assertEqual(
            serving_device._pci_address_from_location(0, 3584), "0000:0e:00.0")

    def test_a_nonzero_device_and_function_are_decoded(self):
        # bus 0x03, device 0x00, function 1 -> 0x0301
        self.assertEqual(
            serving_device._pci_address_from_location(0, 0x0301),
            "0000:03:00.1")
        # bus 0x41, device 0x1f, function 7
        self.assertEqual(
            serving_device._pci_address_from_location(1, 0x41FF),
            "0001:41:1f.7")


class TopologyByPciTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gfx-by-pci-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))

    def test_each_card_is_keyed_by_its_own_address(self):
        root = _topology(self.tmp, [(110002, 1536), (110000, 3584)])
        self.assertEqual(
            serving_device.amd_gfx_targets_by_pci(root),
            {"0000:06:00.0": "gfx1102", "0000:0e:00.0": "gfx1100"})

    def test_the_cpu_node_is_not_a_gpu(self):
        """Node 0 reports gfx_target_version 0 and must not become an entry."""
        root = _topology(self.tmp, [])
        self.assertEqual(serving_device.amd_gfx_targets_by_pci(root), {})

    def test_an_absent_topology_is_empty_not_an_error(self):
        self.assertEqual(
            serving_device.amd_gfx_targets_by_pci(
                os.path.join(self.tmp, "nope")), {})

    def test_it_agrees_with_the_machine_level_reader(self):
        root = _topology(self.tmp, [(110002, 1536), (110000, 3584)])
        self.assertEqual(
            set(serving_device.amd_gfx_targets_by_pci(root).values()),
            serving_device.detect_amd_gfx_targets(root))


class PinnedCardSupportTest(unittest.TestCase):
    """The gate's decision, with the pin and the topology handed in."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pinned-card-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.root = _topology(self.tmp, [(110002, 1536), (110000, 3584)])
        # A HIP build covering the RX 7600's architecture and NOT the
        # RX 7900 XT's — the shortened list this defect was measured with.
        self.targets = _targets_file(self.tmp, "gfx1102;gfx1201\n")
        self.sysfs = os.path.join(self.tmp, "sysfs")

    def _support(self, discrete_vram_mb, list_output=_TWO_CARD_LIST_OUTPUT):
        return serving_device.hip_supports_serving_device(
            list_output=list_output,
            discrete_vram_mb=discrete_vram_mb,
            sysfs_root=self.sysfs,
            topology_root=self.root,
            targets_path=self.targets)

    def test_the_uncovered_card_is_refused(self):
        """THE DEFECT. The machine has gfx1102; the PIN is the gfx1100 card."""
        support = self._support(_RX7900XT_VRAM_MB)
        self.assertIs(support.supported, False)
        self.assertEqual(support.pci_id, "0000:0e:00.0")
        self.assertEqual(support.gfx, "gfx1100")

    def test_the_machine_level_check_would_have_said_yes(self):
        """The control that makes the test above mean something.

        If the old machine-level answer were also False here, the two-card
        fixture would prove nothing about which question is being asked.
        """
        self.assertIs(
            serving_device.hip_is_supported_here(self.root, self.targets),
            True)

    def test_the_covered_card_is_accepted(self):
        support = self._support(_RX7600_VRAM_MB)
        self.assertIs(support.supported, True)
        self.assertEqual(support.pci_id, "0000:06:00.0")
        self.assertEqual(support.gfx, "gfx1102")

    def test_the_refusal_names_the_card_and_the_missing_target(self):
        support = self._support(_RX7900XT_VRAM_MB)
        self.assertIn("0000:0e:00.0", support.reason)
        self.assertIn("gfx1100", support.reason)
        self.assertIn("gfx1102", support.reason)

    def test_an_unresolvable_pin_falls_back_to_the_machine_level_answer(self):
        """An engine build without the in-tree PCI suffix names no address.

        There is then no card to ask about, and the answer must be exactly
        what it was before this gate learned to ask — never a refusal on
        "I could not tell".
        """
        no_pci = (
            "Available devices:\n"
            "  ROCm1: AMD Radeon RX 7900 XT (20464 MiB, 13922 MiB free)\n")
        support = self._support(_RX7900XT_VRAM_MB, list_output=no_pci)
        self.assertIs(support.supported, True)
        self.assertIsNone(support.pci_id)

    def test_a_card_missing_from_the_topology_falls_back(self):
        """The pin resolved, but no KFD node claims that address."""
        elsewhere = _topology(self.tmp + "-b", [(110002, 1536)])
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp + "-b", ignore_errors=True))
        support = serving_device.hip_supports_serving_device(
            list_output=_TWO_CARD_LIST_OUTPUT,
            discrete_vram_mb=_RX7900XT_VRAM_MB,
            sysfs_root=self.sysfs,
            topology_root=elsewhere,
            targets_path=self.targets)
        self.assertIs(support.supported, True)   # the machine-level answer
        self.assertIsNone(support.gfx)

    def test_an_unreadable_topology_is_none_not_false(self):
        support = serving_device.hip_supports_serving_device(
            list_output=_TWO_CARD_LIST_OUTPUT,
            discrete_vram_mb=_RX7900XT_VRAM_MB,
            sysfs_root=self.sysfs,
            topology_root=os.path.join(self.tmp, "nope"),
            targets_path=self.targets)
        self.assertIsNone(support.supported)

    def test_a_missing_target_list_is_none_not_false(self):
        support = serving_device.hip_supports_serving_device(
            list_output=_TWO_CARD_LIST_OUTPUT,
            discrete_vram_mb=_RX7900XT_VRAM_MB,
            sysfs_root=self.sysfs,
            topology_root=self.root,
            targets_path=os.path.join(self.tmp, "no-such-file"))
        self.assertIsNone(support.supported)


class OperatorDevicePinTest(unittest.TestCase):
    """A hand-pinned card is the card the gate must ask about.

    ``llama_server.device`` set to a literal ggml name is supreme over the
    selector — the serving model goes onto THAT card. Asking the automatic
    selector instead would give the wrong answer in both directions: refusing
    HIP over a card the operator excluded, or accepting it over a card they
    never chose. Both directions are checked below on the same two-card
    fixture, where the automatic answer and the pinned answer differ.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="operator-pin-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.root = _topology(self.tmp, [(110002, 1536), (110000, 3584)])
        self.targets = _targets_file(self.tmp, "gfx1102;gfx1201\n")
        self.sysfs = os.path.join(self.tmp, "sysfs")

    def _support(self, device_pin):
        return serving_device.hip_supports_serving_device(
            device_pin=device_pin,
            list_output=_TWO_CARD_LIST_OUTPUT,
            discrete_vram_mb=_RX7900XT_VRAM_MB,
            sysfs_root=self.sysfs,
            topology_root=self.root,
            targets_path=self.targets)

    def test_a_pinned_covered_card_is_accepted(self):
        """The automatic selector would pick the UNCOVERED card here."""
        support = self._support("ROCm0")
        self.assertIs(support.supported, True)
        self.assertEqual(support.pci_id, "0000:06:00.0")
        self.assertEqual(support.gfx, "gfx1102")

    def test_a_pinned_uncovered_card_is_refused(self):
        support = self._support("ROCm1")
        self.assertIs(support.supported, False)
        self.assertEqual(support.gfx, "gfx1100")

    def test_auto_is_not_a_card_name(self):
        """"auto" and the empty string mean "no pin", not a device called auto."""
        for value in ("auto", "", "  "):
            with self.subTest(value=value):
                self.assertIs(self._support(value).supported, False)

    def test_a_name_the_engine_does_not_know_falls_back(self):
        """No card is identified, so today's behaviour must stand."""
        support = self._support("Vulkan9")
        self.assertIs(support.supported, True)   # the machine-level answer
        self.assertIsNone(support.pci_id)


class SingleCardControlTest(unittest.TestCase):
    """A one-card machine must answer exactly as it did before."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="single-card-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.sysfs = os.path.join(self.tmp, "sysfs")
        self.list_output = (
            "Available devices:\n"
            "  ROCm0: AMD Radeon RX 7600 (8176 MiB, 6842 MiB free)"
            " [PCI 0000:06:00.0]\n")

    def _answer(self, gfx_version, targets_text):
        root = _topology(self.tmp, [(gfx_version, 1536)])
        targets = _targets_file(self.tmp, targets_text)
        support = serving_device.hip_supports_serving_device(
            list_output=self.list_output,
            discrete_vram_mb=_RX7600_VRAM_MB,
            sysfs_root=self.sysfs,
            topology_root=root,
            targets_path=targets)
        machine_level = serving_device.hip_is_supported_here(root, targets)
        return support.supported, machine_level

    def test_a_covered_single_card_is_accepted_as_before(self):
        per_card, machine_level = self._answer(110002, "gfx1100;gfx1102\n")
        self.assertIs(per_card, True)
        self.assertIs(machine_level, True)

    def test_an_uncovered_single_card_is_refused_as_before(self):
        """The gfx90c APU case, which is why this gate exists at all."""
        per_card, machine_level = self._answer(90012, "gfx1100;gfx1102\n")
        self.assertIs(per_card, False)
        self.assertIs(machine_level, False)


class EngineChoiceWiringTest(unittest.TestCase):
    """select_serving_engine and engine_ladder must consult the per-card gate.

    The decision itself is proved above; what is proved here is that the two
    places that choose an engine actually ask the new question, end to end,
    with only the pin injected.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="engine-wiring-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self._orig_paths = dict(serving_device.ENGINE_SERVER_PATHS)
        self.addCleanup(
            lambda: serving_device.ENGINE_SERVER_PATHS.update(self._orig_paths))
        self._orig_select = serving_device.select_serving_device_and_pci
        self.addCleanup(
            lambda: setattr(serving_device, "select_serving_device_and_pci",
                            self._orig_select))
        self._orig_topology = serving_device.KFD_TOPOLOGY_NODES
        self._orig_targets = serving_device.HIP_GPU_TARGETS_PATH
        self.addCleanup(
            lambda: setattr(serving_device, "KFD_TOPOLOGY_NODES",
                            self._orig_topology))
        self.addCleanup(
            lambda: setattr(serving_device, "HIP_GPU_TARGETS_PATH",
                            self._orig_targets))
        for engine in ("hip", "vulkan"):
            serving_device.ENGINE_SERVER_PATHS[engine] = _fake_binary(
                self.tmp, f"{engine}-server")
        serving_device.ENGINE_SERVER_PATHS["cuda"] = os.path.join(
            self.tmp, "absent-cuda")
        serving_device.KFD_TOPOLOGY_NODES = _topology(
            self.tmp, [(110002, 1536), (110000, 3584)])
        serving_device.HIP_GPU_TARGETS_PATH = _targets_file(
            self.tmp, "gfx1102;gfx1201\n")

    def _pin(self, name, pci):
        serving_device.select_serving_device_and_pci = (
            lambda *a, **k: (name, pci))

    def test_hip_is_refused_when_the_pinned_card_is_uncovered(self):
        self._pin("ROCm1", "0000:0e:00.0")
        engine, path = serving_device.select_serving_engine(vendor="amd")
        self.assertEqual(engine, "vulkan")
        self.assertEqual(
            [e for e, _ in serving_device.engine_ladder("amd")], ["vulkan"])

    def test_hip_is_chosen_when_the_pinned_card_is_covered(self):
        self._pin("ROCm0", "0000:06:00.0")
        engine, _path = serving_device.select_serving_engine(vendor="amd")
        self.assertEqual(engine, "hip")
        self.assertEqual(
            [e for e, _ in serving_device.engine_ladder("amd")],
            ["hip", "vulkan"])

    def test_an_operator_device_pin_reaches_the_gate(self):
        """The automatic selector picks the uncovered card; the operator
        pinned the covered one, and HIP is then the right answer."""
        self._pin("ROCm1", "0000:0e:00.0")
        self._orig_lookup = serving_device.pci_for_device_name
        self.addCleanup(
            lambda: setattr(serving_device, "pci_for_device_name",
                            self._orig_lookup))
        serving_device.pci_for_device_name = (
            lambda name, *a, **k: {"ROCm0": "0000:06:00.0",
                                   "ROCm1": "0000:0e:00.0"}.get(name))
        engine, _path = serving_device.select_serving_engine(
            vendor="amd", device_pin="ROCm0")
        self.assertEqual(engine, "hip")
        engine, _path = serving_device.select_serving_engine(
            vendor="amd", device_pin="ROCm1")
        self.assertEqual(engine, "vulkan")


class TheLaunchersFallbackAsksTheSameQuestionTest(unittest.TestCase):
    """LlamaManager._find_server is the second place an engine is chosen.

    The daemon resolves the engine itself and hands the binary to start(), so
    this fallback runs only for a caller that did not — but when it runs it must
    ask the SAME question, about the SAME card. Found by running the fix as the
    installed daemon on 2026-09-18: with the device pinned to the covered card,
    the daemon chose HIP and this call, asking without the pin, logged Vulkan in
    the same start. The launch was correct; the second answer was not, and on a
    caller that uses it the pinned card's engine would have been declined.
    """

    def test_it_passes_the_pinned_device_to_the_selector(self):
        from intergen import llama_manager

        seen = {}

        def _fake_select(vendor=None, engine_pin=None, device_pin=None):
            seen["called"] = True
            seen["device_pin"] = device_pin
            return "vulkan", "/usr/bin/llama-server"

        import intergen.serving_device as sd
        orig = sd.select_serving_engine
        sd.select_serving_engine = _fake_select
        self.addCleanup(lambda: setattr(sd, "select_serving_engine", orig))

        mgr = llama_manager.LlamaManager.__new__(llama_manager.LlamaManager)
        mgr._find_server(device_pin="ROCm0")
        self.assertTrue(seen.get("called"), "the selector was never called")
        self.assertEqual(seen.get("device_pin"), "ROCm0")

    def test_no_pin_is_still_no_pin(self):
        from intergen import llama_manager

        seen = {}

        def _fake_select(vendor=None, engine_pin=None, device_pin=None):
            seen["called"] = True
            seen["device_pin"] = device_pin
            return "vulkan", "/usr/bin/llama-server"

        import intergen.serving_device as sd
        orig = sd.select_serving_engine
        sd.select_serving_engine = _fake_select
        self.addCleanup(lambda: setattr(sd, "select_serving_engine", orig))

        mgr = llama_manager.LlamaManager.__new__(llama_manager.LlamaManager)
        mgr._find_server()
        # assertIsNone alone would ALSO pass if the stand-in had never been
        # called: anything raised inside _find_server is caught there and falls
        # through to the path search, leaving `seen` empty. A future change to
        # the selector's signature would then leave this case green while it
        # measured nothing. The call itself is asserted first.
        self.assertTrue(seen.get("called"), "the selector was never called")
        self.assertIsNone(seen.get("device_pin"))


class CudaCounterpartTest(unittest.TestCase):
    """The CUDA gate is machine-level too, and that is not the same defect.

    ``cuda_is_usable_here`` asks whether SOME NVIDIA card on this machine is
    bound to NVIDIA's proprietary driver, which is a machine-level question of
    exactly the shape the HIP gate had. It does not produce the same defect,
    and the reason is structural rather than a second gate: the pin is chosen
    from the CHOSEN ENGINE'S OWN enumeration, and the CUDA runtime does not
    enumerate a card that is not on its driver. A card the CUDA build cannot
    use is therefore never a candidate to pin, so there is no equivalent of
    "accepted for card A, pinned onto card B".

    This test pins that reasoning to the selection code, so a change that made
    the selector enumerate with something other than the launching binary
    would fail here instead of silently reopening the machine-level shape.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cuda-counterpart-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))

    def test_only_the_engines_own_devices_are_candidates(self):
        """Two NVIDIA cards, one on nouveau: the CUDA build lists one."""
        cuda_list = (
            "Available devices:\n"
            "  CUDA0: NVIDIA GeForce RTX 4070 (12282 MiB, 11900 MiB free)"
            " [PCI 0000:01:00.0]\n")
        name, pci = serving_device.select_serving_device_and_pci(
            list_output=cuda_list, discrete_vram_mb=12282,
            sysfs_root=os.path.join(self.tmp, "sysfs"))
        self.assertEqual(name, "CUDA0")
        self.assertEqual(pci, "0000:01:00.0")

    def test_the_cuda_gate_is_still_a_machine_level_question(self):
        """Stated rather than implied: it reads driver binding, not a card."""
        empty = Path(self.tmp) / "drm-empty"
        empty.mkdir()
        self.assertIs(serving_device.cuda_is_usable_here(str(empty)), False)


if __name__ == "__main__":
    unittest.main()
