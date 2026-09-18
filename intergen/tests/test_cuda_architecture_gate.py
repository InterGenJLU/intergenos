# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The CUDA engine's architecture gate: does the installed build carry code
this machine's card can actually run?

The HIP engine has had this gate since the APU that segfaulted at model load
(tests/installer/test_gpu_detect_gfx_gate.py). The CUDA engine shipped with a
usability test that asked only whether NVIDIA's proprietary driver was bound —
a real question, but a different one. A card can be NVIDIA, on the proprietary
driver, and still be outside the set of architectures the installed engine was
compiled for; CUDA 13 dropped every architecture below Turing, so a Volta card
on a perfectly good driver has neither compiled kernels nor PTX it can JIT
from in the shipped build.

The CUDA case differs from the HIP case in one way that the gate has to
respect rather than flatten: upstream's declared target list carries `-virtual`
PTX entries as well as `-real` compiled ones, and the driver JIT-compiles PTX
for any newer architecture. So "my exact number is not in the list" is not the
same as "this build cannot run here", and a gate that treated it that way would
refuse the engine on hardware it serves perfectly well after a one-time JIT
pause.
"""
from __future__ import annotations

import os
import tempfile
import unittest

from intergen import serving_device


def _targets_file(tmp: str, text: str) -> str:
    path = os.path.join(tmp, "gpu-targets")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


# The list the shipped recipe declares, so these cases are about the engine
# users actually receive rather than about an invented one. Since the recipe's
# r6 every generation the pinned toolkit can emit is declared -real (a PTX
# entry was measured to fail on a shipped machine whose driver was older than
# the toolkit), so the shipped list carries no -virtual token; the reader keeps
# its -virtual handling for a record that does carry one, and the fixture
# cases below exercise it.
SHIPPED = ("75-real;80-real;86-real;87-real;88-real;89-real;90-real;100-real;"
           "103-real;110-real;120a-real;121a-real")
SHIPPED_SET = {"75-real", "80-real", "86-real", "87-real", "88-real",
               "89-real", "90-real", "100-real", "103-real", "110-real",
               "120a-real", "121a-real"}


class TargetListReaderTest(unittest.TestCase):
    """Reading the record the recipe installs beside the engine."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cuda-targets-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))

    def test_the_list_parses_in_the_shape_the_recipe_writes(self):
        """cmake's own semicolon-separated form, and the tolerant siblings."""
        for text in (SHIPPED + "\n",
                     SHIPPED.replace(";", " ") + "\n",
                     SHIPPED.replace(";", ",") + "\n"):
            with self.subTest(text=text):
                self.assertEqual(
                    serving_device.cuda_build_gpu_targets(
                        _targets_file(self.tmp, text)),
                    SHIPPED_SET)

    def test_a_missing_file_is_an_empty_set_not_an_exception(self):
        self.assertEqual(
            serving_device.cuda_build_gpu_targets(
                os.path.join(self.tmp, "no-such-file")),
            set())

    def test_comments_are_not_targets(self):
        self.assertEqual(
            serving_device.cuda_build_gpu_targets(
                _targets_file(self.tmp, "# written by the recipe\n86-real\n")),
            {"86-real"})


class RecordPathTest(unittest.TestCase):
    """Where the runtime looks for the record.

    Deliberately NOT in RecipeParityTest below: this asserts a module constant
    and needs no packaging tree, so it must keep running on an installed
    machine, where it is the half of the contract that can still be checked.
    """

    def test_the_runtime_reads_under_the_engine_s_own_prefix(self):
        self.assertEqual(
            serving_device.CUDA_GPU_TARGETS_PATH,
            "/opt/llama-cpp-cuda/share/llama-cpp-cuda/gpu-targets")


