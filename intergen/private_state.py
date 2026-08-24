# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Owner-only creation of per-user state.

WHY THIS MODULE EXISTS. InterGen keeps conversation transcripts, a
personal-fact database, a decision trace, a tool-dispatch ledger, the last
answer and the raw model output behind it, the web-auth token and the dispatch
signing key under the user's home directory. Created through a bare
``Path.mkdir()`` or a bare ``open(path, "a")`` those land ``0755`` and ``0644``
under the ordinary ``umask 0022`` — readable by every other local account on
the machine.

The mode is therefore set AT CREATION, never applied afterwards. ``os.mkdir``
and ``os.open`` both take the mode as an argument, and because ``0700`` and
``0600`` carry no group or other bits, no umask can loosen them: umask only
removes bits. A create-then-chmod sequence would leave a window in which the
file already holds its first record and is still world-readable, which is the
window this module exists to close. Every InterGen-owned directory on the way
to a path is created that way individually, because ``mkdir(mode=..., parents=
True)`` applies the mode to the FINAL component only and would leave the ones
above it at the platform default for as long as it took to tighten them.

Nothing here widens a mode. :func:`_tighten` only ever removes group and other
bits, so a directory an administrator has deliberately made stricter than
``0700`` is left as it is.

WHERE IT STOPS. Two boundaries, and both are enforced rather than documented:

* **The mode is read and written through ONE file descriptor.** ``chmod(2)``
  follows a symbolic link, so reading a mode with ``lstat`` and writing it with
  a path-based ``chmod`` can act on a different inode than the one that was
  measured — outside the tree entirely, and a symbolic link's own mode is
  ``0777``, so the mode written to its target can GRANT a bit the target never
  had. Every mode change here goes through a descriptor opened ``O_PATH |
  O_NOFOLLOW``, which is refused a symbolic link by the kernel itself, so
  neither the substitution nor the window between the two calls exists.
* **Only the owned trees are touched.** :func:`owned_roots` is the whole list.
  A path outside every one of them — a system log directory a privileged daemon
  keeps, a caller-supplied test location — is created the way any other program
  would create it and has its mode left alone.

WHAT IS AND IS NOT OWNED. The four trees below belong to InterGen end to end
and everything inside them is created owner-only. Their XDG parents —
``~/.local``, ``~/.local/share``, ``~/.local/state``, ``~/.config``,
``~/.cache`` — are shared with every other application on the machine and are
NEVER touched: creating them is left to the platform default, exactly as any
other program would leave them.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import stat
import time
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO, Any, Iterable

logger = logging.getLogger(__name__)

#: Mode for every directory InterGen creates under the user's home.
DIR_MODE = 0o700
#: Mode for every file InterGen creates under the user's home.
FILE_MODE = 0o600

#: The bits that must never be set on per-user state.
_FORBIDDEN_BITS = stat.S_IRWXG | stat.S_IRWXO

_TREE_NAME = "intergen"

#: Bumped when a release needs the one-time pass to run again over homes that
#: already carry a marker. The marker records the value it was written with.
MIGRATION_VERSION = 1

_MARKER_NAME = ".permissions-migrated"


class SymlinkEncountered(Exception):
    """A path that had to be a real file or directory is a symbolic link.

    Deliberately not an :class:`OSError`: every caller in this module handles
    this case on purpose, and inheriting from ``OSError`` would let it be
    swallowed by a handler written for a disk error.
    """

    def __init__(self, path: str) -> None:
        super().__init__(f"{path}: is a symbolic link")
        self.path = path


# ── Canonical per-user locations ──────────────────────────────────────────
#
# These RESOLVE a path; they do not create anything. They exist so the
# create helpers below can tell an InterGen-owned directory from a shared XDG
# parent, and so the migration pass knows exactly which trees to walk.


def _xdg_base(var: str, fallback: tuple[str, ...]) -> Path:
    value = os.environ.get(var)
    if value:
        return Path(value)
    return Path.home().joinpath(*fallback)


def state_dir_path() -> Path:
    """``$XDG_STATE_HOME/intergen`` (logs, traces, ledgers). Not created."""
    return _xdg_base("XDG_STATE_HOME", (".local", "state")) / _TREE_NAME


