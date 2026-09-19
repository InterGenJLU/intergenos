# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A server that pins no card says so, and the card gate is still asked.

THE DEFECT, measured on a two-card AMD workstation on 2026-09-18 and again on
2026-09-19. The embedding server is CPU-resident by design: it starts with zero
GPU layers, and the launcher answers that with ``--device none``. No card is
pinned for it.

It nevertheless reached the engine selector with no device pin, and the selector
did what it does with no pin: it asked the per-card architecture gate about the
card the AUTOMATIC selection would choose, and the launcher logged that card
decision with nothing around it to say what the start was. With one architecture
removed from the installed build's record, the line read as a refusal of an
engine over a card the reader had no reason to connect to this start.

THE DEFECT IS THE LINE, NOT THE QUESTION. Decided 2026-09-19, against an earlier
reading of it: the per-card gate stays consulted for a cardless start. The engine
binary enumerates every visible card when it starts, whatever ``--device none``
then does about loading a model onto one, so "can this build cope with the cards
in this machine" is a real question even for a start that pins none. Dropping the
gate would replace a MEASURED refusal with an EXPECTATION about how an uncovered
card behaves during enumeration — an expectation no machine in this project can
currently test. A wrong but silent choice is worse than a correct one that has to
be read carefully.

WHAT THE FIX HAS TO DO.
  1. A CPU-pinned start tells the launcher so. The launcher already has the
     notion: gpu_layers == 0 is what produces ``--device none``, and that wins
     over any device argument, so gpu_layers == 0 IS "this start takes no card".
  2. The launcher logs ONE line BEFORE the selection, saying that this start is
     CPU-pinned, that no card is pinned, and why the card gate is asked anyway.
     The embedding server is named when it is one.
  3. Nothing else moves. ``select_serving_engine`` is not changed and takes no
     new argument. The gates are consulted exactly as before, the same engine is
     resolved as before, and the decline line that may follow is unchanged — it
     is simply now read underneath a line that says what the start is.

Everything below runs with no GPU present: fake engine binaries, a fake KFD
topology, and a spy on the gate that records whether it was asked at all.
"""
from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from intergen import serving_device

# The line the launcher must write, in full. A test that matched a fragment
# would pass on a line that said half of it.
EXPECTED_EMBEDDING_LINE = (
    "engine selection for the embedding server: CPU-pinned (--device none), "
    "no card is pinned; the per-card architecture gate is still asked about "
    "the card the automatic selection would take, because the engine binary "
    "enumerates every visible card at start"
)
EXPECTED_GENERIC_LINE = EXPECTED_EMBEDDING_LINE.replace(
    "the embedding server", "a CPU-resident server")


def _topology(tmp, nodes):
    """A KFD topology tree; node 0 is the CPU node the driver always writes."""
    root = Path(tmp) / "nodes"
    for i, (version, location_id) in enumerate([(0, 0)] + list(nodes)):
        node = root / str(i)
        node.mkdir(parents=True)
        (node / "properties").write_text(
            f"cpu_cores_count {0 if i else 16}\n"
            f"simd_count {64 if i else 0}\n"
            f"gfx_target_version {version}\n"
            f"location_id {location_id}\n"
            "domain 0\n")
    return str(root)


def _targets_file(tmp, text):
    p = Path(tmp) / "gpu-targets"
    p.write_text(text)
    return str(p)


def _fake_binary(directory, name):
    p = Path(directory) / name
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p)


class _GateSpy:
    """Stands in for the HIP architecture gate and records every question."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append(kwargs)
        return self.answer