class TargetCoverageTest(unittest.TestCase):
    """Which compute capabilities the declared list can actually serve."""

    TARGETS = {"75-virtual", "80-virtual", "86-real", "89-real",
               "120a-real", "121a-real"}

    def test_a_compiled_target_is_covered(self):
        """8.6 is 86-real: this box's card, kernels already compiled."""
        self.assertIs(serving_device._cuda_targets_cover(86, self.TARGETS), True)

    def test_an_exact_virtual_target_is_covered(self):
        """7.5 is 75-virtual: PTX, JIT at first load."""
        self.assertIs(serving_device._cuda_targets_cover(75, self.TARGETS), True)

    def test_an_architecture_above_a_virtual_target_is_covered_by_jit(self):
        """9.0 has no entry of its own, but 80-virtual's PTX JITs forward.

        This is the case that makes the CUDA gate different from the HIP one.
        A set-membership test would refuse the engine here, and the engine
        runs.
        """
        self.assertIs(serving_device._cuda_targets_cover(90, self.TARGETS), True)

    def test_an_architecture_below_every_target_is_not_covered(self):
        """7.0 is Volta. CUDA 13 dropped it; nothing in the list reaches it."""
        self.assertIs(serving_device._cuda_targets_cover(70, self.TARGETS), False)

    def test_an_architecture_specific_target_does_not_cover_newer_cards(self):
        """The 'a' suffix means not forwards compatible — upstream's own note.

        120a-real is Blackwell-specific FP4 tensor-core code. It must not be
        read as covering 121, and 121a-real must not be read as covering 130.
        """
        self.assertIs(
            serving_device._cuda_targets_cover(130, {"120a-real", "121a-real"}),
            False)

    def test_an_architecture_specific_target_covers_its_own_number(self):
        self.assertIs(
            serving_device._cuda_targets_cover(120, {"120a-real"}), True)

    def test_an_empty_list_covers_nothing(self):
        self.assertIs(serving_device._cuda_targets_cover(86, set()), False)


class ComputeCapabilityReaderTest(unittest.TestCase):
    """Per-card compute capability, read from the driver's own tool.

    Unlike the HIP side, which reads the amdgpu topology out of sysfs, no
    kernel interface publishes an NVIDIA card's compute capability — the
    number comes from the driver's own query tool. That is acceptable here
    only because the CUDA engine already requires the proprietary driver to
    serve at all, and the tool ships with it; when the tool is absent or
    unreadable the answer is UNKNOWN, never "unsupported".
    """

    def test_per_card_capabilities_are_parsed_with_their_pci_ids(self):
        out = ("0, NVIDIA GeForce RTX 3070 Ti Laptop GPU, 8.6, 00000000:01:00.0\n"
               "1, NVIDIA GeForce RTX 4090, 8.9, 00000000:02:00.0\n")
        self.assertEqual(
            serving_device._parse_compute_caps(out),
            {"0000:01:00.0": 86, "0000:02:00.0": 89})

    def test_a_blackwell_minor_zero_is_not_lost(self):
        out = "0, NVIDIA GeForce RTX 5090, 12.0, 00000000:01:00.0\n"
        self.assertEqual(serving_device._parse_compute_caps(out),
                         {"0000:01:00.0": 120})

    def test_empty_or_garbage_output_yields_nothing_rather_than_a_guess(self):
        for out in ("", "\n", "No devices were found\n", "0, name, n/a, x\n"):
            with self.subTest(out=out):
                self.assertEqual(serving_device._parse_compute_caps(out), {})


