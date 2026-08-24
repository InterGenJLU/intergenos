# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Owner-only file transport for a privileged dispatch request.

WHY THIS EXISTS. A privileged dispatch has to carry three things across the
privilege boundary: which tool to run, the arguments to run it with, and the
approval token that proves a person authorized this exact action. `pkexec`
deliberately scrubs the environment as it crosses that boundary, which is why
the released code put all three on the command line — it was the only channel
that survived. But a command line is world-readable through
`/proc/<pid>/cmdline` for as long as the process lives, and this image carries
no `hidepid`, so any local account could read the approval token and the tool
arguments out of the process listing while a dispatch was in flight.

This module changes WHAT is on the command line rather than fighting the
environment scrub. The request goes into a single owner-only file in the user's
runtime directory, and only that file's PATH is passed as an argument. The path
is not a secret; the contents are, and they are protected by file permissions
instead of by hoping nobody reads /proc.

WHERE THE FILE LIVES. `$XDG_RUNTIME_DIR/intergen/`, falling back to the derived
`/run/user/<uid>/intergen/` when the variable is unset. Not `/tmp`: /tmp is
shared, and a shared directory is the wrong home for an approval token even at
mode 0600. The runtime directory is per-user, on tmpfs, and cleared when the
session ends, so a request cannot survive a logout even if something went wrong
enough to leave one behind.

Measured on intergenos-200-r001-1, 2026-08-24: a probe running under the
assistant unit's exact hardening set — NoNewPrivileges=yes, ProtectSystem=strict
and the rest — created a 0600 file in $XDG_RUNTIME_DIR and read it back. The
location is writable to the daemon as the daemon actually runs, not in principle.

THE TWO SIDES.

  write_request()  runs unprivileged, as the assistant. It creates the file with
                   O_CREAT|O_EXCL|O_NOFOLLOW at mode 0600 — restrictive from the
                   first byte, because a file created wide and narrowed
                   afterwards has a window in which it is readable, and a window
                   is exactly what this module exists to close.

  read_request()   runs as root, inside the privileged runner. It is handed a
                   path chosen by the unprivileged side, so it checks what it is
                   about to read rather than trusting it: a regular file, opened
                   without following symlinks, owned by the calling user, mode
                   0600 exactly, and with a single link. Then it reads, then it
                   removes.

ON REMOVAL. A request that outlives its read is an approval token left on disk.
read_request removes the file on the success path and on every refusal that
concerns the file's CONTENT (malformed, truncated, wrong shape, unknown
version). It deliberately does NOT remove a file it refused because the file is
not ours — wrong owner, a symlink, a directory. Deleting those would turn a
boundary check into a deletion primitive aimed at a path the caller chose, which
is the opposite of checking. The unprivileged caller closes that gap from its
own side with discard_request() in a finally block.

ON OVERLAPPING DISPATCHES. Each request gets a name carrying random bytes, and
creation uses O_EXCL, so two dispatches in flight at once cannot collide and a
pre-planted path cannot be written through. Consuming one request has no effect
on any other.

WHAT THIS MODULE DOES NOT DO. It does not authenticate, authorize, or validate
the request's meaning. The token still proves a human approved this exact
(tool, arguments, uid); the allowlist and the per-tool argument schema are still
re-checked root-side by intergen.privileged_dispatch. This module only moves
those values across the boundary without publishing them.
"""

from __future__ import annotations

import errno
import json
import os
import secrets
import stat

__all__ = [
    "FORMAT_VERSION",
    "RUNTIME_SUBDIR",
    "RequestError",
    "discard_request",
    "read_request",
    "request_dir",
    "write_request",
]

#: Subdirectory of the user's runtime directory that holds in-flight requests.
RUNTIME_SUBDIR = "intergen"

#: Filename prefix, so a stray file in the directory is identifiable.
REQUEST_PREFIX = "privileged-request-"

#: On-disk format version. read_request refuses anything else rather than
#: guessing at a payload written by a different version of this code — a
#: misread request is a privileged action running on the wrong arguments.
FORMAT_VERSION = 1

#: Bytes of randomness in a request filename. Enough that two concurrent
#: dispatches never collide; the O_EXCL create is what makes a collision an
#: error rather than an overwrite, so this only has to make collisions rare.
_NAME_RANDOM_BYTES = 16

#: The only mode a request file may carry when it is read.
_REQUIRED_MODE = 0o600

#: The only mode the request directory may carry.
_DIR_MODE = 0o700


class RequestError(Exception):
    """The request could not be written, or could not be trusted and read.

    Every raise site is a refusal to proceed. There is no partial-success path:
    a request we cannot fully validate is a privileged dispatch we do not run.
    """


def request_dir() -> str:
    """Return the directory in-flight requests live in.

    Prefers $XDG_RUNTIME_DIR; otherwise derives /run/user/<uid>. The fallback is
    derived rather than guessed at in /tmp — see the module docstring.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        runtime = f"/run/user/{os.getuid()}"
    return os.path.join(runtime, RUNTIME_SUBDIR)


