# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""User-data layer — hardlink rotation (spec §2.1).

Each capture writes a full-looking directory tree of the protected home data on
the target. A file unchanged since the previous version is a HARDLINK to the
prior version's inode (rsync --link-dest semantics, implemented here for control
over the manifest + integrity hooks), so the on-disk cost is O(changed bytes)
while every version browses as a complete tree. This is the mechanism that works
when neither source nor target is copy-on-write, and it is already the
distribution's idiom.

The version manifest still records each file's sha256 (spec §3) so a version
self-verifies and a restore re-hashes before writing. An unchanged (hardlinked)
file reuses the previous version's recorded sha rather than re-hashing, so a
capture only hashes what actually changed.

Commit-last (spec §14): the tree is built under a staging name, then renamed to
its version-id name, and only then is the manifest committed. A capture
interrupted before the manifest commit leaves an orphan tree with no committed
manifest — invisible to list(), reclaimed by GC — and never disturbs the prior
version.
"""

import os
import shutil
import stat
from pathlib import Path

from . import cas as _cas
from . import manifest as _manifest
from . import paths as _paths


def userdata_tree(target_root, version_id):
    return Path(target_root) / "userdata" / version_id


def _tree_path(staging, abs_path):
    # Store the file at <staging>/<abs path without leading slash>, so the tree
    # mirrors the real layout and browses naturally.
    return Path(staging) / str(abs_path).lstrip("/")


def _index_by_path(prev_manifest):
    if not prev_manifest:
        return {}
    return {e["path"]: e for e in prev_manifest.get("entries", [])}


def capture(source_roots, target_root, prev_manifest, sequence, wall_clock,
            reason, is_excluded=None):
    """Capture a user-data version by hardlink rotation against prev_manifest.

    Args:
        source_roots: iterable of absolute source directories (config's
            user_data_paths, e.g. ["/home"]).
        target_root: the target store root (whole-volume mountpoint, or the
            directory-class subtree).
        prev_manifest: the previous user-data manifest (or None for the first
            capture) — the hardlink source.
        sequence: the engine's monotonic sequence number for this version.
        wall_clock: epoch seconds to display (ordering is by sequence).
        reason: human reason recorded in the manifest.
        is_excluded: optional callable(path)->bool to skip cache/trash trees.

    Returns:
        the committed version_id.
    """
    target_root = Path(target_root)
    prev_index = _index_by_path(prev_manifest)
    prev_id = prev_manifest["version_id"] if prev_manifest else None
    prev_tree = userdata_tree(target_root, prev_id) if prev_id else None

    staging = target_root / "userdata" / f".staging-{int(sequence)}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    entries = []
    for root in source_roots:
        root = str(root)
        if not os.path.exists(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            # Prune excluded directories so we never descend into them.
            if is_excluded:
                dirnames[:] = [
                    d for d in dirnames
                    if not is_excluded(os.path.join(dirpath, d) + "/")
                ]
            _capture_dir(dirpath, staging, entries)
            for fn in filenames:
                ap = os.path.join(dirpath, fn)
                if is_excluded and is_excluded(ap):
                    continue
                _capture_file_or_link(
                    ap, staging, prev_index, prev_tree, entries
                )

    manifest = _manifest.build_manifest(
        _paths.LAYER_USER_DATA, sequence, wall_clock, reason, entries
    )
    final_tree = userdata_tree(target_root, manifest["version_id"])
    if final_tree.exists():
        shutil.rmtree(final_tree, ignore_errors=True)
    os.replace(staging, final_tree)
    _manifest.commit_manifest(target_root, manifest)
    return manifest["version_id"]


def _stat_meta(st):
    return {
        "mode": stat.S_IMODE(st.st_mode),
        "uid": st.st_uid,
        "gid": st.st_gid,
        "mtime": int(st.st_mtime),
    }


def _capture_dir(dirpath, staging, entries):
    try:
        st = os.lstat(dirpath)
    except OSError:
        return
    tp = _tree_path(staging, dirpath)
    tp.mkdir(parents=True, exist_ok=True)
    e = {"path": dirpath, "type": _manifest.T_DIR}
    e.update(_stat_meta(st))
    entries.append(e)


def _capture_file_or_link(ap, staging, prev_index, prev_tree, entries):
    try:
        st = os.lstat(ap)
    except OSError:
        return
    tp = _tree_path(staging, ap)
    tp.parent.mkdir(parents=True, exist_ok=True)

    if stat.S_ISLNK(st.st_mode):
        target = os.readlink(ap)
        try:
            os.symlink(target, tp)
        except FileExistsError:
            pass
        e = {"path": ap, "type": _manifest.T_SYMLINK, "target": target}
        e.update(_stat_meta(st))
        entries.append(e)
        return

    if not stat.S_ISREG(st.st_mode):
        # Sockets/fifos/devices in a home tree are not user data to restore;
        # record their presence without bytes so the manifest is complete.
        e = {"path": ap, "type": _manifest.T_FILE, "size": 0,
             "sha256": _cas.sha256_bytes(b"")}
        e.update(_stat_meta(st))
        entries.append(e)
        return

    prev = prev_index.get(ap)
    unchanged = (
        prev is not None
        and prev.get("type") == _manifest.T_FILE
        and prev.get("size") == st.st_size
        and prev.get("mtime") == int(st.st_mtime)
        and prev_tree is not None
        and _tree_path(prev_tree, ap).exists()
    )
    if unchanged:
        # Hardlink to the prior version's inode: O(0) bytes, and reuse its sha.
        os.link(_tree_path(prev_tree, ap), tp)
        sha = prev["sha256"]
    else:
        shutil.copy2(ap, tp, follow_symlinks=False)
        sha = _cas.sha256_file(ap)
    e = {"path": ap, "type": _manifest.T_FILE, "size": st.st_size, "sha256": sha}
    e.update(_stat_meta(st))
    entries.append(e)


def read_file(target_root, version_id, entry):
    """Return the stored path of a captured user-data file for restore/verify.
    The bytes live in the version tree (not the CAS)."""
    return _tree_path(userdata_tree(target_root, version_id), entry["path"])


def remove_version_tree(target_root, version_id):
    """Delete a pruned version's tree. Hardlinked inodes shared with surviving
    versions stay alive by refcount; only this version's links are dropped."""
    tree = userdata_tree(target_root, version_id)
    if tree.exists():
        shutil.rmtree(tree, ignore_errors=True)
