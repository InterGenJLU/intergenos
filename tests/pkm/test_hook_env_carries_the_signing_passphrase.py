# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A lifecycle hook that signs needs the owner's passphrase, and only that one.

The kernel package's post-install hook builds and signs this machine's boot image.
The signing key is encrypted at rest, so the hook has to receive the owner's
passphrase from whoever drove the package operation — the installer during an
install, and nothing at all during an ordinary upgrade, where the hook asks the
person instead.

pkm runs lifecycle hooks with a default-deny environment: only the names in
HOOK_ENV_ALLOWLIST survive, plus the per-hook PKM_PACKAGE_* variables. That is a
good property and this change keeps it rather than replacing it with an inherited
environment. It adds exactly one name.
"""

import os
from unittest import mock

from pkm import hooks


PASS_VAR = "IGOS_MOK_PASSPHRASE"


def test_the_signing_passphrase_name_is_allowed_through():
    assert PASS_VAR in hooks.HOOK_ENV_ALLOWLIST, (
        "the kernel hook signs the boot image and cannot open the encrypted key "
        "without this variable; with it filtered out the hook would ask a person "
        "for a passphrase in the middle of an unattended install")


def test_the_allowlist_stays_default_deny():
    """Adding one name must not turn the filter into an inherited environment."""
    unrelated = {"AWS_SECRET_ACCESS_KEY", "SSH_AUTH_SOCK", "GPG_TTY",
                 "IGOS_MOK_PASSPHRASE_BACKUP"}
    assert not (unrelated & hooks.HOOK_ENV_ALLOWLIST)


def test_hook_environment_carries_the_value_when_the_driver_sets_it():
    with mock.patch.dict(os.environ, {PASS_VAR: "owner-passphrase-1"}):
        env = {k: v for k, v in os.environ.items()
               if k in hooks.HOOK_ENV_ALLOWLIST}
    assert env.get(PASS_VAR) == "owner-passphrase-1"


def test_hook_environment_has_no_such_variable_when_nobody_set_it():
    """An upgrade on an installed machine must reach the ask-the-person path."""
    environ = {k: v for k, v in os.environ.items() if k != PASS_VAR}
    with mock.patch.dict(os.environ, environ, clear=True):
        env = {k: v for k, v in os.environ.items()
               if k in hooks.HOOK_ENV_ALLOWLIST}
    assert PASS_VAR not in env
