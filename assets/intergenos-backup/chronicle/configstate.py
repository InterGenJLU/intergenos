# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Config-state layer — content-addressed snapshot of /etc + package state
(spec §2.2).

/etc, the account databases, and package-manager state are small, change often,
and share almost everything between versions, so a sha256 CAS dedupes them far
more tightly than hardlink trees and yields then-vs-now diffs for free (compare
two manifests' shas). Because it is compact, this layer is the always-on local
history on the system disk — the machine has some protection before any external
target is attached.

Package-manager state to version (exact paths, spec §2.2):
  * /var/lib/igos/pkm.db          — installed rows, per-file manifests +
                                     checksums, the history log, and the config
                                     baseline (config_files.original_checksum).
  * /var/lib/igos/packages/       — text manifests.
/var/lib/igos/archives/ and /var/cache/pkm/ are re-fetchable and EXCLUDED.

Config-state capture REUSES the pkm baseline rather than inventing a second
/etc-hash store: pkm's config_files table already records stock-vs-edited, and a
config restore (spec §8) re-enters through pkm's .pkmnew config-protection
semantics — this layer only needs to snapshot the current bytes.
"""

import os
from pathlib import Path

from . import held_open as _held
from . import manifest as _manifest
from . import paths as _paths
from . import userdata as _userdata

# The default config-state set (spec §2.2). /etc carries the account DBs.
DEFAULT_CONFIG_PATHS = (
    "/etc",
    "/var/lib/igos/pkm.db",
    "/var/lib/igos/packages",
)

# Never versioned here — re-fetchable, and large (spec §2.2).
DEFAULT_EXCLUDES = (
    "/var/lib/igos/archives",
    "/var/cache/pkm",
    "/etc/chronicle",   # the tool's own config is not user config-state
)


def capture(config_paths, store_root, store, sequence, wall_clock, reason,
            excludes=DEFAULT_EXCLUDES, store_roots=()):
    """Capture a config-state version into the CAS at store_root.

    Args:
        config_paths: the config set to snapshot (files and/or directories).
        store_root: the store root to commit the manifest under (the always-on
            local store; the engine may also mirror to the target).
        store: a ContentStore rooted at store_root (file bytes land here).
        sequence, wall_clock, reason: version identity.
        excludes: absolute path prefixes to skip.
        store_roots: additional stores to exclude by held directory identity.

    Returns:
        the committed version_id.
    """
    ex = tuple(excludes or ())
    entries = []
    unreadable = []
    with _userdata.store_identities((store_root, *store_roots)) as stores:
        for base in config_paths:
            if _excluded(base, ex):
                continue
            # The hold says what the base is; a scope entry that is a FIFO, a
            # device, or reached through a link is recorded as not captured.
            try:
                e = _manifest.capture_entry(base, base, store)
            except _held.HeldOpenRefused as refused:
                unreadable.append(_manifest.refusal_record(base, refused))
                continue
            if e is None:
                continue
            if e["type"] == _manifest.T_DIR:
                _walk_into(base, store, entries, ex, unreadable, stores)
            else:
                entries.append(e)

    m = _manifest.build_manifest(
        _paths.LAYER_CONFIG_STATE, sequence, wall_clock, reason, entries,
        unreadable
    )
    _manifest.commit_manifest(store_root, m)
    return m["version_id"]


def _excluded(path, excludes):
    p = str(path)
    return any(p == e or p.startswith(e.rstrip("/") + "/") for e in excludes)


def _walk_into(base, store, entries, excludes, unreadable=None, stores=()):
    """Walk one config base. `unreadable` collects the paths the walk could not
    read, for the same reason userdata.capture collects them: without an
    onerror, os.walk drops an unreadable directory silently and the committed
    config-state version is short with nothing said."""
    def record_unreadable(error):
        if unreadable is None:
            return
        unreadable.append({
            "path": error.filename if error.filename is not None else "",
            "error": f"{type(error).__name__}: {error.strerror or error}",
        })

    def record(path, error):
        if unreadable is not None:
            unreadable.append(_manifest.refusal_record(path, error))

    for dirpath, dirnames, filenames in os.walk(
        base, topdown=True, onerror=record_unreadable
    ):
        # Prune excluded subtrees.
        dirnames[:] = [
            d for d in dirnames
            if not _excluded(os.path.join(dirpath, d), excludes)
        ]
        # The names os.walk listed are only names: the directory is walked to
        # from / by descriptor and each name is held there before any act.
        try:
            dir_fd = _held.held_directory(dirpath)
        except OSError as error:
            record(dirpath, error)
            dirnames[:] = []
            continue
        try:
            if _userdata.is_store_directory(dir_fd, stores):
                dirnames[:] = []
                continue
            # Capture the directory node itself, from its hold.
            e = _manifest.capture_entry_at(
                dir_fd, ".", dirpath, dirpath, store)
            if e is not None:
                entries.append(e)
            # os.walk lists directory symlinks in dirnames but does not visit
            # them; the hold tells them apart from the directories it visits.
            for fn in filenames + dirnames:
                ap = os.path.join(dirpath, fn)
                if _excluded(ap, excludes):
                    continue
                try:
                    fe = _manifest.capture_entry_at(dir_fd, fn, ap, ap, store)
                except _held.HeldOpenRefused as refused:
                    record(ap, refused)
                    continue
                if fe is None or (fe["type"] == _manifest.T_DIR
                                  and fn in dirnames):
                    continue
                entries.append(fe)
        finally:
            os.close(dir_fd)
