# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The Chronicle engine — owns all state and implements the verbs.

One privileged object that owns capture, the on-disk stores, retention, restore,
and verification. Both clients (the user-facing CLI/GUI and the automation
sentinel/pkm hook) drive it through the same verbs; neither holds policy.

Versions are ordered by a monotonic sequence number the engine allocates
(spec §14.3), stored in state.json alongside the adopted target and the pin set.
Wall-clock is recorded for display only; a backward wall-clock jump is detected
and flagged, never allowed to mis-order.
"""

import contextlib
import fcntl
import functools
import json
import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import cas as _cas
from . import config as _config
from . import configstate as _configstate
from . import enumerate as _enumerate
from . import escalate as _escalate
from . import manifest as _manifest
from . import paths as _paths
from . import queue as _queue
from . import restorepoint as _restorepoint
from . import retention as _retention
from . import userdata as _userdata

# Every retention event is emitted here at WARNING. The daemon runs under systemd,
# whose journal carries the process's stderr — where an unconfigured logger's
# warnings go — so the journal is the durable copy of the bounded state record.
_LOG = logging.getLogger("chronicle.retention")


class _StoreTransactionLock:
    def __init__(self, root):
        self.path = Path(root) / ".engine.lock"
        self.thread_lock = threading.RLock()
        self.local = threading.local()


_STORE_LOCKS = {}
_STORE_LOCKS_GUARD = threading.Lock()


def _raise_walk_error(error):
    raise error


def _store_paths(root):
    """Yield every allocated directory entry without following symlinks."""
    for dirpath, dirnames, filenames in os.walk(
        root, followlinks=False, onerror=_raise_walk_error
    ):
        yield dirpath
        for name in dirnames:
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                yield path
        for name in filenames:
            yield os.path.join(dirpath, name)


def _allocated_bytes(root, excluded_paths=()):
    """Physical bytes below root, counting each hardlinked inode once."""
    excluded = {os.fspath(path) for path in excluded_paths}
    seen = set()
    total = 0
    for path in _store_paths(root):
        if os.fspath(path) in excluded:
            continue
        metadata = os.lstat(path)
        identity = (metadata.st_dev, metadata.st_ino)
        if identity in seen:
            continue
        seen.add(identity)
        total += metadata.st_blocks * 512
    return total


def _cas_referenced_shas(inventories):
    referenced = set()
    # User-data hashes address files in version trees, not CAS objects.
    for layer in _paths.LOCAL_LAYERS:
        referenced |= _manifest.referenced_shas(inventories[layer])
    return referenced


def _store_transaction_lock(root):
    key = os.path.realpath(root)
    with _STORE_LOCKS_GUARD:
        lock = _STORE_LOCKS.get(key)
        if lock is None:
            lock = _StoreTransactionLock(key)
            _STORE_LOCKS[key] = lock
        return lock


def _state_locked(method):
    @functools.wraps(method)
    def locked(self, *args, **kwargs):
        with self._state_transaction():
            return method(self, *args, **kwargs)
    return locked


class EngineError(Exception):
    pass


class Engine:
    def __init__(self, local_root=None, config=None, config_path=None,
                 now_fn=None):
        self.local_root = Path(local_root) if local_root else _paths.LOCAL_ROOT
        _paths.ensure_store_skeleton(self.local_root)
        self._transaction_lock = _store_transaction_lock(self.local_root)
        self._transaction_depth = 0
        self.config = config if config is not None else _config.load(config_path)
        self.local_store = _cas.ContentStore(self.local_root)
        self.queue = _queue.Queue(self.local_root)
        self._now_fn = now_fn or time.time
        self.state = {}
        with self._state_transaction(refresh=False):
            self.state = self._load_state()

    # -- state ----------------------------------------------------------

    @contextlib.contextmanager
    def _state_transaction(self, refresh=True):
        """Serialize one store operation across threads and processes."""
        shared = self._transaction_lock
        with shared.thread_lock:
            shared_depth = getattr(shared.local, "depth", 0)
            outer_store = shared_depth == 0
            if outer_store:
                fd = os.open(
                    shared.path,
                    os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                )
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                except BaseException:
                    os.close(fd)
                    raise
                shared.local.fd = fd
            shared.local.depth = shared_depth + 1
            outer_instance = self._transaction_depth == 0
            self._transaction_depth += 1
            try:
                if refresh and outer_instance:
                    self.state = self._load_state()
                yield
            finally:
                self._transaction_depth -= 1
                shared.local.depth -= 1
                if outer_store:
                    fd = shared.local.fd
                    del shared.local.fd
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)

    def _load_state(self):
        p = _paths.state_path(self.local_root)
        default = {"sequence": 0, "pins": [], "target": None,
                   "last_capture": {}, "clock_last_wall": 0,
                   "clock_skew_events": [], "retention_events": []}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except (OSError, ValueError) as error:
            raise EngineError(
                f"cannot load Chronicle state {p}: "
                f"{type(error).__name__}: {error}"
            ) from error
        if not isinstance(data, dict):
            raise EngineError(f"invalid Chronicle state {p}: expected an object")
        for k, v in default.items():
            data.setdefault(k, v)
        return data

    def _save_state(self):
        p = _paths.state_path(self.local_root)
        fd, tmp = _mkstemp_in(self.local_root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.state, f, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
        except BaseException:
            _silent_unlink(tmp)
            raise

    def _next_sequence(self):
        self.state["sequence"] = int(self.state.get("sequence", 0)) + 1
        self._save_state()
        return self.state["sequence"]

    def _wall_clock(self):
        now = int(self._now_fn())
        last = int(self.state.get("clock_last_wall", 0))
        if now < last:
            # Backward wall-clock jump — flagged, never allowed to mis-order
            # (ordering is by sequence, spec §14.3).
            self.state.setdefault("clock_skew_events", []).append(
                {"at_sequence": self.state.get("sequence", 0),
                 "from_wall": last, "to_wall": now}
            )
        self.state["clock_last_wall"] = max(now, last)
        return now

    # -- target ---------------------------------------------------------

    def target_root(self):
        """The target store root when a target is adopted AND present, else
        None. Directory-class targets root at <mount>/ChronicleBackups."""
        t = self.state.get("target")
        if not t:
            return None
        mount = t.get("mountpoint")
        if not mount or not os.path.ismount(mount) and not os.path.isdir(mount):
            return None
        return _paths.target_store_root(
            mount, directory_class=(t.get("class") == "directory")
        )

    def target_store(self):
        root = self.target_root()
        return _cas.ContentStore(root) if root else None

    def target_scan(self, home_estimate_bytes=None, floor_bytes=None,
                    _lsblk_json=None):
        if home_estimate_bytes is None:
            home_estimate_bytes = self._estimate_home_size()
        if floor_bytes is None:
            floor_bytes = self.config.size_floor_bytes
        return _enumerate.scan(
            home_estimate_bytes, floor_bytes,
            cap_default_bytes=self.config.target_size_cap_bytes,
            _lsblk_json=_lsblk_json,
        )

    @_state_locked
    def target_adopt(self, mountpoint, target_class="whole-volume",
                     device=None, cap_bytes=None):
        """Initialize a target and record it. Creates the store skeleton at the
        resolved root (directory-class => <mount>/ChronicleBackups)."""
        if target_class not in ("whole-volume", "directory"):
            raise EngineError(f"unknown target class: {target_class}")
        root = _paths.target_store_root(
            mountpoint, directory_class=(target_class == "directory")
        )
        _paths.ensure_store_skeleton(root)
        self.state["target"] = {
            "class": target_class, "mountpoint": str(mountpoint),
            "device": device, "cap_bytes": cap_bytes,
        }
        self._save_state()
        return {"adopted": True, "root": str(root), "class": target_class}

    # -- capture --------------------------------------------------------

    @_state_locked
    def capture(self, layer, scope=None, reason="", sync=True, estimate=None):
        """Take a version of a layer. sync=True blocks and returns the
        version-id; sync=False writes a durable queue intent for off-peak drain
        and returns the intent id (spec §5)."""
        if layer not in _paths.LAYERS:
            raise EngineError(f"unknown layer: {layer}")
        if not sync:
            intent = {"layer": layer, "scope": scope, "reason": reason,
                      "trigger_time": self._wall_clock(),
                      "estimate": int(estimate or 0)}
            result = {"queued": self.queue.enqueue(intent)}
            self._save_state()
            return result
        return {"version_id": self._capture_now(layer, scope, reason)}

    def _capture_now(self, layer, scope, reason):
        seq = self._next_sequence()
        wall = self._wall_clock()
        if layer == _paths.LAYER_CONFIG_STATE:
            paths_set = scope or _configstate.DEFAULT_CONFIG_PATHS
            vid = _configstate.capture(
                paths_set, self.local_root, self.local_store, seq, wall, reason
            )
        elif layer == _paths.LAYER_RESTORE_POINT:
            if not isinstance(scope, dict):
                raise EngineError(
                    "restore-point capture requires a footprint dict as scope"
                )
            vid = _restorepoint.capture_from_footprint(
                scope, self.local_root, self.local_store, seq, wall
            )
        elif layer == _paths.LAYER_USER_DATA:
            target_root = self.target_root()
            if not target_root:
                raise EngineError(
                    "user-data capture needs the backup target attached"
                )
            self._cap_inventory_preflight(target_root)
            prev = self._latest(_paths.LAYER_USER_DATA, root=target_root)
            vid = _userdata.capture(
                self.config.user_data_paths, target_root, prev, seq, wall,
                reason, is_excluded=self.config.is_excluded,
            )
            self._finalize_target_candidate(target_root, layer, vid)
        else:  # pragma: no cover - guarded above
            raise EngineError(f"unhandled layer: {layer}")
        if layer in _paths.LOCAL_LAYERS:
            try:
                self._mirror_to_target(layer, vid)
            except Exception as mirror_error:
                try:
                    self._rollback_local_capture(layer, vid)
                except Exception as rollback_error:
                    raise EngineError(
                        f"mirror failed ({type(mirror_error).__name__}: "
                        f"{mirror_error}) and local capture rollback failed "
                        f"({type(rollback_error).__name__}: {rollback_error})"
                    ) from mirror_error
                raise
        self.state.setdefault("last_capture", {})[layer] = wall
        self._save_state()
        return vid

    def _rollback_local_capture(self, layer, version_id):
        """Remove one local version whose required target mirror failed."""
        manifest = _manifest.find_version(self.local_root, layer, version_id)
        if manifest is None:
            raise EngineError(
                f"local capture {version_id} is missing during mirror rollback"
            )
        candidate_shas = _manifest.referenced_shas([manifest])
        self._drop_version(self.local_root, layer, manifest)
        self._discard_unreferenced_blobs(self.local_root, candidate_shas)

    def _discard_unreferenced_blobs(self, store_root, candidates):
        """Remove named unreferenced blobs and verify each removal."""
        candidates = set(candidates)
        if not candidates:
            return
        inventories = self._complete_manifest_inventory(store_root)
        referenced = _cas_referenced_shas(inventories)
        store = _cas.ContentStore(store_root)
        for sha in sorted(candidates):
            if sha in referenced:
                continue
            path = store.blob_path(sha)
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            shard = path.parent
            if shard.exists() and not any(shard.iterdir()):
                shard.rmdir()
            if path.exists():
                raise EngineError(
                    f"unreferenced blob remains after rollback: {sha}"
                )

    def _finalize_target_candidate(
        self, target_root, layer, version_id, created=()
    ):
        """Enforce the cap and roll back this candidate on any refusal."""
        candidate = _manifest.find_version(target_root, layer, version_id)
        candidate_shas = (
            _manifest.referenced_shas([candidate])
            if candidate is not None and layer in _paths.LOCAL_LAYERS
            else set()
        )
        try:
            self._enforce_directory_target_cap(
                target_root, candidate=(layer, version_id)
            )
        except Exception as cap_error:
            manifest = _manifest.find_version(target_root, layer, version_id)
            if manifest is not None:
                try:
                    self._prune_versions(
                        target_root, layer, [manifest], reason="cap"
                    )
                except Exception as rollback_error:
                    raise EngineError(
                        f"directory target cap failed ({type(cap_error).__name__}: "
                        f"{cap_error}) and candidate rollback failed "
                        f"({type(rollback_error).__name__}: {rollback_error})"
                    ) from cap_error
            try:
                self._discard_unreferenced_blobs(
                    target_root, set(created) | candidate_shas
                )
            except Exception as rollback_error:
                raise EngineError(
                    f"directory target cap failed ({type(cap_error).__name__}: "
                    f"{cap_error}) and target blob rollback failed "
                    f"({type(rollback_error).__name__}: {rollback_error})"
                ) from cap_error
            raise

    def _mirror_to_target(self, layer, version_id):
        """Copy a local-layer version (manifest + its blobs) to the target when
        one is attached, so the target is a complete recovery source (spec §10:
        config-state + restore-points live local AND on target)."""
        target_root = self.target_root()
        if not target_root:
            return
        m = _manifest.find_version(self.local_root, layer, version_id)
        if not m:
            return
        self._cap_inventory_preflight(target_root)
        tstore = _cas.ContentStore(target_root)
        created = []
        try:
            for e in m.get("entries", []):
                if e.get("type") == _manifest.T_FILE and e.get("sha256"):
                    expected = e["sha256"]
                    if tstore.exists(expected):
                        tstore.require_valid(expected)
                    else:
                        data = self.local_store.read_bytes(expected)
                        actual = _cas.sha256_bytes(data)
                        if actual != expected:
                            raise _cas.CorruptBlob(
                                f"source blob changed while mirroring: expected "
                                f"{expected}, found {actual}"
                            )
                        stored = tstore.put_bytes(data)
                        created.append(stored)
                        if stored != expected:
                            raise _cas.CorruptBlob(
                                f"target stored source blob as {stored}: expected "
                                f"{expected}"
                            )
            _manifest.commit_manifest(target_root, m)
        except Exception as mirror_error:
            try:
                self._discard_unreferenced_blobs(target_root, created)
            except Exception as cleanup_error:
                raise EngineError(
                    f"target mirror failed ({type(mirror_error).__name__}: "
                    f"{mirror_error}) and target cleanup failed "
                    f"({type(cleanup_error).__name__}: {cleanup_error})"
                ) from mirror_error
            raise
        self._finalize_target_candidate(
            target_root, layer, version_id, created=created
        )

    def _store_root_for(self, layer):
        if layer in _paths.TARGET_ONLY_LAYERS:
            return self.target_root()
        return self.local_root

    def _latest(self, layer, root=None):
        root = root or self._store_root_for(layer)
        if not root:
            return None
        vs = _manifest.list_versions(root, layer)
        return vs[-1] if vs else None

    # -- read verbs -----------------------------------------------------

    @_state_locked
    def list_versions(self, layer, since=None, until=None):
        root = self._store_root_for(layer)
        if not root:
            return []
        pins = set(self.state.get("pins", []))
        out = []
        for m in _manifest.list_versions(root, layer):
            w = m.get("wall_clock", 0)
            if since is not None and w < since:
                continue
            if until is not None and w > until:
                continue
            out.append({
                "version_id": m["version_id"], "layer": layer,
                "sequence": m["sequence"], "wall_clock": w,
                "reason": m.get("reason", ""),
                "pinned": m["version_id"] in pins,
                "files": sum(1 for e in m.get("entries", [])
                             if e.get("type") == _manifest.T_FILE),
            })
        return out

    @_state_locked
    def get_manifest(self, layer, version_id):
        root = self._store_root_for(layer)
        m = _manifest.find_version(root, layer, version_id) if root else None
        if not m:
            raise EngineError(f"version {version_id} not found in {layer}")
        return m

    @_state_locked
    def diff(self, layer, version_id, path):
        """then-vs-now for a config path: compare the stored sha to the live
        file's sha (feeds config restore, spec §8)."""
        m = self.get_manifest(layer, version_id)
        entry = next((e for e in m["entries"] if e["path"] == path), None)
        if entry is None:
            raise EngineError(f"{path} is not in version {version_id}")
        stored = entry.get("sha256")
        live = _cas.sha256_file(path) if os.path.isfile(path) else None
        return {"path": path, "stored_sha256": stored, "live_sha256": live,
                "changed": stored != live, "live_exists": live is not None}

    # -- verify / scrub -------------------------------------------------

    @_state_locked
    def verify(self, layer, version_id):
        root = self._store_root_for(layer)
        m = _manifest.find_version(root, layer, version_id) if root else None
        if not m:
            raise EngineError(f"version {version_id} not found")
        if layer == _paths.LAYER_USER_DATA:
            # Tree-backed: re-hash the version-tree file, not a CAS blob.
            def _check(e):
                src = _userdata.read_file(root, version_id, e)
                if not src.exists():
                    return f"missing stored file for {e['path']}"
                if _cas.sha256_file(src) != e.get("sha256"):
                    return f"corrupt stored file for {e['path']}"
                return None
            ok, problems = _manifest.verify_version(root, m, None, file_checker=_check)
        else:
            store = self.local_store if root == self.local_root else _cas.ContentStore(root)
            ok, problems = _manifest.verify_version(root, m, store)
        return {"version_id": version_id, "ok": ok, "problems": problems}

    @_state_locked
    def scrub(self):
        """Validate every manifest, reference, blob, and user-data file."""
        report = {"corrupt": [], "clean": True}
        roots = []
        seen_roots = set()
        for candidate in filter(None, [self.local_root, self.target_root()]):
            root = Path(candidate)
            key = os.path.realpath(root)
            if key not in seen_roots:
                roots.append(root)
                seen_roots.add(key)

        for root in roots:
            inventories = {}
            for layer in _paths.LAYERS:
                manifests, problems = _manifest.inspect_versions(root, layer)
                inventories[layer] = manifests
                for problem in problems:
                    versions = (
                        [problem["version_id"]]
                        if problem.get("version_id") else []
                    )
                    report["corrupt"].append({
                        "kind": problem["kind"],
                        "path": problem["path"],
                        "store": str(root),
                        "versions": versions,
                        "problem": problem["problem"],
                    })

            # CAS-backed layers: check that every reference exists, then hash
            # every object and report its full CAS-backed version blast radius.
            store = _cas.ContentStore(root)
            refs = self._blob_to_versions(inventories)
            for sha, versions in sorted(refs.items()):
                if not store.exists(sha):
                    report["corrupt"].append({
                        "kind": "missing-blob",
                        "sha256": sha,
                        "store": str(root),
                        "versions": sorted(versions),
                        "problem": "referenced content-addressed blob is missing",
                    })
            for sha in store.scrub():
                report["corrupt"].append({
                    "kind": "corrupt-blob",
                    "sha256": sha,
                    "store": str(root),
                    "versions": sorted(refs.get(sha, [])),
                    "problem": "blob bytes do not match its content address",
                })

            # Tree-backed user-data: re-hash each version tree's files.
            for m in inventories[_paths.LAYER_USER_DATA]:
                for e in m.get("entries", []):
                    if e.get("type") != _manifest.T_FILE:
                        continue
                    src = _userdata.read_file(root, m["version_id"], e)
                    if not src.exists():
                        report["corrupt"].append({
                            "kind": "missing-user-data",
                            "path": e["path"],
                            "store": str(root),
                            "versions": [m["version_id"]],
                            "problem": "stored user-data file is missing",
                        })
                        continue
                    try:
                        actual = _cas.sha256_file(src)
                    except OSError as exc:
                        report["corrupt"].append({
                            "kind": "unreadable-user-data",
                            "path": e["path"],
                            "store": str(root),
                            "versions": [m["version_id"]],
                            "problem": f"{type(exc).__name__}: {exc}",
                        })
                        continue
                    if actual != e.get("sha256"):
                        report["corrupt"].append({
                            "kind": "corrupt-user-data",
                            "path": e["path"],
                            "store": str(root),
                            "versions": [m["version_id"]],
                            "problem": "stored bytes do not match the manifest",
                        })
        report["clean"] = not report["corrupt"]
        return report

    def _blob_to_versions(self, inventories):
        idx = {}
        for layer in _paths.LOCAL_LAYERS:
            for m in inventories[layer]:
                for e in m.get("entries", []):
                    if e.get("type") == _manifest.T_FILE and e.get("sha256"):
                        idx.setdefault(e["sha256"], set()).add(m["version_id"])
        return idx

    # -- pins -----------------------------------------------------------

    @_state_locked
    def pin(self, version_id):
        pins = self.state.setdefault("pins", [])
        if version_id not in pins:
            pins.append(version_id)
            self._save_state()
        return {"pinned": version_id}

    @_state_locked
    def unpin(self, version_id):
        pins = self.state.setdefault("pins", [])
        if version_id in pins:
            pins.remove(version_id)
            self._save_state()
        return {"unpinned": version_id}

    # -- retention ------------------------------------------------------

    @_state_locked
    def retention_apply(self, layer):
        """Run graduated thinning for a layer, then GC unreferenced blobs. Pins
        are never pruned (spec §7)."""
        root = self._store_root_for(layer)
        if not root:
            return {"pruned": [], "note": "layer store not present"}
        inventories = self._complete_manifest_inventory(root)
        now = self._wall_clock()
        pins = set(self.state.get("pins", []))
        raw = inventories[layer]
        vs = [{"version_id": m["version_id"], "sequence": m["sequence"],
               "wall_clock": m.get("wall_clock", 0),
               "pinned": m["version_id"] in pins} for m in raw]
        if layer == _paths.LAYER_USER_DATA:
            keep = _retention.thin_keep_user_data(vs, now)
        elif layer == _paths.LAYER_CONFIG_STATE:
            keep = _retention.thin_keep_config_state(vs, now)
        else:
            keep = _retention.thin_keep_restore_points(vs)
        prune_ids = _retention.prune_set(vs, keep)
        manifests_by_id = {m["version_id"]: m for m in raw}
        self._prune_versions(
            root, layer, [manifests_by_id[vid] for vid in prune_ids],
            reason="thinning",
        )
        inventories[layer] = [
            m for m in inventories[layer]
            if m.get("version_id") not in prune_ids
        ]
        self._gc(root, inventories)
        cap_pruned = self._enforce_directory_target_cap(root)
        cap_pruned_ids = set(cap_pruned)
        self._save_state()
        return {
            "pruned": prune_ids + [
                version_id for version_id in cap_pruned
                if version_id not in prune_ids
            ],
            "kept": sorted(set(keep) - cap_pruned_ids),
        }

    def _directory_target_cap(self, root):
        target = self.state.get("target") or {}
        cap = target.get("cap_bytes")
        active_root = self.target_root()
        if (
            target.get("class") != "directory"
            or cap is None
            or active_root is None
            or os.path.realpath(root) != os.path.realpath(active_root)
        ):
            return None
        try:
            cap = int(cap)
        except (TypeError, ValueError) as exc:
            raise EngineError(f"invalid directory target cap: {cap!r}") from exc
        if cap < 0:
            raise EngineError(f"invalid directory target cap: {cap}")
        return cap

    def _cap_inventory_preflight(self, root):
        if self._directory_target_cap(root) is not None:
            self._complete_manifest_inventory(root)

    def _projected_target_usage(self, root, inventories, removed):
        removed = set(removed)
        excluded = set()
        remaining = {}
        for layer, manifests in inventories.items():
            remaining[layer] = []
            for manifest in manifests:
                key = (layer, manifest["version_id"])
                if key not in removed:
                    remaining[layer].append(manifest)
                    continue
                excluded.add(manifest["_path"])
                if layer == _paths.LAYER_USER_DATA:
                    tree = _userdata.userdata_tree(root, manifest["version_id"])
                    if tree.is_symlink():
                        excluded.add(tree)
                    elif tree.exists():
                        excluded.update(_store_paths(tree))

        referenced = _cas_referenced_shas(remaining)
        store = _cas.ContentStore(root)
        for sha in store.iter_blobs():
            if sha not in referenced:
                excluded.add(store.blob_path(sha))

        if store.cas.exists():
            excluded_text = {os.fspath(path) for path in excluded}
            for shard in store.cas.iterdir():
                if not shard.is_dir():
                    continue
                if not any(
                    os.fspath(child) not in excluded_text
                    for child in shard.iterdir()
                ):
                    excluded.add(shard)
        return _allocated_bytes(root, excluded)

    def _reject_cap_candidate(self, root, inventories, candidate, message):
        if candidate is not None:
            layer, version_id = candidate
            manifest = next(
                (m for m in inventories[layer]
                 if m["version_id"] == version_id),
                None,
            )
            if manifest is not None:
                self._prune_versions(
                    root, layer, [manifest], reason="cap"
                )
                inventories[layer] = [
                    m for m in inventories[layer]
                    if m["version_id"] != version_id
                ]
        raise EngineError(message)

    def _collect_cap_orphans(self, root, inventories):
        """Collect current CAS orphans before deciding version reclamation."""
        self._gc(root, inventories)
        referenced = _cas_referenced_shas(inventories)
        store = _cas.ContentStore(root)
        leftovers = sorted(
            sha for sha in store.iter_blobs() if sha not in referenced
        )
        if leftovers:
            raise EngineError(
                "directory target cap cleanup could not remove unreferenced "
                "blobs: " + ", ".join(leftovers)
            )

    def _enforce_directory_target_cap(self, root, candidate=None):
        """Prune a directory target to its physical cap, or reject candidate.

        The whole oldest-first plan is proven before its first unlink. A new
        candidate is never counted as reclaimable: if prior unpinned history
        cannot make it fit, only that candidate is rolled back.
        """
        cap = self._directory_target_cap(root)
        if cap is None:
            return []
        if _allocated_bytes(root) <= cap:
            return []
        inventories = self._complete_manifest_inventory(root)
        self._collect_cap_orphans(root, inventories)
        if _allocated_bytes(root) <= cap:
            return []
        user_data = sorted(
            inventories[_paths.LAYER_USER_DATA],
            key=lambda manifest: int(manifest["sequence"]),
        )
        protected_id = (
            candidate[1]
            if candidate is not None
            and candidate[0] == _paths.LAYER_USER_DATA
            else None
        )
        history = [
            manifest for manifest in user_data
            if manifest["version_id"] != protected_id
        ]
        prior = {
            (_paths.LAYER_USER_DATA, manifest["version_id"])
            for manifest in history
        }

        minimum = self._projected_target_usage(root, inventories, prior)
        if minimum > cap:
            self._reject_cap_candidate(
                root,
                inventories,
                candidate,
                "directory target cap exceeded: minimum projected use "
                f"is {minimum} bytes, above the {cap}-byte cap",
            )

        pins = set(self.state.get("pins", []))
        current = _allocated_bytes(root)
        need = current - cap
        projected = current
        projected_removed = set()
        versions = []
        for manifest in history:
            version_id = manifest["version_id"]
            pinned = version_id in pins
            reclaimable = 0
            if not pinned:
                key = (_paths.LAYER_USER_DATA, version_id)
                next_removed = projected_removed | {key}
                next_projected = self._projected_target_usage(
                    root, inventories, next_removed
                )
                reclaimable = max(0, projected - next_projected)
                projected_removed = next_removed
                projected = next_projected
            versions.append({
                "version_id": version_id,
                "sequence": int(manifest["sequence"]),
                "wall_clock": manifest.get("wall_clock", 0),
                "pinned": pinned,
                "size_bytes": reclaimable,
            })
        try:
            order, _freed = _retention.volume_full_prune_plan(versions, need)
        except _retention.PinsHoldingSpace:
            self._reject_cap_candidate(
                root,
                inventories,
                candidate,
                "directory target cap exceeded: pinned versions are holding "
                "space; unpin a version or raise the cap",
            )

        by_id = {manifest["version_id"]: manifest for manifest in history}
        plan = [by_id[version_id] for version_id in order]
        self._prune_versions(
            root, _paths.LAYER_USER_DATA, plan, reason="cap"
        )
        inventories[_paths.LAYER_USER_DATA] = [
            manifest for manifest in inventories[_paths.LAYER_USER_DATA]
            if manifest["version_id"] not in set(order)
        ]
        self._gc(root, inventories)
        actual = _allocated_bytes(root)
        if actual > cap:
            raise EngineError(
                "directory target cap enforcement stopped: physical use is "
                f"{actual} bytes after the planned prune, above the "
                f"{cap}-byte cap"
            )
        return order

    # The engine keeps the newest retention records in persistent state, and
    # eviction never drops an announcement whose plan has no terminal record —
    # an interrupted plan stays visible until it is resolved. Every event also
    # goes to the module logger (see _LOG). Review finding R2, 2026-09-11: the
    # earlier unconditional newest-200 eviction could erase an unresolved plan,
    # and the comment here claimed a journal timer task that did not exist.
    _RETENTION_EVENTS_KEPT = 200
    _RETENTION_TERMINAL = ("prune-completed", "prune-stopped", "prune-refused")

    def _record_retention_event(self, kind, layer, reason, version_ids, **extra):
        event = {"kind": kind, "layer": layer, "reason": reason,
                 "version_ids": list(version_ids),
                 "at_sequence": self.state.get("sequence", 0),
                 "wall_clock": self._wall_clock()}
        event.update(extra)
        events = self.state.setdefault("retention_events", [])
        events.append(event)
        events[:] = self._evict_retention_events(events)
        self._save_state()
        _LOG.warning(
            "retention %s layer=%s reason=%s plan=%s root=%s versions=%s%s",
            kind, layer, reason, event.get("plan", "-"), event.get("root", "-"),
            ",".join(event["version_ids"]) or "-",
            f" error={extra['error']}" if "error" in extra else "",
        )

    def _evict_retention_events(self, events):
        """Keep the newest records, plus every older announcement whose plan
        has no terminal record (completed / stopped / refused). An announcement
        without a plan id predates plan ids and is kept as unresolved."""
        keep = self._RETENTION_EVENTS_KEPT
        if len(events) <= keep:
            return list(events)
        old, recent = events[:-keep], events[-keep:]
        resolved = {e.get("plan") for e in events
                    if e.get("kind") in self._RETENTION_TERMINAL and e.get("plan")}
        unresolved = [e for e in old
                      if e.get("kind") == "prune-announced"
                      and (not e.get("plan") or e["plan"] not in resolved)]
        return unresolved + recent

    def _prune_versions(self, root, layer, manifests, reason):
        """Remove a planned set of versions all-before-any, announced, and
        fail-loud (spec §7: pruning is loud and announced before it runs).

        Every candidate is checked with the non-mutating validator first; one
        refused candidate refuses the whole plan and nothing is removed. The
        plan is then recorded in persistent state (prune-announced) BEFORE the
        first removal, and its outcome after (prune-completed, or prune-stopped
        naming the error, what was removed and what remains). Every caller
        that prunes — graduated thinning, the directory-class cap — goes
        through here; nothing drops a version any other way.
        """
        version_ids = [m["version_id"] for m in manifests]
        if not version_ids:
            return []
        # One plan id ties the announcement to its outcome, and the store root
        # says which target the plan acted on (events outlive a target swap).
        ident = {"plan": uuid.uuid4().hex, "root": str(root)}
        problems = []
        for m in manifests:
            try:
                self._check_drop(root, layer, m)
            except EngineError as exc:
                problems.append(str(exc))
        if problems:
            self._record_retention_event(
                "prune-refused", layer, reason, version_ids, problems=problems, **ident
            )
            raise EngineError(
                "retention refused, nothing removed: " + "; ".join(problems)
            )
        self._record_retention_event("prune-announced", layer, reason, version_ids, **ident)
        removed = []
        try:
            for m in manifests:
                self._drop_version(root, layer, m)
                removed.append(m["version_id"])
        except EngineError as exc:
            self._record_retention_event(
                "prune-stopped", layer, reason, version_ids,
                removed=removed,
                remaining=[v for v in version_ids if v not in removed],
                error=str(exc), **ident,
            )
            raise
        self._record_retention_event("prune-completed", layer, reason, version_ids, **ident)
        return version_ids

    def _complete_manifest_inventory(self, root):
        try:
            return {
                layer: _manifest.list_versions_complete(root, layer)
                for layer in _paths.LAYERS
            }
        except _manifest.ManifestInventoryError as exc:
            raise EngineError(f"retention stopped: {exc}") from exc

    def _check_drop(self, root, layer, manifest):
        """Every check _drop_version performs, touching nothing."""
        version_id = manifest["version_id"]
        if not manifest.get("_path"):
            raise EngineError(
                f"retention stopped: manifest path missing for {version_id}"
            )
        if layer == _paths.LAYER_USER_DATA:
            # ValueError = a layout the removal refuses; OSError = the store
            # could not be inspected (lstat/open/fstat). Both are the engine's
            # error so the plan records prune-refused (preflight) or
            # prune-stopped (a later candidate) instead of escaping without a
            # saved outcome (review finding R1, 2026-09-11).
            try:
                _userdata.check_version_removal(root, version_id)
            except (ValueError, OSError) as exc:
                raise EngineError(f"retention stopped: {exc}") from exc

    def _drop_version(self, root, layer, manifest):
        """Remove one version: its tree FIRST, its manifest LAST.

        A tree removal that fails stops retention loudly with the manifest
        still in place, so the version stays listed and the next pass plans
        it again — never an orphaned tree behind a deleted manifest.
        """
        version_id = manifest["version_id"]
        self._check_drop(root, layer, manifest)
        path = manifest["_path"]
        if layer == _paths.LAYER_USER_DATA:
            try:
                _userdata.remove_version_tree(root, version_id)
            except (OSError, ValueError) as exc:
                raise EngineError(
                    f"retention stopped: could not remove the user-data tree "
                    f"of {version_id}: {exc}"
                ) from exc
        try:
            os.unlink(path)
        except OSError as exc:
            raise EngineError(
                f"retention stopped: could not remove manifest "
                f"{Path(path).name}: {exc}"
            ) from exc

    def _gc(self, root, inventories=None):
        if inventories is None:
            inventories = self._complete_manifest_inventory(root)
        return _cas.ContentStore(root).gc(_cas_referenced_shas(inventories))

    # -- restore --------------------------------------------------------

    @staticmethod
    def _check_restore_paths(paths):
        if any(path == "" for path in paths):
            raise EngineError("Restore paths must not be empty.")

    @staticmethod
    def _missing_restore_path_reason(path, entries):
        # A captured file need not have a directory entry for every parent.
        # Use the saved path boundary even when that directory no longer exists.
        prefix = path.rstrip("/") + "/"
        if path and (any(saved.startswith(prefix) for saved in entries)
                     or (os.path.isdir(path.rstrip("/") or "/")
                         and not os.path.islink(path.rstrip("/") or "/"))):
            return ("directory contents are not restored recursively; "
                    "name the individual stored paths")
        return "not in this version"

    @_state_locked
    def restore_plan(self, layer, version_id, paths, mode="replace-confirm"):
        """Describe what a restore will change WITHOUT writing (spec §8: never
        a silent overwrite — the plan is shown and confirmed first)."""
        self._check_restore_paths(paths)
        m = self.get_manifest(layer, version_id)
        by_path = {e["path"]: e for e in m["entries"]}
        actions = []
        for p in paths:
            e = by_path.get(p)
            if e is None:
                actions.append({"path": p, "action": "skip",
                                "reason": self._missing_restore_path_reason(p, by_path)})
                continue
            live_exists = os.path.lexists(p)
            actions.append({
                "path": p, "type": e.get("type"),
                "action": "restore",
                "mode": mode,
                "live_exists": live_exists,
                "will_overwrite": live_exists and mode == "replace-confirm",
            })
        return {"version_id": version_id, "mode": mode, "actions": actions}

    def restore_apply(self, layer, version_id, paths, mode="replace-confirm"):
        """Apply a restore. Every file is re-hashed before it lands (spec §3.2);
        a mismatch aborts that file loudly. `replace-confirm` assumes the caller
        already confirmed the plan; `beside` writes next to the original.
        Config-layer restores land as `.pkmnew` so they are a reviewed change,
        never a silent revert (spec §8).

        Restore recreates recorded ownership/mode (`_apply_meta`), which needs
        CAP_CHOWN. When this process lacks it (the always-on low-capability
        `chronicled.service` handling a socket verb), the work is escalated to
        the higher-capability `chronicle-restore@` unit rather than silently
        dropping ownership (spec §6, §16.2). A caller that already holds the
        capability — a root CLI, or the restore unit itself — runs it directly.
        """
        self._check_restore_paths(paths)
        if not _escalate.has_cap_chown():
            return _escalate.run_restore_via_unit(layer, version_id, paths, mode)
        with self._state_transaction():
            return self._restore_apply_direct(layer, version_id, paths, mode)

    def _restore_apply_direct(self, layer, version_id, paths, mode):
        m = self.get_manifest(layer, version_id)
        by_path = {e["path"]: e for e in m["entries"]}
        root = self._store_root_for(layer)
        store = _cas.ContentStore(root)
        results = []
        for p in paths:
            e = by_path.get(p)
            if e is None:
                results.append({"path": p, "ok": False,
                                "reason": self._missing_restore_path_reason(p, by_path)})
                continue
            try:
                dest = self._restore_one(layer, version_id, e, root, store, mode)
                results.append({"path": p, "ok": True, "written_to": str(dest)})
            except Exception as exc:  # loud per-file abort, keep going
                results.append({"path": p, "ok": False, "reason": str(exc)})
        return {"version_id": version_id, "results": results}

    def _restore_one(self, layer, version_id, entry, root, store, mode):
        src_type = entry.get("type")
        target_path = Path(entry["path"])
        if mode == "beside":
            dest = Path(str(target_path) + f".chronicle-restored-{version_id}")
        elif layer == _paths.LAYER_CONFIG_STATE:
            # Reviewed change, not a silent revert (spec §8).
            dest = Path(str(target_path) + ".pkmnew")
        else:
            dest = target_path

        dest.parent.mkdir(parents=True, exist_ok=True)
        if src_type == _manifest.T_DIR:
            dest.mkdir(parents=True, exist_ok=True)
            _apply_meta(dest, entry)
            return dest
        if src_type == _manifest.T_SYMLINK:
            if dest.lexists() if hasattr(dest, "lexists") else os.path.lexists(dest):
                os.unlink(dest)
            os.symlink(entry["target"], dest)
            return dest
        # Regular file: fetch bytes, re-hash before writing (spec §3.2).
        sha = entry["sha256"]
        if layer == _paths.LAYER_USER_DATA:
            src = _userdata.read_file(root, version_id, entry)
            if not src.exists():
                raise EngineError(f"stored file missing for {entry['path']}")
            actual = _cas.sha256_file(src)
            data = src.read_bytes()
        else:
            if not store.exists(sha):
                raise EngineError(f"stored blob missing for {entry['path']}")
            data = store.read_bytes(sha)
            actual = _cas.sha256_bytes(data)
        if actual != sha:
            raise EngineError(
                f"integrity check failed for {entry['path']}: version "
                f"{version_id} blob hashes to {actual}, manifest says {sha}"
            )
        fd, tmp = _mkstemp_in(dest.parent)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)
        _apply_meta(dest, entry)
        return dest

    # -- queue / status -------------------------------------------------

    @_state_locked
    def queue_status(self):
        window = f"{self.config.work_start}–{self.config.work_end}"
        return {"count": self.queue.count(),
                "summary": self.queue.status_summary(window),
                "intents": self.queue.list()}

    @_state_locked
    def status(self):
        target_root = self.target_root()
        free = None
        if target_root and os.path.isdir(target_root):
            try:
                st = shutil.disk_usage(str(target_root))
                free = st.free
            except OSError:
                free = None
        return {
            "target": self.state.get("target"),
            "target_present": target_root is not None,
            "target_free_bytes": free,
            "last_capture": self.state.get("last_capture", {}),
            "queue": self.queue_status(),
            "clock_skew_events": self.state.get("clock_skew_events", []),
            "retention_events": self.state.get("retention_events", []),
            "pins": self.state.get("pins", []),
        }

    # -- helpers --------------------------------------------------------

    def _estimate_home_size(self):
        total = 0
        for base in self.config.user_data_paths:
            for dirpath, _dirs, files in os.walk(base):
                for fn in files:
                    try:
                        total += os.lstat(os.path.join(dirpath, fn)).st_size
                    except OSError:
                        pass
        return total


def _apply_meta(path, entry):
    try:
        os.chmod(path, entry.get("mode", 0o644), follow_symlinks=False)
    except (OSError, NotImplementedError):
        try:
            os.chmod(path, entry.get("mode", 0o644))
        except OSError:
            pass
    try:
        os.chown(path, entry.get("uid", -1), entry.get("gid", -1),
                 follow_symlinks=False)
    except (OSError, AttributeError, PermissionError):
        pass


def _mkstemp_in(directory):
    import tempfile
    Path(directory).mkdir(parents=True, exist_ok=True)
    return tempfile.mkstemp(prefix=".tmp-", dir=str(directory))


def _silent_unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass
