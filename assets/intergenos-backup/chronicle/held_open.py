# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Act on a path an account controls as the object it names.

The engine runs as root and reads, hashes, creates and replaces files under
directories other accounts own: every home it captures and restores, and any
path a capture scope or a diff names. A path there can name a link, a FIFO or
a device as easily as a regular file, and a directory above it can be swapped
for a link between a check and an act. Opening a FIFO or a device runs what
that object does when it is opened, and an act by path after a check by path
lands on whatever the path names at that moment.

So the object is reached and identified before anything acts on it:

  * the directory is walked from / one component at a time, by descriptor,
    never through a link (held_directory), and a missing component is made
    with mkdirat at the descriptor above it when the caller creates;
  * the name is held with O_PATH|O_NOFOLLOW, which reads nothing and runs
    nothing a FIFO or a device does when it is opened (hold_at);
  * the held descriptor's fstat says what the object is, and whatever the
    caller asks of its type, owner, mode or size is asked of that fstat;
  * a regular file is then opened through /proc/self/fd/<the held
    descriptor>, without waiting on anything, and the descriptor that opens
    must name the same device and inode (open_held_at); a directory is opened
    the same way to change its mode or owner (opened_directory); a link's
    target is read from the held link itself (read_link);
  * a file is written as a new name made with O_EXCL|O_NOFOLLOW at the walked
    parent's descriptor and renamed within it (the restore does this).

Nothing is acted on by path a second time. A refusal raises HeldOpenRefused
before any open for reading or writing, so its words can say that nothing was
opened. The same contract is written for intergen as intergen.held_open; the
two packages share no library, so each carries its own copy.
"""

import errno
import os
import stat

_KINDS = (
    (stat.S_ISDIR, "a directory"),
    (stat.S_ISFIFO, "a FIFO"),
    (stat.S_ISSOCK, "a socket"),
    (stat.S_ISCHR, "a character device"),
    (stat.S_ISBLK, "a block device"),
)


class HeldOpenRefused(OSError):
    """The object at a path is not one this act hands on; nothing was opened
    for reading or writing. `refusal` says which check refused it: "path",
    "walk", "link", "kind", "check", "identity" or "owner"."""

    def __init__(self, refusal, message, code=errno.EPERM, where=None):
        super().__init__(code, message)
        self.refusal = refusal
        self.filename = where

    def __str__(self):
        return self.strerror or ""


def kind_of(mode):
    """The object a mode describes, in words."""
    for test, words in _KINDS:
        if test(mode):
            return words
    if stat.S_ISLNK(mode):
        return "a link"
    if stat.S_ISREG(mode):
        return "a regular file"
    return "not a regular file"


def written_out(path):
    """`path` as text, refused unless it is absolute and written out in full
    (no '.', '..', doubled or trailing separator), so the walk acts on exactly
    the components it names."""
    text = os.fspath(path)
    if not os.path.isabs(text) or os.path.normpath(text) != text:
        raise HeldOpenRefused(
            "path", f"{text!r} is not an absolute path written out in full, "
                    f"so nothing was opened.", errno.EINVAL)
    return text


def held_directory(directory, create=False):
    """An O_PATH descriptor on `directory`, an absolute path, walked from /
    one component at a time, by descriptor, never through a link. With
    `create`, a missing component is made with mkdirat at the descriptor above
    it (mode 0777 less the umask, as mkdir by path makes it).

    Raises HeldOpenRefused ("walk") when a component is a link or not a
    directory, and OSError from the walk otherwise (a missing component)."""
    fd = os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    walked = "/"
    try:
        for part in [part for part in os.fspath(directory).split("/") if part]:
            here = os.path.join(walked, part)
            if part in (".", ".."):
                raise HeldOpenRefused(
                    "walk", f"{directory} names {part!r} as a directory; a "
                    f"path is walked only as written out in full, so nothing "
                    f"was opened.", errno.EINVAL, here)
            below = _hold_directory_at(fd, part, here, create)
            os.close(fd)
            fd, walked = below, here
    except BaseException:
        os.close(fd)
        raise
    return fd


def _hold_directory_at(fd, part, here, create):
    for attempt in (1, 2):
        try:
            return os.open(part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
                           | os.O_CLOEXEC, dir_fd=fd)
        except FileNotFoundError:
            if not create or attempt == 2:
                raise
            try:
                os.mkdir(part, 0o777, dir_fd=fd)
            except FileExistsError:
                pass
        except OSError as exc:
            if exc.errno in (errno.ENOTDIR, errno.ELOOP):
                raise HeldOpenRefused(
                    "walk", f"{here} is not a directory reached without a "
                    f"link ({exc.strerror}), so nothing was opened.",
                    exc.errno, here) from exc
            raise
    raise AssertionError("unreachable")  # pragma: no cover


def held_parent(path, create=False):
    """Walk to the directory holding `path` (held_directory). Returns (the
    directory's O_PATH descriptor, the last name, the path as text); the
    caller owns the descriptor."""
    text = written_out(path)
    parent, name = os.path.split(text)
    if not name:
        raise HeldOpenRefused("path", f"{text!r} names no file, so nothing "
                              f"was opened.", errno.EINVAL)
    return held_directory(parent, create=create), name, text


def hold_at(dir_fd, name):
    """Hold `name` in the directory `dir_fd` with O_PATH|O_NOFOLLOW, which
    opens nothing. Returns (the held descriptor, its fstat); the caller owns
    the descriptor. OSError (FileNotFoundError when nothing is there)."""
    held = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
                   dir_fd=dir_fd)
    try:
        return held, os.fstat(held)
    except BaseException:
        os.close(held)
        raise


def read_link(held):
    """The target of the link held by `held` (an O_PATH|O_NOFOLLOW
    descriptor on a link), read from the link itself, not by its name."""
    return os.readlink("", dir_fd=held)


def reopened(held, info, flags, text):
    """Open the object `held` identifies, whose fstat is `info`, through
    /proc/self/fd with `flags`, without waiting on anything, and require the
    same device and inode. Returns (the open descriptor, its fstat)."""
    fd = os.open(f"/proc/self/fd/{held}",
                 flags | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        now = os.fstat(fd)
        if (now.st_dev, now.st_ino) != (info.st_dev, info.st_ino):
            raise HeldOpenRefused(
                "identity", f"{text} opened as another object than the one "
                f"held; nothing was read or written.", errno.ESTALE)
        os.set_blocking(fd, True)
    except BaseException:
        os.close(fd)
        raise
    return fd, now


def open_held_at(dir_fd, name, flags, text, check=None):
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
    held, info = hold_at(dir_fd, name)
    try:
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
        return reopened(held, info,
                        flags & ~(os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW),
                        text)
    finally:
        os.close(held)


def open_held(path, flags, check=None):
    """Walk to `path`'s directory (held_directory) and open the regular file
    there (open_held_at). `path` must be absolute and written out in full."""
    dir_fd, name, text = held_parent(path)
    try:
        return open_held_at(dir_fd, name, flags, text, check)
    finally:
        os.close(dir_fd)


def opened_directory(held, info, text):
    """The directory `held` identifies (fstat `info`), opened for reading
    through the hold so its mode and owner can be set on a descriptor."""
    if not stat.S_ISDIR(info.st_mode):
        raise HeldOpenRefused(
            "kind", f"{text} is {kind_of(info.st_mode)}, not a directory, so "
            f"nothing was opened.", errno.ENOTDIR)
    return reopened(held, info, os.O_RDONLY | os.O_DIRECTORY, text)[0]


def require_owned_tree(path, owners):
    """Refuse a directory another account could change, or change the
    contents of.

    `path` is walked from / by descriptor without following a link; every
    directory on the way, `path` included, must be owned by a uid in
    `owners`. `path` itself must not be writable by its group or by others,
    and a directory above it may be only when its sticky bit is set (another
    account may then add names there but not replace the one walked through,
    which is owned by `owners`). Raises HeldOpenRefused ("walk" or "owner")
    naming the directory that fails."""
    text = written_out(path)
    fd = os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    walked = "/"
    try:
        parts = [part for part in text.split("/") if part]
        for index in range(len(parts) + 1):
            if index:
                here = os.path.join(walked, parts[index - 1])
                below = _hold_directory_at(fd, parts[index - 1], here, False)
                os.close(fd)
                fd, walked = below, here
            info = os.fstat(fd)
            if info.st_uid not in owners:
                raise HeldOpenRefused(
                    "owner", f"{walked} is owned by uid {info.st_uid}, so "
                    f"another account could change what is under {text}.",
                    errno.EPERM, walked)
            open_to_others = info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            last = index == len(parts)
            if open_to_others and (last or not info.st_mode & stat.S_ISVTX):
                raise HeldOpenRefused(
                    "owner", f"{walked} is writable by its group or by other "
                    f"accounts (mode {stat.S_IMODE(info.st_mode):04o}), so "
                    f"another account could change what is under {text}.",
                    errno.EPERM, walked)
    finally:
        os.close(fd)
