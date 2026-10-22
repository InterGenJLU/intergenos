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

from . import held_open as _held

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


#: The mode of every directory the engine makes in a store. Nothing that reads
#: a store runs as another account: the engine and every task run as root, and
#: the window and the command line reach the engine through its socket, so no
#: other account needs to list a store's names (a blob's name is its content's
#: sha256, which answers whether a guessed file is in the store).
STORE_DIR_MODE = 0o700


def store_owners():
    """The uids a store's directories may belong to: root, and the uid the
    engine runs as (root in every shipped unit)."""
    return {0, os.geteuid()}


def require_trusted_root(root):
    """Refuse a store another account could change.

    A store holds the manifests a restore writes from, as root, wherever they
    say, so a store root another account could change, or whose parent it
    could change, would let that account choose what root writes and where.
    The root and every directory above it are walked from / by descriptor
    without following a link; each must be owned by root or by the engine's
    own uid, the root must be writable by no one else, and a directory above
    it may be writable by others only when its sticky bit keeps them from
    replacing the directory walked through (chronicle.held_open
    .require_owned_tree). Raises chronicle.held_open.HeldOpenRefused naming
    the directory that fails, FileNotFoundError when a directory is missing."""
    _held.require_owned_tree(os.path.abspath(os.fspath(root)), store_owners())


def ensure_store_skeleton(root):
    """Create the directory skeleton of a store root (idempotent) and refuse
    a store another account could change (require_trusted_root), the root and
    each skeleton directory alike. Returns the resolved root Path."""
    root = Path(root)
    root.mkdir(mode=STORE_DIR_MODE, parents=True, exist_ok=True)
    require_trusted_root(root)
    skeleton = [root / "cas", root / "versions"]
    skeleton += [root / "versions" / layer for layer in LAYERS]
    for directory in skeleton:
        directory.mkdir(mode=STORE_DIR_MODE, exist_ok=True)
        require_trusted_root(directory)
    # A store made before this mode was set keeps its older directories; a
    # root and skeleton at STORE_DIR_MODE close every name beneath them.
    for directory in [root] + skeleton:
        if os.stat(directory).st_mode & 0o7777 != STORE_DIR_MODE:
            os.chmod(directory, STORE_DIR_MODE)
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
