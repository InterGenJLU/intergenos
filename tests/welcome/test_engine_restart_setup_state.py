# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An engine install only asks for a restart if InterGen is already set up.

Exercise the real notice, package outcome, and terminal footer helpers with
both model-store states. No install or setup handler runs in these tests.
"""

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


SCRIPT = (Path(__file__).resolve().parents[2]
          / "assets/intergen-welcome/intergen-welcome.py")
_spec = importlib.util.spec_from_file_location("welcome_setup_text", SCRIPT)
welcome = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(welcome)

SETUP_NEXT = (
    "InterGen is not set up yet — set him up next; he will use the new "
    "engine from his first start.")
SETUP_ADVISORY = (
    "InterGen is not set up yet — set him up below; he will use the new "
    "engine from his first start.")
NOTICE_START = (
    "A TERMINAL WINDOW WILL OPEN MOMENTARILY - enter your password "
    "when the package manager asks for it.")
LICENSE_NOTICE = (
    "ACCEPT the vendor license when it is shown; nothing is "
    "installed until you do.")
RESTART_NOTICE = (
    "InterGen chooses which engine to run with when his service starts, "
    "so once the installation finishes, restart InterGen — or the whole "
    "machine — for the new engine to take over.")
RESTART_OUTCOME = (
    "InterGen chooses which engine to run with when his service starts, "
    "so restart InterGen — or the whole machine — for the new engine "
    "to take over.")
RESTART_FOOTER = (
    ">>> RESTART INTERGEN: he chooses which engine to run with when his "
    "service starts, so the new engine takes over after a restart.")


def _offers(vendor):
    return welcome._gpu_offers({
        "version": welcome._GPU_RECORD_VERSION,
        "vendor": vendor,
        "upgrade_engine": "hip" if vendor == "amd" else "cuda",
        "upgrade_outranks_shipped": True,
        "upgrade_engine_supported": True,
    }, probe=lambda name: False)


def _message(surface, selected, offers, installed=True):
    if surface == "notice":
        return welcome._install_notice(selected, offers)
    if surface == "outcome":
        return welcome._install_outcome(
            selected, offers, probe=lambda name: installed)["message"]
    return welcome._closing_note(selected, offers)


@pytest.mark.parametrize("vendor", ["amd", "nvidia"])
@pytest.mark.parametrize("set_up", [False, True], ids=["not-set-up", "set-up"])
@pytest.mark.parametrize("surface", ["notice", "outcome", "footer"])
def test_engine_activation_text_matches_setup_state(vendor, set_up, surface):
    with patch.object(welcome, "_intergen_is_set_up", return_value=set_up):
        message = _message(surface, ["compute_engine"], _offers(vendor))

    if not set_up:
        assert (SETUP_ADVISORY if surface == "notice" else SETUP_NEXT) in message
        if surface != "notice":
            assert "set him up below" not in message
        assert "restart intergen" not in message.lower()
        assert "reboot" not in message.lower()
        return

    if surface == "notice":
        parts = [NOTICE_START]
        if vendor == "nvidia":
            parts.append(LICENSE_NOTICE)
        parts.append(RESTART_NOTICE)
        expected = " ".join(parts)
    elif surface == "outcome":
        packages = ("llama-cpp-hip" if vendor == "amd"
                    else "cuda-toolkit and llama-cpp-cuda")
        expected = packages + " installed. " + RESTART_OUTCOME
    else:
        expected = RESTART_FOOTER
    assert message == expected


@pytest.mark.parametrize("set_up", [False, True])
@pytest.mark.parametrize("installed", [False, None], ids=["missing", "unknown"])
def test_incomplete_engine_install_does_not_claim_the_new_engine_is_ready(
        set_up, installed):
    with patch.object(welcome, "_intergen_is_set_up", return_value=set_up):
        outcome = welcome._install_outcome(
            ["compute_engine"], _offers("amd"),
            probe=lambda name: installed)
    assert outcome["installed"] is installed
    assert SETUP_NEXT not in outcome["message"]
    assert "restart InterGen" not in outcome["message"]
    assert ("still not installed" if installed is False else "could not be determined"
            ) in outcome["message"]


@pytest.mark.parametrize("surface", ["notice", "outcome", "footer"])
@pytest.mark.parametrize("selected", [["nvidia_driver"],
                                     ["nvidia_driver", "compute_engine"]])
def test_driver_reboot_wording_does_not_depend_on_setup_state(surface, selected):
    messages = []
    for set_up in (False, True):
        with patch.object(welcome, "_intergen_is_set_up", return_value=set_up):
            messages.append(_message(surface, selected, _offers("nvidia")))
    assert messages[0] == messages[1]
    assert "reboot" in messages[0].lower()
    assert welcome._WELCOMER_RETURNS_AFTER_REBOOT in messages[0]
    assert SETUP_NEXT not in messages[0]
