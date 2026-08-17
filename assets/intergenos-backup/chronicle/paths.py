# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Chronicle on-disk layout.

Two roots:

  * The LOCAL store on the system disk (`/var/lib/chronicle`). It is always on
    — it holds the config-state layer and the restore points, so the machine
    has *some* protection before any external target is attached. It is compact
    (a content-addressed store dedupes /etc + package state tightly).

  * The TARGET store on the backup volume (or a size-capped directory on a
    POSIX volume — the addendum's directory-class target). It holds the bulk
    user-data hardlink trees plus a mirror of the CAS layers, so a lost system
    disk is fully recoverable.

Every store, wherever rooted, has the same internal shape:

    <root>/
        cas/                content-addressed blobs, sha256-sharded (ab/cdef…)
        versions/<layer>/   committed version manifests (one JSON per version)
        queue/              durable capture-intent spool (local store only)
        state.json          engine state (monotonic sequence, adopted target)

The engine never hard-codes these strings; it derives every path through this
module so the layout has exactly one source of truth.
"""

import os
from pathlib import Path

# The always-on local store on the system disk.
LOCAL_ROOT = Path("/var/lib/chronicle")

# Runtime IPC socket directory + path.
RUNTIME_DIR = Path("/run/chronicle")
SOCKET_PATH = RUNTIME_DIR / "engine.sock"

# Configuration.
CONFIG_DIR = Path("/etc/chronicle")
CONFIG_PATH = CONFIG_DIR / "chronicle.conf"

# The three layers.
LAYER_CONFIG_STATE = "config-state"
LAYER_USER_DATA = "user-data"
LAYER_RESTORE_POINT = "restore-point"
LAYERS = (LAYER_CONFIG_STATE, LAYER_USER_DATA, LAYER_RESTORE_POINT)

# Which layers live in the always-on local store vs. require the external
# target. Config-state and restore-points are compact and local-first; user
# data is bulk and target-only (spec §10).
LOCAL_LAYERS = (LAYER_CONFIG_STATE, LAYER_RESTORE_POINT)
TARGET_ONLY_LAYERS = (LAYER_USER_DATA,)

# The subtree Chronicle owns when a target is a size-capped DIRECTORY on an
# existing POSIX volume (addendum target class A). Chronicle never touches
# anything else on that volume.
TARGET_DIR_NAME = "ChronicleBackups"


def cas_dir(root):
    return Path(root) / "cas"


def versions_dir(root, layer):
    return Path(root) / "versions" / layer


def queue_dir(root):
    return Path(root) / "queue"


def state_path(root):
    return Path(root) / "state.json"


def blob_path(root, sha256):
    """Sharded blob path: cas/<first two hex>/<rest>. Sharding keeps any one
    directory from holding the whole store's blob count."""
    sha256 = sha256.lower()
    return cas_dir(root) / sha256[:2] / sha256[2:]


def ensure_store_skeleton(root):
    """Create the directory skeleton of a store root (idempotent). Returns the
    resolved root Path."""
    root = Path(root)
    (root / "cas").mkdir(parents=True, exist_ok=True)
    for layer in LAYERS:
        (root / "versions" / layer).mkdir(parents=True, exist_ok=True)
    return root


def target_store_root(volume_mountpoint, directory_class=False):
    """Resolve the store root on a target volume.

    Args:
        volume_mountpoint: where the target POSIX volume is mounted.
        directory_class: when True the target is the addendum's size-capped
            directory class — Chronicle owns only <mount>/ChronicleBackups/,
            never the whole volume.

    Returns:
        Path to the store root on the target.
    """
    mount = Path(volume_mountpoint)
    if directory_class:
        return mount / TARGET_DIR_NAME
    return mount
