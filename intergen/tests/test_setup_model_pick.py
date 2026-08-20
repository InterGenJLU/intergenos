# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Regressions for `intergen setup` flow correctness.

1. Model pick: on an integrated-GPU Tier 2 box (the A12: ~15GB RAM, AMD Wani,
   512MB VRAM), detect() applies the CPU-only override and recommends the 2B,
   but a bare get_model_for_tier(Tier 2) lookup returns the 9B. setup must use
   the recommended model (mirrors the daemon), not the bare tier lookup.

2. License acceptance: model_manager refuses to download a license-gated model
   (Qwen → Tongyi-Qianwen) until acceptance is recorded, and delegates the
   show-license / consent / record flow to the CLI. setup must drive it — prompt,
   record on yes, abort cleanly on no — not dead-end with "License not accepted".
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from intergen import net_diagnostics
from intergen.model_choice import GpuDriverState
from intergen.interfaces.types import HardwareTierLevel


def _no_nvidia():
    """A GPU driver state with no NVIDIA card, so no driver advisory applies.

    THIS IS THE DEFAULT FOR EVERY TEST BELOW, AND IT HAS TO BE STATED RATHER
    THAN INHERITED FROM THE MACHINE. `setup.run_setup` calls
    `model_choice.build_offer`, which — given no explicit state — calls
    `model_choice.detect_driver_state()`, and that walks the REAL
    /sys/class/drm to read the PCI vendor id and bound kernel driver of the
    box the suite happens to be running on. That made these tests report the
    hardware under the desk rather than the behaviour under test.

    Measured 2026-08-05: on a machine with an NVIDIA card bound to the
    open-source driver (vendor 0x10de, driver nouveau), `detect_driver_state`
    returns needs_driver_advisory=True, `build_offer` sets advisory=True, and
    `_choose_tier` therefore falls past its "nothing to ask" early return and
    reaches `input()`. That extra question exhausts the scripted `side_effect`
    list and four tests in this file die with StopIteration inside
    unittest.mock — on that box only. The same four passed on every machine
    without an NVIDIA card, which is precisely what makes this shape hard to
    see: the suite looks green on most machines and the failure looks like
    a code regression on the one box that has the hardware.

    A test must not read host hardware. Pinning the driver state makes these
    tests answer the same way on every box and every GPU vendor.
    """
    return GpuDriverState()


def _nvidia_on_open_driver():
    """An NVIDIA card bound to the open-source driver — the advisory case.

    This is the state the development machine box reports for real. It is written out here so
    the advisory behaviour is exercised deliberately, on every machine, instead
    of being reached by accident on whichever machines happen to have the card.
    """
    return GpuDriverState(nvidia_present=True, driver="nouveau",
                          proprietary_nvidia=False)


def _reachable():
    """A probe result meaning "a download source answered"."""
    return net_diagnostics.ProbeResult(
        True, net_diagnostics.REACHABLE, True, True)


def _unreachable(cause):
    """A probe result meaning "nothing answered, and here is why"."""
    return net_diagnostics.ProbeResult(
        False, cause, False, cause == net_diagnostics.NAME_RESOLUTION)


def _model(name, size_gb):
    return SimpleNamespace(
        name=name, quant="Q4_K_M", size_gb=size_gb,
        repo_id=f"unsloth/{name}-GGUF", downloaded=False, local_path=None,
        sha256="00" * 32,
    )


class _FakeMM:
    """Stand-in ModelManager. name lookup honors the CPU-only 2B; the bare
    tier-level lookup returns the 9B. License acceptance state is configurable;
    record/provision calls are captured."""

    def __init__(self, *, license_accepted=False):
        self._by_name = {"Qwen3.5-2B": _model("Qwen3.5-2B", 1.5),
                         "Qwen3.5-9B": _model("Qwen3.5-9B", 5.5)}
        self.license_accepted = license_accepted
        self.recorded = []
        self.provisioned = []

    def get_model_by_name(self, name):
        return self._by_name.get(name)

    def get_model_for_tier(self, tier):
        # Tier-faithful: a Tier-2 lookup gives the 9B (the point of the
        # regression below — setup must NOT use the bare detected-tier lookup on
        # an integrated box), a Tier-1 lookup gives the 2B. The earlier stub
        # returned the 9B for EVERY tier, which stopped modelling reality once
        # setup gained an explicit model choice.
        return self._by_name["Qwen3.5-2B" if tier is HardwareTierLevel.TIER_1
                             else "Qwen3.5-9B"]

    def resolve_for_detected(self, tier):
        # Mirrors the real ModelManager.resolve_for_detected: the ONE shared path
        # setup + daemon use — recommended name first (honors the CPU-only 2B),
        # bare tier lookup only as a fallback.
        return (self.get_model_by_name(tier.recommended_model)
                or self.get_model_for_tier(tier.tier))

    def check_license_acceptance(self, model):
        return self.license_accepted

    def record_license_acceptance(self, model, *, accepted_by=""):
        self.recorded.append(model.name)
        self.license_accepted = True

    def provision_model(self, model, *, progress_callback=None):
        self.provisioned.append(model.name)
        model.local_path = f"/var/lib/intergen/models/llm/{model.name}.gguf"
        return True


