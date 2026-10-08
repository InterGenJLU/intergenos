# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every process the assistant starts for a tool starts where the install's
credential files cannot be opened.

WHY A PROCESS BOUNDARY. A tool that runs as its own process performs its own
opens. The dispatcher's screen sees only a call's arguments, and the file
boundary in read_file, analyze_file and write_file decides only the opens those
tools make in the daemon, so neither can see what run_command's child or an MCP
server opens: a parent-directory spelling, a symbolic link, or an ordinary
containing directory from which the process derives the protected child itself.
Only a boundary around the process answers all of those, because there the
kernel decides every open.

THE BOUNDARY. Such a process starts as a transient service of the owner's own
systemd user manager, through the launcher the privileged dispatch already uses
(systemd-run --user), with InaccessiblePaths= naming the install's two
credential files (intergen.dispatch_token.SECRET_RELATIVE_PATHS under the owning
account's home). systemd mounts an inaccessible node over each of them in the
service's own mount namespace, so every open of either file from inside the
service fails, whatever spelling or derivation reached it, and the service holds
nothing that could remove the mount: no capabilities, no new namespaces, no new
privileges. The service also runs in a user namespace of its own, so the kernel
refuses it /proc/<pid>/root, cwd and fd of every process outside it, which would
otherwise spell the same file through a process that sees it unmasked.

THE MANAGER IS OUT OF REACH. The same property makes the owner's user-manager
control socket and session bus socket inaccessible in the service, because a
process that reached either could ask the user manager to start another service,
one without the mask. A command that does that is never read-only, and
run_command sends every command that is not read-only to the review surface and
the privileged runner; an MCP server's tools are not in that tier, though, and
the gate can run them without review on a user-direct or user-implied call, so
the route is closed at the process rather than left to the review.

THE SAME HARDENING AS THE DAEMON. A transient service does not inherit the
daemon's sandbox; it gets what it is given. It is given the daemon unit's own
hardening lines, verbatim (DAEMON_HARDENING, held equal to
intergen/data/intergen.service by a test, so a line added to the unit fails that
test until it is added here). When the daemon itself runs as a service of the
same manager, the tool's service joins its private /tmp, so a tool sees the
/tmp it saw as the daemon's child.

IT FAILS CLOSED. A credential file that is absent cannot be masked, and systemd
refuses to start a service whose mask it cannot make, so the launcher checks
first and names the missing file; a credential path that cannot be read at all
is refused with that reason instead. Nothing is started directly in either case.
If the user manager cannot be reached, the tool's process does not run.

WHAT IT DOES NOT COVER, named rather than claimed:
  * a second name (a hard link) for either file made before this boundary
    existed reaches the same bytes under a path the mask does not name. The first
    launch in each daemon reads back both files' link counts and logs a warning
    when either is not 1; it does not refuse.
  * a process outside the service that a tool's process can still reach some
    other way, such as a terminal multiplexer's socket or an X11 client, could be
    driven to open the file on the tool's behalf.

COST. One round trip to the user manager per command, about 36 ms measured on a
reference machine against under 2 ms for a direct start, and one per MCP server
start.
"""

from __future__ import annotations

import logging
import os
import secrets
import subprocess
from pathlib import Path
from urllib.parse import unquote

from intergen import dispatch_token

logger = logging.getLogger(__name__)

SYSTEMD_RUN = "/usr/bin/systemd-run"
SYSTEMCTL = "/usr/bin/systemctl"

#: The daemon unit's hardening lines, verbatim and in order, from the [Service]
#: section of intergen/data/intergen.service. A test holds the two equal.
DAEMON_HARDENING = (
    "NoNewPrivileges=yes",
    "PrivateTmp=yes",
    "PrivateDevices=no",
    "DevicePolicy=closed",
    "DeviceAllow=char-drm rw",
    "DeviceAllow=/dev/nvidiactl rw",
    "DeviceAllow=/dev/nvidia0 rw",
    "DeviceAllow=/dev/nvidia-uvm rw",
    "DeviceAllow=/dev/nvidia-uvm-tools rw",
    "ProtectSystem=strict",
    "ProtectKernelTunables=yes",
    "ProtectKernelModules=yes",
    "ProtectKernelLogs=yes",
    "ProtectControlGroups=yes",
    "ProtectClock=yes",
    "ProtectHostname=yes",
    "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
    "RestrictNamespaces=yes",
    "RestrictRealtime=yes",
    "RestrictSUIDSGID=yes",
    "LockPersonality=yes",
    "SystemCallArchitectures=native",
    "SystemCallFilter=@system-service",
    "SystemCallFilter=~@privileged",
    "SystemCallFilter=~@resources",
    "SystemCallFilter=sched_setaffinity",
    "SystemCallFilter=sched_setscheduler",
    "SystemCallFilter=setpriority",
    "SystemCallFilter=mbind",
    "CapabilityBoundingSet=",
    "AmbientCapabilities=",
)

#: How long the waiting client allows beyond the service's own RuntimeMaxSec
#: before it gives up on the user manager. The service's bound is what ends a
#: command that runs too long; this only keeps a manager that never answers
#: from holding the daemon.
CLIENT_GRACE_SECONDS = 10

_LINK_COUNTS_READ_BACK = False


class ToolProcessNotStarted(RuntimeError):
    """The tool's process was not started, because the boundary could not be made."""


def credential_paths() -> tuple[Path, ...]:
    """The install's credential files, at the owning account's home."""
    return dispatch_token.install_secret_paths()


def manager_socket_paths() -> tuple[Path, ...]:
    """The sockets through which a process could ask the owner's user manager,
    or any service on the owner's session bus, to act for it."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    paths = [Path(runtime, "systemd", "private"), Path(runtime, "bus")]
    for address in os.environ.get("DBUS_SESSION_BUS_ADDRESS", "").split(";"):
        transport, _, params = address.partition(":")
        if transport != "unix":
            continue
        for param in params.split(","):
            key, _, value = param.partition("=")
            if key == "path" and value:
                path = Path(unquote(value))
                if path not in paths:
                    paths.append(path)
    return tuple(paths)


def _quoted(path: Path, *, if_present: bool = False) -> str:
    """One InaccessiblePaths= entry, quoted so a path with a space stays one path.

    A double quote or a backslash in the path has no spelling here that this
    module has measured, so such a path refuses the start instead of being
    passed in a form systemd might read as another path.
    """
    text = str(path)
    if not text.startswith("/") or '"' in text or "\\" in text:
        raise ToolProcessNotStarted(
            f"{text!r} cannot be named to systemd as one path, so the tool's "
            f"process was not started")
    return f'"{"-" if if_present else ""}{text}"'


def own_service_unit() -> str | None:
    """The service of this account's user manager that this process runs in.

    Read from the process's own cgroup. None when the process is not in a service
    of its own account's user manager (a test run from a terminal, for one); the
    tool's service then keeps a private /tmp of its own.
    """
    try:
        text = Path("/proc/self/cgroup").read_text(encoding="utf-8")
    except OSError:
        return None
    manager = f"/user@{os.getuid()}.service/"
    for line in text.splitlines():
        if not line.startswith("0::") or manager not in line:
            continue
        leaf = line[3:].rstrip("/").rsplit("/", 1)[-1]
        if leaf.endswith(".service"):
            return leaf
    return None


def new_unit_name() -> str:
    """A fresh name for one tool process's service."""
    return f"intergen-tool-{secrets.token_hex(8)}.service"


def read_back_link_counts(paths: tuple[Path, ...] | None = None) -> list[str]:
    """Once per process: say so when a credential file has a second name.

    The mask names paths, so a hard link made before the boundary existed would
    still reach the same file. This reads the link count back and logs a warning
    for each file whose count is not 1. It refuses nothing.
    """
    global _LINK_COUNTS_READ_BACK
    if _LINK_COUNTS_READ_BACK:
        return []
    _LINK_COUNTS_READ_BACK = True
    warnings = []
    for path in credential_paths() if paths is None else paths:
        try:
            count = os.stat(path, follow_symlinks=False).st_nlink
        except OSError:
            continue
        if count != 1:
            message = (
                f"{path} has {count} names on disk. The tool-process boundary "
                f"masks this path only, so a tool's process can read the same "
                f"file through any other name. Remove the other names, or "
                f"generate a new credential.")
            logger.warning(message)
            warnings.append(message)
    return warnings


def launch_argv(argv: list[str], *, unit: str,
                runtime_max_sec: int | None = None,
                setenv_names: tuple[str, ...] = ()) -> list[str]:
    """The command line that starts `argv` as a tool service behind the boundary.

    Raises ToolProcessNotStarted when a credential file is absent, cannot be
    read or cannot be named, so nothing is started without its mask.
    """
    if not argv:
        raise ToolProcessNotStarted("an empty command has no process to start")
    try:
        credentials = credential_paths()
    except dispatch_token.InstallOwnerUnresolved as exc:
        raise ToolProcessNotStarted(
            f"the account that owns this install could not be established "
            f"({exc}), so there is nothing to mask and the tool's process was "
            f"not started") from exc
    missing = []
    for path in credentials:
        try:
            os.lstat(path)
        except (FileNotFoundError, NotADirectoryError):
            missing.append(str(path))
        except OSError as exc:
            # Present or not, a path that cannot be read cannot be masked with
            # certainty, so the start is refused with what actually happened.
            raise ToolProcessNotStarted(
                f"{path} could not be read ({exc.strerror or exc}). A credential "
                f"file whose path cannot be read cannot be masked, so the tool's "
                f"process was not started.") from exc
    if missing:
        raise ToolProcessNotStarted(
            f"{', '.join(missing)} does not exist. A credential file that is "
            f"absent cannot be masked, so the tool's process was not started; "
            f"running intergen setup creates it.")
    read_back_link_counts(credentials)
    run = [SYSTEMD_RUN, "--user", "--quiet", "--collect", "--wait", "--pipe",
           "--same-dir", "--expand-environment=no", f"--unit={unit}"]
    run += [f"--property={line}" for line in DAEMON_HARDENING]
    run += [f"--property=InaccessiblePaths={_quoted(p)}" for p in credentials]
    run += [f"--property=InaccessiblePaths={_quoted(p, if_present=True)}"
            for p in manager_socket_paths()]
    if runtime_max_sec is not None:
        run.append(f"--property=RuntimeMaxSec={int(runtime_max_sec)}")
    joined = own_service_unit()
    if joined:
        run.append(f"--property=JoinsNamespaceOf={joined}")
    # A name alone: systemd-run takes the value from its own environment, so a
    # value never appears in a process listing.
    run += [f"--setenv={name}" for name in setenv_names]
    return run + ["--"] + list(argv)


def stop_unit(unit: str) -> None:
    """Stop a tool's service whose waiting client has gone. Best effort, logged.

    The service outlives its client by design, so a client that is killed or
    gives up leaves the service running until it is stopped here.
    """
    try:
        completed = subprocess.run(
            [SYSTEMCTL, "--user", "stop", unit],
            capture_output=True, text=True, timeout=CLIENT_GRACE_SECONDS,
            check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("could not stop the tool service %s: %s", unit, exc)
        return
    if completed.returncode != 0:
        logger.warning("stopping the tool service %s exited %d: %s", unit,
                       completed.returncode, completed.stderr.strip())