class CudaSupportedHereTest(unittest.TestCase):
    """The three-valued gate, the same discipline the HIP gate keeps."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cuda-gate-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.targets = _targets_file(self.tmp, SHIPPED + "\n")

    def test_this_box_s_card_is_supported(self):
        self.assertIs(
            serving_device.cuda_is_supported_here(
                targets_path=self.targets, caps={"0000:01:00.0": 86}),
            True)

    def test_a_volta_card_is_measurably_unsupported(self):
        self.assertIs(
            serving_device.cuda_is_supported_here(
                targets_path=self.targets, caps={"0000:01:00.0": 70}),
            False)

    def test_one_supported_card_among_several_is_enough(self):
        """Selection pins ONE card; a box that has a servable card can serve."""
        self.assertIs(
            serving_device.cuda_is_supported_here(
                targets_path=self.targets,
                caps={"0000:01:00.0": 70, "0000:02:00.0": 86}),
            True)

    def test_no_readable_capability_is_none_not_false(self):
        """Refusing on "I could not tell" would strand working machines."""
        self.assertIsNone(
            serving_device.cuda_is_supported_here(
                targets_path=self.targets, caps={}))

    def test_a_missing_target_record_is_none_not_false(self):
        """An older engine build installed no record. That is unknown, not no."""
        self.assertIsNone(
            serving_device.cuda_is_supported_here(
                targets_path=os.path.join(self.tmp, "no-such-file"),
                caps={"0000:01:00.0": 86}))

    def test_the_per_card_verdict_is_available_to_callers(self):
        """Per-card from the start: which card, and what it is missing.

        A box with two cards where only one is servable must be able to say
        which, by PCI id — the same granularity the device pin works at.
        """
        verdict = serving_device.cuda_card_support(
            targets_path=self.targets,
            caps={"0000:01:00.0": 70, "0000:02:00.0": 86})
        self.assertEqual(verdict, {"0000:01:00.0": False,
                                   "0000:02:00.0": True})


class SelectorWiringTest(unittest.TestCase):
    """The gate is wired into both walks, not just the first one."""

    def setUp(self):
        self._orig_usable = serving_device.cuda_is_usable_here
        self._orig_supported = serving_device.cuda_is_supported_here
        self._orig_hip = serving_device.hip_is_supported_here
        self.addCleanup(lambda: setattr(
            serving_device, "cuda_is_usable_here", self._orig_usable))
        self.addCleanup(lambda: setattr(
            serving_device, "cuda_is_supported_here", self._orig_supported))
        self.addCleanup(lambda: setattr(
            serving_device, "hip_is_supported_here", self._orig_hip))
        serving_device.hip_is_supported_here = lambda *a, **k: None
        # The driver question is answered YES throughout, so these cases are
        # only ever about the architecture question.
        serving_device.cuda_is_usable_here = lambda *a, **k: True

        self._orig_paths = dict(serving_device.ENGINE_SERVER_PATHS)
        self.addCleanup(lambda: serving_device.ENGINE_SERVER_PATHS.update(
            self._orig_paths))
        self.tmp = tempfile.mkdtemp(prefix="cuda-wiring-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        for engine in ("cuda", "vulkan"):
            path = os.path.join(self.tmp, f"llama-server-{engine}")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\n")
            os.chmod(path, 0o755)
            serving_device.ENGINE_SERVER_PATHS[engine] = path

    def test_an_unservable_card_falls_to_vulkan(self):
        serving_device.cuda_is_supported_here = lambda *a, **k: False
        engine, path = serving_device.select_serving_engine(vendor="nvidia")
        self.assertEqual(engine, "vulkan")
        self.assertEqual(path, serving_device.ENGINE_SERVER_PATHS["vulkan"])

    def test_a_servable_card_still_gets_cuda(self):
        serving_device.cuda_is_supported_here = lambda *a, **k: True
        engine, _path = serving_device.select_serving_engine(vendor="nvidia")
        self.assertEqual(engine, "cuda")

    def test_an_unknown_answer_leaves_the_preference_alone(self):
        """Only a MEASURED no skips the engine."""
        serving_device.cuda_is_supported_here = lambda *a, **k: None
        engine, _path = serving_device.select_serving_engine(vendor="nvidia")
        self.assertEqual(engine, "cuda")

    def test_the_ladder_does_not_offer_an_unservable_cuda_rung(self):
        """engine_ladder is what a caller uses AFTER an engine died. Offering
        a rung the architecture gate has already refused would spend the
        restart budget on an engine that cannot work."""
        serving_device.cuda_is_supported_here = lambda *a, **k: False
        rungs = [e for e, _p in serving_device.engine_ladder(vendor="nvidia")]
        self.assertNotIn("cuda", rungs)
        self.assertIn("vulkan", rungs)


class RecipeParityTest(unittest.TestCase):
    """The record the runtime reads is the record the recipe writes, and it
    carries the list the build actually compiled.

    These cases read the packaging tree, which does not exist beside the
    installed package on a user's machine — this file ships inside it. They
    therefore SKIP rather than fail when the recipe is absent, the same answer
    test_intergen_unit_scoping.py and test_destructive_policy.py give, and the
    one tests/preflight/test_shipped_tests_survive_installed_layout.py holds
    every shipped test to. The parity itself is still enforced on every run in
    the repository, which is where a recipe can actually change.
    """

    def setUp(self):
        if not (self._repo_root() / "packages" / "compute" / "llama-cpp-cuda"
                / "package.yml").is_file():
            self.skipTest("packaging tree (packages/compute/llama-cpp-cuda) "
                          "not present")

    def _repo_root(self):
        import pathlib
        return pathlib.Path(__file__).resolve().parents[2]

    def test_the_recipe_installs_the_list_it_compiled_with(self):
        """Read from the recipe's code, not from a mention of the path.

        The list must be written FROM the same declaration the cmake configure
        consumed — the builder's exported IGOS_GPU_TARGETS, present in every
        phase. A second literal list in do_install would be free to drift away
        from what was actually compiled, which is the whole failure this record
        exists to prevent.
        """
        build = (self._repo_root() / "packages" / "compute" / "llama-cpp-cuda"
                 / "build.sh").read_text()
        code = "\n".join(ln for ln in build.splitlines()
                         if not ln.strip().startswith("#"))
        flat = " ".join(code.replace("\\\n", " ").split())
        self.assertIn("${IGOS_GPU_TARGETS:?", flat,
                      "do_install must fail loudly rather than write an empty "
                      "record if the target list is not declared")
        # Each recipe phase runs in its own shell: a variable configure() set
        # (CUDA_ARCHS) is not there when do_install() runs. The record line
        # must read the builder's exported declaration instead.
        record_line = next(ln for ln in code.splitlines()
                           if "> gpu-targets.txt" in ln)
        self.assertNotIn("CUDA_ARCHS", record_line)
        self.assertIn(
            'install -Dm644 gpu-targets.txt '
            '"${DESTDIR}/opt/llama-cpp-cuda/share/llama-cpp-cuda/gpu-targets"',
            flat)

    def test_the_package_verifies_the_installed_record(self):
        import yaml
        pkg = yaml.safe_load(
            (self._repo_root() / "packages" / "compute" / "llama-cpp-cuda"
             / "package.yml").read_text())
        self.assertIn(
            "/opt/llama-cpp-cuda/share/llama-cpp-cuda/gpu-targets",
            pkg.get("verify_paths", []))

    def test_the_declared_targets_are_the_ones_these_tests_reason_about(self):
        """If the recipe's declared list moves, these cases must move with it."""
        import yaml
        pkg = yaml.safe_load(
            (self._repo_root() / "packages" / "compute" / "llama-cpp-cuda"
             / "package.yml").read_text())
        self.assertEqual(pkg.get("gpu_targets", ""), SHIPPED)