class _SetupHarness(unittest.TestCase):
    # `_restart_user_daemon` is mocked in every harness below. Unmocked, these
    # tests reach setup.py's "model already downloaded -> reload it" branch and
    # run `systemctl --user restart intergen` against the BOX's live daemon:
    # measured on a dev box, one suite run bounced the resident daemon twice in
    # five seconds, and a persistence battery running concurrently was killed by
    # those bounces (its restart-before boundaries collided with them). A model-
    # pick test must never touch the machine's running service.
    # `_invoking_user` is redirected to a throwaway directory for the same
    # reason `_restart_user_daemon` is mocked: without it these tests write
    # into the REAL user's home. run_setup records the model-tier choice under
    # the invoking user's XDG data dir, and unpatched that resolves to the
    # person running the suite — so a test run left a real preference file
    # behind that a later unattended setup would honour, and it re-wrote itself
    # on every run so its timestamp kept advancing. Measured on a dev box
    # 2026-08-03: running this file alone recreated
    # ~/.local/share/intergen/model-tier-choice.json reading tier 1 while the
    # machine had the tier-2 model installed and serving. A model-pick test
    # must never write the machine's real state.
    # `model_choice.detect_driver_state` is pinned in every harness below for
    # the same reason: unpatched it reads the real /sys/class/drm of whatever
    # box runs the suite. See `_no_nvidia` for the measurement and the four
    # failures it caused on the one NVIDIA-carrying machine.
    def _run(self, *, mm, inputs, tier_override=None, recommended="Qwen3.5-2B",
             driver_state=None):
        from intergen import setup as setup_mod
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fake_tier = SimpleNamespace(
            tier=HardwareTierLevel.TIER_2, ram_gb=15.1, gpu_vendor="amd",
            gpu_model="Wani", gpu_vram_mb=512, recommended_model=recommended,
        )
        fake_detector = mock.Mock()
        fake_detector.detect.return_value = fake_tier
        buf = io.StringIO()
        with mock.patch("intergen.hardware.HardwareDetector",
                        return_value=fake_detector), \
             mock.patch("intergen.model_choice.detect_driver_state",
                        return_value=driver_state or _no_nvidia()), \
             mock.patch("intergen.model_manager.ModelManager",
                        return_value=mm), \
             mock.patch("builtins.input", side_effect=list(inputs)), \
             mock.patch("intergen.setup._generate_auth_token"), \
             mock.patch("intergen.setup._generate_dispatch_key"), \
             mock.patch("intergen.setup._probe_model_sources",
                        return_value=_reachable()), \
             mock.patch("intergen.setup._restart_user_daemon",
                        return_value=True), \
             mock.patch("intergen.setup._invoking_user",
                        return_value=(Path(tmp.name), 1000, 1000)), \
             mock.patch("intergen.setup._chown_user"), \
             redirect_stdout(buf):
            ok = setup_mod.run_setup(tier_override=tier_override)
        return ok, buf.getvalue()


class TestSetupModelPick(_SetupHarness):
    def test_integrated_tier2_offers_2b_not_9b(self):
        # decline at "Download now?" — selection happens before that prompt
        mm = _FakeMM()
        ok, out = self._run(mm=mm, inputs=["n"])
        self.assertTrue(ok)
        self.assertIn("Qwen3.5-2B", out)
        self.assertNotIn("Qwen3.5-9B", out)

    def test_tier_override_is_honored_literally(self):
        mm = _FakeMM()
        ok, out = self._run(mm=mm, inputs=["n"], tier_override=2)
        self.assertTrue(ok)
        self.assertIn("Qwen3.5-9B", out)