class TheLauncherSaysWhatAStartIsTest(unittest.TestCase):
    """LlamaManager._find_server is where the daemon's embedding server, which
    passes no resolved engine, reaches the selector."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cardless-launcher-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self._orig_paths = dict(serving_device.ENGINE_SERVER_PATHS)
        self.addCleanup(
            lambda: serving_device.ENGINE_SERVER_PATHS.update(self._orig_paths))
        for engine in ("hip", "vulkan"):
            serving_device.ENGINE_SERVER_PATHS[engine] = _fake_binary(
                self.tmp, f"{engine}-server")

    def _manager(self):
        from intergen import llama_manager
        return llama_manager.LlamaManager.__new__(llama_manager.LlamaManager)

    def _record_selector(self):
        """Replace the selector and record exactly what it was asked.

        The stand-in takes the selector's REAL signature. If the launcher ever
        grows a new keyword for it, this raises rather than silently falling
        through to the path search — which is how a sibling test in
        test_engine_gate_asks_about_the_pinned_card.py once stayed green for
        the wrong reason.
        """
        seen = {}
        orig = serving_device.select_serving_engine

        def fake(vendor=None, engine_pin=None, device_pin=None):
            seen["called"] = True
            seen["device_pin"] = device_pin
            return "hip", serving_device.ENGINE_SERVER_PATHS["hip"]

        serving_device.select_serving_engine = fake
        self.addCleanup(
            lambda: setattr(serving_device, "select_serving_engine", orig))
        return seen

    def test_the_embedding_server_gets_its_own_line_in_full(self):
        """THE RED. The whole sentence, not a fragment of it."""
        self._record_selector()
        with self.assertLogs("intergen.llama_manager", level="INFO") as logged:
            self._manager()._find_server(cpu_pinned=True, embedding=True)
        self.assertIn(EXPECTED_EMBEDDING_LINE, "\n".join(logged.output))

    def test_a_cardless_start_that_is_not_the_embedder_says_so_too(self):
        """THE RED. The same sentence, naming what it actually is."""
        self._record_selector()
        with self.assertLogs("intergen.llama_manager", level="INFO") as logged:
            self._manager()._find_server(cpu_pinned=True)
        self.assertIn(EXPECTED_GENERIC_LINE, "\n".join(logged.output))

    def test_the_line_comes_BEFORE_the_selection(self):
        """THE RED. It explains the decline line that may follow, so it has to
        be written before the selector is asked, not after it answers."""
        self._record_selector()
        with self.assertLogs("intergen.llama_manager", level="INFO") as logged:
            self._manager()._find_server(cpu_pinned=True, embedding=True)
        text = [r.getMessage() for r in logged.records]
        said = next(i for i, m in enumerate(text)
                    if EXPECTED_EMBEDDING_LINE in m)
        chose = next(i for i, m in enumerate(text)
                     if "engine selector chose" in m)
        self.assertLess(said, chose)

    def test_a_cardless_start_still_asks_the_selector_with_no_pin(self):
        """THE RED for the classification: no device pin travels for a start
        that pins no card, and the selector is asked in the ordinary way."""
        seen = self._record_selector()
        with self.assertLogs("intergen.llama_manager", level="INFO"):
            self._manager()._find_server(cpu_pinned=True, embedding=True)
        self.assertTrue(seen.get("called"), "the selector was never called")
        self.assertIsNone(seen["device_pin"])

    def test_a_card_taking_start_is_unchanged_and_silent(self):
        """The control: the pin still travels, and no cardless line is written
        for a start that does take a card."""
        seen = self._record_selector()
        with self.assertLogs("intergen.llama_manager", level="INFO") as logged:
            self._manager()._find_server(device_pin="ROCm0")
        self.assertTrue(seen.get("called"), "the selector was never called")
        self.assertEqual(seen["device_pin"], "ROCm0")
        text = "\n".join(logged.output)
        self.assertNotIn("CPU-pinned", text)
        self.assertNotIn("no card is pinned", text)

    def test_the_fallback_path_search_is_untouched(self):
        """When the selector cannot answer at all, the floor is the same path
        search it has always been — for a cardless start too."""
        orig = serving_device.select_serving_engine

        def boom(*a, **k):
            raise RuntimeError("selector unavailable")

        serving_device.select_serving_engine = boom
        self.addCleanup(
            lambda: setattr(serving_device, "select_serving_engine", orig))
        got = self._manager()._find_server(cpu_pinned=True)
        self.assertTrue(got is None or got.endswith("llama-server"))


class TheGateIsStillAskedForACardlessStartTest(unittest.TestCase):
    """The whole point of the correction: the card question is still asked, and
    the engine a cardless start resolves is the same one it resolved before.

    THIS CLASS ASSERTS ON AN AMD MACHINE, and pins that below rather than
    letting the machine it runs on decide. The property under test is that the
    per-card HIP gate is ASKED, and only the AMD row of the engine preference
    table has a HIP rung to ask about: on a machine whose detector answers
    "nvidia" the row is cuda then vulkan, the gate is never reached, and both
    assertions below are about something that machine never does. Measured
    2026-09-19: with the vendor detector forced to "nvidia" these two tests
    failed at the tree they were written on (the gate spy recorded 0 calls
    where they expect 1) and passed with it forced to "amd" — a result that
    read the host rather than the code. The class stands in for the detector
    for the same reason it stands in for the topology file, the architecture
    record and the device selector: every one of them is the machine, and a
    test that reads the machine is measuring the machine."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cardless-gate-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self._orig_paths = dict(serving_device.ENGINE_SERVER_PATHS)
        self.addCleanup(
            lambda: serving_device.ENGINE_SERVER_PATHS.update(self._orig_paths))
        for attr in ("KFD_TOPOLOGY_NODES", "HIP_GPU_TARGETS_PATH",
                     "hip_supports_serving_device",
                     "select_serving_device_and_pci", "_detect_vendor"):
            orig = getattr(serving_device, attr)
            self.addCleanup(
                lambda a=attr, o=orig: setattr(serving_device, a, o))
        for engine in ("hip", "vulkan"):
            serving_device.ENGINE_SERVER_PATHS[engine] = _fake_binary(
                self.tmp, f"{engine}-server")
        serving_device.ENGINE_SERVER_PATHS["cuda"] = os.path.join(
            self.tmp, "absent-cuda")
        serving_device.KFD_TOPOLOGY_NODES = _topology(
            self.tmp, [(110002, 1536), (110000, 3584)])
        # A SHORTENED record: the installed build covers neither card, so the
        # gate — when it is asked — refuses HIP. That is what makes "was it
        # asked?" visible in the answer rather than only in a spy.
        serving_device.HIP_GPU_TARGETS_PATH = _targets_file(
            self.tmp, "gfx1030;gfx1201\n")
        serving_device.select_serving_device_and_pci = (
            lambda *a, **k: ("ROCm1", "0000:0e:00.0"))
        # The machine this class asserts on. select_serving_engine() asks the
        # detector when no vendor is passed, and _find_server() passes none.
        serving_device._detect_vendor = lambda: "amd"

    def _spy(self, answer_supported):
        spy = _GateSpy(serving_device.HipDeviceSupport(
            answer_supported, "0000:0e:00.0", "gfx1100", frozenset(), "spy"))
        serving_device.hip_supports_serving_device = spy
        return spy

    def _manager(self):
        from intergen import llama_manager
        return llama_manager.LlamaManager.__new__(llama_manager.LlamaManager)

    def test_a_cardless_start_resolves_the_SAME_engine_as_before(self):
        """THE RED, and the reason the correction exists. On a shortened record
        the gate refuses HIP, and a cardless start must land on exactly the
        engine it landed on before this change — vulkan — not on the HIP build
        the preference table would otherwise have picked first."""
        spy = self._spy(False)
        with self.assertLogs("intergen.llama_manager", level="INFO"):
            got = self._manager()._find_server(cpu_pinned=True, embedding=True)
        self.assertEqual(len(spy.calls), 1, "the card gate was not asked")
        self.assertEqual(got, serving_device.ENGINE_SERVER_PATHS["vulkan"])

    def test_the_gate_is_asked_about_the_automatic_card_with_no_pin(self):
        """The gate is asked in the ordinary no-pin way: the selector resolves
        the card itself, exactly as it does for any unpinned caller."""
        spy = self._spy(False)
        with self.assertLogs("intergen.llama_manager", level="INFO"):
            self._manager()._find_server(cpu_pinned=True, embedding=True)
        self.assertEqual(len(spy.calls), 1)
        self.assertIsNone(spy.calls[0].get("device_pin"))

    def test_the_selector_itself_takes_no_new_argument(self):
        """The correction is confined to the launcher. A future change that
        reintroduced a gate-skipping switch on the selector would fail here."""
        import inspect
        names = list(inspect.signature(
            serving_device.select_serving_engine).parameters)
        self.assertEqual(names, ["vendor", "engine_pin", "device_pin"])

    def test_an_ordinary_card_taking_start_is_unchanged(self):
        """The control: nothing about a start that does take a card moves."""
        spy = self._spy(False)
        engine, _path = serving_device.select_serving_engine(vendor="amd")
        self.assertEqual(engine, "vulkan")
        self.assertEqual(len(spy.calls), 1)