def _ensure_request_dir() -> str:
    """Create the request directory at mode 0700 and return it.

    An existing directory has its mode asserted rather than assumed: a
    directory another process left group- or world-readable would let someone
    list which privileged actions are in flight, which is information we do not
    owe them even when they cannot read the requests themselves.
    """
    path = request_dir()
    try:
        os.makedirs(path, mode=_DIR_MODE, exist_ok=True)
    except OSError as exc:
        raise RequestError(
            f"cannot create the privileged-request directory {path}: {exc}"
        ) from exc
    try:
        info = os.stat(path)
    except OSError as exc:
        raise RequestError(
            f"cannot stat the privileged-request directory {path}: {exc}"
        ) from exc
    if not stat.S_ISDIR(info.st_mode):
        raise RequestError(
            f"the privileged-request directory {path} is not a directory"
        )
    if stat.S_IMODE(info.st_mode) != _DIR_MODE:
        # makedirs(exist_ok=True) does not re-apply the mode to a directory
        # that already existed, so correct it rather than proceed into it.
        try:
            os.chmod(path, _DIR_MODE)
        except OSError as exc:
            raise RequestError(
                f"the privileged-request directory {path} is mode "
                f"{stat.S_IMODE(info.st_mode):o} and cannot be corrected: {exc}"
            ) from exc
    return path


def _create_request_file(path: str) -> int:
    """Create `path` for writing, owner-only, refusing anything already there.

    O_EXCL makes an existing path an error instead of an overwrite, and
    O_NOFOLLOW refuses a symlink at the final component. Together they mean a
    pre-planted file — created wide by someone else, or pointed somewhere else
    entirely — is a refusal rather than a channel. The 0600 is applied by the
    kernel at creation, so the file is never briefly readable.

    Raises:
        FileExistsError: the path is already taken.
        OSError: any other creation failure.
    """
    return os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        _REQUIRED_MODE,
    )


def write_request(tool_name: str, arguments: dict, token: str) -> str:
    """Write a privileged request and return the path to pass on the argv.

    Args:
        tool_name: the privileged tool to run.
        arguments: the tool's arguments (must be JSON-serialisable).
        token: the human-approval dispatch token binding this exact call.

    Returns:
        Absolute path of the request file. This path is the ONLY thing that
        belongs on a command line.

    Raises:
        RequestError: the request could not be written. The caller fails the
            dispatch closed — a request we could not write is an action we
            cannot carry across the boundary safely.
    """
    directory = _ensure_request_dir()
    payload = {
        "version": FORMAT_VERSION,
        "tool": tool_name,
        "arguments": arguments,
        "token": token,
    }
    try:
        body = json.dumps(payload)
    except (TypeError, ValueError) as exc:
        raise RequestError(
            f"privileged request for {tool_name!r} is not serialisable: {exc}"
        ) from exc

    # Retry only on a name collision, which O_EXCL reports and randomness makes
    # vanishingly rare. Any other error is reported as-is rather than retried:
    # a permission or filesystem failure will not fix itself on a second pass.
    last_exc: OSError | None = None
    for _ in range(8):
        path = os.path.join(
            directory,
            f"{REQUEST_PREFIX}{secrets.token_hex(_NAME_RANDOM_BYTES)}",
        )
        try:
            fd = _create_request_file(path)
        except FileExistsError as exc:
            last_exc = exc
            continue
        except OSError as exc:
            raise RequestError(
                f"cannot create a privileged request in {directory}: {exc}"
            ) from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as fh:
                fh.write(body)
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            # A half-written request must not be left where a reader could pick
            # it up; remove it before reporting.
            discard_request(path)
            raise RequestError(
                f"cannot write the privileged request {path}: {exc}"
            ) from exc
        return path

    raise RequestError(
        f"cannot allocate a privileged request filename in {directory} "
        f"after 8 attempts: {last_exc}"
    )