class TestSetupLicenseFlow(_SetupHarness):
    def test_unaccepted_license_prompts_records_and_provisions(self):
        # "y" download, "y" accept license -> records acceptance and provisions
        mm = _FakeMM(license_accepted=False)
        ok, out = self._run(mm=mm, inputs=["y", "y"])
        self.assertTrue(ok)
        self.assertIn("Tongyi-Qianwen", out)
        self.assertEqual(mm.recorded, ["Qwen3.5-2B"])
        self.assertEqual(mm.provisioned, ["Qwen3.5-2B"])

    def test_declined_license_aborts_without_provisioning(self):
        # "y" download, "n" accept license -> no record, no provision, no crash
        mm = _FakeMM(license_accepted=False)
        ok, out = self._run(mm=mm, inputs=["y", "n"])
        self.assertTrue(ok)
        self.assertIn("License not accepted", out)
        self.assertEqual(mm.recorded, [])
        self.assertEqual(mm.provisioned, [])

    def test_already_accepted_skips_prompt_and_provisions(self):
        # license already on record -> no license prompt, straight to provision
        mm = _FakeMM(license_accepted=True)
        ok, out = self._run(mm=mm, inputs=["y"])
        self.assertTrue(ok)
        self.assertEqual(mm.provisioned, ["Qwen3.5-2B"])
        self.assertEqual(mm.recorded, [])


class TestSetupDriverAdvisory(_SetupHarness):
    """The NVIDIA-on-the-open-driver path, exercised on purpose everywhere.

    Until now this path had no test and was reached only by accident, on
    whichever machines happened to carry an NVIDIA card — where it silently
    broke four unrelated tests instead of proving anything. Pinning the driver
    state turns it into behaviour every box checks.

    `hardware.open_driver_vram_mb` is pinned alongside the driver state because
    `build_offer` reads the card's size through it whenever the advisory
    applies. It is the second real-hardware read on this path, it is reached
    ONLY here, and leaving it live would have made this very test report the
    machine it ran on.
    """

    def _run_advisory(self, *, mm, inputs, reported_vram_mb=None):
        with mock.patch("intergen.hardware.open_driver_vram_mb",
                        return_value=reported_vram_mb):
            return self._run(mm=mm, inputs=inputs,
                             driver_state=_nvidia_on_open_driver())

    def test_advisory_is_shown_and_costs_one_extra_answer(self):
        # THE REGRESSION THIS FILE EXISTS TO PIN NOW. With the advisory in
        # play, setup asks which model to install even though only one rung is
        # on offer, so the flow consumes one MORE answer than the no-advisory
        # path does. Scripting exactly two answers proves the count: pick the
        # 2B, then decline the download.
        mm = _FakeMM()
        ok, out = self._run_advisory(mm=mm, inputs=["1", "n"])
        self.assertTrue(ok)
        self.assertIn("open-source driver", out)
        self.assertIn("Install NVIDIA's drivers first", out)
        self.assertIn("Qwen3.5-2B", out)
        self.assertEqual(mm.provisioned, [])

    def test_no_advisory_asks_nothing_when_only_one_rung_is_offered(self):
        # The other half of the same fact, and the reason the four failures
        # were invisible on most machines: with no NVIDIA card the ladder
        # holds one rung, setup asks NOTHING, and a single scripted answer is
        # enough. Same box, same suite, one fewer question.
        mm = _FakeMM()
        ok, out = self._run(mm=mm, inputs=["n"])
        self.assertTrue(ok)
        self.assertNotIn("open-source driver", out)
        self.assertNotIn("Which would you like?", out)

    def test_choosing_drivers_first_installs_nothing(self):
        # Picking the extra option stops setup cleanly: nothing downloaded,
        # nothing recorded as provisioned, and a success return so the caller
        # does not report a failure the user did not have.
        mm = _FakeMM()
        ok, out = self._run_advisory(mm=mm, inputs=["2"])
        self.assertTrue(ok)
        self.assertIn("Nothing was installed", out)
        self.assertEqual(mm.provisioned, [])
        self.assertEqual(mm.recorded, [])

    def test_advisory_reports_the_card_size_when_the_open_driver_knows_it(self):
        # The advisory says something specific when the size is readable. The
        # size never changes which rungs are offered — only the wording.
        mm = _FakeMM()
        ok, out = self._run_advisory(mm=mm, inputs=["1", "n"],
                                     reported_vram_mb=4096)
        self.assertTrue(ok)
        self.assertIn("4096", out)


