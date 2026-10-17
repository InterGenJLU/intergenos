# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Hardware explanations preserve the distinction between capability and state."""

import re

import pytest

from intergen.tests.howto_content_helpers import answer, commands, entry


def test_nvidia_setup_names_the_key_passphrase_and_enrollment_conditions():
    text = answer("hw-how-do-i-install-nvidia-drivers")
    assert ["sudo", "pkm", "install", "nvidia"] in commands(text)
    assert "license" in text.lower()
    assert "passphrase" in text.lower()
    assert "enroll" in text.lower() and "Secure Boot" in text
    assert not re.search(r"signed.{0,100}automatically", text, re.I | re.S)


def test_gpu_switching_acknowledges_packaged_but_hardware_conditional_support():
    text = answer("hw-how-do-i-switch-between-integrated-and-nvidia-gr")
    assert "switcheroo-control" in text
    assert "Launch using Discrete Graphics Card" in text
    assert "hardware" in text.lower()
    assert re.search(r"\bdepends?\b", text.lower())
    assert "no PRIME/switcheroo tooling" not in text


def test_battery_health_has_a_reading_not_just_a_charge_level():
    text = answer("hw-how-do-i-check-my-battery-health")
    assert ["upower", "--dump"] in commands(text)
    assert "design" in text.lower() and "capacity" in text.lower()
    assert "charge" in text.lower() and "health" in text.lower()
    assert "unavailable" in text.lower()


@pytest.mark.parametrize("question", [
    "what CPU do I have",
    "how many cores do I have",
])
def test_cpu_explanation_does_not_rename_logical_processors_as_physical_cores(question):
    item = entry("hw-what-cpu-do-i-have")
    assert question in item.triggers
    assert ["lscpu"] in commands(item.answer)
    assert "CPU(s)" in item.answer
    assert "logical processors" in item.answer.lower()
    assert "not physical cores" in item.answer.lower()
