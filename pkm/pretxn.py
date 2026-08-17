# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""pkm pre-transaction hook — fires BEFORE a package transaction mutates the
live filesystem, so an external agent can capture a restore point of exactly
the transaction's footprint first.

This is deliberately distinct from pkm/hooks.py. Every hook in that module —
the canonical content-triggered hooks and the archive `.scripts/` lifecycle
hooks alike — runs AFTER the package is deployed and the database transaction
commits. That is the correct time for depmod/ldconfig/daemon-reload, but it is
too late to record the pre-transaction state: pkm's tar deploy to the live root
happens before its own DB BEGIN/COMMIT, so a failed transaction can leave the
disk mutated while only the database rolls back. A restore-point-before-every-
transaction guarantee therefore needs a point that runs before the first byte
is written. pkm exposes it here and calls it from the top of the mutating
`cmd_install` / `cmd_upgrade` / `cmd_remove` verbs, before their per-package
loops.

Contract:

  - Handlers are executables placed in ``PRETXN_HANDLER_DIR``
    (``/usr/lib/pkm/pre-transaction.d/``). An ABSENT directory or one with NO
    executables is a NO-OP — pkm behaves exactly as it does today. Nothing in
    base pkm registers a handler; a backup engine (Chronicle) ships one.

  - Each handler is run once per transaction with a JSON document on stdin
    describing the footprint — the transaction verb, a human reason, the
    absolute paths whose CURRENT bytes the transaction will overwrite or
    delete, and the pkm database path — plus a stripped environment (the same
    ``HOOK_ENV_ALLOWLIST`` the lifecycle hooks use) carrying ``PKM_TXN_VERB``
    and ``PKM_TXN_REASON``.

  - Failure semantics (PRIME DIRECTIVE + the design's "never blocked" rule): a
    handler failure is LOUD but NON-FATAL. A backup safety net that cannot run
    must not stop the user from installing a security update, and the engine
    itself already degrades gracefully (it falls back to always-on local
    capture when the external target is absent). pkm names the failing handler
    loudly and proceeds. A hook that hard-blocks a transaction is not part of
    v1 by design — the engine falls back rather than refusing.

Only paths that currently EXIST have bytes to capture, so the footprint is the
outgoing side of the transaction: for an upgrade or a remove, the currently
installed files of the affected packages; for a fresh install, nothing but the
database (the reversal of a fresh install is the pkm.db diff, which the handler
reads from ``db_path``). A reinstall/forced install of an already-installed
package includes that package's current files, since those bytes are about to
be overwritten.
"""

import json
import os
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

from .hooks import HOOK_ENV_ALLOWLIST

# Forensic-trace shim — defensive import (mirrors hooks.py).
try:
    from . import _trace
    _TRACE_AVAILABLE = True
except ImportError:
    _trace = None
    _TRACE_AVAILABLE = False


# Drop-in directory for pre-transaction handlers. Each executable here is run
# once, before the transaction, with the footprint JSON on stdin. Absent dir or
# no executables => no-op.
PRETXN_HANDLER_DIR = Path("/usr/lib/pkm/pre-transaction.d")

# The authoritative pkm database, relative to the install root. The restore
# point always includes it (see module docstring: it is what reverses a fresh
# install and anchors the transaction's before-state).
PKM_DB_RELPATH = "var/lib/igos/pkm.db"

# A pre-transaction restore point of a bounded footprint should complete in
# seconds; this generous ceiling bounds the worst case so a wedged handler
# cannot block a package transaction indefinitely.
PRETXN_TIMEOUT = 600


PreTxnResult = namedtuple(
    "PreTxnResult", ["ran", "handlers", "failures", "messages"]
)


def transaction_footprint(db, verb, package_names, reason):
    """Compute the footprint document for a pending transaction.

    Args:
        db: PackageDB — queried for each package's currently-installed files.
        verb: "install" | "upgrade" | "remove".
        package_names: iterable of package names the transaction will touch.
        reason: human-readable reason string, embedded verbatim.

    Returns:
        dict with keys:
          verb, reason, packages: as passed (packages de-duped, order kept).
          paths: sorted, de-duped ABSOLUTE paths (under db.root) of the files
            the transaction will overwrite/delete — the outgoing side. Only
            currently-installed regular files are included; directories are
            excluded (they have no bytes to restore).
          db_path: absolute path to pkm.db under db.root.
    """
    root = getattr(db, "root", None) or Path("/")
    root = Path(root)
    packages = list(dict.fromkeys(package_names))
    paths = set()
    for name in packages:
        # Only an already-installed package has current bytes to capture. A
        # brand-new install contributes none; its reversal is the pkm.db diff.
        if not db.get_installed(name):
            continue
        for f in db.get_files(name):
            if f.get("is_dir"):
                continue
            paths.add(str(root / f["path"]))
    return {
        "verb": verb,
        "reason": reason,
        "packages": packages,
        "paths": sorted(paths),
        "db_path": str(root / PKM_DB_RELPATH),
    }


def list_handlers(handler_dir=None):
    """Return the executable pre-transaction handlers, sorted by name.

    Args:
        handler_dir: override for PRETXN_HANDLER_DIR (tests). Defaults to the
            live drop-in directory.

    Returns:
        list[Path] of executable regular files. Empty when the directory is
        absent or holds nothing executable — the no-op path.
    """
    d = Path(handler_dir) if handler_dir is not None else PRETXN_HANDLER_DIR
    if not d.is_dir():
        return []
    return [
        p for p in sorted(d.iterdir())
        if p.is_file() and os.access(str(p), os.X_OK)
    ]


def _emit(reporter, text, is_failure):
    """Surface a hook status line loudly and consistently.

    Uses the Reporter when the caller has one; falls back to stderr so a
    failure is never swallowed on a code path (e.g. cmd_upgrade) that emits
    through module-level helpers rather than a Reporter instance.
    """
    if reporter is not None:
        (reporter.warn if is_failure else reporter.note)(text)
    elif is_failure:
        sys.stderr.write(text + "\n")


def run_pre_transaction_hook(db, verb, package_names, reason,
                             reporter=None, handler_dir=None):
    """Run the registered pre-transaction handlers for a pending transaction.

    Args:
        db: PackageDB (for footprint computation).
        verb: "install" | "upgrade" | "remove".
        package_names: package names the transaction will touch.
        reason: human reason embedded in the footprint + hook env.
        reporter: optional pkm Reporter; failures surface via .warn, OK lines
            via .note (verbose). Absent -> failures go loudly to stderr.
        handler_dir: override for the handler directory (tests).

    Returns:
        PreTxnResult(ran, handlers, failures, messages). ran is False and the
        rest empty when no handler is registered (the no-op path) — pkm then
        proceeds exactly as it does without any backup engine present.

    Never raises for a handler failure and never blocks the transaction on one:
        a failing backup safety net is loud, not fatal.
    """
    handlers = list_handlers(handler_dir)
    if not handlers:
        return PreTxnResult(ran=False, handlers=[], failures=[], messages=[])

    footprint = transaction_footprint(db, verb, package_names, reason)
    payload = json.dumps(footprint)

    env = {k: v for k, v in os.environ.items() if k in HOOK_ENV_ALLOWLIST}
    env.setdefault("PATH", "/usr/sbin:/usr/bin")
    env.setdefault("HOME", "/root")
    env["PKM_TXN_VERB"] = verb
    env["PKM_TXN_REASON"] = reason

    if _TRACE_AVAILABLE:
        try:
            _trace.trace_event(
                "pkm_pretxn_fire", verb=verb,
                handler_count=len(handlers), path_count=len(footprint["paths"]),
            )
        except Exception:
            pass

    failures = []
    messages = []
    for h in handlers:
        try:
            if _TRACE_AVAILABLE:
                result = _trace.traced_run(
                    [str(h)], input=payload, env=env, timeout=PRETXN_TIMEOUT,
                    phase="pkm_pretxn_hook", intent=f"pre-transaction {verb}",
                )
            else:
                result = subprocess.run(  # trace-coverage: allow — _trace shim unavailable fallback
                    [str(h)], input=payload, env=env,
                    capture_output=True, text=True, timeout=PRETXN_TIMEOUT,
                )
            if result.returncode == 0:
                msg = f"  pre-transaction[{h.name}] OK — restore point captured"
                messages.append(msg)
                _emit(reporter, msg, is_failure=False)
            else:
                snip = (result.stderr or "").strip().replace("\n", " ")[:200]
                msg = (
                    f"  pre-transaction[{h.name}] WARNING: restore point NOT "
                    f"captured (exit {result.returncode}); {snip}. Proceeding "
                    f"with the transaction — this operation will not be "
                    f"backed-up-first."
                )
                messages.append(msg)
                failures.append(h.name)
                _emit(reporter, msg, is_failure=True)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            msg = (
                f"  pre-transaction[{h.name}] WARNING: restore point NOT "
                f"captured (handler did not run: {e}). Proceeding with the "
                f"transaction — this operation will not be backed-up-first."
            )
            messages.append(msg)
            failures.append(h.name)
            _emit(reporter, msg, is_failure=True)

    if _TRACE_AVAILABLE:
        try:
            _trace.trace_event(
                "pkm_pretxn_done", verb=verb, failures=len(failures),
            )
        except Exception:
            pass

    return PreTxnResult(
        ran=True, handlers=[h.name for h in handlers],
        failures=failures, messages=messages,
    )
