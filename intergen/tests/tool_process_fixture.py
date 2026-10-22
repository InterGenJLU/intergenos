# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Fixtures for cells that start a tool's process behind its boundary.

A tool's process runs as a transient service with a private /tmp of its own, so
a fixture under /tmp does not exist inside it: a refusal measured there would be
an absent file passing for a refused one, and a mask named there would fail the
service's start. These fixtures therefore live under the session's runtime
directory, which the service sees, read-only.

The service masks the credential files of the account that owns the install and
refuses to start when either is absent, so a cell that starts a tool's process
makes its fixture that account's home, with FAKE credentials in it, answering the
lookup the module itself makes: the password database, not HOME.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen import dispatch_token as dt

NO_USER_MANAGER = (
    "no systemd user manager answers in this session, so a tool's process "
    "cannot be started behind its boundary here"
)


def user_manager_answers() -> bool:
    """Whether this session's systemd user manager answers at all."""
    try:
        completed = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True, text=True, timeout=10,
            stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.stdout.strip() in {
        "running", "degraded", "starting", "initializing", "maintenance"}


def visible_directory(prefix: str) -> tempfile.TemporaryDirectory:
    """A throwaway directory a tool's service can see (not under /tmp)."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime or not os.path.isdir(runtime):
        raise unittest.SkipTest(
            "XDG_RUNTIME_DIR is not set, so there is no directory a tool's "
            "service sees outside its own private /tmp")
    return tempfile.TemporaryDirectory(prefix=prefix, dir=runtime)


def write_fake_credentials(home: Path) -> dict[str, str]:
    """Generate both credential files under `home`, owner-only, and return them.

    Keyed by file name (dispatch-key, web-token). Freshly random every time, so
    a value found in a tool's output can only have come from that file.
    """
    values: dict[str, str] = {}
    for relative in dt.SECRET_RELATIVE_PATHS:
        path = home.joinpath(*relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_hex(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value + "\n")
        values[relative[-1]] = value
    return values


def install_lives_in(home: Path):
    """Resolve the owning account's home to `home`, as dispatch_token does."""
    fake = mock.MagicMock()
    fake.getpwuid.return_value.pw_dir = str(home)
    fake.getpwnam.return_value.pw_dir = str(home)
    return mock.patch.object(dt, "pwd", fake)


def unit_search_path_is(*directories: Path | str):
    """Answer the launcher's read of the user manager's unit search path.

    For a cell that stands in for subprocess.run, which would otherwise answer
    that read too (the launcher asks the manager over busctl before every
    start): the drop-in gate then reads `directories` and nothing else.
    """
    from intergen import tool_process
    return mock.patch.object(tool_process, "unit_search_path",
                             return_value=[str(d) for d in directories])


def written_environment_and_program(argv: list[str]) -> tuple[dict[str, str], list[str]]:
    """Split a launch's command after its "--" into env -i's assignments and the program's argv.

    A command the executor runs starts as `env -i NAME=VALUE ... program args`;
    the assignments are what the command's environment holds, all of it.
    """
    from intergen import tool_process
    own = argv[argv.index("--") + 1:]
    if own[:2] != [tool_process.ENV, "-i"]:
        raise AssertionError(f"the command does not start through env -i: {own!r}")
    rest = own[2:]
    environment: dict[str, str] = {}
    while rest and "=" in rest[0] and not rest[0].startswith("/"):
        name, _, value = rest.pop(0).partition("=")
        environment[name] = value
    return environment, rest