def data_dir_path() -> Path:
    """``$XDG_DATA_HOME/intergen`` (facts, sessions, records). Not created."""
    return _xdg_base("XDG_DATA_HOME", (".local", "share")) / _TREE_NAME


def config_dir_path() -> Path:
    """``$XDG_CONFIG_HOME/intergen`` (token, signing key). Not created."""
    return _xdg_base("XDG_CONFIG_HOME", (".config",)) / _TREE_NAME


def cache_dir_path() -> Path:
    """``$XDG_CACHE_HOME/intergen`` (the last answer). Not created.

    Regenerable, which is why it is a cache — but ``last-answer.json`` holds
    the delivered answer AND the raw model output behind it, so its contents
    are the same class of material as a session transcript and it is owned
    here for exactly that reason.
    """
    return _xdg_base("XDG_CACHE_HOME", (".cache",)) / _TREE_NAME


def owned_roots() -> tuple[Path, ...]:
    """Every per-user tree InterGen owns end to end.

    Both the XDG-resolved and the plain ``~/.local`` forms are reported when
    they differ: modules in this package resolve their paths both ways, so a
    sweep that only walked one form could leave a real directory untouched.
    Duplicates are collapsed and order is stable.
    """
    candidates: list[Path] = [state_dir_path(), data_dir_path(),
                              config_dir_path(), cache_dir_path()]
    home = Path.home()
    candidates += [
        home / ".local" / "state" / _TREE_NAME,
        home / ".local" / "share" / _TREE_NAME,
        home / ".config" / _TREE_NAME,
        home / ".cache" / _TREE_NAME,
    ]
    seen: dict[Path, None] = {}
    for path in candidates:
        seen.setdefault(path, None)
    return tuple(seen)


def owning_root(path: Path) -> Path | None:
    """The owned root *path* lies in, or ``None`` if it lies in none of them."""
    for root in owned_roots():
        if path == root or root in path.parents:
            return root
    return None


def migration_marker_path() -> Path:
    """Where the one-time pass records that it has run. Not created."""
    return state_dir_path() / _MARKER_NAME


# ── Mode enforcement ──────────────────────────────────────────────────────

#: ``O_PATH`` opens the name rather than the contents: no read or write
#: permission on the file itself is required, which matters because a mode this
#: pass has to FIX may be one the owner cannot read through. ``O_NOFOLLOW``
#: added to it returns a descriptor for the symbolic link itself rather than
#: for its target, which is what makes the link detectable instead of followed.
_NOFOLLOW_FLAGS = os.O_PATH | os.O_NOFOLLOW


def _fd_path(fd: int) -> str:
    """The one name that always refers to *fd*'s inode and to no other.

    ``fchmod`` is refused on an ``O_PATH`` descriptor, so the mode is applied
    through the descriptor's entry in the process's own file-descriptor
    directory. The kernel resolves that to the open file description, not by
    re-walking the path, so nothing can be substituted in between; applied to a
    descriptor that IS a symbolic link it is refused outright rather than
    followed, which is a second guard behind the check below.
    """
    return f"/proc/self/fd/{fd}"


