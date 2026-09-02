# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The Welcomer's detection and install flow, as one coherent pass.

These tests cover the decisions the graphics page makes: what it OFFERS a
machine, what it RUNS when the user accepts, what it PROMISES about coming
back, and what it SAYS afterwards. They were written against the observed
behaviour of the released build on real installs of both graphics paths
(AMD on this project's reference workstation, NVIDIA on the reference
laptop), where the flow completed correctly but described itself wrongly:

  - software that was already installed was offered again as available, on
    both the AMD and the NVIDIA path;
  - a page that had just told the user their driver was installed carried,
    below it, a banner asserting the machine was still running the open
    source driver;
  - the same driver was offered twice on one page, by two separate boxes
    with two separate buttons;
  - the user was promised the Welcomer would return after a reboot on a
    path where nothing arranged for it to return;
  - a selection that needed no reboot was told to reboot, and a selection
    that installed a new inference engine was told nothing at all.

Every test here is pure and runs headless — the same rule the other
Welcomer tests follow. What a page LOOKS like is proven by rendering it,
not asserted here.
"""

import importlib.util
import re
import unittest
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WELCOME_PY = REPO_ROOT / "assets" / "intergen-welcome" / "intergen-welcome.py"

_spec = importlib.util.spec_from_file_location("intergen_welcome", WELCOME_PY)
welcome = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(welcome)


def _record(vendor, upgrade, outranks=False, supported=None):
    """A detection record shaped exactly as the installer writes one."""
    return {
        "version": welcome._GPU_RECORD_VERSION,
        "vendor": vendor,
        "pci_vendors": [],
        "shipped_engine": "vulkan",
        "upgrade_engine": upgrade,
        "upgrade_outranks_shipped": outranks,
        "gfx_targets": [],
        "upgrade_engine_supported": supported,
    }


NVIDIA = _record("nvidia", "cuda")
AMD = _record("amd", "hip", outranks=True, supported=True)
INTEL = _record("intel", None)


def _probe(**states):
    """A stand-in for the package-database question.

    Maps package name -> True (installed) / False (not installed) /
    None (could not be determined). An unnamed package answers False.
    """
    return lambda name: states.get(name, False)


NOTHING_INSTALLED = _probe()


class TestWhatIsOffered(unittest.TestCase):
    """An offer is a statement about what this machine can ADD."""

    def test_the_amd_engine_is_offered_when_it_is_not_installed(self):
        offers = welcome._gpu_offers(AMD, probe=NOTHING_INSTALLED)
        self.assertEqual([o["key"] for o in offers], ["compute_engine"])

    def test_an_installed_amd_engine_is_not_offered_again(self):
        """Reproduced on the reference AMD workstation: llama-cpp-hip was
        installed and the page still offered it, switch off, as available."""
        offers = welcome._gpu_offers(
            AMD, probe=_probe(**{"llama-cpp-hip": True}))
        self.assertEqual(offers, [])

    def test_an_installed_nvidia_driver_is_not_offered_again(self):
        offers = welcome._gpu_offers(NVIDIA, probe=_probe(nvidia=True))
        self.assertEqual([o["key"] for o in offers], ["compute_engine"])

    def test_an_installed_cuda_engine_is_not_offered_again(self):
        """The engine AND the toolkit: the offer installs both, so both have
        to be present for it to be withdrawn."""
        offers = welcome._gpu_offers(
            NVIDIA, probe=_probe(**{"cuda-toolkit": True, "llama-cpp-cuda": True}))
        self.assertEqual([o["key"] for o in offers], ["nvidia_driver"])

    def test_an_engine_whose_toolkit_never_downloaded_is_still_offered(self):
        """The reference laptop, 2026-09-02: the engine package present, the
        toolkit's installer package present but its download never run. The
        package manager now answers "not installed" for the toolkit, and the
        offer stays."""
        offers = welcome._gpu_offers(
            NVIDIA, probe=_probe(nvidia=True, **{"cuda-toolkit": False,
                                                 "llama-cpp-cuda": True}))
        self.assertEqual([o["key"] for o in offers], ["compute_engine"])

    def test_a_fully_equipped_machine_is_offered_nothing(self):
        offers = welcome._gpu_offers(
            NVIDIA, probe=_probe(nvidia=True, **{"cuda-toolkit": True,
                                                 "llama-cpp-cuda": True}))
        self.assertEqual(offers, [])

    def test_a_package_whose_state_cannot_be_read_is_still_offered(self):
        """Unknown is not installed. Withdrawing an offer because the
        question could not be answered would hide a real upgrade path and
        look exactly like hardware that has none."""
        offers = welcome._gpu_offers(
            AMD, probe=_probe(**{"llama-cpp-hip": None}))
        self.assertEqual([o["key"] for o in offers], ["compute_engine"])

    def test_every_offer_names_the_packages_it_installs(self):
        """The installed-state question is asked of exact package names, not
        parsed back out of a shell command."""
        for record in (NVIDIA, AMD):
            for offer in welcome._gpu_offers(record, probe=NOTHING_INSTALLED):
                self.assertTrue(offer["packages"],
                                f"{offer['key']} names no packages")
                for name in offer["packages"]:
                    self.assertIsInstance(name, str)
                    self.assertIn(name, offer["command"])

    def test_hardware_with_no_upgrade_path_is_offered_nothing(self):
        self.assertEqual(welcome._gpu_offers(INTEL, probe=NOTHING_INSTALLED), [])


class TestTheStandaloneDriverBanner(unittest.TestCase):
    """One subject, one box.

    The released page carried two: a 'proprietary drivers are recommended'
    banner with its own install button, and directly beneath it an
    'optional software is available' box whose first switch was that same
    driver. Decided 2026-08-22: one box, with the explanatory text moved
    inside it.
    """

    def test_the_driver_is_never_offered_by_two_boxes_at_once(self):
        offers = welcome._gpu_offers(NVIDIA, probe=NOTHING_INSTALLED)
        carries_driver = any(o["key"] == "nvidia_driver" for o in offers)
        self.assertTrue(carries_driver)
        self.assertFalse(
            welcome._standalone_driver_banner_applies(
                NVIDIA, probe=NOTHING_INSTALLED),
            "the offer box already carries the driver; a second box offering "
            "it again is the defect")

    def test_the_nouveau_claim_is_withheld_once_the_driver_is_installed(self):
        """The released page asserted 'this machine is currently running the
        Nouveau opensource GPU driver' on a machine where the proprietary
        driver was installed — directly under its own line saying so."""
        self.assertFalse(
            welcome._driver_advisory_applies(NVIDIA, probe=_probe(nvidia=True)))

    def test_the_advisory_applies_on_a_machine_still_on_the_open_driver(self):
        self.assertTrue(
            welcome._driver_advisory_applies(NVIDIA, probe=NOTHING_INSTALLED))

    def test_the_advisory_never_applies_off_the_nvidia_path(self):
        self.assertFalse(
            welcome._driver_advisory_applies(AMD, probe=NOTHING_INSTALLED))


class TestWhatIsRun(unittest.TestCase):
    """One selection is one package transaction."""

    def test_a_multi_package_selection_is_one_install_invocation(self):
        """The released build chained a separate sync-and-install per offer.
        The package manager prints its 'next steps' advisory at the end of
        each transaction, so the first transaction's REBOOT REQUIRED block
        was scrolled off the screen by the second transaction's output —
        the aggravator the operator photographed. One transaction prints one
        advisory, at the end, where it is read."""
        offers = welcome._gpu_offers(NVIDIA, probe=NOTHING_INSTALLED)
        command = welcome._gpu_install_command(
            ["nvidia_driver", "compute_engine"], offers)
        self.assertEqual(command.count("pkm install"), 1)
        self.assertEqual(command.count("pkm update"), 1)
        self.assertIn("nvidia", command)
        self.assertIn("llama-cpp-cuda", command)

    def test_the_index_is_synced_before_the_install(self):
        offers = welcome._gpu_offers(AMD, probe=NOTHING_INSTALLED)
        command = welcome._gpu_install_command(["compute_engine"], offers)
        self.assertLess(command.index("pkm update"), command.index("pkm install"))
        self.assertIn("&&", command)

    def test_nothing_selected_runs_nothing(self):
        offers = welcome._gpu_offers(AMD, probe=NOTHING_INSTALLED)
        self.assertIsNone(welcome._gpu_install_command([], offers))

    def test_the_driver_is_installed_before_the_engine_that_links_it(self):
        offers = welcome._gpu_offers(NVIDIA, probe=NOTHING_INSTALLED)
        command = welcome._gpu_install_command(
            ["compute_engine", "nvidia_driver"], offers)
        self.assertLess(command.index("nvidia"), command.index("llama-cpp-cuda"))


class TestWhatIsPromised(unittest.TestCase):
    """The notice shown before the terminal opens must describe THIS
    selection — and whatever it promises must be arranged."""

    def test_the_driver_selection_needs_a_reboot(self):
        offers = welcome._gpu_offers(NVIDIA, probe=NOTHING_INSTALLED)
        self.assertEqual(
            welcome._activation_required(["nvidia_driver"], offers), "reboot")

    def test_the_engine_selection_needs_the_assistant_restarted(self):
        """A newly installed inference engine is chosen when the InterGen
        service starts a server, so it does not take over until that service
        restarts. The released build said nothing at all here."""
        offers = welcome._gpu_offers(AMD, probe=NOTHING_INSTALLED)
        self.assertEqual(
            welcome._activation_required(["compute_engine"], offers),
            "service-restart")

    def test_a_reboot_outranks_a_service_restart(self):
        offers = welcome._gpu_offers(NVIDIA, probe=NOTHING_INSTALLED)
        self.assertEqual(
            welcome._activation_required(
                ["nvidia_driver", "compute_engine"], offers), "reboot")

    def test_nothing_selected_needs_nothing(self):
        offers = welcome._gpu_offers(AMD, probe=NOTHING_INSTALLED)
        self.assertEqual(welcome._activation_required([], offers), "none")

    def test_the_notice_mentions_a_reboot_only_when_one_is_needed(self):
        offers = welcome._gpu_offers(AMD, probe=NOTHING_INSTALLED)
        notice = welcome._install_notice(["compute_engine"], offers)
        self.assertNotIn("reboot", notice.lower())

    def test_the_notice_names_the_reboot_when_one_is_needed(self):
        offers = welcome._gpu_offers(NVIDIA, probe=NOTHING_INSTALLED)
        notice = welcome._install_notice(["nvidia_driver"], offers)
        self.assertIn("reboot", notice.lower())

    def test_the_notice_names_the_licence_gate_for_a_proprietary_selection(self):
        offers = welcome._gpu_offers(NVIDIA, probe=NOTHING_INSTALLED)
        notice = welcome._install_notice(["nvidia_driver"], offers)
        self.assertIn("license", notice.lower())

    def test_a_promise_to_return_is_made_only_when_it_is_arranged(self):
        """Observed on the reference AMD workstation: the notice promised the
        Welcomer would be shown again after the reboot, on a path where the
        re-arm request was never written, so it never was."""
        offers_amd = welcome._gpu_offers(AMD, probe=NOTHING_INSTALLED)
        offers_nv = welcome._gpu_offers(NVIDIA, probe=NOTHING_INSTALLED)
        for selected, offers in ((["compute_engine"], offers_amd),
                                 (["compute_engine"], offers_nv),
                                 (["nvidia_driver"], offers_nv),
                                 (["nvidia_driver", "compute_engine"], offers_nv)):
            notice = welcome._install_notice(selected, offers)
            promises = "shown the welcomer again" in notice.lower()
            self.assertEqual(
                promises,
                welcome._welcomer_rearm_is_needed(selected, offers),
                f"the notice for {selected} promises a return "
                f"({promises}) that the re-arm does not arrange")


class TestWhatIsSaidAfterwards(unittest.TestCase):
    """The outcome is asked of the package database, not inferred from a
    window closing."""

    def test_a_completed_install_is_reported_as_completed(self):
        offers = welcome._gpu_offers(AMD, probe=NOTHING_INSTALLED)
        outcome = welcome._install_outcome(
            ["compute_engine"], offers, probe=_probe(**{"llama-cpp-hip": True}))
        self.assertTrue(outcome["installed"])
        self.assertEqual(outcome["missing"], [])
        self.assertEqual(outcome["activation"], "service-restart")
        self.assertIn("restart", outcome["message"].lower())

    def test_a_completed_driver_install_names_the_reboot(self):
        """Reported on both reference machines: no reboot notice reached the
        user after the install, on either graphics path."""
        offers = welcome._gpu_offers(NVIDIA, probe=NOTHING_INSTALLED)
        outcome = welcome._install_outcome(
            ["nvidia_driver"], offers, probe=_probe(nvidia=True))
        self.assertTrue(outcome["installed"])
        self.assertIn("reboot", outcome["message"].lower())

    def test_an_install_that_did_not_happen_is_not_reported_as_success(self):
        offers = welcome._gpu_offers(NVIDIA, probe=NOTHING_INSTALLED)
        outcome = welcome._install_outcome(
            ["nvidia_driver"], offers, probe=_probe(nvidia=False))
        self.assertFalse(outcome["installed"])
        self.assertEqual(outcome["missing"], ["nvidia"])
        self.assertNotIn("reboot", outcome["message"].lower())

    def test_an_unanswerable_question_is_not_reported_as_either(self):
        offers = welcome._gpu_offers(NVIDIA, probe=NOTHING_INSTALLED)
        outcome = welcome._install_outcome(
            ["nvidia_driver"], offers, probe=_probe(nvidia=None))
        self.assertIsNone(outcome["installed"])
        self.assertIn("could not", outcome["message"].lower())

    def test_a_partial_install_names_what_is_missing(self):
        offers = welcome._gpu_offers(NVIDIA, probe=NOTHING_INSTALLED)
        outcome = welcome._install_outcome(
            ["nvidia_driver", "compute_engine"], offers,
            probe=_probe(nvidia=True, **{"cuda-toolkit": True,
                                         "llama-cpp-cuda": False}))
        self.assertFalse(outcome["installed"])
        self.assertEqual(outcome["missing"], ["llama-cpp-cuda"])
        self.assertIn("llama-cpp-cuda", outcome["message"])

    def test_a_toolkit_that_never_downloaded_is_named_as_missing(self):
        """The laptop's outcome, had the verdict been asked at the right
        moment: the engine installed, the toolkit's download not run."""
        offers = welcome._gpu_offers(NVIDIA, probe=NOTHING_INSTALLED)
        outcome = welcome._install_outcome(
            ["nvidia_driver", "compute_engine"], offers,
            probe=_probe(nvidia=True, **{"cuda-toolkit": False,
                                         "llama-cpp-cuda": True}))
        self.assertFalse(outcome["installed"])
        self.assertEqual(outcome["missing"], ["cuda-toolkit"])


