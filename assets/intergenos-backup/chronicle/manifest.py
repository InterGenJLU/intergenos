# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Version manifests — the signed-hash-tree record of one captured version.

A version manifest lists every file it captured with its metadata and sha256.
The manifest itself is hashed to a **root hash**, and a version is *committed
only when its root hash is written last* (spec §3, §14): the complete manifest
— root hash included — is written to a temp file, fsynced, and atomically
linked into place without replacement under a name that embeds the root hash.
That no-clobber link is the single commit point, so a version half-written when
a volume vanishes or the machine shuts down has no committed manifest at all:
list() never sees it, and its orphaned blobs are reclaimed by GC. The previous
version is never touched.

Ordering across the timeline is by a **monotonic engine sequence number**, not
wall-clock (spec §14.3): the sequence sorts the timeline; the wall-clock is
only displayed. A backward wall-clock jump is detected and flagged, never
allowed to mis-order or overwrite a version.
"""

import json
import re
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


class ManifestCollision(Exception):
    """A committed version already occupies the requested identity."""


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
    sorted by path, keys sorted, compact separators, UTF-8 with surrogateescape.

    Filesystem names may contain arbitrary bytes. Surrogateescape returns
    those bytes to the hash input without changing the encoding or hashes of
    previously supported UTF-8 names. The stored JSON uses escapes instead;
    hashing always operates on the decoded entries, independent of spelling.
    """
    ordered = sorted(entries, key=lambda e: e["path"])
    return json.dumps(
        ordered, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8", "surrogateescape")


def canonical_unreadable_bytes(unreadable):
    """Deterministic serialization of the unreadable-path list, same rules as
    canonical_bytes: sorted, keys sorted, compact, surrogateescape so a name
    that is not valid UTF-8 is recorded rather than lost."""
    ordered = sorted(unreadable, key=lambda u: u["path"])
    return json.dumps(
        ordered, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8", "surrogateescape")


def compute_root_hash(entries, unreadable=()):
    """The version's root hash: sha256 over the canonical entry serialization,
    and over the unreadable-path list when there is one.

    A capture that could not read part of its source is not the same version as
    one that read all of it, so the two cannot share a hash. The omission is
    therefore INSIDE the integrity hash: anything that edits a committed
    manifest to hide what was dropped makes it stop verifying.

    With nothing unreadable the input is the entry serialization alone, byte for
    byte as this engine has always computed it, so every manifest already on
    disk keeps its hash and its version id.
    """
    payload = canonical_bytes(entries)
    if unreadable:
        payload += b"\n" + canonical_unreadable_bytes(unreadable)
    return _cas.sha256_bytes(payload)


def build_manifest(layer, sequence, wall_clock, reason, entries, unreadable=()):
    """Assemble a manifest dict with its computed root hash.

    `unreadable` is the list of paths the capture could not read, each a dict of
    `path` and `error`. It is always present in the manifest, empty when the
    capture read everything, so a reader never has to distinguish "nothing was
    dropped" from "this engine did not record it".
    """
    unreadable = sorted(
        ({"path": u["path"], "error": u["error"]} for u in unreadable),
        key=lambda u: u["path"],
    )
    root = compute_root_hash(entries, unreadable)
    return {
        "chronicle_manifest_version": 1,
        "layer": layer,
        "sequence": int(sequence),
        "wall_clock": wall_clock,
        "reason": reason,
        "entries": entries,
        "unreadable": unreadable,
        "root_hash": root,
        "version_id": _version_id(sequence, root),
    }


VERSION_ID_RE = re.compile(r"^[0-9]{10}-[0-9a-f]{12}$")


def _version_id(sequence, root_hash):
    return f"{int(sequence):010d}-{root_hash[:12]}"


def is_canonical_version_id(value):
    """True only for the exact shape _version_id() produces. A version id is
    joined to the store root as a path segment (userdata trees, manifest file
    names), so any other string — a dot pair, a slash, an empty string — is
    refused before it can name a path outside the version's own directory."""
    return isinstance(value, str) and VERSION_ID_RE.fullmatch(value) is not None


def version_id(manifest):
    return manifest["version_id"]


def commit_manifest(store_root, manifest):
    """Write a manifest commit-last (temp → fsync → no-clobber link).

    Returns the version_id. After this returns the version is durable and
    visible to list_versions; before it, nothing is.
    """
    layer = manifest["layer"]
    vdir = _paths.versions_dir(store_root, layer)
    vdir.mkdir(parents=True, exist_ok=True)
    final = vdir / f"{manifest['version_id']}.json"
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(vdir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            # Keep the stored document valid ASCII JSON, including names that
            # contain surrogate-escaped filesystem bytes (e.g. \\udcff).
            json.dump(manifest, f, sort_keys=True, ensure_ascii=True)
            f.flush()
            os.fsync(f.fileno())
        try:
            # link() is the no-replace commit point: unlike replace(), it
            # atomically refuses an existing version instead of overwriting it.
            os.link(tmp, final)
        except FileExistsError as exc:
            raise ManifestCollision(
                f"version {manifest['version_id']} is already committed"
            ) from exc
        try:
            os.unlink(tmp)
        except OSError:
            # A leftover .tmp file is ignored by every inventory. The final
            # hardlink is already complete and committed.
            pass
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return manifest["version_id"]


def load_manifest(path):
    # Both JSON escapes and raw filesystem bytes decode to the same entries.
    # Previously written manifests and their root hashes remain unchanged.
    with open(path, "r", encoding="utf-8", errors="surrogateescape") as f:
        return json.load(f)


def _manifest_problem(path, layer, manifest):
    """Return (kind, detail) for a structural/root problem, or None."""
    invalid = lambda detail: ("manifest-invalid", detail)
    if not isinstance(manifest, dict):
        return invalid("top level is not an object")
    version = manifest.get("version_id")
    if not isinstance(version, str) or not version:
        return invalid("version_id is missing or is not a string")
    if not is_canonical_version_id(version):
        # A version id is joined to the store root as a path segment (user-data
        # trees, manifest file names). Anything but the canonical shape — a dot
        # pair, a slash, an empty string — is refused here, before any consumer
        # (pruning, restore, scrub) can turn it into a path outside the store.
        return invalid("version_id is not in the canonical shape")
    if version != Path(path).stem:
        return invalid(
            f"version_id {version!r} does not name its own file {Path(path).name!r}"
        )
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
    unreadable = manifest.get("unreadable", ())
    if not isinstance(unreadable, (list, tuple)):
        return invalid("unreadable is not a list")
    try:
        recomputed = compute_root_hash(entries, unreadable)
    except (TypeError, ValueError, KeyError) as exc:
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
    # The recompute must use the SAME inputs build_manifest used, or a version
    # that recorded an unreadable path fails its own integrity check. A
    # manifest written before this field existed has none, and compute_root_hash
    # then hashes the entries alone exactly as it always did.
    recomputed = compute_root_hash(
        manifest["entries"], manifest.get("unreadable", ()))
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
