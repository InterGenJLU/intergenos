# SPDX-License-Identifier: GPL-3.0-or-later
"""Where pkm's own state lives, derived from one install root.

pkm has always been able to act on a filesystem that is not the running
system's: PackageDB takes ``root=``, PackageInstaller takes ``root=``,
PackageRemover defaults its root to the database's, and the canonical hooks in
``pkm/hooks.py`` each adapt (``depmod -b``, ``ldconfig -r``) or skip when the
root is not ``/``. The Forge installer uses all of it to install a whole system
into ``/mnt/target``. What was missing was a single place that answers the
question the command line has to ask: given an install root, where does every
piece of pkm's own state go?

Answering it in one module is not tidiness. The paths were nine separate
constants in five modules, and a ``--root`` option that moved eight of them
would report a successful install into a directory while writing the ninth onto
the live machine. Rule 21 — a stub is a lie — applies exactly: either all of it
derives from the root, or the option must not exist.

THE ONE ASYMMETRY, AND WHY IT IS RIGHT.

Two inputs deliberately do NOT derive from the root: the repository
configuration (``/etc/pkm/repos.conf``) and the signature keyring
(``/etc/pkm/trusted.gpg``). They are not state pkm writes into a target; they
are what pkm BELIEVES. Deriving them would mean two things, both wrong. A target
being bootstrapped has no keyring at all, so a derived keyring leaves a fresh
root with nothing to verify its packages against — verification would either
fail closed on every install into an empty directory, or, far worse, be skipped.
And a target that DOES carry a keyring would then be the thing that decides
which signatures pkm accepts while installing into it, which inverts the trust
direction: the artifact under construction would choose its own verifier. Where
packages are PUT is what the install root controls. Which signatures pkm
believes is a property of the machine doing the installing, and stays there.

That distinction is stated in the option's own help text, because a reader who
takes ``--root`` to mean "behave entirely as though this directory were the
system" would assume the opposite.
"""

from __future__ import annotations

from pathlib import Path

# The install root a run resolves against when the operator names none. Every
# accessor below defaults to it, so a pkm invocation without the option
# resolves exactly the paths the shipped constants have always named.
DEFAULT_ROOT = Path("/")


# Every path here is the ROOT'S OWN STATE: something pkm writes into, or reads
# back out of, the filesystem it is installing to. Stored relative (no leading
# slash) so the join below cannot be defeated by pathlib's rule that an
# absolute right-hand operand discards the left — the same trap the PackageDB
# docstring warns about for manifest paths.
_STATE_RELPATHS = {
    # The package database itself.
    "db_path": "var/lib/igos/pkm.db",
    # The text manifests, one per installed package. Written by the installer,
    # read by `pkm import` and by the remover.
    "manifest_dir": "var/lib/igos/packages",
    # The sealed archives an install resolves from when no --archive is given.
    "archive_dir": "var/lib/igos/archives",
    # Footprint manifests for the proprietary-download helper packages.
    "helper_manifest_dir": "var/lib/igos/helpers",
    # The mutation lock. Rooting it is what lets a transaction against a target
    # run beside one against the live system without either waiting on the
    # other — they are genuinely independent, and a shared lock would have said
    # they were not.
    "lock_path": "var/lock/pkm.lock",
    # The synced repository index and downloaded packages.
    "repo_cache_dir": "var/cache/pkm",
    # What `pkm check-updates` writes for the desktop notifier.
    "available_updates_path": "var/lib/pkm/available-updates.json",
    # Per-package post-install hooks, shipped inside the packages themselves.
    "package_hooks_dir": "var/lib/pkm/hooks",
    # Pre-transaction restore-point handlers. Rooted so a target that has
    # registered none is a no-op rather than running the live system's.
    "pretxn_handler_dir": "usr/lib/pkm/pre-transaction.d",
    # The anti-rollback record: the newest index generation this root has
    # accepted. Rooted because it is a SECURITY record about one filesystem's
    # history — a sync into a target must not advance the running system's
    # record, and the target must end up with its own. Found by the reality
    # proof: a rooted `pkm update` wrote /var/lib/pkm/state on the live machine
    # while the target got none.
    "repo_state_dir": "var/lib/pkm/state",
}

# Inputs read from the RUNNING system whatever root is named. See the module
# docstring for why these two are the exception.
_RUNNING_SYSTEM_PATHS = {
    "repo_config_path": Path("/etc/pkm/repos.conf"),
    "keyring_path": Path("/etc/pkm/trusted.gpg"),
}


def _resolve(root):
    """The install root as a Path, with None meaning the running system."""
    if root is None:
        return DEFAULT_ROOT
    return Path(root)


def state_relpaths():
    """The name -> root-relative path map, for callers that need to enumerate.

    Returned as a copy so a caller cannot edit the table that every path in the
    program is derived from.
    """
    return dict(_STATE_RELPATHS)


def under(root, relpath):
    """Join a root-relative path onto an install root.

    The single join every accessor goes through, so there is one place where a
    leading slash could ever silently discard the root — and it cannot, because
    the table holds no leading slashes and this asserts it.
    """
    rel = str(relpath)
    if rel.startswith("/"):
        raise ValueError(
            f"install-root paths are stored relative; {rel!r} begins with a "
            f"slash, which would discard the root entirely"
        )
    return _resolve(root) / rel


def db_path(root=None):
    """The package database file under `root`."""
    return under(root, _STATE_RELPATHS["db_path"])


def manifest_dir(root=None):
    """The text-manifest directory under `root`."""
    return under(root, _STATE_RELPATHS["manifest_dir"])


def archive_dir(root=None):
    """The sealed-archive directory under `root`."""
    return under(root, _STATE_RELPATHS["archive_dir"])


def helper_manifest_dir(root=None):
    """The helper-footprint manifest directory under `root`."""
    return under(root, _STATE_RELPATHS["helper_manifest_dir"])


def lock_path(root=None):
    """The mutation lock file under `root`."""
    return under(root, _STATE_RELPATHS["lock_path"])


def repo_cache_dir(root=None):
    """The repository cache directory under `root`."""
    return under(root, _STATE_RELPATHS["repo_cache_dir"])


def available_updates_path(root=None):
    """The available-updates report file under `root`."""
    return under(root, _STATE_RELPATHS["available_updates_path"])


def package_hooks_dir(root=None):
    """The per-package post-install hook directory under `root`."""
    return under(root, _STATE_RELPATHS["package_hooks_dir"])


def pretxn_handler_dir(root=None):
    """The pre-transaction handler drop-in directory under `root`."""
    return under(root, _STATE_RELPATHS["pretxn_handler_dir"])


def repo_state_dir(root=None):
    """The anti-rollback freshness record directory under `root`."""
    return under(root, _STATE_RELPATHS["repo_state_dir"])


def repo_config_path(root=None):
    """The repository configuration — the RUNNING system's, always.

    `root` is accepted and ignored so that every path in the program is asked
    for the same way and the exception is visible at each call site rather than
    being a call that looks different for no stated reason.
    """
    return _RUNNING_SYSTEM_PATHS["repo_config_path"]


def keyring_path(root=None):
    """The signature keyring — the RUNNING system's, always. See repo_config_path."""
    return _RUNNING_SYSTEM_PATHS["keyring_path"]
