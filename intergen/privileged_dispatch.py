# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Privileged-context entry point for the pkexec runner.

Invoked by `intergen.tool_registry.execute()` via the pkexec runner
shim at `intergen/data/intergen-privileged-runner` (landed at 49a585ca,
authored by the build-system coordinator's T0-4-E integration pkexec
gate work). This module is the AUTHORIZATION + ARGUMENT-VALIDATION
boundary that re-enforces the same contract the unprivileged caller
enforced pre-pkexec; PolicyKit is the upstream AUTHENTICATION boundary;
the provenance gate at `intergen/provenance.py` is the upstream INTENT
boundary.

Argv contract (from `intergen-privileged-runner`):

    python3 -m intergen.privileged_dispatch --request-id <request_id>

That is the whole command line, and it is the point. The tool name, the
arguments and the human-approval token are read from the request file, which is
owner-only and is removed as it is read (intergen.privileged_request). They used
to be argv words instead, and a command line is world-readable through
/proc/<pid>/cmdline for the life of the process — on this image /proc carries no
hidepid, so any local account could read the approval token and the tool
arguments out of the process listing while a dispatch was in flight. Changed
2026-08-24, together with the transport module and the runner shim; the three
are one contract and move together.

The request file is not trusted merely because its path was handed to us. Before
its contents are believed it is checked: a regular file, opened without
following symlinks, owned by PKEXEC_UID, mode exactly 0600, single-linked. A
file that fails any of those is a refused dispatch, not a best-effort read.

Environment (set by the runner from pkexec):

    PKEXEC_UID   — calling user's uid
    PKEXEC_USER  — calling user's username (resolved via getent passwd)

Exit code:

    0 — tool dispatch succeeded; ToolResult.content on stdout
    1 — tool dispatch failed (validation error / refusal / exception);
        human-readable reason on stdout
    2 — argv shape wrong (caller bug; should never happen via the
        runner shim)

The runner's job is to set up a clean root-context environment + hand
off to this module. This module's job is to re-validate (defense in
depth — the privileged context cannot trust the un-privileged caller's
prior validation in the abstract; we re-run the same validation
against the same source-of-truth so the privileged boundary is
self-contained) + execute + print + exit cleanly.

