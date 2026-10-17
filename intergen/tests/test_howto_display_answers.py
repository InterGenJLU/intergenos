# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Display guidance uses the configured shortcuts and qualifies hardware limits."""

import re

from intergen.tests.howto_content_helpers import answer


def test_dual_monitor_guidance_does_not_guarantee_detection_or_invent_a_shortcut():
    text = answer("disp-how-do-i-set-up-dual-monitors")
    assert "Settings" in text and "Displays" in text
    assert "Super+Shift+Left" in text and "Super+Shift+Right" in text
    assert "hardware" in text.lower() and re.search(r"\bdepends?\b", text.lower())
    assert "detects it immediately" not in text
    assert "Super+Shift+Arrow" not in text
