# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""`intergen setup` offers the model choice instead of deciding alone.

Decided 2026-07-31. Setup reports what the box can run and lets the person pick:
35B/9B/2B on a card that holds the largest, 9B/2B on a mid card, the 2B alone on
everything else. On an NVIDIA card running the open-source driver, capability is
unreadable — setup says so and offers the 2B now or drivers first, and picking
drivers installs nothing.

The harness never touches the machine: hardware, the model manager, the daemon
restart and the token writers are all substituted, and the recorded choice goes
to a temporary home.
"""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from intergen import model_choice
from intergen import net_diagnostics
from intergen.interfaces.types import HardwareTierLevel


def _model(name, size_gb):
    return SimpleNamespace(
        name=name, quant="Q4_K_M", size_gb=size_gb,
        repo_id=f"unsloth/{name}-GGUF", downloaded=False, local_path=None,
        sha256="00" * 32,
    )


class _FakeMM:
    """Model manager stand-in whose tier lookup is distinguishable per tier."""

    def __init__(self):
        self._by_tier = {
            HardwareTierLevel.TIER_1: _model("Qwen3.5-2B", 1.2),
            HardwareTierLevel.TIER_2: _model("Qwen3.5-9B", 5.5),
            HardwareTierLevel.TIER_3: _model("Qwen3.5-35B-A3B", 21.0),
        }
        self.license_accepted = True
        self.provisioned = []

    def get_model_by_name(self, name):
        for m in self._by_tier.values():
            if m.name == name:
                return m
        return None

    def get_model_for_tier(self, tier):
        return self._by_tier[tier]

    def resolve_for_detected(self, tier):
        return (self.get_model_by_name(tier.recommended_model)
                or self.get_model_for_tier(tier.tier))

    def check_license_acceptance(self, model):
        return self.license_accepted

    def record_license_acceptance(self, model, *, accepted_by=""):
        self.license_accepted = True

    def provision_model(self, model, *, progress_callback=None):
        self.provisioned.append(model.name)
        model.local_path = f"/var/lib/intergen/models/llm/{model.name}.gguf"
        return True


class _Harness(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)

    def _run(self, *, vram_mb, is_discrete, driver_state, inputs,
             detected=HardwareTierLevel.TIER_2, recommended="Qwen3.5-9B",
             auto_yes=False, tier_override=None):
        from intergen import setup as setup_mod
        mm = _FakeMM()
        fake_tier = SimpleNamespace(
            tier=detected, ram_gb=32.0, gpu_vendor="nvidia",
            gpu_model="Test Card", gpu_vram_mb=vram_mb,
            recommended_model=recommended,
        )
        fake_detector = mock.Mock()
        fake_detector.detect.return_value = fake_tier
        buf = io.StringIO()
        with mock.patch("intergen.hardware.HardwareDetector",
                        return_value=fake_detector), \
             mock.patch("intergen.model_manager.ModelManager",
                        return_value=mm), \
             mock.patch("intergen.setup._is_discrete_for",
                        return_value=is_discrete), \
             mock.patch("intergen.model_choice.detect_driver_state",
                        return_value=driver_state), \
             mock.patch("intergen.setup._invoking_user",
                        return_value=(self.home, 1000, 1000)), \
             mock.patch("intergen.setup._chown_user"), \
             mock.patch("builtins.input", side_effect=list(inputs)), \
             mock.patch("intergen.setup._generate_auth_token"), \
             mock.patch("intergen.setup._generate_dispatch_key"), \
             mock.patch("intergen.setup._probe_model_sources",
                        return_value=net_diagnostics.ProbeResult(
                            True, net_diagnostics.REACHABLE, True, True)), \
             mock.patch("intergen.setup._restart_user_daemon",
                        return_value=True), \
             redirect_stdout(buf):
            ok = setup_mod.run_setup(auto_yes=auto_yes,
                                     tier_override=tier_override)
        return ok, buf.getvalue()


_HEALTHY_NVIDIA = model_choice.GpuDriverState(
    nvidia_present=True, driver="nvidia", proprietary_nvidia=True)
_NOUVEAU = model_choice.GpuDriverState(
    nvidia_present=True, driver="nouveau", proprietary_nvidia=False)
_NO_NVIDIA = model_choice.GpuDriverState(nvidia_present=False, driver="amdgpu")


class LadderPresentationTests(_Harness):
    def test_big_card_offers_all_three_models(self):
        # first input answers the ladder, second declines the download
        ok, out = self._run(vram_mb=24000, is_discrete=True,
                            driver_state=_HEALTHY_NVIDIA,
                            detected=HardwareTierLevel.TIER_3,
                            recommended="Qwen3.5-35B-A3B",
                            inputs=["1", "n"])
        self.assertTrue(ok)
        self.assertIn("This box can run:", out)
        self.assertIn("35B", out)
        self.assertIn("9B", out)
        self.assertIn("2B", out)

    def test_mid_card_offers_nine_b_and_two_b_only(self):
        ok, out = self._run(vram_mb=8192, is_discrete=True,
                            driver_state=_NO_NVIDIA, inputs=["1", "n"])
        self.assertTrue(ok)
        self.assertIn("9B", out)
        self.assertIn("2B", out)
        self.assertNotIn("35B", out)

    def test_box_with_one_runnable_model_is_not_asked(self):
        # No discrete card: nothing to choose, so no prompt is spent.
        ok, out = self._run(vram_mb=None, is_discrete=False,
                            driver_state=_NO_NVIDIA,
                            detected=HardwareTierLevel.TIER_1,
                            recommended="Qwen3.5-2B",
                            inputs=["n"])
        self.assertTrue(ok)
        self.assertNotIn("This box can run:", out)


class UserPickTests(_Harness):
    def test_picking_the_two_b_on_a_capable_box_is_honoured(self):
        # The 2B is a legitimate preference, not a fallback: a 24 GB card that
        # detected Tier 3 must still install the 2B when the user says so.
        ok, out = self._run(vram_mb=24000, is_discrete=True,
                            driver_state=_HEALTHY_NVIDIA,
                            detected=HardwareTierLevel.TIER_3,
                            recommended="Qwen3.5-35B-A3B",
                            inputs=["3", "n"])
        self.assertTrue(ok)
        self.assertIn("Qwen3.5-2B", out)
        self.assertNotIn("Recommended model: Qwen3.5-35B-A3B", out)

    def test_the_pick_is_remembered(self):
        self._run(vram_mb=24000, is_discrete=True,
                  driver_state=_HEALTHY_NVIDIA,
                  detected=HardwareTierLevel.TIER_3,
                  recommended="Qwen3.5-35B-A3B",
                  inputs=["3", "n"])
        self.assertEqual(model_choice.load_choice(self.home),
                         HardwareTierLevel.TIER_1)

    def test_an_unattended_run_leaves_no_record_behind(self):
        """Nobody was asked, so nothing may be recorded as a decision.

        An unattended run picks the top of the ladder when there is no saved
        choice. Writing that back stamps an automatic pick with a username and
        a timestamp and gives it the standing of a preference — and a later
        unattended run honours whatever is recorded, so one such write can
        outrank the person's real choice. Measured on a real machine
        (2026-07-31): a record reading tier 1 sat beside a store holding the
        tier-2 model that had actually been chosen and was serving.
        """
        ok, _ = self._run(vram_mb=24000, is_discrete=True,
                          driver_state=_HEALTHY_NVIDIA,
                          detected=HardwareTierLevel.TIER_3,
                          recommended="Qwen3.5-35B-A3B",
                          inputs=[], auto_yes=True)
        self.assertTrue(ok)
        self.assertIsNone(model_choice.load_choice(self.home))
        self.assertFalse(model_choice.choice_path(self.home).exists())

    def test_an_unattended_run_does_not_overwrite_a_real_choice(self):
        """The harm the rule prevents: a saved decision must survive.

        A person chose the small model; an unattended run afterwards must
        honour it and leave it exactly as it was, not re-stamp it with its own
        reasoning.
        """
        model_choice.record_choice(HardwareTierLevel.TIER_1, home=self.home,
                                   chosen_by="a-person")
        before = model_choice.choice_path(self.home).read_text()
        ok, _ = self._run(vram_mb=24000, is_discrete=True,
                          driver_state=_HEALTHY_NVIDIA,
                          detected=HardwareTierLevel.TIER_3,
                          recommended="Qwen3.5-35B-A3B",
                          inputs=[], auto_yes=True)
        self.assertTrue(ok)
        self.assertEqual(model_choice.choice_path(self.home).read_text(),
                         before)

    def test_a_saved_pick_is_reused_when_not_asking(self):
        # The Welcomer runs `setup --yes`; a choice already made must stand
        # rather than being silently re-decided.
        model_choice.record_choice(HardwareTierLevel.TIER_1, home=self.home)
        ok, out = self._run(vram_mb=24000, is_discrete=True,
                            driver_state=_HEALTHY_NVIDIA,
                            detected=HardwareTierLevel.TIER_3,
                            recommended="Qwen3.5-35B-A3B",
                            inputs=[], auto_yes=True)
        self.assertTrue(ok)
        self.assertIn("saved choice: tier 1", out)
        self.assertIn("Qwen3.5-2B", out)

    def test_an_empty_answer_takes_the_top_of_the_ladder(self):
        ok, out = self._run(vram_mb=8192, is_discrete=True,
                            driver_state=_NO_NVIDIA, inputs=["", "n"])
        self.assertTrue(ok)
        self.assertIn("Qwen3.5-9B", out)

    def test_a_nonsense_answer_re_asks_rather_than_guessing(self):
        ok, out = self._run(vram_mb=8192, is_discrete=True,
                            driver_state=_NO_NVIDIA,
                            inputs=["banana", "9", "2", "n"])
        self.assertTrue(ok)
        self.assertIn("Enter the number of one of the choices above.", out)
        self.assertIn("Qwen3.5-2B", out)

    def test_explicit_tier_argument_still_wins_outright(self):
        ok, out = self._run(vram_mb=8192, is_discrete=True,
                            driver_state=_NO_NVIDIA, inputs=["n"],
                            tier_override=1)
        self.assertTrue(ok)
        self.assertIn("Qwen3.5-2B", out)
        self.assertNotIn("This box can run:", out)

    def test_an_explicit_tier_is_remembered_like_any_other_choice(self):
        """An explicit tier is a user decision, so it is recorded as one.

        This is how the Welcomer hands over the model picked on its card, which
        makes it the path a new user actually takes. It previously left no
        preference behind at all: someone who deliberately picked the smaller
        model on capable hardware had that wish forgotten, and the next
        unattended run resolved to the most capable model on offer instead.
        """
        ok, _ = self._run(vram_mb=8192, is_discrete=True,
                          driver_state=_NO_NVIDIA, inputs=["n"],
                          tier_override=1)
        self.assertTrue(ok)
        self.assertEqual(model_choice.load_choice(self.home),
                         HardwareTierLevel.TIER_1)


class AdvisoryTests(_Harness):
    def test_nouveau_box_gets_the_advisory_and_a_real_choice(self):
        ok, out = self._run(vram_mb=24000, is_discrete=True,
                            driver_state=_NOUVEAU,
                            detected=HardwareTierLevel.TIER_3,
                            recommended="Qwen3.5-35B-A3B",
                            inputs=["1", "n"])
        self.assertTrue(ok)
        self.assertIn("open-source driver", out)
        self.assertIn("Install NVIDIA's drivers", out)
        # Proceeding installs the 2B for real.
        self.assertIn("Qwen3.5-2B", out)

    def test_choosing_drivers_first_installs_nothing(self):
        ok, out = self._run(vram_mb=24000, is_discrete=True,
                            driver_state=_NOUVEAU,
                            detected=HardwareTierLevel.TIER_3,
                            recommended="Qwen3.5-35B-A3B",
                            inputs=["2"])
        self.assertTrue(ok)
        self.assertIn("Nothing was installed", out)
        self.assertNotIn("Recommended model:", out)

    def test_healthy_nvidia_box_sees_no_advisory(self):
        ok, out = self._run(vram_mb=24000, is_discrete=True,
                            driver_state=_HEALTHY_NVIDIA,
                            detected=HardwareTierLevel.TIER_3,
                            recommended="Qwen3.5-35B-A3B",
                            inputs=["1", "n"])
        self.assertTrue(ok)
        self.assertNotIn("open-source driver", out)


if __name__ == "__main__":
    unittest.main()