D-008 RFC v1.0 §6 invariant preserved: privileged operations require
BOTH the provenance gate (intent) AND pkexec (authentication). The
gate ran in the user context (`tool_registry.execute()` → `verify_tool_call`)
before pkexec invoked this module; the user authenticated to PolicyKit;
this module is the post-auth dispatcher that re-validates the tool +
args against the same allowlist + schema the gate consulted.
"""

from __future__ import annotations

import fcntl
import os
import sys
import time

from intergen import dispatch_token
from intergen import privileged_request
from intergen.tool_registry import ToolRegistry, _PRIVILEGED_TOOLS
from intergen.interfaces.types import SafetyTier


#: Persistent, root-owned consumed-nonce store for single-use enforcement of
#: AI-6 dispatch tokens. dispatch_token.verify_token is deliberately stateless
#: (signature + binding + freshness only); replay protection lives HERE because
#: only the root context has a stable, privileged place to keep the store.
_CONSUMED_NONCE_DIR = "/var/lib/intergen"
_CONSUMED_NONCE_PATH = "/var/lib/intergen/consumed-nonces"


class _NonceReplay(Exception):
    """The approval-nonce has already been consumed — a replayed token. A
    valid signature + binding + freshness can still be a replay of a prior
    single approval; the stateless primitive leaves this defense to the
    root-side store implemented here."""


def _emit(message: str) -> None:
    """Print to stdout (the runner captures this via subprocess.run)."""
    print(message)


def _fail(message: str, exit_code: int = 1) -> None:
    """Print error to stdout (so subprocess.run captures it in the
    calling registry's ToolResult.content) and exit with non-zero.
    """
    _emit(message)
    sys.exit(exit_code)


def _consume_nonce(nonce: str, exp: int, *, now: int | None = None) -> None:
    """Record a single-use approval-nonce, refusing replays.

    The store is persistent (survives across dispatches), root-owned (0o600 in
    a 0o700 dir), and file-locked (LOCK_EX) so two concurrent privileged
    dispatches cannot race a double-spend of the same approval. The check and
    the record happen under ONE held lock, and recording precedes the caller's
    execute step, so a second dispatch carrying the same nonce cannot pass the
    check before the first records it. Each line is ``<nonce> <exp>``; entries
    past their exp are pruned on every pass so the store stays bounded by the
    in-flight token window (~DEFAULT_TTL_SECONDS) — a token that can no longer
    pass verify_token's freshness check can never be replayed, so its nonce no
    longer needs to be remembered.

    Raises:
        _NonceReplay: the nonce is already present (and not expired).
        OSError: the store could not be created/read/written — the caller
            treats this as fail-closed (a replay store we cannot consult is a
            dispatch we must refuse).
    """
    if now is None:
        now = int(time.time())
    os.makedirs(_CONSUMED_NONCE_DIR, mode=0o700, exist_ok=True)
    fd = os.open(_CONSUMED_NONCE_PATH, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(fd, "r+", closefd=False) as fh:
            kept: list[tuple[str, int]] = []
            seen = False
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) != 2:
                    continue  # skip malformed line rather than trust it
                rec_nonce, rec_exp_s = parts
                try:
                    rec_exp = int(rec_exp_s)
                except ValueError:
                    continue
                if rec_exp < now:
                    continue  # prune expired — cannot be replayed past freshness
                kept.append((rec_nonce, rec_exp))
                if rec_nonce == nonce:
                    seen = True
            if seen:
                raise _NonceReplay(nonce)
            kept.append((nonce, int(exp)))
            fh.seek(0)
            fh.truncate()
            for rec_nonce, rec_exp in kept:
                fh.write(f"{rec_nonce} {rec_exp}\n")
            fh.flush()
            os.fsync(fd)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns exit code (0 success / 1 dispatch-failure /
    2 argv-shape-wrong). main() does not return on the exit-2 path; it
    calls _fail() which calls sys.exit().
    """
    args = argv if argv is not None else sys.argv[1:]

    if len(args) != 2 or args[0] != "--request-id":
        _fail(
            "privileged_dispatch: usage: python3 -m intergen.privileged_dispatch "
            "--request-id <request_id>",
            exit_code=2,
        )

    request_id = args[1]

    # PKEXEC_UID + PKEXEC_USER are set by the runner shim. Their absence
    # means this module was invoked outside the runner — a bug or a
    # malicious bypass attempt. Refuse rather than silently running.
    if not os.environ.get("PKEXEC_UID"):
        _fail(
            "privileged_dispatch: PKEXEC_UID unset; refusing to run "
            "outside the pkexec runner context.",
        )

    # PKEXEC_UID presence was checked above; read both identity vars here
    # robustly (a non-int UID or a missing USER is a malformed runner context).
    # This has to happen BEFORE the request is read, because the uid is what the
    # request's ownership is checked against.
    try:
        uid = int(os.environ["PKEXEC_UID"])
        user = os.environ["PKEXEC_USER"]
    except (KeyError, ValueError) as exc:
        _fail(
            f"privileged_dispatch: PKEXEC identity unavailable/invalid "
            f"({type(exc).__name__}: {exc}); refusing.",
        )

    # Turn the identifier into a path OURSELVES (2026-08-24). The unprivileged
    # side names thirty-two hex characters and nothing else; this process
    # derives the directory from the uid pkexec reported and verifies that
    # directory belongs to that user at mode 0700 before joining anything to
    # it. There is no path spelling to fall back to. The runner shim and this
    # module ship in the same package and are upgraded together, so there is no
    # window in which the two disagree, and a caller that hands over a path is
    # a caller this boundary does not recognise.
    try:
        request_path = privileged_request.resolve_request(request_id, uid)
    except privileged_request.RequestError as exc:
        _fail(f"privileged_dispatch: {exc}; refusing dispatch.")

    # Read the request. We are root, so privileged_request checks what it is
    # about to read rather than trusting it — regular file, no symlink, owned
    # by the calling user, mode 0600, single-linked, within its size bound —
    # and removes the file as it reads. A request we cannot fully trust is a
    # privileged action we do not run.
    try:
        tool_name, arguments, token = privileged_request.read_request(
            request_path, expected_uid=uid,
        )
    except privileged_request.RequestError as exc:
        _fail(f"privileged_dispatch: {exc}; refusing dispatch.")

    # Re-validate tool_name against the PRIVILEGED_STATE_CHANGING
    # allowlist. The gate already filtered, but the privileged boundary
    # must not trust the caller — it re-checks against the same SOT.
    if tool_name not in _PRIVILEGED_TOOLS:
        _fail(
            f"privileged_dispatch: tool {tool_name!r} is not in the "
            f"PRIVILEGED_STATE_CHANGING allowlist; refusing dispatch. "
            f"(allowlist: {sorted(_PRIVILEGED_TOOLS)})",
        )

    # AI-6 (option iii) — HUMAN-APPROVAL TOKEN VERIFY, BEFORE execute.
    # The canonical-pair keystone: root stops *trusting* that the upstream
    # provenance gate fired (it is doubly inert and cannot be trusted to pick
    # which calls get human review) and instead *verifies* that a human
    # approved THIS exact (tool, args, uid) in the review modal. The token was
    # minted on that approval and travelled here inside the request file. No
    # valid, fresh, single-use token ⇒ refuse. This is the independent backstop
    # at the privilege crossing that the allowlist/schema re-checks above
    # cannot provide (they re-derive from the same SOT the caller used; the
    # token proves a human, not the AI, authorized this).
    #
    # read_request has already refused an empty or non-string token, so the
    # only remaining question is whether it verifies.

    # verify_token resolves the signing key via PKEXEC_USER's home
    # (dispatch_key_path_for_user) — NOT HOME=/root, which the runner sets.
    # Order inside: HMAC (constant-time) → version → binding (tool+args+uid) →
    # freshness. Any DispatchTokenError ⇒ refused dispatch (fail closed).
    try:
        payload = dispatch_token.verify_token(
            token, tool_name, arguments, uid, username=user,
        )
    except dispatch_token.DispatchTokenError as exc:
        _fail(
            f"privileged_dispatch: dispatch token verification failed "
            f"({type(exc).__name__}): {exc}; refusing dispatch.",
        )

    # Single-use enforcement (replay defense). verify_token is stateless by
    # design; the persistent consumed-nonce store lives root-side. A valid,
    # in-freshness token whose approval-nonce was already spent is a replay —
    # refuse. A store we cannot consult (OSError) is also a refusal (fail
    # closed): we never execute a privileged action we cannot prove is the
    # first use of its approval.
    try:
        _consume_nonce(payload.nonce, payload.exp)
    except _NonceReplay:
        _fail(
            "privileged_dispatch: dispatch token approval-nonce already "
            "consumed (replay); refusing dispatch.",
        )
    except OSError as exc:
        _fail(
            f"privileged_dispatch: consumed-nonce store unavailable "
            f"({type(exc).__name__}: {exc}); refusing dispatch (fail closed).",
        )

    # Discover tools via the same registry the unprivileged caller used.
    # The registry's discover_tools() walks intergen/tools/*.py and
    # instantiates each BaseTool subclass — same SOT as the caller side.
    registry = ToolRegistry()
    discovered = registry.discover_tools()
    if discovered == 0:
        _fail(
            "privileged_dispatch: tool registry discovered 0 tools; "
            "intergen.tools package may be broken upstream of this point.",
        )

    tool = registry.get_tool(tool_name)
    if tool is None:
        _fail(
            f"privileged_dispatch: tool {tool_name!r} is in the privileged "
            f"allowlist but the registry did not discover it; "
            f"intergen.tools.{tool_name} may be missing or broken.",
        )

    # Per-tool argument schema validation. Same call the unprivileged
    # registry's execute() makes — defense-in-depth re-check.
    validation_error = tool.validate_arguments(arguments)
    if validation_error:
        _fail(
            f"privileged_dispatch: argument validation failed for "
            f"{tool_name!r}: {validation_error}",
        )

    # BLOCKED tier remains a tool-level refusal even in privileged
    # context. The gate already filtered, but BLOCKED is the
    # provenance-independent invariant — it must hold here too.
    safety = tool.classify_safety(arguments)
    if safety == SafetyTier.BLOCKED:
        _fail(
            f"privileged_dispatch: tool {tool_name!r} classified BLOCKED "
            f"for this argument set; refusing dispatch.",
        )

    # Execute the tool in root context. tool.execute() returns a
    # ToolResult; we surface .content to stdout and map success/failure
    # to exit code so the calling registry can construct a ToolResult
    # of its own from this subprocess's output.
    try:
        result = tool.execute(arguments)
    except Exception as exc:  # noqa: BLE001 — wrap into ToolResult
        _fail(
            f"privileged_dispatch: tool {tool_name!r} raised "
            f"{type(exc).__name__}: {exc}",
        )

    _emit(result.content)
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