class TestThePageOrder(unittest.TestCase):
    """A page's own heading stays at the top of it."""

    def test_the_setup_card_is_never_placed_above_the_page_title(self):
        """On the reference laptop the setup block rendered ABOVE the 'Meet
        InterGen' heading it belongs under, because the card was moved to
        position zero of the page box."""
        self.assertEqual(welcome._setup_card_placement(driver_leg_done=True),
                         "after-title")

    def test_the_default_order_is_unchanged_on_a_first_visit(self):
        self.assertEqual(welcome._setup_card_placement(driver_leg_done=False),
                         "default")


class TestTheApplyButtonOnTheNameServerPage(unittest.TestCase):
    """Choosing a different name server arms an Apply button at the bottom
    of a scrolling page. A user who cannot see it does not know the choice
    still has to be applied."""

    def test_the_button_is_revealed_when_it_becomes_the_next_action(self):
        self.assertTrue(welcome._should_reveal_apply(was_live=False, is_live=True))

    def test_an_already_visible_button_is_not_scrolled_to_again(self):
        self.assertFalse(welcome._should_reveal_apply(was_live=True, is_live=True))

    def test_nothing_is_revealed_when_the_button_goes_back_to_inert(self):
        self.assertFalse(welcome._should_reveal_apply(was_live=True, is_live=False))
        self.assertFalse(welcome._should_reveal_apply(was_live=False, is_live=False))


