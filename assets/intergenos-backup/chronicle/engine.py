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

import json
import os
import shutil
import time
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


class EngineError(Exception):
    pass


class Engine:
    def __init__(self, local_root=None, config=None, config_path=None,
                 now_fn=None):
        self.local_root = Path(local_root) if local_root else _paths.LOCAL_ROOT
        _paths.ensure_store_skeleton(self.local_root)
        self.config = config if config is not None else _config.load(config_path)
        self.local_store = _cas.ContentStore(self.local_root)
        self.queue = _queue.Queue(self.local_root)
        self._now_fn = now_fn or time.time
        self.state = self._load_state()

    # -- state ----------------------------------------------------------

    def _load_state(self):
        p = _paths.state_path(self.local_root)
        default = {"sequence": 0, "pins": [], "target": None,
                   "last_capture": {}, "clock_last_wall": 0,
                   "clock_skew_events": []}
        if not p.exists():
            return default
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            for k, v in default.items():
                data.setdefault(k, v)
            return data
        except (OSError, ValueError):
            return default

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
            return {"queued": self.queue.enqueue(intent)}
        return {"version_id": self._capture_now(layer, scope, reason)}

    def _capture_now(self, layer, scope, reason):
        seq = self._next_sequence()
        wall = self._wall_clock()
        if layer == _paths.LAYER_CONFIG_STATE:
            paths_set = scope or _configstate.DEFAULT_CONFIG_PATHS
            vid = _configstate.capture(
                paths_set, self.local_root, self.local_store, seq, wall, reason
            )
            self._mirror_to_target(_paths.LAYER_CONFIG_STATE, vid)
        elif layer == _paths.LAYER_RESTORE_POINT:
            if not isinstance(scope, dict):
                raise EngineError(
                    "restore-point capture requires a footprint dict as scope"
                )
            vid = _restorepoint.capture_from_footprint(
                scope, self.local_root, self.local_store, seq, wall
            )
            self._mirror_to_target(_paths.LAYER_RESTORE_POINT, vid)
        elif layer == _paths.LAYER_USER_DATA:
            target_root = self.target_root()
            if not target_root:
                raise EngineError(
                    "user-data capture needs the backup target attached"
                )
            prev = self._latest(_paths.LAYER_USER_DATA, root=target_root)
            vid = _userdata.capture(
                self.config.user_data_paths, target_root, prev, seq, wall,
                reason, is_excluded=self.config.is_excluded,
            )
        else:  # pragma: no cover - guarded above
            raise EngineError(f"unhandled layer: {layer}")
        self.state.setdefault("last_capture", {})[layer] = wall
        self._save_state()
        return vid

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
        tstore = _cas.ContentStore(target_root)
        for e in m.get("entries", []):
            if e.get("type") == _manifest.T_FILE and e.get("sha256"):
                if not tstore.exists(e["sha256"]):
                    tstore.put_bytes(self.local_store.read_bytes(e["sha256"]))
        _manifest.commit_manifest(target_root, m)

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

    def get_manifest(self, layer, version_id):
        root = self._store_root_for(layer)
        m = _manifest.find_version(root, layer, version_id) if root else None
        if not m:
            raise EngineError(f"version {version_id} not found in {layer}")
        return m

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

    def scrub(self):
        """Walk every store, re-hashing blobs; report each corrupt blob with
        EVERY version that references it (spec §3)."""
        report = {"corrupt": [], "clean": True}
        for root in filter(None, [self.local_root, self.target_root()]):
            # CAS-backed layers: re-hash every blob, map each corrupt one back
            # to EVERY version that references it (spec §3 — true blast radius).
            store = _cas.ContentStore(root)
            corrupt = store.scrub()
            if corrupt:
                report["clean"] = False
                refs = self._blob_to_versions(root)
                for sha in corrupt:
                    report["corrupt"].append(
                        {"sha256": sha, "store": str(root),
                         "versions": sorted(refs.get(sha, []))}
                    )
            # Tree-backed user-data: re-hash each version tree's files.
            for m in _manifest.list_versions(root, _paths.LAYER_USER_DATA):
                for e in m.get("entries", []):
                    if e.get("type") != _manifest.T_FILE:
                        continue
                    src = _userdata.read_file(root, m["version_id"], e)
                    if not src.exists() or _cas.sha256_file(src) != e.get("sha256"):
                        report["clean"] = False
                        report["corrupt"].append(
                            {"path": e["path"], "store": str(root),
                             "versions": [m["version_id"]]}
                        )
        return report

    def _blob_to_versions(self, root):
        idx = {}
        for layer in _paths.LAYERS:
            for m in _manifest.list_versions(root, layer):
                for e in m.get("entries", []):
                    if e.get("type") == _manifest.T_FILE and e.get("sha256"):
                        idx.setdefault(e["sha256"], set()).add(m["version_id"])
        return idx

    # -- pins -----------------------------------------------------------

    def pin(self, version_id):
        pins = self.state.setdefault("pins", [])
        if version_id not in pins:
            pins.append(version_id)
            self._save_state()
        return {"pinned": version_id}

    def unpin(self, version_id):
        pins = self.state.setdefault("pins", [])
        if version_id in pins:
            pins.remove(version_id)
            self._save_state()
        return {"unpinned": version_id}

    # -- retention ------------------------------------------------------

    def retention_apply(self, layer):
        """Run graduated thinning for a layer, then GC unreferenced blobs. Pins
        are never pruned (spec §7)."""
        root = self._store_root_for(layer)
        if not root:
            return {"pruned": [], "note": "layer store not present"}
        now = self._wall_clock()
        pins = set(self.state.get("pins", []))
        raw = _manifest.list_versions(root, layer)
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
        for vid in prune_ids:
            self._drop_version(root, layer, vid)
        self._gc(root)
        return {"pruned": prune_ids, "kept": sorted(keep)}

    def _drop_version(self, root, layer, version_id):
        m = _manifest.find_version(root, layer, version_id)
        if m and m.get("_path"):
            try:
                os.unlink(m["_path"])
            except OSError:
                pass
        if layer == _paths.LAYER_USER_DATA:
            _userdata.remove_version_tree(root, version_id)

    def _gc(self, root):
        referenced = set()
        for layer in _paths.LAYERS:
            referenced |= _manifest.referenced_shas(
                _manifest.list_versions(root, layer)
            )
        return _cas.ContentStore(root).gc(referenced)

    # -- restore --------------------------------------------------------

    def restore_plan(self, layer, version_id, paths, mode="replace-confirm"):
        """Describe what a restore will change WITHOUT writing (spec §8: never
        a silent overwrite — the plan is shown and confirmed first)."""
        m = self.get_manifest(layer, version_id)
        by_path = {e["path"]: e for e in m["entries"]}
        actions = []
        for p in paths:
            e = by_path.get(p)
            if e is None:
                actions.append({"path": p, "action": "skip",
                                "reason": "not in this version"})
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
        if not _escalate.has_cap_chown():
            return _escalate.run_restore_via_unit(layer, version_id, paths, mode)
        m = self.get_manifest(layer, version_id)
        by_path = {e["path"]: e for e in m["entries"]}
        root = self._store_root_for(layer)
        store = _cas.ContentStore(root)
        results = []
        for p in paths:
            e = by_path.get(p)
            if e is None:
                results.append({"path": p, "ok": False, "reason": "not in version"})
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

    def queue_status(self):
        window = f"{self.config.work_start}–{self.config.work_end}"
        return {"count": self.queue.count(),
                "summary": self.queue.status_summary(window),
                "intents": self.queue.list()}

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
