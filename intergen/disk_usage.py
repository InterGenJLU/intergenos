# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A bounded, read-only disk-usage probe: what is taking up the space.

"What's taking up all my disk space" is a question about WHERE the space went,
not how much is left. The free-space probe (``df``) answers the second; nothing
answered the first, so the turn fell to the model, which composed a table. This
module measures it, in code, from the machine itself:

  * the largest consumers under a root (its top-level directories, sized as
    du sizes them: everything beneath, a hard link counted once);
  * the well-known caches by name — the package cache, the system journal, the
    system logs, the user's cache and trash;
  * the filesystem's own totals (``statvfs``) for the root filesystem and the
    user's home.

Every walk is BOUNDED and declares its bounds in the result:

  * a wall-clock BUDGET — breadth-first, so the top levels are measured before
    the deep ones; when the budget ends the directories not yet entered are
    counted, every reported entry above one of them is marked incomplete, and
    the report says the measurement is partial;
  * ONE FILESYSTEM per root — a directory on another device (``st_dev``) is not
    entered and is listed as skipped, so /proc, /sys, /dev, /run and every mount
    stay out of a root walk;
  * NO SYMLINK is followed — a link is an entry of its own, never a path into
    another tree (a loop cannot form; a link to / cannot pull the world in);
  * a DEPTH LIMIT — a directory deeper than it is not entered and is declared;
  * an ENTRIES CAP on what is reported;
  * NO PRIVILEGE — the walk runs as the daemon's own account; a directory it
    cannot read is counted as unreadable and named, never escalated;
  * no write and no network, by construction: ``scandir``, ``lstat``,
    ``statvfs`` and nothing else.