if __name__ == "__main__":
    unittest.main()


class CudaRefusalReasonTest(unittest.TestCase):
    """A measured refusal has to SAY WHY, in the log, naming the card.

    The HIP rung has done this since it grew its per-card gate: when it
    declines it writes the card it would have pinned, that card's
    architecture, and the list the installed build declares. The CUDA rung
    reached the same verdict through a bare ``continue``, so a machine that
    dropped from CUDA to Vulkan recorded only the decision it arrived at and
    never the reason — measured twice on 2026-09-18, on an RTX 3070 Ti
    machine and on a compute-7.5 machine, with a grep over the selector's own
    loggers returning zero. Someone reading that journal can see the engine
    changed and cannot see that the build had no code for their card, which
    is the one fact that tells them whether to reinstall the engine or to
    leave it alone.

    Only a MEASURED refusal is logged. "I could not tell" writes nothing: a
    log line that fires on an unreadable capability would train a reader to
    ignore the line that matters.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cuda-reason-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        # A REAL record file, read by the REAL parser — only the path is
        # redirected, because the shipped path is bound as a default argument
        # and a caller deep in the walk passes none.
        self.targets = _targets_file(self.tmp, "75-virtual;89-real\n")
        _real_reader = serving_device.cuda_build_gpu_targets
        self._orig_reader = _real_reader
        self.addCleanup(lambda: setattr(
            serving_device, "cuda_build_gpu_targets", self._orig_reader))
        serving_device.cuda_build_gpu_targets = (
            lambda *_a, **_k: _real_reader(self.targets))

        self._orig_caps = serving_device.detect_nvidia_compute_caps
        self.addCleanup(lambda: setattr(
            serving_device, "detect_nvidia_compute_caps", self._orig_caps))
        # A compute-7.0 card against a build declaring 75-virtual and
        # 89-real. 89-real is SASS for exactly 8.9 and runs on nothing else;
        # 75-virtual is PTX the driver can JIT forward for 7.5 and ABOVE, and
        # 7.0 is below it. So this card is covered by neither entry and the
        # verdict is a measured False, not an unknown.
        serving_device.detect_nvidia_compute_caps = (
            lambda *_a, **_k: {"0000:01:00.0": 70})

        self._orig_hip = serving_device.hip_is_supported_here
        self.addCleanup(lambda: setattr(
            serving_device, "hip_is_supported_here", self._orig_hip))
        serving_device.hip_is_supported_here = lambda *_a, **_k: None
        self._orig_usable = serving_device.cuda_is_usable_here
        self.addCleanup(lambda: setattr(
            serving_device, "cuda_is_usable_here", self._orig_usable))
        serving_device.cuda_is_usable_here = lambda *_a, **_k: True

        self._orig_paths = dict(serving_device.ENGINE_SERVER_PATHS)
        self.addCleanup(lambda: serving_device.ENGINE_SERVER_PATHS.update(
            self._orig_paths))
        for engine in ("cuda", "vulkan"):
            path = os.path.join(self.tmp, f"llama-server-{engine}")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\n")
            os.chmod(path, 0o755)
            serving_device.ENGINE_SERVER_PATHS[engine] = path

    def _assert_names_the_card_and_the_list(self, text):
        self.assertIn("0000:01:00.0", text)
        self.assertIn("7.0", text)
        self.assertIn("75-virtual", text)
        self.assertIn("89-real", text)

    def test_the_engine_choice_logs_the_card_and_the_declared_list(self):
        with self.assertLogs(serving_device.log, level="INFO") as caught:
            engine, _path = serving_device.select_serving_engine(
                vendor="nvidia")
        self.assertEqual(engine, "vulkan")
        self._assert_names_the_card_and_the_list("\n".join(caught.output))

    def test_the_ladder_logs_the_card_and_the_declared_list(self):
        with self.assertLogs(serving_device.log, level="INFO") as caught:
            rungs = [e for e, _p in serving_device.engine_ladder(
                vendor="nvidia")]
        self.assertNotIn("cuda", rungs)
        self._assert_names_the_card_and_the_list("\n".join(caught.output))

    def test_an_unreadable_capability_logs_nothing(self):
        """No capability read at all is None, not a refusal, and silent."""
        serving_device.detect_nvidia_compute_caps = lambda *_a, **_k: {}
        with self.assertNoLogs(serving_device.log, level="INFO"):
            engine, _path = serving_device.select_serving_engine(
                vendor="nvidia")
        self.assertEqual(engine, "cuda")

    def test_a_build_with_no_target_record_logs_nothing(self):
        """An engine built before the record existed is unknown, not refused."""
        serving_device.cuda_build_gpu_targets = lambda *_a, **_k: set()
        with self.assertNoLogs(serving_device.log, level="INFO"):
            engine, _path = serving_device.select_serving_engine(
                vendor="nvidia")
        self.assertEqual(engine, "cuda")

    def test_the_reason_is_none_when_nothing_was_refused(self):
        """A covered card produces no sentence at all, so no caller can log
        a refusal that did not happen."""
        serving_device.detect_nvidia_compute_caps = (
            lambda *_a, **_k: {"0000:01:00.0": 89})
        self.assertIsNone(serving_device.cuda_refusal_reason())

    def test_the_reason_names_every_refused_card_not_just_one(self):
        """A two-card box that is refused on both says so about both: a
        reader told about one card would reinstall the engine for it and
        still be stranded on the other."""
        serving_device.detect_nvidia_compute_caps = (
            lambda *_a, **_k: {"0000:01:00.0": 70, "0000:02:00.0": 60})
        reason = serving_device.cuda_refusal_reason()
        self.assertIn("0000:01:00.0", reason)
        self.assertIn("0000:02:00.0", reason)
        self.assertIn("7.0", reason)
        self.assertIn("6.0", reason)
