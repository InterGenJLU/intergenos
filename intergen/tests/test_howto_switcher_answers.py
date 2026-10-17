# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Switcher guidance describes this desktop and its optional compatibility tools."""

from intergen.tests.howto_content_helpers import answer, commands


def test_task_manager_answer_uses_the_actual_logout_binding():
    text = answer("switch-what-s-the-task-manager-on-linux")
    assert "System Monitor" in text
    assert "Ctrl+Alt+Del" in text and "logs out" in text.lower()
    assert "combo opens the power-off dialog" not in text


def test_windows_compatibility_packages_are_available_but_not_assumed_installed():
    text = answer("switch-how-do-i-run-windows-programs-on-linux")
    assert ["pkm", "info", "wine"] in commands(text)
    assert ["pkm", "info", "ge-proton"] in commands(text)
    assert "installed" in text.lower()
    assert "compatibility" in text.lower()
    assert "which" in text.lower()
    assert "not yet merged" not in text