class TestSetupConnectivityGuard(unittest.TestCase):
    """The offline preflight: when the model is not present AND no download
    source is reachable (the 'set up InterGen before joining WiFi' case),
    run_setup must ABORT with a non-success return BEFORE recording license
    acceptance or attempting a download — so cli.py exits non-zero and the
    Welcomer shows 'didn't finish' rather than a false 'ready'."""

    def _run_offline(self, mm, cause=None):
        from intergen import setup as setup_mod
        if cause is None:
            cause = net_diagnostics.NO_LINK
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fake_tier = SimpleNamespace(
            tier=HardwareTierLevel.TIER_2, ram_gb=15.1, gpu_vendor="amd",
            gpu_model="Wani", gpu_vram_mb=512, recommended_model="Qwen3.5-2B",
        )
        fake_detector = mock.Mock()
        fake_detector.detect.return_value = fake_tier
        buf = io.StringIO()
        # `_invoking_user` is redirected here for the same reason the harness
        # above redirects it. These tests run with auto_yes=True, so nothing is
        # WRITTEN — but run_setup still passes the invoking user's home to
        # `model_choice.load_choice`, which READS the real user's saved
        # model-tier choice. A person who had previously picked a tier on the
        # machine running the suite would steer this flow from outside the
        # test. Reading the machine's state is the same isolation defect as
        # reading its hardware; it just fails less loudly.
        with mock.patch("intergen.hardware.HardwareDetector",
                        return_value=fake_detector), \
             mock.patch("intergen.model_choice.detect_driver_state",
                        return_value=_no_nvidia()), \
             mock.patch("intergen.model_manager.ModelManager",
                        return_value=mm), \
             mock.patch("intergen.setup._probe_model_sources",
                        return_value=_unreachable(cause)), \
             mock.patch("intergen.setup._restart_user_daemon",
                        return_value=True), \
             mock.patch("intergen.setup._invoking_user",
                        return_value=(Path(tmp.name), 1000, 1000)), \
             redirect_stdout(buf):
            ok = setup_mod.run_setup(auto_yes=True)
        return ok, buf.getvalue()

    def test_offline_aborts_without_provision_or_license(self):
        mm = _FakeMM(license_accepted=False)
        ok, out = self._run_offline(mm)
        self.assertFalse(ok)                  # non-success → cli.py exits 1
        self.assertEqual(mm.provisioned, [])  # no doomed download attempted
        self.assertEqual(mm.recorded, [])     # no spurious license record
        self.assertIn("download", out.lower())

    def test_no_link_says_connect_to_a_network(self):
        # The one case where "connect to a network" is the right advice.
        ok, out = self._run_offline(_FakeMM(license_accepted=False),
                                    cause=net_diagnostics.NO_LINK)
        self.assertFalse(ok)
        self.assertIn("not connected to a network", out.lower())

    def test_name_resolution_does_not_tell_the_user_to_connect(self):
        # THE regression this guards. A machine with a working connection and
        # a name server that does not answer used to be told to join WiFi — a
        # network it was already on. The message must name the name-lookup
        # failure and point at the page that fixes it, and must not tell the
        # user to connect to anything.
        ok, out = self._run_offline(_FakeMM(license_accepted=False),
                                    cause=net_diagnostics.NAME_RESOLUTION)
        lowered = out.lower()
        self.assertFalse(ok)
        self.assertIn("cannot look up website names", lowered)
        self.assertIn("intergen-welcome", lowered)
        self.assertNotIn("join wifi", lowered)
        self.assertNotIn("connect to wifi", lowered)
        self.assertNotIn("you may not be online", lowered)

    def test_no_route_blames_neither_the_link_nor_the_name_server(self):
        ok, out = self._run_offline(_FakeMM(license_accepted=False),
                                    cause=net_diagnostics.NO_ROUTE)
        lowered = out.lower()
        self.assertFalse(ok)
        self.assertIn("name server is working", lowered)
        self.assertNotIn("not connected to a network", lowered)


if __name__ == "__main__":
    unittest.main()
