# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The automation client's decision logic (spec §5, §6).

The timers and the long-running sentinel call these helpers; they decide, per
trigger, whether to capture now, hold the change for the off-peak window, or
quietly leave the intent queued because the target is absent. Kept pure-ish
(engine + config + an injected clock) so the branching is unit-testable without
timers or a real disk.
"""

import hashlib
import json
import os
import stat
import time

from . import paths as _paths


def minutes_of_day(now_fn=time.time):
    lt = time.localtime(now_fn())
    return lt.tm_hour * 60 + lt.tm_min


def estimate_userdata_change(engine):
    """Cheap change estimate for the user-data threshold decision: the total
    size of files modified since the last user-data capture. Not a full rsync
    dry-run — enough to route small-vs-large (spec §5)."""
    last = int(engine.state.get("last_capture", {}).get(
        _paths.LAYER_USER_DATA, 0))
    total = 0
    for base in engine.config.user_data_paths:
        for dirpath, _dirs, files in os.walk(base):
            for fn in files:
                ap = os.path.join(dirpath, fn)
                if engine.config.is_excluded(ap):
                    continue
                try:
                    st = os.lstat(ap)
                except OSError:
                    continue
                if int(st.st_mtime) > last:
                    total += st.st_size
    return total


def userdata_trigger(engine, now_min=None):
    """Decide + act for an hourly user-data trigger.

    Returns a short outcome string:
      "queued-absent" — target not attached; intent left queued for catch-up.
      "queued-offpeak" — change over threshold; held for the off-peak window.
      "captured:<vid>" — captured immediately.
    """
    cfg = engine.config
    if now_min is None:
        now_min = minutes_of_day(engine._now_fn)
    target_root = engine.target_root()
    if target_root is None:
        engine.capture(_paths.LAYER_USER_DATA, reason="hourly user-data",
                       sync=False, estimate=0)
        return "queued-absent"
    estimate = estimate_userdata_change(engine)
    free = _free_bytes(target_root)
    if cfg.exceeds_threshold(estimate, free) and not cfg.is_off_peak(now_min):
        engine.capture(_paths.LAYER_USER_DATA, reason="hourly user-data (large)",
                       sync=False, estimate=estimate)
        return "queued-offpeak"
    res = engine.capture(_paths.LAYER_USER_DATA, reason="hourly user-data",
                         sync=True)
    return "captured:" + res["version_id"]


def drain_offpeak(engine, now_min=None):
    """Drain the queue during the off-peak window. Outside the window it is a
    no-op. An intent whose target is still absent is left queued (quiet
    catch-up); a captured intent is removed. Returns (drained, remaining)."""
    cfg = engine.config
    if now_min is None:
        now_min = minutes_of_day(engine._now_fn)
    if not cfg.is_off_peak(now_min):
        return (0, engine.queue.count())

    def _run(intent):
        layer = intent.get("layer")
        if layer in _paths.TARGET_ONLY_LAYERS and engine.target_root() is None:
            return False  # target still absent: leave queued (quiet catch-up)
        try:
            engine.capture(layer, scope=intent.get("scope"),
                           reason=intent.get("reason", "off-peak drain"),
                           sync=True)
            return True
        except Exception:
            return False

    return engine.queue.drain(_run)


def config_set_fingerprint(config_paths, excludes=None):
    """Hash a deterministic metadata record for every watched path.

    This deliberately avoids reading file contents on every poll, but includes
    each path's nanosecond timestamps, identity, type, size, ownership, mode,
    and symlink target. It detects changes hidden beneath another file's future
    timestamp and honors the same excluded subtrees as config-state capture.
    A complete change-and-revert between polls remains outside a poller's
    observation boundary.
    """
    if excludes is None:
        from . import configstate as _configstate
        excludes = _configstate.DEFAULT_EXCLUDES
    excluded = tuple(str(path) for path in (excludes or ()))
    records = {}

    def is_excluded(path):
        value = str(path)
        return any(
            value == prefix or value.startswith(prefix.rstrip("/") + "/")
            for prefix in excluded
        )

    def record(path):
        value = str(path)
        if is_excluded(value):
            return
        try:
            metadata = os.lstat(value)
        except OSError as exc:
            records[value] = [
                value, "unreadable", type(exc).__name__, exc.errno
            ]
            return
        mode = metadata.st_mode
        if stat.S_ISDIR(mode):
            kind = "directory"
        elif stat.S_ISLNK(mode):
            kind = "symlink"
        elif stat.S_ISREG(mode):
            kind = "file"
        else:
            kind = "other"
        target = None
        if kind == "symlink":
            try:
                target = os.readlink(value)
            except OSError as exc:
                target = f"unreadable:{type(exc).__name__}:{exc.errno}"
        records[value] = [
            value,
            kind,
            stat.S_IMODE(mode),
            metadata.st_uid,
            metadata.st_gid,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
            metadata.st_dev,
            metadata.st_ino,
            target,
        ]

    for base in sorted(str(path) for path in config_paths):
        if is_excluded(base):
            continue
        if not os.path.lexists(base):
            records[base] = [base, "missing"]
            continue
        if os.path.isdir(base) and not os.path.islink(base):
            for dirpath, dirnames, filenames in os.walk(base, topdown=True):
                dirnames[:] = sorted(
                    name for name in dirnames
                    if not is_excluded(os.path.join(dirpath, name))
                )
                record(dirpath)
                for name in dirnames:
                    candidate = os.path.join(dirpath, name)
                    if os.path.islink(candidate):
                        record(candidate)
                for name in sorted(filenames):
                    record(os.path.join(dirpath, name))
        else:
            record(base)

    payload = json.dumps(
        [records[path] for path in sorted(records)],
        # ASCII escaping preserves surrogate-escaped filesystem bytes without
        # requiring those surrogate code points to be UTF-8 encodable.
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _free_bytes(path):
    import shutil
    try:
        return shutil.disk_usage(str(path)).free
    except OSError:
        return 0
