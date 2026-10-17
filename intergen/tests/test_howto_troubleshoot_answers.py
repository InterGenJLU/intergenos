# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Recovery explanations do not promise unavailable session or boot behavior."""

from intergen.tests.howto_content_helpers import answer


def test_shell_freeze_guidance_distinguishes_wayland_from_x11_restart():
    text = answer("trbl-my-system-froze-how-do-i-force-quit")
    assert "Wayland" in text and "X11" in text
    assert "unavailable" in text.lower()
    assert "try `Alt+F2` then type `r`" not in text


def test_boot_guidance_requires_an_available_fallback_and_an_observed_failure():
    text = answer("trbl-my-computer-won-t-boot")
    assert "UKI" in text
    assert "not guaranteed" in text.lower()
    assert "encrypted" in text.lower()
    assert "message" in text.lower()
    assert "your safe fallback" not in text
    assert "case below" not in text
