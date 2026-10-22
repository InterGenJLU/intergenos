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

THE ENVIRONMENT IS WRITTEN HERE. A transient service inherits the user
manager's environment block, which any process of the account can change
without asking anyone, and the daemon's own environment came from the same
block. A value there can name a program a command then runs (BASH_ENV,
NODE_OPTIONS, RIPGREP_CONFIG_PATH, a PATH with the account's directory first,
LD_PRELOAD) or a destination it contacts (CUPS_SERVER). So a command the
executor runs gets an environment written here and nothing inherited
(tool_environment): the service unsets glibc's unsafe variables
(LOADER_UNSAFE_VARIABLES) for env itself, and env -i starts the command with
the allowlist alone, its values read from the password database and the
account's id, never from the environment, and the program resolved on the
fixed TOOL_PATH. An MCP server's service unsets the same list, and a
launcher starts the server with the allowlist and its configured names
alone (intergen/mcp_client.py).

DROP-INS ARE REFUSED. A drop-in directory in the user manager's unit search
path applies to every service whose name it matches (service.d to every
service, intergen-.service.d and intergen-tool-.service.d to every tool's), and
one in a directory the account can write is anyone's to fill. The launcher
reads the search path the manager actually uses, at every launch, and refuses
the start, naming the directory, when such a drop-in exists where the account
can write (check_unit_dropins).

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

import json
import logging
import os
import pwd
import re
import secrets
import subprocess
from pathlib import Path
from urllib.parse import unquote

from intergen import dispatch_token

logger = logging.getLogger(__name__)

SYSTEMD_RUN = "/usr/bin/systemd-run"
SYSTEMCTL = "/usr/bin/systemctl"
BUSCTL = "/usr/bin/busctl"
ENV = "/usr/bin/env"

#: The PATH a command is resolved on and runs with: systemd's own default for a
#: service on a merged /usr, every directory of it root's. The daemon's
#: inherited PATH is never read for a tool: a directory the account can write,
#: placed first in it, would decide which program a name starts.
TOOL_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"

#: glibc's own list of unsafe environment variables (UNSECURE_ENVVARS in
#: sysdeps/generic/unsecvars.h at glibc 2.43, the 34 names its loader carries).
#: env is dynamically linked, so a loader variable in the service's environment
#: (LD_PRELOAD, LD_AUDIT) would run inside env before env -i cleared anything:
#: the service unsets these first, and neither env nor the command sees them.
LOADER_UNSAFE_VARIABLES = (
    "GCONV_PATH", "GETCONF_DIR", "GLIBC_TUNABLES", "HOSTALIASES", "LD_AUDIT",
    "LD_BIND_NOT", "LD_BIND_NOW", "LD_DEBUG", "LD_DEBUG_OUTPUT",
    "LD_DYNAMIC_WEAK", "LD_LIBRARY_PATH", "LD_ORIGIN_PATH", "LD_PRELOAD",
    "LD_PROFILE", "LD_PROFILE_OUTPUT", "LD_SHOW_AUXV", "LD_VERBOSE", "LD_WARN",
    "LOCALDOMAIN", "LOCPATH", "MALLOC_ARENA_MAX", "MALLOC_ARENA_TEST",
    "MALLOC_MMAP_MAX_", "MALLOC_MMAP_THRESHOLD_", "MALLOC_PERTURB_",
    "MALLOC_TOP_PAD_", "MALLOC_TRACE", "MALLOC_TRIM_THRESHOLD_", "NIS_PATH",
    "NLSPATH", "RESOLV_HOST_CONF", "RES_OPTIONS", "TMPDIR", "TZDIR",
)

#: Settings every command runs with, each closing a configuration read that
#: would start a program or contact a destination no one asked for: npm list's
#: update check contacts a registry the configuration may name; lpstat and lpq
#: ask the local scheduler, never a ServerName from the home; python runs
#: nothing from the user site (a .pth file, usercustomize).
TOOL_PINS = (
    ("npm_config_update_notifier", "false"),
    ("CUPS_SERVER", "/run/cups/cups.sock"),
    ("PYTHONNOUSERSITE", "1"),
)

#: A locale value carried into a command's environment: a name, never a path
#: (glibc reads a locale whose name holds a slash from that file).
_LOCALE_VALUE = re.compile(r"[A-Za-z0-9_.@-]+\Z")

#: Drop-in directories the user manager applies to a tool's service: the
#: top-level one for every service, and the prefix ones of the tool services'
#: names (systemd reads a drop-in for each dash-ended prefix of a unit's name).
TOOL_UNIT_DROPINS = ("service.d", "intergen-.service.d", "intergen-tool-.service.d")

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
    or any service on the owner's session bus, to act for it.

    The account's own runtime directory always, since the environment a
    command is written points there (tool_environment); then any other the
    daemon's environment names.
    """
    paths: list[Path] = []
    for runtime in (f"/run/user/{os.getuid()}", os.environ.get("XDG_RUNTIME_DIR")):
        if not runtime:
            continue
        for path in (Path(runtime, "systemd", "private"), Path(runtime, "bus")):
            if path not in paths:
                paths.append(path)
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


def tool_environment() -> dict[str, str]:
    """The whole environment a command the executor runs starts with.

    Written, never inherited: the account's name and home from the password
    database, its runtime directory and session bus by its id, the fixed
    TOOL_PATH, TERM=dumb (no terminal is ever attached), the daemon's LANG and
    LC_* only when each is a locale name, and TOOL_PINS. Raises
    ToolProcessNotStarted when the account has no entry to write it from.
    """
    uid = os.getuid()
    try:
        entry = pwd.getpwuid(uid)
    except KeyError as exc:
        raise ToolProcessNotStarted(
            f"account {uid} has no entry in the password database, so the "
            f"command's environment could not be written and the tool's "
            f"process was not started") from exc
    env = {
        "PATH": TOOL_PATH,
        "HOME": entry.pw_dir,
        "USER": entry.pw_name,
        "LOGNAME": entry.pw_name,
        "XDG_RUNTIME_DIR": f"/run/user/{uid}",
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus",
        "TERM": "dumb",
    }
    for name, value in sorted(os.environ.items()):
        if re.fullmatch(r"LANG|LC_[A-Z]+", name) and _LOCALE_VALUE.match(value):
            env[name] = value
    env.update(TOOL_PINS)
    return env


def unit_search_path() -> list[str]:
    """The unit search path the user manager uses, asked of the manager itself.

    Read at every launch from the manager's UnitPath, over the session bus at
    the account's own runtime directory (never an address the environment
    names), so a directory added to the path is read as the manager reads it.
    Raises ToolProcessNotStarted when the manager does not answer.
    """
    command = [BUSCTL, f"--address=unix:path=/run/user/{os.getuid()}/bus",
               "--json=short", "get-property", "org.freedesktop.systemd1",
               "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager",
               "UnitPath"]
    why = None
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, check=False,
            timeout=CLIENT_GRACE_SECONDS, stdin=subprocess.DEVNULL,
            env={"PATH": TOOL_PATH, "LANG": "C"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        why = str(exc)
    else:
        try:
            reply = json.loads(done.stdout) if done.returncode == 0 else None
        except ValueError:
            reply = None
        paths = reply.get("data") if isinstance(reply, dict) else None
        if (isinstance(paths, list) and paths
                and all(isinstance(p, str) and p.startswith("/") for p in paths)):
            return paths
        why = (done.stderr.strip() or f"exit {done.returncode}, no path list")
    raise ToolProcessNotStarted(
        f"the user manager's unit search path could not be read ({why}), so "
        f"the drop-ins that would apply to the tool's service are unknown and "
        f"the tool's process was not started")


def check_unit_dropins(unit: str) -> None:
    """Refuse a tool's service that a drop-in the account can write would change.

    A drop-in directory named for every service (service.d), for the tool
    services' name prefixes, or for this unit, applies to the service it
    matches; one where the account can write is anyone's to fill. Such a
    directory refuses the start and is named. One that only root can write is
    the administrator's, and is logged.
    """
    for directory in unit_search_path():
        for name in TOOL_UNIT_DROPINS + (f"{unit}.d",):
            path = os.path.join(directory, name)
            if not os.path.lexists(path):
                continue
            if os.access(directory, os.W_OK) or os.access(path, os.W_OK):
                raise ToolProcessNotStarted(
                    f"{path} is a drop-in directory in the user manager's unit "
                    f"search path, where this account can write, and what it "
                    f"sets would apply to the tool's service, so the tool's "
                    f"process was not started. Remove it if you did not create "
                    f"it.")
            logger.warning(
                "%s applies to every tool service; only root can write it, so "
                "it is read as the administrator's", path)


def launch_argv(argv: list[str], *, unit: str,
                runtime_max_sec: int | None = None,
                setenv_names: tuple[str, ...] = (),
                write_environment: bool = False) -> list[str]:
    """The command line that starts `argv` as a tool service behind the boundary.

    With `write_environment` (every command the executor runs), the service
    unsets LOADER_UNSAFE_VARIABLES and env -i starts the command with
    tool_environment() and nothing else, so no value of the user manager's
    block reaches it; `argv[0]` must then be a path (resolved on TOOL_PATH by
    the caller) with no "=" in it, which env would read as an assignment. For
    an MCP server, intergen/mcp_client.py hands its configured variables over
    by name (`setenv_names`) and adds to this command line the written
    environment, the unsetting of LOADER_UNSAFE_VARIABLES and a launcher that
    keeps only those names.

    Raises ToolProcessNotStarted when a credential file is absent, cannot be
    read or cannot be named, when the program cannot be named to env, or when
    a drop-in the account can write would apply to the service
    (check_unit_dropins), so nothing is started without its boundary.
    """
    if not argv:
        raise ToolProcessNotStarted("an empty command has no process to start")
    if write_environment and (not argv[0].startswith("/") or "=" in argv[0]):
        raise ToolProcessNotStarted(
            f"{argv[0]!r} is not a path env can start as the program, so the "
            f"tool's process was not started")
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
        except FileNotFoundError:
            missing.append(str(path))
        except OSError as exc:
            # Present or not, a path that cannot be read (a path that runs
            # through a file included) cannot be masked with certainty, so the
            # start is refused with what actually happened.
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
    environment = tool_environment() if write_environment else None
    check_unit_dropins(unit)
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
    if environment is None:
        return run + ["--"] + list(argv)
    run.append("--property=UnsetEnvironment=" + " ".join(LOADER_UNSAFE_VARIABLES))
    return (run + ["--", ENV, "-i"]
            + [f"{name}={value}" for name, value in environment.items()]
            + list(argv))


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
