#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The HIP architecture gate: an AMD GPU is not automatically a HIP GPU.

The HIP build of the inference engine is compiled for a declared list of AMD
architectures and carries device code for those and no others. On an AMD GPU
outside that list — a gfx90c APU, measured — llama-server SEGFAULTS at model
load instead of refusing cleanly. So selecting HIP, or offering it, on the
strength of the PCI vendor id alone takes a machine that was serving correctly
on Vulkan and breaks it.

Three things have to hold together, and they live in three files:

  * the recipe (packages/compute/llama-cpp-hip) installs the architecture list
    it was compiled for, so the runtime can read what was actually built;
  * intergen.serving_device declines HIP when the machine's architecture is
    measurably absent from that list;
  * installer.backend.gpu_detect records the same answer at install time, so
    the first-boot offer never proposes a download that would crash.

THE THREE-VALUED ANSWER IS THE POINT. "Supported", "not supported" and "could
not tell" are three different states with three different correct responses.
Collapsing the third into the second would refuse HIP on every machine whose
topology is unreadable; collapsing it into the first is what the old
vendor-only test did. Every gate below is checked in all three states.

The architecture is read from the amdgpu driver's own KFD topology, so these
tests build a fake topology tree and point the readers at it — no AMD hardware
is required to exercise the decision, which is what makes the logic testable at
all on a machine that has none.

UNPROVEN, and stated rather than implied: none of this has been executed
against a real AMD APU. What is proven here is the decision logic and the
parity between the copies; what is not proven is the underlying claim that a
gfx90c part reports 90012 in gfx_target_version, which was measured elsewhere
and is taken on report.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from installer.backend import gpu_detect  # noqa: E402
from intergen import serving_device  # noqa: E402


def _fake_topology(tmp, gfx_target_versions):
    """Build a KFD topology tree reporting the given gfx_target_version values.

    Shaped like the real thing: nodes/<n>/properties, one key per line, with
    other properties present so the reader has to actually find the right one
    rather than parse the only line in the file.
    """
    root = Path(tmp) / "nodes"
    for i, version in enumerate(gfx_target_versions):
        node = root / str(i)
        node.mkdir(parents=True)
        (node / "properties").write_text(
            "cpu_cores_count 0\n"
            "simd_count 16\n"
            f"gfx_target_version {version}\n"
            "max_engine_clk_ccompute 3200\n")
    return str(root)


def _targets_file(tmp, text):
    p = Path(tmp) / "gpu-targets"
    p.write_text(text)
    return str(p)


class GfxNameTest(unittest.TestCase):
    """The encoding: major*10000 + minor*100 + step, minor/step as hex digits."""

    CASES = {
        90012: "gfx90c",     # the APU that segfaults
        110000: "gfx1100",
        110002: "gfx1102",
        120001: "gfx1201",
        90300: "gfx930",     # minor 3, step 0
    }

    def test_known_encodings(self):
        for value, name in self.CASES.items():
            with self.subTest(value=value):
                self.assertEqual(serving_device._gfx_name(value), name)

    def test_the_installer_copy_agrees(self):
        for value, name in self.CASES.items():
            with self.subTest(value=value):
                self.assertEqual(gpu_detect._gfx_name(value), name)

    def test_nonsense_values_are_not_turned_into_answers(self):
        for value in (0, -1, None, "90012"):
            with self.subTest(value=value):
                self.assertIsNone(serving_device._gfx_name(value))
                self.assertIsNone(gpu_detect._gfx_name(value))


class TopologyReaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gfx-topology-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))

    def test_it_reads_every_node(self):
        root = _fake_topology(self.tmp, [110000, 120001])
        self.assertEqual(serving_device.detect_amd_gfx_targets(root),
                         {"gfx1100", "gfx1201"})

    def test_an_absent_topology_is_empty_not_an_error(self):
        self.assertEqual(
            serving_device.detect_amd_gfx_targets(
                os.path.join(self.tmp, "nope")),
            set())

    def test_the_installer_copy_reads_the_same_tree_the_same_way(self):
        root = _fake_topology(self.tmp, [90012, 110000])
        self.assertEqual(serving_device.detect_amd_gfx_targets(root),
                         gpu_detect.detect_amd_gfx_targets(root))


