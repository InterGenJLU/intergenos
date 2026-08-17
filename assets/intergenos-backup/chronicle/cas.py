# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Content-addressed blob store.

Every blob is named by the sha256 of its contents, so a blob read back that
does not hash to its name is corrupt by definition (spec §3). This is the store
behind the config-state layer and the restore points: /etc, the account
databases, and package state are small, change often, and share almost
everything between versions, so sha-keyed dedup is far tighter than hardlink
trees — and identical bytes are stored exactly once.

Guarantees:
  * Atomic writes — a blob is written to a temp file in the same shard
    directory, fsynced, and renamed into place, so a crash never leaves a
    partially-written blob at its final name.
  * Read-back verification at write — immediately after storing, the blob is
    re-read and re-hashed (spec §3.1: catches a target that is already
    failing). A mismatch removes the bad blob and raises.
  * Self-verification — verify() recomputes any blob's hash on demand; scrub
    walks the whole store (the engine drives the scheduled scrub).
  * Garbage collection — gc() deletes exactly the blobs no surviving manifest
    references (spec §7: a blob is deleted only when unreferenced).
"""

import hashlib
import os
from pathlib import Path

from . import paths as _paths

_CHUNK = 1024 * 1024


class CorruptBlob(Exception):
    """A blob's bytes do not hash to its name."""


def sha256_file(path):
    """Return the hex sha256 of a file's contents, streamed."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


class ContentStore:
    """A sha256 content-addressed blob store rooted at a store root's cas/."""

    def __init__(self, store_root):
        self.root = Path(store_root)
        self.cas = _paths.cas_dir(self.root)

    # -- write ----------------------------------------------------------

    def _finalize(self, tmp_path, sha):
        dest = _paths.blob_path(self.root, sha)
        if dest.exists():
            # Already stored (dedup) — drop the redundant temp copy.
            os.unlink(tmp_path)
            return sha
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp_path, dest)
        # Read-back verification (spec §3.1): re-hash what actually landed.
        actual = sha256_file(dest)
        if actual != sha:
            os.unlink(dest)
            raise CorruptBlob(
                f"blob failed read-back verification: expected {sha}, "
                f"target returned {actual} (the destination volume may be "
                f"failing)"
            )
        return sha

    def put_bytes(self, data):
        """Store bytes; return the sha256. Idempotent (dedup)."""
        sha = sha256_bytes(data)
        self.cas.mkdir(parents=True, exist_ok=True)
        shard = _paths.blob_path(self.root, sha).parent
        shard.mkdir(parents=True, exist_ok=True)
        fd, tmp = _mkstemp_in(shard)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            return self._finalize(tmp, sha)
        except BaseException:
            _silent_unlink(tmp)
            raise

    def put_file(self, src_path):
        """Store a file's contents; return the sha256. The file is streamed, so
        arbitrarily large files never load whole into memory."""
        sha = sha256_file(src_path)
        if self.exists(sha):
            return sha  # dedup: identical content already stored
        shard = _paths.blob_path(self.root, sha).parent
        shard.mkdir(parents=True, exist_ok=True)
        fd, tmp = _mkstemp_in(shard)
        try:
            with os.fdopen(fd, "wb") as out, open(src_path, "rb") as src:
                for chunk in iter(lambda: src.read(_CHUNK), b""):
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            return self._finalize(tmp, sha)
        except BaseException:
            _silent_unlink(tmp)
            raise

    # -- read -----------------------------------------------------------

    def exists(self, sha):
        return _paths.blob_path(self.root, sha).exists()

    def blob_path(self, sha):
        return _paths.blob_path(self.root, sha)

    def read_bytes(self, sha):
        p = _paths.blob_path(self.root, sha)
        if not p.exists():
            raise FileNotFoundError(f"blob {sha} not in store {self.root}")
        return p.read_bytes()

    # -- integrity ------------------------------------------------------

    def verify(self, sha):
        """Recompute a stored blob's hash; True iff it matches its name."""
        p = _paths.blob_path(self.root, sha)
        if not p.exists():
            return False
        return sha256_file(p) == sha.lower()

    def iter_blobs(self):
        """Yield the sha256 of every stored blob (derived from its path)."""
        if not self.cas.exists():
            return
        for shard in sorted(self.cas.iterdir()):
            if not shard.is_dir() or len(shard.name) != 2:
                continue
            for blob in sorted(shard.iterdir()):
                if blob.is_file():
                    yield shard.name + blob.name

    def scrub(self):
        """Walk the whole store, re-hashing every blob. Returns the sorted list
        of corrupt blob shas (empty when clean). The engine maps each corrupt
        blob back to every version that references it (spec §3)."""
        corrupt = []
        for sha in self.iter_blobs():
            if not self.verify(sha):
                corrupt.append(sha)
        return corrupt

    def gc(self, referenced):
        """Delete every blob NOT in `referenced` (a set/iterable of shas).
        Returns the sorted list of deleted shas. A blob is removed only when no
        surviving manifest references it (spec §7)."""
        keep = {s.lower() for s in referenced}
        deleted = []
        for sha in list(self.iter_blobs()):
            if sha.lower() not in keep:
                try:
                    _paths.blob_path(self.root, sha).unlink()
                    deleted.append(sha)
                except OSError:
                    pass
        # Prune now-empty shard directories.
        if self.cas.exists():
            for shard in self.cas.iterdir():
                if shard.is_dir() and not any(shard.iterdir()):
                    try:
                        shard.rmdir()
                    except OSError:
                        pass
        return sorted(deleted)


def _mkstemp_in(directory):
    import tempfile
    return tempfile.mkstemp(prefix=".tmp-", dir=str(directory))


def _silent_unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass
