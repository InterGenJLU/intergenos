# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Open a path a privileged helper is handed as the object it names.

A path another account names, or one under a directory another account can
change, can name a link, a FIFO or a device as easily as a regular file, and
opening any of those for reading or writing runs what that object does when
it is opened: a writer waiting on a FIFO is let go, a device's driver runs its
open. A check made on the descriptor afterwards comes too late for that, and
a refusal that then says nothing was opened is not true.

So the object is reached and identified before anything opens it:

  * the directory is walked from / one component at a time, by descriptor,
    never through a link (held_directory);
  * the name is held with O_PATH|O_NOFOLLOW, which reads nothing and runs
    nothing a FIFO or a device does when it is opened;
  * that descriptor's fstat must show a regular file, and whatever the caller
    asks of its owner, mode or link count is asked of that fstat;
  * only then is the same inode opened for reading or writing, through
    /proc/self/fd/<the held descriptor>, without waiting on anything, and the
    descriptor that opens must name the same device and inode.

Nothing is opened by path a second time. A refusal raises HeldOpenRefused
before any open for reading or writing, so its words can say that nothing
was opened.
"""
from __future__ import annotations

import errno
import os
import stat
from typing import Callable

_KINDS = (
    (stat.S_ISDIR, "a directory"),
    (stat.S_ISFIFO, "a FIFO"),
    (stat.S_ISSOCK, "a socket"),
    (stat.S_ISCHR, "a character device"),
    (stat.S_ISBLK, "a block device"),
)


class HeldOpenRefused(OSError):
    """The object at a path is not one this open hands on; nothing was opened
    for reading or writing. `refusal` says which check refused it: "path",
    "walk", "link", "kind", "check" or "identity"."""

    def __init__(self, refusal: str, message: str,
                 code: int = errno.EPERM, where: str | None = None) -> None:
        super().__init__(code, message)
        self.refusal = refusal
        self.filename = where

    def __str__(self) -> str:
        return self.strerror or ""


def kind_of(mode: int) -> str:
    """The object a mode describes, in words."""
    for test, words in _KINDS:
        if test(mode):
            return words
    if stat.S_ISLNK(mode):
        return "a link"
    return "not a regular file"


def held_directory(directory: str) -> int:
    """An O_PATH descriptor on `directory`, an absolute path, walked from /
    one component at a time, by descriptor, never through a link.

    Raises HeldOpenRefused ("walk") when a component is a link or not a
    directory, and OSError from the walk otherwise (a missing component)."""
    fd = os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    walked = "/"
    try:
        for part in [part for part in directory.split("/") if part]:
            here = os.path.join(walked, part)
            if part in (".", ".."):
                raise HeldOpenRefused(
                    "walk", f"{directory} names {part!r} as a directory; a "
                    f"path is walked only as written out in full, so nothing "
                    f"was opened.", errno.EINVAL, here)
            try:
                below = os.open(part, os.O_PATH | os.O_DIRECTORY
                                | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            except OSError as exc:
                if exc.errno in (errno.ENOTDIR, errno.ELOOP):
                    raise HeldOpenRefused(
                        "walk", f"{here} is not a directory reached without a "
                        f"link ({exc.strerror}), so nothing was opened.",
                        exc.errno, here) from exc
                raise
            os.close(fd)
            fd, walked = below, here
    except BaseException:
        os.close(fd)
        raise
    return fd


def open_held_at(dir_fd: int, name: str, flags: int, text: str,
                 check: "Callable[[os.stat_result], str | None] | None" = None
                 ) -> "tuple[int, os.stat_result]":
    """Open the regular file `name` in the directory `dir_fd` with `flags`,
    as the object the O_PATH hold identified.

    `text` names the object in a refusal. `check`, given the held object's
    fstat, returns a refusal in the caller's words or None. O_CREAT, O_EXCL
    and O_NOFOLLOW are dropped from `flags` (this opens an existing object
    only, through a descriptor), O_NONBLOCK is added for the open and taken
    off the descriptor after it. Returns (the open descriptor, its fstat); the
    caller owns the descriptor. Raises HeldOpenRefused before any open for
    reading or writing, OSError from the opens themselves (FileNotFoundError
    when nothing is at the name)."""
    held = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
                   dir_fd=dir_fd)
    try:
        info = os.fstat(held)
        if stat.S_ISLNK(info.st_mode):
            raise HeldOpenRefused(
                "link", f"{text} is a link, and a link is never opened; "
                f"nothing was opened.", errno.ELOOP)
        if not stat.S_ISREG(info.st_mode):
            raise HeldOpenRefused(
                "kind", f"{text} is {kind_of(info.st_mode)}, not a regular "
                f"file, so nothing was opened.", errno.EINVAL)
        if check is not None:
            reason = check(info)
            if reason:
                raise HeldOpenRefused("check", reason)
        opened = (flags & ~(os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW)
                  | os.O_NONBLOCK | os.O_CLOEXEC)
        fd = os.open(f"/proc/self/fd/{held}", opened)
        try:
            now = os.fstat(fd)
            if (now.st_dev, now.st_ino) != (info.st_dev, info.st_ino):
                raise HeldOpenRefused(
                    "identity", f"{text} opened as another object than the "
                    f"one held; nothing was read or written.", errno.ESTALE)
            os.set_blocking(fd, True)
        except BaseException:
            os.close(fd)
            raise
        return fd, now
    finally:
        os.close(held)


def open_held(path: str, flags: int,
              check: "Callable[[os.stat_result], str | None] | None" = None
              ) -> "tuple[int, os.stat_result]":
    """Walk to `path`'s directory (held_directory) and open the regular file
    there (open_held_at). `path` must be absolute and written out in full."""
    text = os.fspath(path)
    if not os.path.isabs(text) or os.path.normpath(text) != text:
        raise HeldOpenRefused(
            "path", f"{text!r} is not an absolute path written out in full, "
                    f"so nothing was opened.", errno.EINVAL)
    parent, name = os.path.split(text)
    if not name:
        raise HeldOpenRefused("path", f"{text!r} names no file, so nothing "
                              f"was opened.", errno.EINVAL)
    dir_fd = held_directory(parent)
    try:
        return open_held_at(dir_fd, name, flags, text, check)
    finally:
        os.close(dir_fd)