def discard_request(path: str) -> None:
    """Remove a request if it is still there. Never raises for an absent path.

    The unprivileged caller invokes this in a finally block, so it runs on the
    success path too — where the root side has usually removed the file
    already. Being silent about an absent path is what makes that safe.
    """
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return
        raise


def read_request(path: str, *, expected_uid: int | None = None
                 ) -> tuple[str, dict, str]:
    """Read and remove a privileged request. Runs as root, inside the runner.

    The path comes from the unprivileged side, so everything about the file is
    checked before its contents are believed:

      * opened O_NOFOLLOW|O_RDONLY, so a symlink at the final component is a
        refusal rather than a redirect;
      * a regular file (not a directory, fifo, device or socket);
      * owned by `expected_uid` when one is given — the uid pkexec reports for
        the calling user, so a file planted by anyone else is refused;
      * mode exactly 0600, because a request readable by anyone else has
        already published the token it carries;
      * exactly one link, because a second name for the same inode means
        someone can still read the contents after we unlink our name.

    Args:
        path: the request file, as handed to the runner on its command line.
        expected_uid: the uid that must own the file, or None to skip the
            ownership check (used only where there is no calling user to
            compare against).

    Returns:
        (tool_name, arguments, token).

    Raises:
        RequestError: the request is absent, untrustworthy, or malformed. A
            refusal driven by the file's CONTENT removes the file; a refusal
            driven by the file not being ours leaves it alone — see the module
            docstring.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise RequestError(
                f"privileged request {path} is a symbolic link; refusing"
            ) from exc
        raise RequestError(
            f"cannot open privileged request {path}: {exc}"
        ) from exc

    try:
        info = os.fstat(fd)

        if not stat.S_ISREG(info.st_mode):
            raise RequestError(
                f"privileged request {path} is not a regular file; refusing"
            )
        if expected_uid is not None and info.st_uid != expected_uid:
            raise RequestError(
                f"privileged request {path} is owned by uid {info.st_uid}, "
                f"not the calling user {expected_uid}; refusing"
            )
        if stat.S_IMODE(info.st_mode) != _REQUIRED_MODE:
            raise RequestError(
                f"privileged request {path} is mode "
                f"{stat.S_IMODE(info.st_mode):o}, not {_REQUIRED_MODE:o}; "
                f"its contents may already have been read by another account; "
                f"refusing"
            )
        if info.st_nlink != 1:
            raise RequestError(
                f"privileged request {path} has {info.st_nlink} links; another "
                f"name for the same file would outlive our removal; refusing"
            )

        try:
            with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as fh:
                body = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            discard_request(path)
            raise RequestError(
                f"cannot read privileged request {path}: {exc}"
            ) from exc

        # From here the file is ours and has been read: every remaining exit,
        # success or refusal, removes it.
        discard_request(path)

        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, ValueError) as exc:
            raise RequestError(
                f"privileged request {path} is not valid JSON: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise RequestError(
                f"privileged request {path} must be a JSON object; got "
                f"{type(payload).__name__}"
            )
        if payload.get("version") != FORMAT_VERSION:
            raise RequestError(
                f"privileged request {path} declares format version "
                f"{payload.get('version')!r}; this runner speaks version "
                f"{FORMAT_VERSION}. Refusing rather than guessing at the "
                f"payload of a privileged action."
            )

        missing = [k for k in ("tool", "arguments", "token") if k not in payload]
        if missing:
            raise RequestError(
                f"privileged request {path} is missing {', '.join(missing)}"
            )

        tool_name = payload["tool"]
        arguments = payload["arguments"]
        token = payload["token"]

        if not isinstance(tool_name, str) or not tool_name:
            raise RequestError(
                f"privileged request {path}: tool must be a non-empty string"
            )
        if not isinstance(arguments, dict):
            raise RequestError(
                f"privileged request {path}: arguments must be a JSON object; "
                f"got {type(arguments).__name__}"
            )
        if not isinstance(token, str) or not token:
            raise RequestError(
                f"privileged request {path}: token must be a non-empty string"
            )

        return tool_name, arguments, token
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
