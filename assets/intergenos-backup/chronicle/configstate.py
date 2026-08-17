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

from . import manifest as _manifest
from . import paths as _paths

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
            excludes=DEFAULT_EXCLUDES):
    """Capture a config-state version into the CAS at store_root.

    Args:
        config_paths: the config set to snapshot (files and/or directories).
        store_root: the store root to commit the manifest under (the always-on
            local store; the engine may also mirror to the target).
        store: a ContentStore rooted at store_root (file bytes land here).
        sequence, wall_clock, reason: version identity.
        excludes: absolute path prefixes to skip.

    Returns:
        the committed version_id.
    """
    ex = tuple(excludes or ())
    entries = []
    for base in config_paths:
        if not os.path.lexists(base):
            continue
        if _excluded(base, ex):
            continue
        if os.path.isdir(base) and not os.path.islink(base):
            _walk_into(base, store, entries, ex)
        else:
            e = _manifest.capture_entry(base, base, store)
            if e is not None:
                entries.append(e)

    m = _manifest.build_manifest(
        _paths.LAYER_CONFIG_STATE, sequence, wall_clock, reason, entries
    )
    _manifest.commit_manifest(store_root, m)
    return m["version_id"]


def _excluded(path, excludes):
    p = str(path)
    return any(p == e or p.startswith(e.rstrip("/") + "/") for e in excludes)


def _walk_into(base, store, entries, excludes):
    for dirpath, dirnames, filenames in os.walk(base, topdown=True):
        # Prune excluded subtrees.
        dirnames[:] = [
            d for d in dirnames
            if not _excluded(os.path.join(dirpath, d), excludes)
        ]
        for name in [dirpath] if dirpath == base else []:
            pass  # base dir entry captured below via the per-dir capture
        # Capture the directory node itself.
        e = _manifest.capture_entry(dirpath, dirpath, store)
        if e is not None:
            entries.append(e)
        for fn in filenames:
            ap = os.path.join(dirpath, fn)
            if _excluded(ap, excludes):
                continue
            fe = _manifest.capture_entry(ap, ap, store)
            if fe is not None:
                entries.append(fe)