def _tighten(path: Path) -> bool:
    """Remove every group and other bit from *path*. True if it changed.

    This ONLY removes access; it never grants any. The owner bits are left
    exactly as they are, so a directory someone has deliberately made stricter
    than ``0700`` — say ``0500`` — keeps that choice, and a read-only state
    file stays read-only. Confidentiality is what this function is for;
    granting the daemon access it does not have is a different question, and
    silently answering it here would override a decision the user made.

    The mode is read from and written to the SAME open descriptor. A path-based
    ``chmod`` follows a symbolic link, so it could apply a mode measured on one
    inode to another one — outside the tree, and possibly widening it, because
    a symbolic link's own mode is ``0777``. A symbolic link raises
    :class:`SymlinkEncountered` here; the caller records it and moves on.

    New files and directories do not rely on this at all: :data:`DIR_MODE` and
    :data:`FILE_MODE` are passed to ``mkdir(2)`` and ``open(2)`` so they are
    correct the instant they exist.
    """
    try:
        fd = os.open(path, _NOFOLLOW_FLAGS)
    except OSError as exc:
        # O_NOFOLLOW without O_PATH would report a symbolic link this way; the
        # combination used here does not, but a kernel that refuses O_PATH
        # would, and mistaking that for a real error would be wrong.
        if exc.errno == errno.ELOOP:
            raise SymlinkEncountered(str(path)) from exc
        raise
    try:
        entry = os.fstat(fd)
        if stat.S_ISLNK(entry.st_mode):
            raise SymlinkEncountered(str(path))
        current = stat.S_IMODE(entry.st_mode)
        wanted = current & ~_FORBIDDEN_BITS
        if current == wanted:
            return False
        os.chmod(_fd_path(fd), wanted)
        return True
    finally:
        os.close(fd)


def _tighten_or_warn(path: Path) -> None:
    """Tighten *path* unless it is a symbolic link, in which case say so.

    A create helper handed a symbolic link is not an error — the user may well
    have put their transcripts on another disk on purpose, and refusing to
    write would break their machine over a mode. What it must NOT do is change
    the mode of whatever is on the other end, so the mode change is dropped and
    the condition is named where an operator can see it.
    """
    try:
        _tighten(path)
    except SymlinkEncountered:
        logger.warning(
            "Per-user state permissions: %s is a symbolic link, so its mode "
            "was left alone (changing it would change a file outside the "
            "tree). The data itself is still written through the link, which "
            "is the user's arrangement to make.", path,
        )


def _opener(path: str, flags: int) -> int:
    """``open()`` opener that creates with :data:`FILE_MODE`."""
    return os.open(path, flags, FILE_MODE)


# ── Creation helpers ──────────────────────────────────────────────────────


def private_dir(path: Path | str) -> Path:
    """Create *path* as an owner-only directory and return it.

    Every InterGen-owned component from the owned root down to *path* is
    created individually with the mode passed to ``mkdir(2)``, so none of them
    is briefly world-listable — ``parents=True`` would apply the mode to the
    last component only and leave the ones above it at the platform default.
    Owned components that already exist are tightened.

    Shared XDG parents above the owned root are created exactly as any other
    program would create them, and are never tightened.

    A path outside every owned root is not ours: it is created with a plain
    ``mkdir`` and its mode is left alone. A privileged daemon's system log
    directory is the case that matters — it is root-owned and group-readable by
    design, and taking it to ``0700`` would remove access this module was never
    asked to remove.
    """
    path = Path(path)
    root = owning_root(path)
    if root is None:
        path.mkdir(parents=True, exist_ok=True)
        return path

    # The shared ancestors above the owned root: platform default, untouched.
    root.parent.mkdir(parents=True, exist_ok=True)

    current = root
    components = [root, *(root / rel for rel in _relative_chain(root, path))]
    for current in components:
        try:
            os.mkdir(current, DIR_MODE)
        except FileExistsError:
            _tighten_or_warn(current)
    return path


def _relative_chain(root: Path, path: Path) -> list[Path]:
    """``root``-relative sub-paths of every component between root and path."""
    parts = path.relative_to(root).parts
    return [Path(*parts[:index + 1]) for index in range(len(parts))]


def private_touch(path: Path | str) -> Path:
    """Ensure *path* exists as an owner-only file and return it.

    Use this before handing a path to a writer that opens the file itself and
    would create it world-readable — sqlite, a history backend, any library
    that calls plain ``open``. Those writers open an EXISTING file, and an
    ordinary open never changes a mode, so pre-creating the file at
    :data:`FILE_MODE` is what makes their writes owner-only.
    """
    path = Path(path)
    private_dir(path.parent)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, FILE_MODE)
    os.close(fd)
    _tighten_or_warn(path)
    return path


