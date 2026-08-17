# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""chronicle.conf parsing + working-hours / off-peak windows + the size
threshold (spec §5, §12).

The config is user-tunable policy (working hours, the off-peak deferral
threshold, the chosen target, target-encryption preference, retention
overrides, exclude globs, which home trees to protect). Runtime state the
engine owns — the monotonic sequence, adopted-target details — lives in
state.json, not here.
"""

import configparser
import fnmatch
from pathlib import Path

from . import paths as _paths

# Off-peak deferral threshold defaults (spec §5): a capture is held for the
# off-peak window when its estimated change size exceeds the SMALLER of an
# absolute floor and a fraction of target free space.
DEFAULT_SIZE_FLOOR_BYTES = 1 * 1024 * 1024 * 1024      # ~1 GiB
DEFAULT_FREE_FRACTION = 0.05                            # ~5% of free space

# A sensible default working day when unset (spec §5): 09:00–18:00, so off-peak
# is nights + the early morning.
DEFAULT_WORK_START = "09:00"
DEFAULT_WORK_END = "18:00"

_MINUTES_PER_DAY = 24 * 60


def parse_hhmm(text):
    """Parse "HH:MM" to minutes-since-midnight. Raises ValueError on garbage."""
    parts = str(text).strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"not a HH:MM time: {text!r}")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"time out of range: {text!r}")
    return h * 60 + m


class Config:
    """Parsed chronicle.conf. All fields have safe defaults so an absent or
    partial file still yields a usable policy."""

    def __init__(self):
        self.work_start = DEFAULT_WORK_START
        self.work_end = DEFAULT_WORK_END
        self.size_floor_bytes = DEFAULT_SIZE_FLOOR_BYTES
        self.free_fraction = DEFAULT_FREE_FRACTION
        # Target selection (the actual adoption lives in engine state.json).
        self.target_device = None          # e.g. /dev/sdb1 (whole-volume class)
        self.target_directory = None       # a POSIX dir (directory class)
        self.target_size_cap_bytes = None  # cap for the directory class (§addendum A)
        self.target_encryption = True      # recommend LUKS2 on the target (§9)
        # User-data scope + excludes.
        self.user_data_paths = ["/home"]
        self.exclude_globs = [
            "*/.cache/*", "*/.local/share/Trash/*", "*/.thumbnails/*",
        ]
        # Retention overrides (empty => the built-in graduated schedule, §7).
        self.retention = {}

    # -- off-peak window (spec §5) --------------------------------------

    def is_off_peak(self, minutes_of_day):
        """True when minutes_of_day (0..1439) falls OUTSIDE working hours.

        Working hours [start, end): off-peak is its complement. Supports an
        overnight working span (start > end) as well as a normal daytime one.
        """
        start = parse_hhmm(self.work_start)
        end = parse_hhmm(self.work_end)
        m = minutes_of_day % _MINUTES_PER_DAY
        if start == end:
            # Degenerate: no working hours declared => always off-peak.
            return True
        if start < end:
            working = start <= m < end
        else:
            # Overnight span, e.g. 22:00–06:00 working.
            working = m >= start or m < end
        return not working

    # -- off-peak deferral threshold (spec §5) --------------------------

    def threshold_bytes(self, target_free_bytes):
        """The size above which a capture is held for off-peak: the smaller of
        the absolute floor and the free-space fraction."""
        fractional = int((target_free_bytes or 0) * self.free_fraction)
        candidates = [self.size_floor_bytes]
        if fractional > 0:
            candidates.append(fractional)
        return min(candidates)

    def exceeds_threshold(self, estimated_bytes, target_free_bytes):
        """True when a change of estimated_bytes should wait for off-peak."""
        return estimated_bytes > self.threshold_bytes(target_free_bytes)

    # -- excludes -------------------------------------------------------

    def is_excluded(self, path):
        return any(fnmatch.fnmatch(path, g) for g in self.exclude_globs)


def load(path=None):
    """Load chronicle.conf; return a Config (defaults when the file is absent).
    Unknown keys are ignored; a malformed time/size falls back to the default
    for that field rather than aborting the engine."""
    cfg = Config()
    p = Path(path) if path else _paths.CONFIG_PATH
    if not p.exists():
        return cfg
    parser = configparser.ConfigParser()
    try:
        parser.read(str(p))
    except configparser.Error:
        return cfg
    if not parser.has_section("chronicle"):
        return cfg
    sec = parser["chronicle"]

    def _get(key):
        return sec.get(key, fallback=None)

    if _get("work_start"):
        try:
            parse_hhmm(sec["work_start"]); cfg.work_start = sec["work_start"]
        except ValueError:
            pass
    if _get("work_end"):
        try:
            parse_hhmm(sec["work_end"]); cfg.work_end = sec["work_end"]
        except ValueError:
            pass
    if _get("size_floor_bytes"):
        try:
            cfg.size_floor_bytes = int(sec["size_floor_bytes"])
        except ValueError:
            pass
    if _get("free_fraction"):
        try:
            cfg.free_fraction = float(sec["free_fraction"])
        except ValueError:
            pass
    if _get("target_device"):
        cfg.target_device = sec["target_device"].strip() or None
    if _get("target_directory"):
        cfg.target_directory = sec["target_directory"].strip() or None
    if _get("target_size_cap_bytes"):
        try:
            cfg.target_size_cap_bytes = int(sec["target_size_cap_bytes"])
        except ValueError:
            pass
    if _get("target_encryption") is not None:
        cfg.target_encryption = sec.getboolean("target_encryption", fallback=True)
    if _get("user_data_paths"):
        cfg.user_data_paths = [
            s.strip() for s in sec["user_data_paths"].split(",") if s.strip()
        ]
    if _get("exclude_globs"):
        cfg.exclude_globs = [
            s.strip() for s in sec["exclude_globs"].split(",") if s.strip()
        ]
    return cfg
