# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Owner-only creation of per-user state.

WHY THIS MODULE EXISTS. InterGen keeps conversation transcripts, a
personal-fact database, a decision trace, a tool-dispatch ledger, the web-auth
token and the dispatch signing key under the user's home directory. Created
through a bare ``Path.mkdir()`` or a bare ``open(path, "a")`` those land
``0755`` and ``0644`` under the ordinary ``umask 0022`` — readable by every
other local account on the machine.

The mode is therefore set AT CREATION, never applied afterwards. ``os.mkdir``
and ``os.open`` both take the mode as an argument, and because ``0700`` and
``0600`` carry no group or other bits, no umask can loosen them: umask only
removes bits. A create-then-chmod sequence would leave a window in which the
file already holds its first record and is still world-readable, which is the
window this module exists to close.

Nothing here widens a mode. ``_tighten`` only ever removes group and other
bits, so a directory an administrator has deliberately made stricter than
``0700`` is left as it is.

WHAT IS AND IS NOT OWNED. The three trees below belong to InterGen end to end
and everything inside them is created owner-only. Their XDG parents —
``~/.local``, ``~/.local/share``, ``~/.local/state``, ``~/.config`` — are
shared with every other application on the machine and are NEVER touched:
creating them is left to the platform default, exactly as any other program
would leave them.
"""

from __future__ import annotations

import logging
import os
import stat
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


def owned_roots() -> tuple[Path, ...]:
    """Every per-user tree InterGen owns end to end.

    Both the XDG-resolved and the plain ``~/.local`` forms are reported when
    they differ: modules in this package resolve their paths both ways, so a
    sweep that only walked one form could leave a real directory untouched.
    Duplicates are collapsed and order is stable.
    """
    candidates: list[Path] = [state_dir_path(), data_dir_path(),
                              config_dir_path()]
    home = Path.home()
    candidates += [
        home / ".local" / "state" / _TREE_NAME,
        home / ".local" / "share" / _TREE_NAME,
        home / ".config" / _TREE_NAME,
    ]
    seen: dict[Path, None] = {}
    for path in candidates:
        seen.setdefault(path, None)
    return tuple(seen)


# ── Mode enforcement ──────────────────────────────────────────────────────


def _tighten(path: Path) -> bool:
    """Remove every group and other bit from *path*. True if it changed.

    This ONLY removes access; it never grants any. The owner bits are left
    exactly as they are, so a directory someone has deliberately made stricter
    than ``0700`` — say ``0500`` — keeps that choice, and a read-only state
    file stays read-only. Confidentiality is what this function is for;
    granting the daemon access it does not have is a different question, and
    silently answering it here would override a decision the user made.

    New files and directories do not rely on this at all: :data:`DIR_MODE` and
    :data:`FILE_MODE` are passed to ``mkdir(2)`` and ``open(2)`` so they are
    correct the instant they exist.
    """
    current = stat.S_IMODE(path.lstat().st_mode)
    wanted = current & ~_FORBIDDEN_BITS
    if current == wanted:
        return False
    os.chmod(path, wanted)
    return True


def _opener(path: str, flags: int) -> int:
    """``open()`` opener that creates with :data:`FILE_MODE`."""
    return os.open(path, flags, FILE_MODE)


# ── Creation helpers ──────────────────────────────────────────────────────


def private_dir(path: Path | str) -> Path:
    """Create *path* as an owner-only directory and return it.

    The mode is passed to ``mkdir(2)`` so a newly created directory is never
    briefly world-listable. Any InterGen-owned directory between one of the
    :func:`owned_roots` and *path* is tightened too, which is what a bare
    ``mkdir(parents=True)`` cannot do: ``parents=True`` creates intermediate
    directories at the platform default, not at the mode you asked for.

    A path outside every owned root — a caller-supplied test location — has
    only its final component tightened. Shared XDG parents are never touched.
    """
    path = Path(path)
    path.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    _tighten(path)

    for root in owned_roots():
        if root != path and root not in path.parents:
            continue
        if root.is_dir():
            _tighten(root)
        current = root
        for part in path.relative_to(root).parts:
            current = current / part
            if current.is_dir():
                _tighten(current)
        break
    return path


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
    _tighten(path)
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
        _tighten(path)
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
            _tighten(Path(self.baseFilename))
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
            _tighten(Path(self.baseFilename))
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
    failures: list[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return len(self.dirs_changed) + len(self.files_changed)

    @property
    def clean(self) -> bool:
        return not self.failures


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

    Symlinks are recorded and skipped: ``chmod`` follows a symlink, so acting
    on one would change the mode of a file outside the tree.

    Never raises. A path that cannot be adjusted is recorded in
    ``failures`` and the walk continues, so one unwritable file cannot stop
    the daemon from starting or leave the rest of the tree untouched.
    """
    report = HardenReport()
    targets = tuple(roots) if roots is not None else owned_roots()

    for root in targets:
        try:
            if not root.is_dir():
                continue
        except OSError as exc:
            report.failures.append(f"{root}: {type(exc).__name__}: {exc}")
            continue
        report.roots_present.append(str(root))

        entries: list[Path] = [root]
        try:
            entries.extend(sorted(root.rglob("*")))
        except OSError as exc:
            report.failures.append(f"{root}: walk failed: "
                                   f"{type(exc).__name__}: {exc}")

        for entry in entries:
            try:
                if entry.is_symlink():
                    report.skipped_symlinks.append(str(entry))
                    continue
                mode = entry.lstat().st_mode
                if stat.S_ISDIR(mode):
                    if _tighten(entry):
                        report.dirs_changed.append(str(entry))
                elif stat.S_ISREG(mode):
                    if _tighten(entry):
                        report.files_changed.append(str(entry))
            except OSError as exc:
                report.failures.append(
                    f"{entry}: {type(exc).__name__}: {exc}")

    return report


def harden_user_state_at_startup() -> HardenReport:
    """Run the migration and report what happened, truthfully.

    Called once from the daemon entry point. The log line states the counts
    that were actually applied — it says "already owner-only" only when
    nothing needed changing, and every failure is named rather than folded
    into a success message.
    """
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
            "Per-user state permissions tightened to owner-only: %d "
            "director%s, %d file(s) across %s.",
            len(report.dirs_changed),
            "y" if len(report.dirs_changed) == 1 else "ies",
            len(report.files_changed),
            ", ".join(report.roots_present) or "no existing tree",
        )
    else:
        logger.debug(
            "Per-user state permissions already owner-only (%d tree(s) checked).",
            len(report.roots_present),
        )

    if report.skipped_symlinks:
        logger.warning(
            "Per-user state permissions: %d symlink(s) left untouched (chmod "
            "follows a symlink, so adjusting one would change a file outside "
            "the tree): %s",
            len(report.skipped_symlinks), "; ".join(report.skipped_symlinks),
        )

    return report
