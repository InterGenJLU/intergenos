# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""File-tool explanations name the providers present in the package tree."""

from intergen.tests.howto_content_helpers import answer, commands


def test_unzip_is_the_info_zip_program_and_bsdunzip_is_separate():
    text = answer("file-how-do-i-unzip-a-file")
    assert ["unzip", "archive.zip"] in commands(text)
    assert "Info-ZIP" in text and "separate" in text.lower()
    assert "provided by bsdunzip" not in text
    assert "is the libarchive `bsdunzip` engine" not in text


def test_terminal_editing_does_not_deny_the_neovim_package():
    text = answer("file-how-do-i-edit-a-file-in-the-terminal")
    assert ["nano", "file.txt"] in commands(text)
    assert ["vim", "file.txt"] in commands(text)
    assert "neovim" in text.lower() and "nvim" in text
    assert "no nvim" not in text
