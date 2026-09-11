# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Version manifests — the signed-hash-tree record of one captured version.

A version manifest lists every file it captured with its metadata and sha256.
The manifest itself is hashed to a **root hash**, and a version is *committed
only when its root hash is written last* (spec §3, §14): the complete manifest
— root hash included — is written to a temp file, fsynced, and atomically
renamed into place under a name that embeds the root hash. The rename is the
single commit point, so a version half-written when a volume vanishes or the
machine shuts down has no committed manifest at all: list() never sees it, and
its orphaned blobs are reclaimed by GC. The previous version is never touched.

Ordering across the timeline is by a **monotonic engine sequence number**, not
wall-clock (spec §14.3): the sequence sorts the timeline; the wall-clock is
only displayed. A backward wall-clock jump is detected and flagged, never
allowed to mis-order or overwrite a version.
"""

import json
import os
import stat
import tempfile
from pathlib import Path

from . import cas as _cas
from . import paths as _paths


# Entry types.
T_FILE = "file"
T_DIR = "dir"
T_SYMLINK = "symlink"


class ManifestInventoryError(Exception):
    """One or more committed manifests could not be read."""


def capture_entry(abs_path, rel_path, store):
    """Capture one path into a manifest entry, storing file bytes in the CAS.

    Args:
        abs_path: the source path on disk (not followed if a symlink).
        rel_path: the path as recorded in the manifest (store-relative or
            absolute, caller's choice — used verbatim for restore).
        store: a ContentStore; regular-file bytes are put into it.

    Returns:
        an entry dict, or None if the path does not exist (a caller capturing a
        footprint of not-yet-existing paths simply records their absence).
    """
    try:
        st = os.lstat(abs_path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    mode = st.st_mode
    entry = {
        "path": str(rel_path),
        "mode": stat.S_IMODE(mode),
        "uid": st.st_uid,
        "gid": st.st_gid,
        "mtime": int(st.st_mtime),
    }
    if stat.S_ISDIR(mode):
        entry["type"] = T_DIR
    elif stat.S_ISLNK(mode):
        entry["type"] = T_SYMLINK
        entry["target"] = os.readlink(abs_path)
    else:
        # Regular file (and, conservatively, anything else with bytes).
        entry["type"] = T_FILE
        entry["size"] = st.st_size
        entry["sha256"] = store.put_file(abs_path)
    return entry


def canonical_bytes(entries):
    """Deterministic serialization of the entry list for hashing: entries
    sorted by path, keys sorted, compact separators, UTF-8."""
    ordered = sorted(entries, key=lambda e: e["path"])
    return json.dumps(
        ordered, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def compute_root_hash(entries):
    """The version's root hash: sha256 over the canonical entry serialization.
    Recomputing it from the entries proves the manifest intact."""
    return _cas.sha256_bytes(canonical_bytes(entries))


def build_manifest(layer, sequence, wall_clock, reason, entries):
    """Assemble a manifest dict with its computed root hash."""
    root = compute_root_hash(entries)
    return {
        "chronicle_manifest_version": 1,
        "layer": layer,
        "sequence": int(sequence),
        "wall_clock": wall_clock,
        "reason": reason,
        "entries": entries,
        "root_hash": root,
        "version_id": _version_id(sequence, root),
    }


def _version_id(sequence, root_hash):
    return f"{int(sequence):010d}-{root_hash[:12]}"


def version_id(manifest):
    return manifest["version_id"]


def commit_manifest(store_root, manifest):
    """Write a manifest commit-last (temp → fsync → atomic rename). Returns the
    version_id. After this returns the version is durable and visible to
    list_versions; before it, nothing is."""
    layer = manifest["layer"]
    vdir = _paths.versions_dir(store_root, layer)
    vdir.mkdir(parents=True, exist_ok=True)
    final = vdir / f"{manifest['version_id']}.json"
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(vdir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(manifest, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, final)  # the commit point
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return manifest["version_id"]


def load_manifest(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _manifest_problem(path, layer, manifest):
    """Return (kind, detail) for a structural/root problem, or None."""
    invalid = lambda detail: ("manifest-invalid", detail)
    if not isinstance(manifest, dict):
        return invalid("top level is not an object")
    version = manifest.get("version_id")
    if not isinstance(version, str) or not version:
        return invalid("version_id is missing or is not a string")
    if manifest.get("layer") != layer:
        return invalid(
            f"declares layer {manifest.get('layer')!r}, expected {layer!r}"
        )
    sequence = manifest.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int):
        return invalid("sequence is missing or is not an integer")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        return invalid("entries is missing or is not a list")
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            return invalid(f"entry {index} is not an object")
        if not isinstance(entry.get("path"), str):
            return invalid(f"entry {index} has no string path")
        if entry.get("type") not in (T_FILE, T_DIR, T_SYMLINK):
            return invalid(f"entry {index} has an unknown type")
        if entry.get("type") == T_FILE and not isinstance(
            entry.get("sha256"), str
        ):
            return invalid(f"file entry {index} has no string sha256")
    try:
        recomputed = compute_root_hash(entries)
    except (TypeError, ValueError) as exc:
        return invalid(
            f"entries cannot be hashed: {type(exc).__name__}: {exc}"
        )
    if recomputed != manifest.get("root_hash"):
        return (
            "manifest-root-hash",
            f"manifest claims {manifest.get('root_hash')}, "
            f"entries recompute to {recomputed}",
        )
    return None


def _load_versions(store_root, layer):
    """Return (readable manifests, per-path integrity diagnostics)."""
    vdir = _paths.versions_dir(store_root, layer)
    if not vdir.exists():
        return [], []
    out = []
    problems = []
    for p in vdir.iterdir():
        if p.is_file() and p.suffix == ".json" and not p.name.startswith(".tmp-"):
            try:
                m = load_manifest(p)
            except (OSError, ValueError) as exc:
                problems.append({
                    "kind": "manifest-unreadable",
                    "path": str(p),
                    "version_id": None,
                    "problem": f"{type(exc).__name__}: {exc}",
                })
                continue
            problem = _manifest_problem(p, layer, m)
            if problem:
                kind, detail = problem
                problems.append({
                    "kind": kind,
                    "path": str(p),
                    "version_id": m.get("version_id")
                    if isinstance(m, dict) else None,
                    "problem": detail,
                })
                # Root-invalid but structurally safe manifests remain
                # browseable and directly verifiable. A structurally invalid
                # record is reported but must not reach downstream consumers.
                if kind == "manifest-invalid":
                    continue
            m["_path"] = str(p)
            out.append(m)
    out.sort(key=lambda m: m["sequence"])
    return out, problems


def inspect_versions(store_root, layer):
    """Return readable manifests and every committed-manifest diagnostic."""
    return _load_versions(store_root, layer)


def list_versions(store_root, layer):
    """Return readable committed manifests, oldest first (by sequence).

    This tolerant view is for browsing: one damaged manifest must not hide
    every healthy version from the user interface. Destructive callers use
    list_versions_complete() instead.
    """
    out, _problems = _load_versions(store_root, layer)
    return out


def list_versions_complete(store_root, layer):
    """Return all committed manifests or fail before destructive work.

    Garbage collection cannot prove that a blob is unreferenced while any
    committed manifest is unreadable, so reclamation must use this view.
    """
    out, problems = _load_versions(store_root, layer)
    if problems:
        details = "; ".join(
            f"{Path(problem['path']).name}: {problem['problem']}"
            for problem in problems
        )
        raise ManifestInventoryError(
            f"manifest inventory is incomplete for {layer}: {details}"
        )
    return out


def find_version(store_root, layer, version_id):
    for m in list_versions(store_root, layer):
        if m["version_id"] == version_id:
            return m
    return None


def verify_version(store_root, manifest, store, file_checker=None):
    """Verify a version end to end.

    Recomputes the root hash from the manifest's own entries (structural
    integrity) and re-checks every file the manifest references (byte
    integrity). The byte check is storage-model aware:

      * CAS-backed layers (config-state, restore-point) verify the blob in the
        content store — the default when no file_checker is given.
      * The tree-backed user-data layer stores bytes in the version tree, not
        the CAS, so the engine passes a file_checker that re-hashes the tree
        file instead.

    file_checker(entry) returns a problem string, or None when the file is
    intact.

    Returns (ok, problems).
    """
    problems = []
    recomputed = compute_root_hash(manifest["entries"])
    if recomputed != manifest.get("root_hash"):
        problems.append(
            f"root hash mismatch: manifest claims {manifest.get('root_hash')}, "
            f"entries recompute to {recomputed}"
        )
    for e in manifest["entries"]:
        if e.get("type") != T_FILE:
            continue
        if file_checker is not None:
            prob = file_checker(e)
            if prob:
                problems.append(prob)
            continue
        sha = e.get("sha256")
        if not store.exists(sha):
            problems.append(f"missing blob for {e['path']} ({sha})")
        elif not store.verify(sha):
            problems.append(f"corrupt blob for {e['path']} ({sha})")
    return (not problems, problems)


def referenced_shas(manifests):
    """The set of every file sha referenced across a collection of manifests —
    the GC keep-set."""
    refs = set()
    for m in manifests:
        for e in m.get("entries", []):
            if e.get("type") == T_FILE and e.get("sha256"):
                refs.add(e["sha256"])
    return refs