class TheStartPathClassifiesItselfTest(unittest.TestCase):
    """start() is where a CPU-pinned start is recognised: gpu_layers == 0 is
    already what produces --device none, and --device none wins over any
    device argument, so gpu_layers == 0 IS 'this start takes no card'."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cardless-start-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.model = Path(self.tmp) / "model.gguf"
        self.model.write_text("not really a model")

    def _manager_recording_find_server(self):
        from intergen import llama_manager
        mgr = llama_manager.LlamaManager.__new__(llama_manager.LlamaManager)
        seen = {}

        def fake_find(device_pin=None, cpu_pinned=False, embedding=False):
            seen["device_pin"] = device_pin
            seen["cpu_pinned"] = cpu_pinned
            seen["embedding"] = embedding
            return None            # stops the start right after the choice

        mgr._find_server = fake_find
        mgr._last_error = None
        mgr._last_failure = None
        return mgr, seen

    def test_zero_gpu_layers_reaches_the_launcher_as_cardless(self):
        """THE RED."""
        mgr, seen = self._manager_recording_find_server()
        mgr.start(str(self.model), port=8081, context_size=2048, gpu_layers=0,
                  parallel=1, jinja=False, embedding=True)
        self.assertIs(seen["cpu_pinned"], True)
        self.assertIs(seen["embedding"], True)

    def test_a_gpu_start_is_not_cardless(self):
        mgr, seen = self._manager_recording_find_server()
        mgr.start(str(self.model), port=8080, context_size=4096, gpu_layers=99,
                  parallel=1, jinja=False, device="ROCm0")
        self.assertIs(seen["cpu_pinned"], False)
        self.assertEqual(seen["device_pin"], "ROCm0")

    def test_zero_layers_with_a_device_is_still_cardless(self):
        """--device none is supreme over a device argument at zero layers, so
        the classification has to agree with the argv the launcher builds."""
        mgr, seen = self._manager_recording_find_server()
        mgr.start(str(self.model), port=8081, context_size=2048, gpu_layers=0,
                  parallel=1, jinja=False, device="ROCm0")
        self.assertIs(seen["cpu_pinned"], True)


if __name__ == "__main__":
    unittest.main()
