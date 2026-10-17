# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Disk guidance uses packaged tools and describes cache retention accurately."""

from intergen.tests.howto_content_helpers import answer, commands


def test_large_file_guidance_does_not_invent_an_ncdu_package():
    text = answer("disk-how-do-i-find-large-files")
    assert "du -ah" in text
    assert "Disk Usage Analyzer" in text
    assert "pkm install ncdu" not in text


def test_cache_cleanup_retains_the_previous_release_and_separates_targets():
    text = answer("disk-how-do-i-clear-the-package-cache")
    assert ["sudo", "pkm", "cache", "clean"] in commands(text)
    assert ["sudo", "pkm", "cache", "clean", "--rollback"] in commands(text)
    assert "previous release" in text.lower()
    assert "packages/" in text and "rollback/" in text
    assert "separate" in text.lower()
    assert "drop the rest" not in text.lower()
    assert "rollback snapshots too" not in text.lower()
