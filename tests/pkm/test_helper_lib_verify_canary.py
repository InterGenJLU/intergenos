# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The helper library's verification canary covers its installed files."""

import re
import shlex
from pathlib import Path

import pytest
import yaml

PACKAGE = Path(__file__).resolve().parents[2] / "packages/core/intergenos-helper-lib"
HELPERS = "/usr/share/igos/helpers/"


def installed_helpers():
    build = (PACKAGE / "build.sh").read_text()
    paths = set()
    for line in re.sub(r"\\\n\s*", " ", build).splitlines():
        if not line.strip().startswith("install "):
            continue
        destination = shlex.split(line)[-1]
        if destination.startswith("${DESTDIR}" + HELPERS):
            paths.add(destination.removeprefix("${DESTDIR}"))
    assert paths, "build.sh has no recognized helper-file installs"
    return paths


def declared_paths():
    return set(yaml.safe_load((PACKAGE / "package.yml").read_text())["verify_paths"])


def check_canary(installed, declared):
    missing = installed - declared
    uninstalled = {p for p in declared if p.startswith(HELPERS)} - installed
    assert not missing, f"installed helpers missing from verify_paths: {sorted(missing)}"
    assert not uninstalled, f"verify_paths names uninstalled helpers: {sorted(uninstalled)}"


def test_canary_covers_every_installed_helper():
    check_canary(installed_helpers(), declared_paths())


def test_missing_installed_helper_is_rejected():
    installed = installed_helpers()
    with pytest.raises(AssertionError, match="missing from verify_paths"):
        check_canary(installed, installed - {next(iter(installed))})


def test_uninstalled_canary_entry_is_rejected():
    installed = installed_helpers()
    with pytest.raises(AssertionError, match="uninstalled helpers"):
        check_canary(installed, installed | {HELPERS + "not-installed.py"})