def private_open(path: Path | str, mode: str = "a", **kwargs: Any) -> IO[Any]:
    """``open()`` that creates at :data:`FILE_MODE` instead of ``0666 & ~umask``.

    An existing file is tightened as well, which closes the recreate hole: a
    log file that is rotated away or deleted out from under the daemon is
    re-created by the next append, and a plain ``open(path, "a")`` re-creates
    it world-readable with no signal to anyone.
    """
    path = Path(path)
    stream = open(path, mode, opener=_opener, **kwargs)
    try:
        _tighten_or_warn(path)
    except OSError:  # pragma: no cover - the handle is already valid
        stream.close()
        raise
    return stream


def private_write_text(path: Path | str, text: str, *,
                       encoding: str = "utf-8") -> Path:
    """Replace *path*'s contents, owner-only. Returns the path."""
    path = Path(path)
    with private_open(path, "w", encoding=encoding) as handle:
        handle.write(text)
    return path


class PrivateRotatingFileHandler(RotatingFileHandler):
    """``RotatingFileHandler`` whose file — and every rollover — is ``0600``.

    The stock handler opens through plain ``open``, so both the first file and
    every rotated successor land ``0644``. Overriding ``_open`` covers both:
    ``doRollover`` calls it for the fresh file, and the renamed backups keep
    the mode they were created with.
    """

    def _open(self) -> IO[Any]:
        stream = open(self.baseFilename, self.mode,
                      encoding=self.encoding,
                      errors=getattr(self, "errors", None),
                      opener=_opener)
        try:
            _tighten_or_warn(Path(self.baseFilename))
        except OSError:  # pragma: no cover - handle already valid
            stream.close()
            raise
        return stream


class PrivateFileHandler(logging.FileHandler):
    """``logging.FileHandler`` whose file is created ``0600``."""

    def _open(self) -> IO[Any]:
        stream = open(self.baseFilename, self.mode,
                      encoding=self.encoding,
                      errors=getattr(self, "errors", None),
                      opener=_opener)
        try:
            _tighten_or_warn(Path(self.baseFilename))
        except OSError:  # pragma: no cover - handle already valid
            stream.close()
            raise
        return stream


# ── One-time migration for a home directory created before this change ────


@dataclass
class HardenReport:
    """What the migration actually did. Every field is a measured count."""

    roots_present: list[str] = field(default_factory=list)
    dirs_changed: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    skipped_symlinks: list[str] = field(default_factory=list)
    symlinked_roots: list[str] = field(default_factory=list)
    crossed_filesystems: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return len(self.dirs_changed) + len(self.files_changed)

    @property
    def clean(self) -> bool:
        return not self.failures

    @property
    def changed_paths(self) -> list[str]:
        return [*self.dirs_changed, *self.files_changed]


def _mount_points() -> frozenset[str]:
    """Every path something is mounted on, as the kernel reports it.

    A device-number comparison finds a mount of a DIFFERENT filesystem, but a
    bind mount of a directory on the same filesystem keeps the same device
    number and would slip past one. The mount table names both.

    An unreadable mount table leaves the device comparison as the only guard,
    which is the honest degradation: fewer mounts are recognised, none are
    invented.
    """
    points: set[str] = set()
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as handle:
            for line in handle:
                fields = line.split(" ")
                if len(fields) > 4:
                    points.add(_unescape_mount_field(fields[4]))
    except OSError as exc:
        logger.debug("Per-user state permissions: mount table unreadable "
                     "(%s); relying on device numbers alone.", exc)
    return frozenset(points)


def _unescape_mount_field(value: str) -> str:
    """Undo the octal escapes the kernel writes for space, tab, newline, ``\\``."""
    for escape, character in (("\\040", " "), ("\\011", "\t"),
                              ("\\012", "\n"), ("\\134", "\\")):
        value = value.replace(escape, character)
    return value