class TestTheWordsOnScreen(unittest.TestCase):

    def test_one_spelling_of_license_is_used_throughout(self):
        """Two spellings of the same word appeared on one page — 'license'
        in the model copy and 'licence' in the vendor lines directly below
        it. American spelling is the project's, and the AMD and NVIDIA
        banners both carry the word."""
        source = WELCOME_PY.read_text()
        offenders = [line.strip() for line in source.splitlines()
                     if re.search(r"\blicence", line, re.IGNORECASE)]
        self.assertEqual(offenders, [], "British spelling of 'license' found")

    def test_the_hint_text_scales_with_the_users_font(self):
        """The hint under the setup heading was pinned at 12 physical pixels,
        so it did not follow the user's text size. Every size in this
        stylesheet's body copy is relative."""
        block = _css_block(welcome.CUSTOM_CSS, ".intergen-summon-text")
        self.assertRegex(block, r"font-size:\s*[0-9.]+em",
                         "hint text must be sized relative to the user's font")

    def test_the_switch_row_descriptions_are_not_dimmed(self):
        """Photographed on the reference machines: the switch rows' grey
        description text read as disabled, so live options looked
        unselectable.

        The colour is asserted on the mechanism that actually carries it. A
        stylesheet rule does not: measured on GTK 4.20.3 / libadwaita 1.8.4,
        a colour written for these labels from the application stylesheet is
        not applied, because libadwaita styles them from inside the row. The
        description is coloured as a Pango attribute on the text, and that is
        what this pins.
        """
        source = WELCOME_PY.read_text()
        self.assertRegex(welcome._OFFER_DETAIL_COLOR, r"^#[0-9a-fA-F]{6}$")
        self.assertIn(f'<span foreground="{{_OFFER_DETAIL_COLOR}}">', source)
        # Near-white, not the interface's secondary grey — this text is the
        # whole description of what is about to be installed.
        red, green, blue = (int(welcome._OFFER_DETAIL_COLOR[i:i + 2], 16)
                            for i in (1, 3, 5))
        self.assertGreater(min(red, green, blue), 190,
                           "the description is still drawn in a dim colour")

    def test_the_description_is_escaped_before_it_is_marked_up(self):
        """Markup has to be ON for the colour attribute, and the driver
        command contains a bare ampersand — unescaped, it aborts the Pango
        parse and the whole row renders nothing but a GTK warning, so the
        offer that matters most is the one that vanishes."""
        source = WELCOME_PY.read_text()
        self.assertIn("GLib.markup_escape_text(detail)", source)

    def test_the_offer_box_is_clamped_to_the_window_width(self):
        """The banner overflowed the window on the reference laptop, clipping
        its own button row. The box is bounded rather than centred at its
        natural width."""
        self.assertLessEqual(welcome._OFFER_BOX_MAX_WIDTH, 700)
        self.assertGreater(welcome._OFFER_BOX_MAX_WIDTH, 400)


def _css_block(css, selector):
    """The declarations of the first rule whose selector list matches."""
    match = re.search(re.escape(selector) + r"[^{}]*\{([^}]*)\}", css)
    if match is None:
        raise AssertionError(f"no CSS rule for {selector}")
    return match.group(1)


if __name__ == "__main__":
    unittest.main()
