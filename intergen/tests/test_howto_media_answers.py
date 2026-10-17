# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Media and appearance guidance names the shipped application and theme tools."""

from intergen.tests.howto_content_helpers import answer


def test_video_diagnosis_uses_celluloid_and_requests_the_observed_error():
    text = answer("media-why-won-t-my-video-play")
    assert "Celluloid" in text and "mpv" in text
    assert "error" in text.lower()
    assert "Totem" not in text
    assert "play out of the box" not in text


def test_icon_and_cursor_themes_use_tweaks_or_the_welcomer():
    text = answer("media-how-do-i-change-the-theme")
    assert "GNOME Tweaks" in text
    assert "Welcomer" in text
    assert "icons" in text and "cursor" in text
    assert "Settings → Appearance, to switch among" not in text
