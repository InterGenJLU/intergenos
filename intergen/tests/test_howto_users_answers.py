# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Account and permission answers are standalone and distinguish login contexts."""

from intergen.tests.howto_content_helpers import answer, commands


def test_root_password_is_distinct_from_ssh_root_login():
    text = answer("user-how-do-i-change-the-root-password")
    assert ["sudo", "passwd", "root"] in commands(text)
    assert "local root" in text.lower()
    assert "SSH" in text
    assert "direct root login is typically disabled" not in text


def test_new_account_administration_does_not_require_another_answer():
    text = answer("user-how-do-i-add-a-new-user")
    assert ["sudo", "useradd", "-m", "-s", "/bin/bash", "<username>"] in commands(text)
    assert ["sudo", "passwd", "<username>"] in commands(text)
    assert ["sudo", "usermod", "-aG", "wheel", "<username>"] in commands(text)
    assert "next answer" not in text.lower()


def test_administrator_group_is_stated_without_an_authoring_note():
    text = answer("user-how-do-i-give-a-user-sudo-rights")
    assert ["sudo", "usermod", "-aG", "wheel", "<username>"] in commands(text)
    assert "verify before this ships" not in text.lower()
    assert "LFS/BLFS convention" not in text


def test_denied_access_does_not_describe_installed_root_as_read_only():
    text = answer("user-why-do-i-get-permission-denied")
    assert "installed" in text.lower() and "writable" in text.lower()
    assert "live" in text.lower() and "read-only" in text.lower()
    assert "ls -l" in text
    assert "chown you:you" not in text


def test_numeric_permissions_are_explained_in_the_answer_itself():
    text = answer("user-how-do-i-change-file-permissions")
    for word in ("owner", "group", "others", "read", "write", "execute"):
        assert word in text.lower()
    for bit in ("4", "2", "1"):
        assert bit in text
    assert 'See "what does chmod 777 mean"' not in text