class SupportDecisionTest(unittest.TestCase):
    """serving_device.hip_is_supported_here, in all three states."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gfx-support-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.targets = _targets_file(self.tmp, "gfx1100;gfx1102;gfx1201\n")

    def test_a_supported_card_is_true(self):
        root = _fake_topology(self.tmp, [120001])
        self.assertIs(
            serving_device.hip_is_supported_here(root, self.targets), True)

    def test_the_apu_that_segfaults_is_false(self):
        root = _fake_topology(self.tmp, [90012])
        self.assertIs(
            serving_device.hip_is_supported_here(root, self.targets), False)

    def test_an_unreadable_topology_is_none_not_false(self):
        """Refusing on "I could not tell" would strand working machines."""
        self.assertIsNone(serving_device.hip_is_supported_here(
            os.path.join(self.tmp, "nope"), self.targets))

    def test_a_missing_target_list_is_none_not_false(self):
        root = _fake_topology(self.tmp, [120001])
        self.assertIsNone(serving_device.hip_is_supported_here(
            root, os.path.join(self.tmp, "no-such-file")))

    def test_one_supported_card_among_several_is_enough(self):
        root = _fake_topology(self.tmp, [90012, 120001])
        self.assertIs(
            serving_device.hip_is_supported_here(root, self.targets), True)

    def test_the_list_parses_in_the_shape_the_recipe_writes(self):
        """The recipe writes cmake's own semicolon-separated form."""
        for text in ("gfx1100;gfx1102;gfx1201\n",
                     "gfx1100 gfx1102 gfx1201\n",
                     "gfx1100,gfx1102,gfx1201\n"):
            with self.subTest(text=text):
                self.assertEqual(
                    serving_device.hip_build_gpu_targets(
                        _targets_file(self.tmp, text)),
                    {"gfx1100", "gfx1102", "gfx1201"})


class ParityTest(unittest.TestCase):
    """The copies must equal what they are copies of."""

    def test_hip_targets_match_the_recipe(self):
        """gpu_detect's copy vs packages/compute/llama-cpp-hip/package.yml.

        The copy exists because the installer runs where the HIP package is not
        present. A recipe rebuilt for new architectures with this list left
        behind would make the installer offer HIP to hardware it has no code
        for — which is the exact defect the gate exists to prevent, reappearing
        one level up.
        """
        import yaml
        pkg = yaml.safe_load(
            (_REPO_ROOT / "packages" / "compute" / "llama-cpp-hip"
             / "package.yml").read_text())
        declared = pkg.get("gpu_targets", "")
        recipe_targets = {t for t in declared.replace(";", " ").split() if t}
        self.assertTrue(recipe_targets, "the recipe declares no gpu_targets")
        self.assertEqual(set(gpu_detect.HIP_GPU_TARGETS), recipe_targets)

    def test_the_recipe_installs_the_list_it_compiled_with(self):
        """The install step, read from the recipe, not a mention of the path."""
        build = (_REPO_ROOT / "packages" / "compute" / "llama-cpp-hip"
                 / "build.sh").read_text()
        code = "\n".join(ln for ln in build.splitlines()
                         if not ln.strip().startswith("#"))
        flat = " ".join(code.replace("\\\n", " ").split())
        self.assertIn("${GPU_TARGETS}", flat,
                      "the installed list is not written from GPU_TARGETS, so "
                      "it can disagree with what was compiled")
        self.assertIn(
            'install -Dm644 gpu-targets.txt '
            '"${DESTDIR}/opt/rocm/share/llama-cpp-hip/gpu-targets"', flat)

    def test_the_package_verifies_the_installed_list(self):
        import yaml
        pkg = yaml.safe_load(
            (_REPO_ROOT / "packages" / "compute" / "llama-cpp-hip"
             / "package.yml").read_text())
        self.assertIn("/opt/rocm/share/llama-cpp-hip/gpu-targets",
                      pkg.get("verify_paths", []))

    def test_the_runtime_reads_where_the_recipe_writes(self):
        self.assertEqual(serving_device.HIP_GPU_TARGETS_PATH,
                         "/opt/rocm/share/llama-cpp-hip/gpu-targets")

    def test_both_readers_name_the_same_topology_root(self):
        self.assertEqual(serving_device.KFD_TOPOLOGY_NODES,
                         gpu_detect.KFD_TOPOLOGY_NODES)


class RecordTest(unittest.TestCase):
    """What the installer writes for the first-boot offer to read."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gfx-record-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))

    def test_an_amd_record_carries_the_support_answer(self):
        record = gpu_detect.detection_record({"1002"})
        self.assertEqual(record["upgrade_engine"], "hip")
        self.assertIn("upgrade_engine_supported", record)
        self.assertIn("gfx_targets", record)

    def test_the_question_does_not_arise_for_cuda(self):
        """NVIDIA has no equivalent architecture list, so the answer is None.

        Inventing a True here would be asserting a fact this code does not
        have.
        """
        record = gpu_detect.detection_record({"10de"})
        self.assertEqual(record["upgrade_engine"], "cuda")
        self.assertIsNone(record["upgrade_engine_supported"])

    def test_the_record_still_carries_every_version_1_key(self):
        """The reader takes keys through .get(), but removing one would still
        change behaviour silently on an upgraded machine."""
        record = gpu_detect.detection_record({"10de"})
        for key in ("version", "vendor", "pci_vendors", "shipped_engine",
                    "upgrade_engine", "upgrade_outranks_shipped"):
            self.assertIn(key, record)

    def test_the_record_is_json_serialisable(self):
        """gfx_targets is a set internally; a set would raise here."""
        json.dumps(gpu_detect.detection_record({"1002"}))


if __name__ == "__main__":
    sys.exit(unittest.main())
