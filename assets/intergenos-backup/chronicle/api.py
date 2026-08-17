# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The engine's local IPC surface (spec §1).

A SOCK_STREAM Unix domain socket at /run/chronicle/engine.sock, line-delimited
JSON request/response. Both clients — the user-facing CLI/GUI and the automation
sentinel/pkm hook — consume exactly these verbs; the engine holds all policy.

A request is one JSON line: {"verb": "...", "args": {...}}. The response is one
JSON line: {"ok": true, "result": ...} or {"ok": false, "error": "..."}.

The socket is root-owned, mode 0660, group `chronicle`, so only root and the
accounts placed in that group can open it at all. Two polkit actions then
authorize a desktop user's verbs (the recipe ships
org.intergenos.Chronicle.policy): a read action for backup health and version
metadata, and an administrative action for everything that changes state or that
could disclose another user's paths or file content.

dispatch() is pure over (engine, request), so the whole verb surface is
unit-testable without a real socket; serve() is the thin socket loop.
"""

import grp
import json
import os
import socket
import stat
import struct
import subprocess
import sys as _sys
from pathlib import Path

from . import engine as _engine
from . import paths as _paths

# TWO polkit actions, because "may I see whether my backups are healthy" and
# "may I restore over the filesystem" are not the same question. The recipe
# ships org.intergenos.Chronicle.policy declaring both; the daemon enforces them
# per request against the peer's credentials (below). Root peers (the CLI under
# sudo, the pkm pre-transaction handler) are already privileged and skip the
# check; every non-root peer must be polkit-authorized for the tier its verb
# needs, or the request is refused — fail-closed, never fail-open.
#
# Until 2026-08-03 there was one action and every verb sat behind it, so reading
# a status line raised an administrator password dialog. That is not just
# friction: teaching a user to clear administrator prompts for routine, harmless
# reads is itself a security failure, because it trains the reflex that an
# attacker needs.
POLKIT_ACTION_ID = "org.intergenos.Chronicle.manage"
POLKIT_ACTION_READ = "org.intergenos.Chronicle.read"

# The group that may open the engine socket at all.
#
# Polkit answers "may this user do this thing"; it cannot answer "may this
# process reach the daemon", because by the time polkit is consulted the caller
# is already connected. Until 2026-08-04 the socket was mode 0666, so every
# local process — every browser tab's helper, every script a user was talked
# into running — could connect and speak to the backup engine, and could make an
# administrator password dialog appear in the logged-in session as often as it
# liked. The group is the floor under the polkit tiers: the kernel refuses the
# connect() for anyone outside it, before a byte is read.
#
# The group is created declaratively by the sysusers.d fragment the package
# ships (/usr/lib/sysusers.d/chronicle.conf), and the console user is placed in
# it by the installer at account creation.
ENGINE_SOCKET_GROUP = "chronicle"

# The socket's final mode: owner (root) and group read+write, nothing for other.
ENGINE_SOCKET_MODE = 0o660

# The runtime directory holding the socket. 0750 keeps a non-member from even
# traversing to the socket — a second, independent gate, so a future change that
# got the socket mode wrong would still not expose it.
ENGINE_RUNTIME_DIR_MODE = 0o750

# Test seam: set to a callable(group_name) -> gid to bypass the real group
# database. Returns None to simulate a group that does not exist.
_GROUP_RESOLVER = None

# Verbs served under the READ action. The test for membership is not "does this
# verb write" — it is "can its response disclose another user's file paths or
# file contents". The store is shared: config.user_data_paths defaults to
# ["/home"], so ONE userdata store holds EVERY user's home directory with no
# per-user partition. Any response carrying a path from that store is another
# user's business.
#
#   status        — target identity, whether the target is attached, free bytes,
#                   last-capture time, clock-skew events, pinned version ids,
#                   and a queue COUNT plus a human sentence. Machine-level backup
#                   health. Path-bearing fields are stripped (see _redact_for_read).
#   queue-status  — the same queue view, same stripping.
#   list          — per-version metadata only: version id, layer, sequence,
#                   wall-clock time, reason string, pinned flag, and a count of
#                   files. No path and no file content appears in the response
#                   (engine.list_versions builds each row field by field).
#   target-scan   — candidate backup devices: block devices, sizes, mountpoints.
#                   Hardware inventory, not anyone's data.
#
# DELIBERATELY NOT READ-TIER, though they only read:
#   manifest      — IS the file list of a version. Direct path disclosure.
#   diff          — takes a path and returns stored-vs-live hashes for it; an
#                   oracle for the existence and content of another user's file.
#   verify        — its `problems` entries embed paths ("missing stored file for
#                   <path>"), so a corrupt userdata store leaks names.
#   restore-plan  — returns per-path actions for the paths asked about.
# Each of those stays behind the administrator action. The cost of gating one
# extra verb is one password; the cost of freeing one wrongly is a privacy hole,
# and the dispatch was explicit about which way to err.
READ_ONLY_VERBS = frozenset({"status", "queue-status", "list", "target-scan"})

# Test seam: set to a callable(pid, uid, action_id) -> bool to bypass the real
# pkcheck. It takes the action so a test can grant one tier and refuse the other.
_PEER_AUTHORIZER = None


def _proc_start_time(pid):
    """Field 22 of /proc/<pid>/stat (process start time in clock ticks). Used
    to build a reuse-proof polkit subject (pid alone is racy). The comm field
    (2) can contain spaces and parens, so split after the final ')'."""
    with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as f:
        data = f.read()
    after = data[data.rindex(")") + 1:].split()
    # after[0] is field 3 (state); field 22 is after[19].
    return int(after[19])


def _pkcheck_authorized(pid, uid, action_id):
    """Ask polkit (via its own pkcheck CLI, no extra dependency) whether the
    peer process is authorized for action_id. Fail-closed on any error (pkcheck
    absent, non-zero, unreadable /proc): an unauthorized-by-default stance is
    the only safe one for a privileged engine socket.

    --allow-user-interaction is passed ONLY for the administrative action. The
    read action is declared yes/no in the policy for a local active session and
    needs no dialog, so asking for interaction there could only ever produce a
    prompt for something that should never prompt.
    """
    try:
        start = _proc_start_time(pid)
        subject = f"{pid},{start},{uid}"
        argv = ["pkcheck", "--action-id", action_id, "--process", subject]
        if action_id == POLKIT_ACTION_ID:
            argv.append("--allow-user-interaction")
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return proc.returncode == 0
    except (OSError, ValueError):
        return False


def peer_credentials(conn):
    """(pid, uid) of the peer on a connected socket."""
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                            struct.calcsize("3i"))
    pid, uid, _gid = struct.unpack("3i", creds)
    return pid, uid


def authorize_verb(pid, uid, verb):
    """Authorize ONE verb for a peer. Returns (ok, tier, reason).

    tier is "root", "manage" or "read" when ok, and None when refused; the
    caller uses it to decide whether the response needs redacting.

    The verb is required, which is why this cannot be answered before the
    request is read — see _handle_conn. A verb that is not in the dispatch
    table gets no authorization at all: it is refused here rather than being
    authorized and then rejected later, so an unknown verb can never be a way
    to raise a dialog.
    """
    if uid == 0:
        return True, "root", None
    # A JSON object or array as the verb is unhashable, so the membership test
    # below would raise TypeError instead of refusing. The connection guard in
    # serve() caught that and the engine survived, but the client got a dropped
    # connection where it should have got a refusal line — a defect answer, not
    # a refusal. Anything that is not a string is simply not a verb.
    if not isinstance(verb, str) or verb not in _VERBS:
        return False, None, f"unknown verb: {verb!r}"
    authz = _PEER_AUTHORIZER or _pkcheck_authorized
    if verb in READ_ONLY_VERBS:
        if authz(pid, uid, POLKIT_ACTION_READ):
            return True, "read", None
        # A read-tier refusal is NOT escalated to the administrative action.
        # Escalating would turn every refused read into a password dialog,
        # which is the behaviour this change exists to remove.
        return False, None, (f"not authorized: uid {uid} is not permitted the "
                             f"Chronicle action {POLKIT_ACTION_READ}")
    if authz(pid, uid, POLKIT_ACTION_ID):
        return True, "manage", None
    return False, None, (f"not authorized: uid {uid} is not permitted the "
                         f"Chronicle action {POLKIT_ACTION_ID}")


def _redact_for_read(verb, result):
    """Strip path-bearing fields from a read-tier response.

    Only one field needs it. A queued capture intent is
    {id, layer, scope, trigger_time, reason, estimate} and `scope` is the
    footprint that triggered the capture — for a package transaction that is a
    list of file paths, and for a userdata capture it describes paths under
    /home, which is every user's home directory in one shared store. The count
    and the human summary carry no paths and are what "is my backup healthy"
    actually needs, so they stay.

    Returns a NEW structure; the engine's own objects are never mutated, so a
    root caller in the same process still sees the whole thing.
    """
    if verb not in ("status", "queue-status"):
        return result
    if not isinstance(result, dict):
        return result
    def _scrub_queue(q):
        if not isinstance(q, dict):
            return q
        out = dict(q)
        intents = out.get("intents")
        if isinstance(intents, list):
            out["intents"] = [
                {k: v for k, v in i.items() if k != "scope"}
                if isinstance(i, dict) else i
                for i in intents
            ]
        return out
    out = dict(result)
    if verb == "queue-status":
        return _scrub_queue(out)
    if isinstance(out.get("queue"), dict):
        out["queue"] = _scrub_queue(out["queue"])
    return out


# verb -> (engine method name, arg-name list in call order)
_VERBS = {
    "capture": ("capture", ["layer", "scope", "reason", "sync", "estimate"]),
    "list": ("list_versions", ["layer", "since", "until"]),
    "manifest": ("get_manifest", ["layer", "version_id"]),
    "diff": ("diff", ["layer", "version_id", "path"]),
    "restore-plan": ("restore_plan", ["layer", "version_id", "paths", "mode"]),
    "restore": ("restore_apply", ["layer", "version_id", "paths", "mode"]),
    "verify": ("verify", ["layer", "version_id"]),
    "scrub": ("scrub", []),
    "target-scan": ("target_scan", ["home_estimate_bytes", "floor_bytes"]),
    "target-adopt": ("target_adopt",
                     ["mountpoint", "target_class", "device", "cap_bytes"]),
    "retention-apply": ("retention_apply", ["layer"]),
    "pin": ("pin", ["version_id"]),
    "unpin": ("unpin", ["version_id"]),
    "queue-status": ("queue_status", []),
    "status": ("status", []),
}


def dispatch(engine, request):
    """Route one request dict to the engine; return a response dict. Never
    raises — an engine error becomes {"ok": false, "error": ...}."""
    verb = request.get("verb")
    # isinstance first: _VERBS.get() raises TypeError on an unhashable verb
    # (a JSON object or array), and the in-process CLI path reaches dispatch()
    # without passing through authorize_verb's identical guard.
    spec = _VERBS.get(verb) if isinstance(verb, str) else None
    if spec is None:
        return {"ok": False, "error": f"unknown verb: {verb!r}"}
    method_name, arg_names = spec
    args = request.get("args") or {}
    kwargs = {k: args[k] for k in arg_names if k in args}
    try:
        result = getattr(engine, method_name)(**kwargs)
        return {"ok": True, "result": result}
    except _engine.EngineError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # defensive: never take the engine down on one call
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def engine_group_gid(group_name=ENGINE_SOCKET_GROUP):
    """The gid of the socket's group, or None when the group does not exist.

    A missing group is a real misconfiguration — the package ships the
    sysusers.d fragment that creates it — so this reports absence rather than
    inventing a fallback, and every caller treats absence as "stay closed".
    """
    resolver = _GROUP_RESOLVER
    if resolver is not None:
        return resolver(group_name)
    try:
        return grp.getgrnam(group_name).gr_gid
    except KeyError:
        return None


def prepare_runtime_dir(runtime_dir, gid):
    """Create the socket's directory with its final ownership and mode.

    With gid None the directory stays root-only (0700): if the group is
    missing, the safe state is unreachable, never world-reachable.

    The chown is CONDITIONAL — see the note above secure_socket() for why an
    unnecessary chown is fatal here rather than merely wasteful.
    """
    runtime_dir = Path(runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    if gid is None:
        os.chmod(runtime_dir, 0o700)
        return
    if os.stat(runtime_dir).st_gid != gid:
        try:
            os.chown(runtime_dir, -1, gid)
        except PermissionError:
            # Not privileged enough to hand the directory to the group. Leave
            # it owner-only rather than opening it to a group it does not
            # belong to.
            os.chmod(runtime_dir, 0o700)
            return
        # Read the result back rather than trusting the call. The branch above
        # now decides on ownership, so ownership has to be a measured fact and
        # not an assumed one.
        if os.stat(runtime_dir).st_gid != gid:
            os.chmod(runtime_dir, 0o700)
            return
    os.chmod(runtime_dir, ENGINE_RUNTIME_DIR_MODE)


def secure_socket(socket_path, gid):
    """Give a just-bound socket its final group and mode, then CHECK the result.

    Returns True when the socket ended up group-reachable and verified, False
    when it was left owner-only. The caller reports the False case; it is never
    silent.

    bind() already created the socket under a umask that denied every bit
    outside the owner, so the widening happens exactly once, here, and only
    after the group is known. There is no moment at which the socket exists
    with a mode wider than the one it ends up with.

    The owner is whatever the daemon runs as — root, per chronicled.service.
    The final state is read back off the inode rather than assumed from the
    calls returning without error, because "I asked for 0660" and "the socket
    is 0660" are different claims and only the second one is the security
    property. If the read-back disagrees the socket is narrowed to owner-only:
    a socket whose permissions cannot be confirmed is treated as wrong.

    THE CHOWN IS CONDITIONAL, AND THAT IS NOT AN OPTIMISATION.
    chronicled.service denies the privileged syscall set
    (SystemCallFilter=~@privileged), and @chown is in that set. A chown() from
    this daemon is answered by the kernel with SIGSYS, which kills the process
    outright — it is a signal, not an errno, so the except clauses below never
    run and no amount of Python-level handling can survive it. Measured
    2026-08-06: 165 restarts and 166 SIGSYS coredumps on one machine, every one
    frame #0 chown, frame #1 os_chown_impl.

    The unit therefore runs with Group=chronicle, so bind() already creates the
    socket owned by the target group and there is nothing to change; asking
    before acting is what keeps the syscall out of the normal path entirely.
    The call is still here, and still runs, for the case where the group really
    is wrong — that case is a genuine misconfiguration, and refusing to correct
    it silently would be worse than the crash it risks.
    """
    if gid is None:
        os.chmod(socket_path, 0o600)
        return False
    try:
        if os.stat(socket_path).st_gid != gid:
            os.chown(socket_path, -1, gid)
        os.chmod(socket_path, ENGINE_SOCKET_MODE)
    except OSError:
        os.chmod(socket_path, 0o600)
        return False
    st = os.stat(socket_path)
    if st.st_gid != gid or stat.S_IMODE(st.st_mode) != ENGINE_SOCKET_MODE:
        os.chmod(socket_path, 0o600)
        return False
    return True


def serve(engine, socket_path=None, ready_cb=None):
    """Run the blocking accept loop. One request/response per connection."""
    socket_path = str(socket_path or _paths.SOCKET_PATH)
    # Derived from the socket path rather than read from the module constant,
    # so an overridden socket path takes its directory with it. In production
    # the two are the same thing; under a test or a second instance they are
    # not, and creating /run/chronicle for a socket that lives elsewhere would
    # be both wrong and, unprivileged, impossible.
    gid = engine_group_gid()
    prepare_runtime_dir(Path(socket_path).parent, gid)
    try:
        os.unlink(socket_path)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    # Only members of the socket's group may CONNECT; every non-root request is
    # then gated per request by polkit against the peer's credentials
    # (authorize_verb), at the tier that verb requires. The store bytes stay
    # root-only on disk — the socket exposes only the verb surface, and every
    # verb from a non-root peer is authorization-checked on top of the group.
    #
    # The umask is what makes the mode a creation-time property. bind() creates
    # the socket inode with 0777 & ~umask; without this it lands 0755 (or
    # whatever the inherited umask allows) and is world-connectable for the
    # instant before a chmod narrows it. A daemon that is briefly wrong is
    # wrong: that instant is repeated on every restart and is entirely
    # observable to a process that is watching for it.
    old_umask = os.umask(0o177)
    try:
        srv.bind(socket_path)
    finally:
        os.umask(old_umask)
    if not secure_socket(socket_path, gid):
        missing = "does not exist" if gid is None else \
            "could not be given the socket, or the result did not verify"
        print(f"chronicled: the {ENGINE_SOCKET_GROUP!r} group {missing}, so "
              f"{socket_path} stays owner-only and the desktop application "
              f"cannot reach it. The engine still runs and still captures; "
              f"only the non-root clients are shut out. Create the group from "
              f"/usr/lib/sysusers.d/chronicle.conf (systemd-sysusers) and "
              f"restart chronicled.service.",
              file=_sys.stderr, flush=True)
    srv.listen(16)
    if ready_cb:
        ready_cb()
    try:
        while True:
            conn, _ = srv.accept()
            with conn:
                # A per-connection guard. _send() already tolerates a peer that
                # vanished, but one client must not be able to stop the engine
                # for any reason at all — a malformed state, a bug in a verb, an
                # I/O error mid-response. The connection dies; the daemon does
                # not. Anything unexpected is reported to stderr so it lands in
                # the journal instead of disappearing, because a swallowed
                # exception is its own kind of silent failure.
                try:
                    _handle_conn(engine, conn)
                except Exception as e:  # noqa: BLE001 - deliberate isolation
                    print(f"chronicled: connection handler failed: "
                          f"{type(e).__name__}: {e}", file=_sys.stderr,
                          flush=True)
    finally:
        srv.close()
        try:
            os.unlink(socket_path)
        except OSError:
            pass


def _send(conn, payload):
    """Write one JSON line to the peer, tolerating a peer that has gone away.

    EVERY write to a client socket goes through here. The refusal path always
    had this guard; the success path did not, so a client that closed before
    its response was written raised an unhandled OSError out of _handle_conn,
    out of serve()'s accept loop, and took the whole engine down — observed in
    the field on 2026-08-03, engine up seven hours, killed by a client that
    gave up. A backup daemon that a client can crash is worse than a slow one:
    the captures that do not happen while it is down are silent.
    """
    try:
        conn.sendall((json.dumps(payload) + "\n").encode("utf-8"))
    except (OSError, ValueError):
        pass


def _handle_conn(engine, conn):
    """Serve one request. Authorization happens AFTER the request is read,
    because which authorization is required depends on the verb — a status read
    and a restore are not the same question. The request is parsed first and
    nothing is dispatched until the verb has been authorized, so reading the
    line grants nothing.
    """
    buf = b""
    conn.settimeout(30)
    try:
        while b"\n" not in buf:
            chunk = conn.recv(65536)
            if not chunk:
                return
            buf += chunk
            if len(buf) > 8 * 1024 * 1024:
                _send(conn, {"ok": False, "error": "request too large"})
                return
        line, _, _ = buf.partition(b"\n")
        request = json.loads(line.decode("utf-8"))
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")
    except (ValueError, UnicodeDecodeError) as e:
        _send(conn, {"ok": False, "error": f"malformed request: {e}"})
        return
    except socket.timeout:
        return
    except OSError:
        return

    try:
        pid, uid = peer_credentials(conn)
    except OSError as e:
        # Credentials are the whole basis of the decision. If they cannot be
        # read, there is no safe way to proceed — refuse.
        _send(conn, {"ok": False, "error": f"peer credentials unavailable: {e}"})
        return

    verb = request.get("verb")
    ok, tier, reason = authorize_verb(pid, uid, verb)
    if not ok:
        _send(conn, {"ok": False, "error": reason})
        return

    response = dispatch(engine, request)
    if tier == "read" and response.get("ok"):
        response = dict(response)
        response["result"] = _redact_for_read(verb, response.get("result"))
    _send(conn, response)


class EngineAccessDenied(PermissionError):
    """This account may not open the engine socket.

    Distinct from "the engine is not running", and the distinction matters:
    the remedy for one is starting a service and the remedy for the other is
    being added to a group. Telling a user to start a service that is already
    running is the failure this class exists to prevent.
    """


ACCESS_DENIED_MESSAGE = (
    f"not permitted to reach the Chronicle engine: this account is not in the "
    f"{ENGINE_SOCKET_GROUP!r} group, which is what may open the engine socket. "
    f"An administrator can add it with: usermod -aG {ENGINE_SOCKET_GROUP} "
    f"<user> (the account must log out and back in)."
)


class Client:
    """A minimal client for the CLI/GUI/pkm handler."""

    def __init__(self, socket_path=None, timeout=605):
        self.socket_path = str(socket_path or _paths.SOCKET_PATH)
        self.timeout = timeout

    def call(self, verb, **args):
        request = json.dumps({"verb": verb, "args": args}) + "\n"
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(self.socket_path)
        except PermissionError as e:
            s.close()
            raise EngineAccessDenied(ACCESS_DENIED_MESSAGE) from e
        except OSError:
            s.close()
            raise
        try:
            s.sendall(request.encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        finally:
            s.close()
        return json.loads(buf.decode("utf-8").strip() or "{}")

    def available(self):
        """Whether there is an engine to talk to.

        A permission denial is NOT an absence. os.path.exists() answers False
        for a socket this account may not stat — the directory holding it is
        0750 — and every caller reads that False as "the engine is not
        running": the window would show a Start button for a service that is
        already up, and the CLI would fall back to running an engine
        in-process. Deciding it cannot look is reported as present, and the
        connect that follows produces the honest EngineAccessDenied.
        """
        try:
            os.stat(self.socket_path)
        except PermissionError:
            return True
        except OSError:
            return False
        return True