The renderer composes the answer ONLY from the report's figures. A figure it
cannot state exactly is shown as a lower bound ("at least …"), and a partial
measurement says so in the same sentence.
"""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

__all__ = [
    "DiskUsageReport", "UsageEntry", "FilesystemTotals", "KnownConsumer",
    "probe", "filesystem_totals", "known_consumers", "measure_known_consumers",
    "answer", "render", "human_size",
]

# The default bounds. The budget covers ONE probe() call; answer() spends its
# whole budget across the walks it runs (the known consumers first, then the
# root, then the home) so the answer as a whole is bounded.
DEFAULT_BUDGET_S = 4.0
DEFAULT_DEPTH_LIMIT = 12
DEFAULT_MAX_ENTRIES = 8
_BUDGET_CHECK_EVERY = 256  # entries between clock reads inside one directory


@dataclass
class UsageEntry:
    path: str          # absolute
    size: int          # bytes, by the report's size_kind
    complete: bool     # False when a directory beneath it was not entered
    files: int = 0
    dirs: int = 0


@dataclass
class DiskUsageReport:
    root: str
    size_kind: str                       # "allocated" (du's default) or "apparent"
    total: int = 0                       # everything measured under root
    complete: bool = True                # False when any directory was not entered
    entries: list[UsageEntry] = field(default_factory=list)   # the largest, capped
    files_scanned: int = 0
    dirs_scanned: int = 0               # directories actually read
    dirs_seen: int = 0                  # directories found (read or not)
    symlinks_seen: int = 0
    not_entered: int = 0                 # directories left out by the budget or the depth limit
    not_entered_budget: int = 0
    not_entered_depth: int = 0
    other_filesystems: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    budget_s: float = 0.0
    depth_limit: int = 0
    max_entries: int = 0
    root_readable: bool = True

    @property
    def partial(self) -> bool:
        return not self.complete

    def reasons(self) -> list[str]:
        out = []
        if self.not_entered_budget:
            out.append(f"the {self.budget_s:.1f}-second budget ended with "
                       f"{self.not_entered_budget} director{'y' if self.not_entered_budget == 1 else 'ies'} not yet entered")
        if self.not_entered_depth:
            out.append(f"{self.not_entered_depth} director{'y' if self.not_entered_depth == 1 else 'ies'} "
                       f"below the depth limit of {self.depth_limit} not entered")
        if self.unreadable:
            out.append(f"{len(self.unreadable)} director{'y' if len(self.unreadable) == 1 else 'ies'} "
                       f"not readable by this account")
        return out


@dataclass
class FilesystemTotals:
    path: str
    total: int
    used: int
    available: int


@dataclass
class KnownConsumer:
    label: str
    path: str
    report: DiskUsageReport | None   # None when the path does not exist
    exists: bool
    readable: bool


def _lstat(entry: os.DirEntry) -> os.stat_result:
    """The one stat seam: never follows a symlink. Tests replace it to put a
    directory on another device."""
    return entry.stat(follow_symlinks=False)


def _size_of(st: os.stat_result, size_kind: str) -> int:
    if size_kind == "apparent":
        return int(st.st_size)
    return int(getattr(st, "st_blocks", 0)) * 512


def probe(root: str, *, budget_s: float = DEFAULT_BUDGET_S,
          depth_limit: int = DEFAULT_DEPTH_LIMIT,
          max_entries: int = DEFAULT_MAX_ENTRIES,
          size_kind: str = "allocated",
          clock: Callable[[], float] = time.monotonic) -> DiskUsageReport:
    """Measure what is under ``root`` within the bounds (see the module doc).

    Breadth-first over directories on the root's own filesystem, no symlink
    followed, a hard link counted once, every file's size added to each of its
    ancestors up to the root (so a directory's size is its whole subtree, as du
    reports it). The budget is read at every directory and every
    ``_BUDGET_CHECK_EVERY`` entries; when it ends, the directories still queued
    are counted as not entered and the entries above them marked incomplete.
    """
    if size_kind not in ("allocated", "apparent"):
        raise ValueError(f"size_kind must be 'allocated' or 'apparent', not {size_kind!r}")
    if budget_s < 0 or depth_limit < 0 or max_entries < 0:
        raise ValueError("budget_s, depth_limit and max_entries must be non-negative")
    root = os.path.abspath(root)
    report = DiskUsageReport(root=root, size_kind=size_kind, budget_s=budget_s,
                             depth_limit=depth_limit, max_entries=max_entries)
    started = clock()
    deadline = started + budget_s
    try:
        root_st = os.lstat(root)
    except OSError:
        report.root_readable = False
        report.complete = False
        report.unreadable.append(root)
        report.elapsed_s = clock() - started
        return report
    root_dev = root_st.st_dev
    # sizes[path] = (bytes, files, dirs) of the subtree measured so far
    sizes: dict[str, list[int]] = {root: [0, 0, 0]}
    parent: dict[str, str] = {}
    incomplete: set[str] = set()
    seen_inodes: set[tuple[int, int]] = set()
    queue: deque[tuple[str, int]] = deque([(root, 0)])
    budget_over = False

    def mark_incomplete(path: str) -> None:
        p = path
        while p is not None and p not in incomplete:
            incomplete.add(p)
            p = parent.get(p)

    def add_size(path: str, size: int, is_dir: bool) -> None:
        p = path
        while p is not None:
            rec = sizes[p]
            rec[0] += size
            if is_dir:
                rec[2] += 1
            else:
                rec[1] += 1
            p = parent.get(p)

    while queue:
        if clock() >= deadline:
            budget_over = True
            break
        path, depth = queue.popleft()
        try:
            it = os.scandir(path)
        except OSError:
            report.unreadable.append(path)
            mark_incomplete(path)
            continue
        report.dirs_scanned += 1
        n = 0
        with it:
            for entry in it:
                n += 1
                if n % _BUDGET_CHECK_EVERY == 0 and clock() >= deadline:
                    budget_over = True
                    # the rest of this directory is unmeasured: declare it
                    mark_incomplete(path)
                    report.not_entered_budget += 1
                    break
                try:
                    st = _lstat(entry)
                except OSError:
                    report.unreadable.append(entry.path)
                    mark_incomplete(path)
                    continue
                if entry.is_symlink():
                    report.symlinks_seen += 1
                    continue  # never followed; its own bytes are not the question
                if entry.is_dir(follow_symlinks=False):
                    if st.st_dev != root_dev:
                        report.other_filesystems.append(entry.path)
                        continue
                    sizes[entry.path] = [0, 0, 0]
                    parent[entry.path] = path
                    add_size(path, 0, True)
                    if depth + 1 > depth_limit:
                        report.not_entered_depth += 1
                        mark_incomplete(entry.path)
                        continue
                    queue.append((entry.path, depth + 1))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue  # a device, a socket, a fifo: not a consumer of space
                if st.st_nlink > 1:
                    key = (st.st_dev, st.st_ino)
                    if key in seen_inodes:
                        continue
                    seen_inodes.add(key)
                add_size(path, _size_of(st, size_kind), False)
        if budget_over:
            break
    if budget_over:
        for path, _depth in queue:
            report.not_entered_budget += 1
            mark_incomplete(path)
    report.not_entered = report.not_entered_budget + report.not_entered_depth
    report.complete = not incomplete and not report.unreadable
    report.total = sizes[root][0]
    report.files_scanned = sizes[root][1]
    report.dirs_seen = sizes[root][2]
    # The consumers reported: the directories directly under the root that hold
    # anything (an empty one is not a consumer), largest first, capped.
    candidates = [p for p in sizes if p != root and parent.get(p) == root and sizes[p][0] > 0]
    candidates.sort(key=lambda p: (-sizes[p][0], p))
    report.entries = [UsageEntry(path=p, size=sizes[p][0], complete=p not in incomplete,
                                 files=sizes[p][1], dirs=sizes[p][2])
                      for p in candidates[:max_entries]]
    report.elapsed_s = clock() - started
    return report


def filesystem_totals(path: str) -> FilesystemTotals | None:
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    total = st.f_frsize * st.f_blocks
    free = st.f_frsize * st.f_bfree
    available = st.f_frsize * st.f_bavail
    return FilesystemTotals(path=path, total=total, used=total - free, available=available)


def known_consumers(home: str) -> list[tuple[str, str]]:
    """The named places space goes on this system, in the order they are reported."""
    return [
        ("the package cache", "/var/cache/pkm/packages"),
        ("the system journal", "/var/log/journal"),
        ("the system logs", "/var/log"),
        ("your cache", os.path.join(home, ".cache")),
        ("your trash", os.path.join(home, ".local", "share", "Trash")),
    ]


def measure_known_consumers(home: str, *, budget_s: float, size_kind: str = "allocated",
                            clock: Callable[[], float] = time.monotonic,
                            depth_limit: int = DEFAULT_DEPTH_LIMIT) -> list[KnownConsumer]:
    out: list[KnownConsumer] = []
    places = known_consumers(home)
    each = budget_s / max(len(places), 1)
    for label, path in places:
        if not os.path.lexists(path):
            out.append(KnownConsumer(label, path, None, exists=False, readable=False))
            continue
        try:
            os.scandir(path).close()
            readable = True
        except OSError:
            readable = False
        if not readable:
            out.append(KnownConsumer(label, path, None, exists=True, readable=False))
            continue
        rep = probe(path, budget_s=each, depth_limit=depth_limit, max_entries=0,
                    size_kind=size_kind, clock=clock)
        out.append(KnownConsumer(label, path, rep, exists=True, readable=rep.root_readable))
    return out


_UNITS = ("bytes", "KiB", "MiB", "GiB", "TiB", "PiB")


def human_size(n: int) -> str:
    """Binary units, one decimal from KiB up; exact bytes below."""
    if n < 1024:
        return f"{n} byte{'s' if n != 1 else ''}"
    value = float(n)
    unit = 0
    while value >= 1024 and unit < len(_UNITS) - 1:
        value /= 1024.0
        unit += 1
    return f"{value:.1f} {_UNITS[unit]}"


def _entry_size(e: UsageEntry) -> str:
    s = human_size(e.size)
    return s if e.complete else f"at least {s}"


def _report_size(r: DiskUsageReport) -> str:
    s = human_size(r.total)
    return s if r.complete else f"at least {s}"


def render(root_report: DiskUsageReport | None, root_fs: FilesystemTotals | None,
           home_report: DiskUsageReport | None, home_fs: FilesystemTotals | None,
           known: list[KnownConsumer], *, home: str, scope: str = "both") -> str:
    """Compose the answer from the reports ONLY. Every number here is one of the
    probe's figures or statvfs's; a measurement that is partial says so."""
    lines: list[str] = []
    partial_reasons: list[str] = []
    if root_report is not None and scope in ("both", "root"):
        if root_fs is not None:
            lines.append(f"Your root filesystem (/) has {human_size(root_fs.used)} used of "
                         f"{human_size(root_fs.total)}, with {human_size(root_fs.available)} free.")
        if root_report.root_readable and root_report.entries:
            parts = [f"{e.path} {_entry_size(e)}" for e in root_report.entries]
            lines.append("The largest consumers under /: " + ", ".join(parts) + ".")
        elif not root_report.root_readable:
            lines.append("The root filesystem could not be read by this account.")
        partial_reasons += [f"under /: {r}" for r in root_report.reasons()]
    if known:
        parts = []
        for k in known:
            if not k.exists:
                parts.append(f"{k.label} is empty (nothing at {k.path})")
            elif not k.readable or k.report is None:
                parts.append(f"{k.label} ({k.path}) is not readable without administrator rights")
            else:
                parts.append(f"{k.label} {_report_size(k.report)}")
                partial_reasons += [f"{k.label}: {r}" for r in k.report.reasons()]
        lines.append("The well-known places: " + "; ".join(parts) + ".")
    if home_report is not None and scope in ("both", "home"):
        head = f"Your home ({home}) holds {_report_size(home_report)}"
        if home_fs is not None and (root_fs is None or home_fs.total != root_fs.total
                                    or home_fs.used != root_fs.used):
            head += (f" on a filesystem with {human_size(home_fs.used)} used of "
                     f"{human_size(home_fs.total)} and {human_size(home_fs.available)} free")
        lines.append(head + ".")
        if home_report.entries:
            parts = [f"{_tilde(e.path, home)} {_entry_size(e)}" for e in home_report.entries]
            lines.append("The largest in it: " + ", ".join(parts) + ".")
        partial_reasons += [f"in your home: {r}" for r in home_report.reasons()]
    if partial_reasons:
        lines.append("This measurement is partial — " + "; ".join(partial_reasons) +
                     " — so a figure marked 'at least' is a lower bound.")
    else:
        lines.append("This is a complete measurement within the probe's bounds.")
    return "\n".join(lines)


def _tilde(path: str, home: str) -> str:
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def answer(scope: str = "both", *, home: str | None = None,
           budget_s: float = DEFAULT_BUDGET_S, size_kind: str = "allocated",
           depth_limit: int = DEFAULT_DEPTH_LIMIT, max_entries: int = DEFAULT_MAX_ENTRIES,
           clock: Callable[[], float] = time.monotonic, root: str = "/") -> str:
    """The whole answer within ONE budget: the known consumers (a fifth of it),
    then the root walk and the home walk sharing the rest by scope."""
    if scope not in ("both", "root", "home"):
        raise ValueError(f"scope must be both, root or home, not {scope!r}")
    home = home or os.path.expanduser("~")
    started = clock()
    known = measure_known_consumers(home, budget_s=budget_s * 0.2, size_kind=size_kind,
                                    clock=clock, depth_limit=depth_limit)
    remaining = max(budget_s - (clock() - started), 0.0)
    walks = 2 if scope == "both" else 1
    share = remaining / walks
    root_report = root_fs = home_report = home_fs = None
    if scope in ("both", "root"):
        root_fs = filesystem_totals(root)
        root_report = probe(root, budget_s=share, depth_limit=depth_limit,
                            max_entries=max_entries, size_kind=size_kind, clock=clock)
    if scope in ("both", "home"):
        home_fs = filesystem_totals(home)
        home_report = probe(home, budget_s=share, depth_limit=depth_limit,
                            max_entries=max_entries, size_kind=size_kind, clock=clock)
    return render(root_report, root_fs, home_report, home_fs, known, home=home, scope=scope)