def harden_user_state(roots: Iterable[Path] | None = None) -> HardenReport:
    """Tighten an existing per-user tree to owner-only. Idempotent.

    A home directory created before per-user state was made owner-only still
    holds ``0755`` directories and ``0644`` files; nothing in the create path
    fixes those, because the create path only runs when a file does not yet
    exist. This walks each owned tree once and removes group and other access
    from everything in it.

    The walk is deliberately structural rather than a list of known names.
    Files no module in this package creates — a marker dropped by the shell
    extension, a record left by an older release, a log rotated by logrotate —
    sit in these directories too and are just as readable by other accounts.

    FOUR THINGS ARE OUTSIDE THE TREE EVEN THOUGH THEY ARE INSIDE THE PATH, and
    each is recorded in its own field rather than folded into the others:

    * a root that is itself a symbolic link (``symlinked_roots``) names a
      directory somewhere else entirely, so it is refused rather than walked;
    * a symbolic link inside a tree (``skipped_symlinks``) is left alone;
    * anything mounted inside a tree (``crossed_filesystems``) belongs to
      whoever mounted it;
    * a root that exists but is not a directory is a ``failures`` entry saying
      so, because silently skipping it would hide a real misconfiguration.

    Never raises. A path that cannot be adjusted — including a directory that
    cannot be READ, whose contents would otherwise vanish from the walk without
    a word — is recorded in ``failures`` and the walk continues, so one
    unwritable file cannot stop the daemon from starting or leave the rest of
    the tree untouched.
    """
    report = HardenReport()
    targets = tuple(roots) if roots is not None else owned_roots()
    mounted = _mount_points()

    for root in targets:
        try:
            if root.is_symlink():
                report.symlinked_roots.append(str(root))
                continue
            entry = root.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            report.failures.append(f"{root}: {type(exc).__name__}: {exc}")
            continue

        if not stat.S_ISDIR(entry.st_mode):
            report.failures.append(
                f"{root}: expected a directory, found "
                f"{_describe_type(entry.st_mode)}")
            continue

        report.roots_present.append(str(root))
        _harden_tree(root, entry.st_dev, mounted, report)

    return report


