# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Tests for the Welcomer's GPU driver / compute-engine offer.

Decided 2026-08-05: this offer used to be a page in the installer, which
could never perform it. It lives here now, where the package manager is
present and the vendor's own licence gate can run. These tests cover the
parts that decide WHAT is offered and WHAT would be run — the record reader,
the per-vendor offer list, the dependency rule, and the composed command.

Widget construction needs a display and is exercised by the local render
proof, not here; these tests are pure and run headless, the same rule the
other Welcomer tests follow.
"""

import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WELCOME_PY = REPO_ROOT / "assets" / "intergen-welcome" / "intergen-welcome.py"

_spec = importlib.util.spec_from_file_location("intergen_welcome", WELCOME_PY)
welcome = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(welcome)


def _record(vendor, upgrade, outranks, version=None, supported=None):
    """A detection record shaped exactly as the installer writes one."""
    return {
        "version": (welcome._GPU_RECORD_VERSION if version is None
                    else version),
        "vendor": vendor,
        "pci_vendors": [],
        "shipped_engine": "vulkan",
        "upgrade_engine": upgrade,
        "upgrade_outranks_shipped": outranks,
        "gfx_targets": [],
        "upgrade_engine_supported": supported,
    }


NVIDIA = _record("nvidia", "cuda", False)
# AMD with the architecture check UNDETERMINED — the state a machine whose KFD
# topology could not be read lands in, and the state every version-1 record
# reads as.
AMD = _record("amd", "hip", True)
# AMD where the HIP build was measured to have device code for this GPU.
AMD_SUPPORTED = _record("amd", "hip", True, supported=True)
# AMD where it measurably does NOT — the gfx90c APU class, where installing HIP
# would replace a working Vulkan setup with a segfault at model load.
AMD_UNSUPPORTED = _record("amd", "hip", True, supported=False)
INTEL = _record("intel", None, False)
UNKNOWN = _record(None, None, False)


class TestReadingTheRecord(unittest.TestCase):
    def _write(self, directory, text):
        path = Path(directory) / "gpu-detection.json"
        path.write_text(text)
        return str(path)

    def test_a_well_formed_record_is_returned(self):
        with TemporaryDirectory() as d:
            path = self._write(d, json.dumps(NVIDIA))
            self.assertEqual(
                welcome._gpu_detection_record(path)["vendor"], "nvidia")

    def test_an_absent_record_is_none(self):
        with TemporaryDirectory() as d:
            missing = str(Path(d) / "nothing-here.json")
            self.assertIsNone(welcome._gpu_detection_record(missing))

    def test_malformed_json_is_none_not_a_crash(self):
        with TemporaryDirectory() as d:
            self.assertIsNone(
                welcome._gpu_detection_record(self._write(d, "{not json")))

    def test_a_record_that_is_not_an_object_is_none(self):
        with TemporaryDirectory() as d:
            self.assertIsNone(
                welcome._gpu_detection_record(self._write(d, "[1, 2, 3]")))

    def test_an_unknown_schema_version_is_refused(self):
        # Refusing is right: a record written by a schema this build does not
        # know may mean something different by the same key names.
        future = _record("nvidia", "cuda", False,
                         version=welcome._GPU_RECORD_VERSION + 1)
        with TemporaryDirectory() as d:
            self.assertIsNone(
                welcome._gpu_detection_record(self._write(d, json.dumps(future))))

    def test_a_record_missing_the_vendor_key_is_refused(self):
        broken = dict(NVIDIA)
        del broken["vendor"]
        with TemporaryDirectory() as d:
            self.assertIsNone(
                welcome._gpu_detection_record(self._write(d, json.dumps(broken))))


class TestWhatIsOffered(unittest.TestCase):
    def test_nvidia_is_offered_the_driver_and_the_cuda_engine(self):
        keys = [o["key"] for o in welcome._gpu_offers(NVIDIA)]
        self.assertEqual(keys, ["nvidia_driver", "compute_engine"])

    def test_the_driver_comes_first(self):
        # The CUDA engine links a library only the proprietary driver
        # provides, so the order shown is the order it must run in.
        self.assertEqual(welcome._gpu_offers(NVIDIA)[0]["key"], "nvidia_driver")

    def test_amd_is_offered_the_hip_engine_and_no_driver(self):
        offers = welcome._gpu_offers(AMD)
        self.assertEqual([o["key"] for o in offers], ["compute_engine"])
        self.assertIn("HIP", offers[0]["title"])

    def test_the_hip_engine_is_not_proprietary(self):
        self.assertFalse(welcome._gpu_offers(AMD)[0]["proprietary"])

    def test_both_nvidia_offers_are_proprietary(self):
        for offer in welcome._gpu_offers(NVIDIA):
            self.assertTrue(offer["proprietary"], offer["key"])

    def test_intel_is_offered_nothing(self):
        self.assertEqual(welcome._gpu_offers(INTEL), [])

    def test_unidentified_hardware_is_offered_nothing(self):
        self.assertEqual(welcome._gpu_offers(UNKNOWN), [])

    def test_no_record_is_offered_nothing(self):
        # A machine installed before the record existed, or one where writing
        # it failed. Silence is the correct outcome, never a guessed offer.
        self.assertEqual(welcome._gpu_offers(None), [])


class TestWhatTheOfferSays(unittest.TestCase):
    def test_the_cuda_offer_says_vulkan_keeps_serving(self):
        # The ratified table ranks Vulkan ahead of CUDA. Selling CUDA as a
        # speed-up would be selling a measured slow-down.
        detail = welcome._gpu_offers(NVIDIA)[1]["detail"]
        self.assertIn("Vulkan stays the engine that serves", detail)

    def test_the_cuda_offer_follows_the_record_if_the_ranking_changes(self):
        # The sentence is read off the record, not asserted in the Welcomer,
        # so a re-ratified table changes what the user is told.
        flipped = _record("nvidia", "cuda", True)
        detail = welcome._gpu_offers(flipped)[1]["detail"]
        self.assertIn("takes over once installed", detail)
        self.assertNotIn("Vulkan stays the engine that serves", detail)

    def test_the_hip_offer_says_it_takes_over_when_support_is_confirmed(self):
        self.assertIn("takes over from Vulkan",
                      welcome._gpu_offers(AMD_SUPPORTED)[0]["detail"])

    def test_the_hip_offer_does_not_claim_takeover_when_support_is_unknown(self):
        """The claim is scoped to what was checked on THIS machine.

        The wording used to say "on this hardware it is the preferred engine"
        for every AMD machine, which presented a measurement taken on one card
        as a fact about the card in front of the user.
        """
        detail = welcome._gpu_offers(AMD)[0]["detail"]
        self.assertIn("could not be determined here", detail)
        self.assertNotIn("This machine's GPU is one the HIP build supports",
                         detail)

    def test_the_measurement_is_attributed_rather_than_asserted(self):
        for record in (AMD, AMD_SUPPORTED):
            with self.subTest(record=record["upgrade_engine_supported"]):
                detail = welcome._gpu_offers(record)[0]["detail"]
                self.assertIn("the AMD hardware this project measured", detail)

    def test_no_hip_offer_when_the_build_has_no_code_for_this_gpu(self):
        """Offering it would propose replacing a working setup with a crash."""
        keys = [o["key"] for o in welcome._gpu_offers(AMD_UNSUPPORTED)]
        self.assertNotIn("compute_engine", keys,
                         "the HIP engine was offered on hardware the build "
                         "segfaults on")

    def test_the_cuda_offer_states_it_needs_the_driver(self):
        self.assertIn("requires the proprietary driver",
                      welcome._gpu_offers(NVIDIA)[1]["detail"])


class TestTheDependencyRule(unittest.TestCase):
    def test_choosing_the_cuda_engine_chooses_the_driver(self):
        offers = welcome._gpu_offers(NVIDIA)
        self.assertEqual(
            welcome._gpu_required_dependencies(["compute_engine"], offers),
            ["nvidia_driver", "compute_engine"])

    def test_choosing_the_driver_alone_adds_nothing(self):
        offers = welcome._gpu_offers(NVIDIA)
        self.assertEqual(
            welcome._gpu_required_dependencies(["nvidia_driver"], offers),
            ["nvidia_driver"])

    def test_the_amd_engine_drags_in_no_driver(self):
        # There is no AMD driver offer — the open source one is already in use
        # — so the rule must not invent one.
        offers = welcome._gpu_offers(AMD)
        self.assertEqual(
            welcome._gpu_required_dependencies(["compute_engine"], offers),
            ["compute_engine"])

    def test_selecting_nothing_stays_nothing(self):
        self.assertEqual(
            welcome._gpu_required_dependencies([], welcome._gpu_offers(NVIDIA)),
            [])


class TestTheComposedCommand(unittest.TestCase):
    def test_nothing_selected_produces_no_command(self):
        self.assertIsNone(
            welcome._gpu_install_command([], welcome._gpu_offers(NVIDIA)))

    def test_the_driver_alone_runs_the_driver_command(self):
        offers = welcome._gpu_offers(NVIDIA)
        self.assertEqual(
            welcome._gpu_install_command(["nvidia_driver"], offers),
            welcome._ADVISORY_COMMAND)

    def test_both_run_driver_first_chained_so_a_failure_stops_it(self):
        offers = welcome._gpu_offers(NVIDIA)
        command = welcome._gpu_install_command(
            ["nvidia_driver", "compute_engine"], offers)
        self.assertEqual(
            command,
            welcome._ADVISORY_COMMAND + " && " + welcome._CUDA_ENGINE_COMMAND)
        # `&&` and not `;`: a declined licence must stop the chain rather than
        # let the next install run as though the first had succeeded.
        self.assertNotIn(";", command)

    def test_the_amd_command_installs_the_hip_engine(self):
        offers = welcome._gpu_offers(AMD)
        self.assertEqual(
            welcome._gpu_install_command(["compute_engine"], offers),
            welcome._HIP_ENGINE_COMMAND)

    def test_every_command_goes_through_the_package_manager(self):
        # The whole point of moving this out of the installer: the vendor's
        # own gate runs inside pkm, on the user's machine, with its text in
        # front of them. Nothing here may install by another route.
        for record in (NVIDIA, AMD):
            for offer in welcome._gpu_offers(record):
                self.assertIn("pkm install", offer["command"], offer["key"])


class TestTheDriverCommandIsSaidOnce(unittest.TestCase):
    def test_the_offer_reuses_the_advisory_command(self):
        # The banner tells the user to type it and the button runs it. One
        # definition means the sentence and the action cannot drift apart.
        driver = welcome._gpu_offers(NVIDIA)[0]
        self.assertEqual(driver["command"], welcome._ADVISORY_COMMAND)


class TestTheLicenceIsNeverAcceptedHere(unittest.TestCase):
    def test_the_notice_says_the_package_manager_asks(self):
        self.assertIn("installs nothing until you accept",
                      welcome._VENDOR_LICENCE_NOTICE)

    def test_no_offer_claims_a_licence_was_accepted(self):
        for record in (NVIDIA, AMD):
            for offer in welcome._gpu_offers(record):
                self.assertNotIn("you have accepted", offer["detail"].lower())
                self.assertNotIn("licence accepted", offer["detail"].lower())


if __name__ == "__main__":
    unittest.main()
