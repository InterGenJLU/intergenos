# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Tests for the installer's display-controller detection and its record.

The installer no longer has a graphics page. What survived it is
`installer/backend/gpu_detect.py`: the vendor probe, the ratified engine
facts, and the record written onto the installed system for the first-boot
welcome to read. These tests cover exactly that, plus the two agreements that
keep it from drifting — the engine table against the serving-engine
selector's own copy, and the record's schema against the reader in the
Welcomer.

Everything here is pure: no lspci is run (vendor sets are supplied), and the
only filesystem writes go to a temporary directory.
"""

import ast
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from installer.backend import gpu_detect

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WELCOME_PY = REPO_ROOT / "assets" / "intergen-welcome" / "intergen-welcome.py"


def _welcome_literal(name):
    """Read a module-level literal out of the Welcomer's source.

    Parsed rather than imported: importing the Welcomer needs GTK and a
    display, and the value under test is a constant. The parse fails loudly
    if the name is gone, which is the point — a renamed constant must not
    read as agreement.
    """
    tree = ast.parse(WELCOME_PY.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {WELCOME_PY}")


class TestVendorDetection(unittest.TestCase):
    def test_nvidia_is_detected_from_its_pci_vendor_id(self):
        self.assertEqual(gpu_detect.detect_gpu_vendor({"10de"}), "nvidia")

    def test_amd_is_detected_under_both_of_its_vendor_ids(self):
        self.assertEqual(gpu_detect.detect_gpu_vendor({"1002"}), "amd")
        self.assertEqual(gpu_detect.detect_gpu_vendor({"1022"}), "amd")

    def test_intel_is_detected(self):
        self.assertEqual(gpu_detect.detect_gpu_vendor({"8086"}), "intel")

    def test_a_discrete_card_outranks_the_integrated_one(self):
        # Every switchable-graphics laptop presents both. The upgrade paths
        # are about the discrete card, so it must win.
        self.assertEqual(
            gpu_detect.detect_gpu_vendor({"8086", "10de"}), "nvidia")
        self.assertEqual(
            gpu_detect.detect_gpu_vendor({"8086", "1002"}), "amd")

    def test_nothing_detected_is_none_not_a_guess(self):
        self.assertIsNone(gpu_detect.detect_gpu_vendor(set()))

    def test_an_unknown_vendor_id_is_none(self):
        self.assertIsNone(gpu_detect.detect_gpu_vendor({"beef"}))


class TestEngineFacts(unittest.TestCase):
    def test_nvidia_can_add_cuda(self):
        self.assertEqual(gpu_detect.upgrade_engine_for("nvidia"), "cuda")

    def test_amd_can_add_hip(self):
        self.assertEqual(gpu_detect.upgrade_engine_for("amd"), "hip")

    def test_intel_and_unknown_hardware_have_nothing_to_add(self):
        self.assertIsNone(gpu_detect.upgrade_engine_for("intel"))
        self.assertIsNone(gpu_detect.upgrade_engine_for(None))

    def test_hip_outranks_the_shipped_engine_on_amd(self):
        self.assertTrue(gpu_detect.upgrade_outranks_shipped("amd"))

    def test_cuda_does_not_outrank_the_shipped_engine_on_nvidia(self):
        # The ratified table lists Vulkan ahead of CUDA. Anything that offers
        # CUDA must say Vulkan keeps serving, and this is the fact it reads.
        self.assertFalse(gpu_detect.upgrade_outranks_shipped("nvidia"))

    def test_hardware_with_no_upgrade_never_outranks(self):
        self.assertFalse(gpu_detect.upgrade_outranks_shipped("intel"))
        self.assertFalse(gpu_detect.upgrade_outranks_shipped(None))


class TestEnginePreferenceMatchesTheSelector(unittest.TestCase):
    """The copy carried here must equal the serving-engine selector's own.

    The installer runs from a medium that carries no assistant package, so it
    cannot import the selector — it carries a copy. A copy that may drift is
    worse than no copy at all, so this is the assertion that forbids drift.
    """

    def test_the_table_is_identical_to_the_selectors(self):
        from intergen.serving_device import ENGINE_PREFERENCE
        self.assertEqual(gpu_detect._ENGINE_PREFERENCE, ENGINE_PREFERENCE)


class TestDetectionRecord(unittest.TestCase):
    def test_the_record_states_the_vendor_and_its_engine_facts(self):
        record = gpu_detect.detection_record({"10de"})
        self.assertEqual(record["vendor"], "nvidia")
        self.assertEqual(record["shipped_engine"], "vulkan")
        self.assertEqual(record["upgrade_engine"], "cuda")
        self.assertFalse(record["upgrade_outranks_shipped"])
        self.assertEqual(record["version"], gpu_detect.DETECTION_RECORD_VERSION)

    def test_amd_records_hip_taking_over(self):
        record = gpu_detect.detection_record({"1002"})
        self.assertEqual(record["vendor"], "amd")
        self.assertEqual(record["upgrade_engine"], "hip")
        self.assertTrue(record["upgrade_outranks_shipped"])

    def test_the_raw_probe_result_is_kept(self):
        # So a reader can tell "the probe found nothing" apart from "the probe
        # found something we do not act on".
        self.assertEqual(gpu_detect.detection_record(set())["pci_vendors"], [])
        self.assertEqual(
            gpu_detect.detection_record({"beef"})["pci_vendors"], ["beef"])

    def test_unidentified_hardware_records_a_null_vendor_and_no_upgrade(self):
        record = gpu_detect.detection_record(set())
        self.assertIsNone(record["vendor"])
        self.assertIsNone(record["upgrade_engine"])
        self.assertFalse(record["upgrade_outranks_shipped"])

    def test_no_user_choice_is_recorded(self):
        # There is no choice to record any more: the installer makes no offer,
        # so a key that looked like consent would be a lie about where consent
        # was given.
        record = gpu_detect.detection_record({"10de"})
        for key in record:
            self.assertNotIn("accept", key)
            self.assertNotIn("selected", key)
            self.assertNotIn("licence", key)


class TestWritingTheRecord(unittest.TestCase):
    def test_it_lands_at_the_documented_path_and_parses(self):
        with TemporaryDirectory() as target:
            path = gpu_detect.write_detection_record(target, {"10de"})
            self.assertEqual(
                Path(path),
                Path(target) / gpu_detect.DETECTION_RECORD_PATH)
            written = json.loads(Path(path).read_text())
            self.assertEqual(written["vendor"], "nvidia")
            self.assertEqual(written["upgrade_engine"], "cuda")

    def test_it_creates_the_directory_it_needs(self):
        with TemporaryDirectory() as target:
            self.assertFalse((Path(target) / "etc" / "intergen").exists())
            gpu_detect.write_detection_record(target, {"1002"})
            self.assertTrue((Path(target) / "etc" / "intergen").is_dir())

    def test_a_machine_it_cannot_write_to_still_installs(self):
        # A hint file must never take an install down. The failure is reported
        # by returning None, and the caller carries on.
        with TemporaryDirectory() as target:
            blocker = Path(target) / "etc"
            blocker.write_text("not a directory\n")
            self.assertIsNone(
                gpu_detect.write_detection_record(target, {"10de"}))


class TestTheWelcomerReadsWhatIsWritten(unittest.TestCase):
    """The two ends of the record must agree, or the offer silently vanishes.

    The Welcomer refuses any record whose version it does not recognise, which
    is correct — and which makes a version bump on one side only a silent
    loss of the whole offer. These assertions are what make that impossible to
    do by accident.
    """

    def test_the_schema_version_matches_the_readers(self):
        self.assertEqual(gpu_detect.DETECTION_RECORD_VERSION,
                         _welcome_literal("_GPU_RECORD_VERSION"))

    def test_the_path_matches_the_readers(self):
        self.assertEqual("/" + gpu_detect.DETECTION_RECORD_PATH,
                         _welcome_literal("_GPU_RECORD_PATH"))


if __name__ == "__main__":
    unittest.main()