def _describe_type(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "a regular file"
    if stat.S_ISLNK(mode):
        return "a symbolic link"
    return "neither a directory nor a regular file"


def _harden_tree(root: Path, device: int, mounted: frozenset[str],
                 report: HardenReport) -> None:
    """Walk one owned tree, tightening what belongs to it and nothing else."""
    _record(root, report, is_dir=True)

    # The walk is driven by the REAL path of each directory so that a mount
    # point can be recognised against the kernel's own table; the paths put in
    # the report are the ones the caller passed, which are the ones an operator
    # will recognise.
    pending: list[tuple[Path, str]] = [(root, os.path.realpath(root))]
    while pending:
        directory, real_directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                children = list(entries)
        except OSError as exc:
            # The case this exists for: an unreadable directory. Left to a
            # listing call that swallows the error, its contents would simply
            # not appear, and a report built from what appeared would say the
            # tree was clean.
            report.failures.append(
                f"{directory}: contents could not be read, so anything inside "
                f"it is still readable by other local accounts: "
                f"{type(exc).__name__}: {exc}")
            continue

        for child in children:
            path = Path(child.path)
            real_path = os.path.join(real_directory, child.name)
            try:
                if child.is_symlink():
                    report.skipped_symlinks.append(str(path))
                    continue
                info = child.stat(follow_symlinks=False)
                if info.st_dev != device or real_path in mounted:
                    report.crossed_filesystems.append(str(path))
                    continue
                if stat.S_ISDIR(info.st_mode):
                    _record(path, report, is_dir=True)
                    pending.append((path, real_path))
                elif stat.S_ISREG(info.st_mode):
                    _record(path, report, is_dir=False)
            except SymlinkEncountered:
                # Only reachable if the entry became a link between the
                # scandir and the open; the descriptor refused it, which is
                # the point.
                report.skipped_symlinks.append(str(path))
            except OSError as exc:
                report.failures.append(
                    f"{path}: {type(exc).__name__}: {exc}")


def _record(path: Path, report: HardenReport, *, is_dir: bool) -> None:
    try:
        changed = _tighten(path)
    except SymlinkEncountered:
        report.skipped_symlinks.append(str(path))
        return
    except OSError as exc:
        report.failures.append(f"{path}: {type(exc).__name__}: {exc}")
        return
    if changed:
        (report.dirs_changed if is_dir else report.files_changed).append(
            str(path))


def _read_marker() -> dict[str, Any] | None:
    """The recorded migration, or ``None`` if there is not a usable one."""
    try:
        recorded = json.loads(migration_marker_path().read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(recorded, dict):
        return None
    return recorded


def _write_marker(report: HardenReport) -> None:
    """Record that the one-time pass has run, and what it changed."""
    marker = migration_marker_path()
    try:
        private_dir(marker.parent)
        private_write_text(marker, json.dumps({
            "migration": MIGRATION_VERSION,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "roots_present": report.roots_present,
            "paths_changed": report.changed_paths,
            "failures": report.failures,
        }, indent=2) + "\n")
    except OSError as exc:
        # Not fatal: without a marker the next start runs the pass again, which
        # is the behaviour this release is moving away from but is not harmful.
        logger.warning("Per-user state permissions: could not record that the "
                       "one-time pass has run (%s); it will run again at the "
                       "next start.", exc)


def harden_user_state_at_startup() -> HardenReport:
    """Run the one-time migration and report what happened, truthfully.

    Called once from the daemon entry point, and ONCE PER HOME rather than once
    per start. The first run is unconditional, because a home written by an
    earlier release holds world-readable state that nothing else will fix. What
    happens after it is a different question: from then on, anything loose in
    those directories is something the user or another program put there since,
    and re-tightening it every start would silently reverse a sharing decision
    the user is entitled to make on their own machine. New files the product
    itself writes do not need the pass — they are created owner-only.

    A marker in the state directory records that the pass has run, which
    release of the pass it was, and the paths it changed, so what happened is
    recoverable from the machine rather than only from a log. Bumping
    :data:`MIGRATION_VERSION` makes a later release run again.

    The log line states the counts that were actually applied and NAMES the
    paths that changed — it says "already owner-only" only when nothing needed
    changing, and every failure is named rather than folded into a success
    message.
    """
    recorded = _read_marker()
    if recorded is not None and recorded.get("migration", 0) >= MIGRATION_VERSION:
        logger.debug(
            "Per-user state permissions: the one-time pass already ran here "
            "(migration %s, recorded %s); new files are created owner-only, so "
            "it is not repeated.",
            recorded.get("migration"), recorded.get("recorded_at", "unknown"),
        )
        return HardenReport()

    report = harden_user_state()

    if report.failures:
        logger.warning(
            "Per-user state permissions: tightened %d director%s and %d file(s), "
            "but %d path(s) could NOT be adjusted and remain readable by other "
            "local accounts: %s",
            len(report.dirs_changed),
            "y" if len(report.dirs_changed) == 1 else "ies",
            len(report.files_changed),
            len(report.failures), "; ".join(report.failures),
        )
    elif report.changed:
        logger.info(
            "Per-user state permissions tightened to owner-only, once, across "
            "%s: %d director%s and %d file(s) changed: %s",
            ", ".join(report.roots_present) or "no existing tree",
            len(report.dirs_changed),
            "y" if len(report.dirs_changed) == 1 else "ies",
            len(report.files_changed),
            "; ".join(report.changed_paths),
        )
    else:
        logger.debug(
            "Per-user state permissions already owner-only (%d tree(s) checked).",
            len(report.roots_present),
        )

    if report.symlinked_roots:
        logger.warning(
            "Per-user state permissions: %d per-user tree(s) are symbolic "
            "links and were NOT walked, because everything they name is "
            "outside the home directory: %s",
            len(report.symlinked_roots), "; ".join(report.symlinked_roots),
        )

    if report.skipped_symlinks:
        logger.warning(
            "Per-user state permissions: %d symlink(s) left untouched (chmod "
            "follows a symlink, so adjusting one would change a file outside "
            "the tree): %s",
            len(report.skipped_symlinks), "; ".join(report.skipped_symlinks),
        )

    if report.crossed_filesystems:
        logger.warning(
            "Per-user state permissions: %d mounted path(s) left untouched "
            "(what is mounted inside a per-user tree belongs to whoever "
            "mounted it): %s",
            len(report.crossed_filesystems),
            "; ".join(report.crossed_filesystems),
        )

    _write_marker(report)
    return report
