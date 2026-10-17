# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Package guidance must agree with the shipped package-manager contract."""

import re

import pytest

from intergen.tests.howto_content_helpers import answer, commands, entry


def test_uninstall_keeps_orphan_removal_a_separate_explicit_action():
    text = answer("pkm-how-do-i-uninstall-a-program")
    examples = commands(text)
    assert ["sudo", "pkm", "remove", "<name>"] in examples
    assert ["pkm", "autoremove", "--dry-run"] in examples
    assert ["sudo", "pkm", "autoremove"] in examples
    assert "separate" in text.lower()
    assert "offers to drop" not in text.lower()


@pytest.mark.parametrize("package,identifier", [
    ("steam", "pkm-how-do-i-install-steam"),
    ("zoom", "pkm-how-do-i-install-zoom-on-linux"),
])
def test_available_vendor_helpers_are_presented_as_installable(package, identifier):
    text = answer(identifier)
    assert ["sudo", "pkm", "install", package] in commands(text)
    assert "helper" in text.lower()
    assert "license" in text.lower()
    assert not re.search(r"(?:isn't|not) packaged|does \*\*not\*\*.*ship", text, re.I)


def test_package_system_comparison_answers_the_differences():
    text = answer("pkm-what-s-the-difference-between-snap-flatpak-and-a")
    assert "Debian" in text and ".deb" in text
    assert "snapd" in text
    assert "Flatpak" in text and "bundle" in text.lower()
    assert "pkm" in text and ".igos.tar.gz" in text


@pytest.mark.parametrize("question", [
    "how do I update my system",
    "how do I update everything",
])
def test_update_answer_and_offer_explicitly_upgrade_all_packages(question):
    item = entry("pkm-update-system")
    assert question in item.triggers
    assert ["sudo", "pkm", "sync"] in commands(item.answer)
    assert ["sudo", "pkm", "upgrade", "--all"] in commands(item.answer)
    assert item.action.tool == "manage_packages"
    assert item.action.command == "sudo pkm sync && sudo pkm upgrade --all"
    assert "rebuilds-or-fetches" not in item.answer


def test_reboot_guidance_includes_session_services():
    text = answer("pkm-do-i-need-to-reboot-after-updating")
    assert ["pkm", "restart-services", "--list"] in commands(text)
    assert "kernel" in text.lower() and "session" in text.lower()
    assert not re.search(r"reboot is only needed for.*kernel", text, re.I | re.S)


def test_overview_does_not_reintroduce_a_bare_mass_upgrade():
    text = answer("pkm-overview")
    examples = commands(text)
    assert ["sudo", "pkm", "upgrade", "--all"] in examples
    assert ["pkm", "upgrade"] not in examples
    assert ["sudo", "pkm", "upgrade"] not in examples


def test_vendor_helper_package_is_distinct_from_the_vendor_payload():
    text = answer("pkm-install")
    assert "helper packages" in text.lower()
    assert "repository" in text.lower()
    assert "license" in text.lower()
    assert "aren't in the repos" not in text


def test_verification_describes_its_limited_coverage():
    text = answer("pkm-verify")
    assert ["sudo", "pkm", "verify", "--all"] in commands(text)
    assert "configuration" in text.lower()
    assert "existence-only" in text.lower()
    assert "undetermined" in text.lower()
    assert "re-hashes the files each package owns" not in text
