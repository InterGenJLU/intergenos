# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Verbose forensic trace for the InterGenOS build pipeline.

This module is the canonical implementation of the JSON-line forensic-trace
framework shared across:

  - scripts/build-intergenos.sh  (the master orchestrator, via lib/trace.sh)
  - scripts/chroot-build-*.sh    (the per-tier chroot builders, via lib/trace.sh)
  - scripts/build-{iso,squashfs,uki,ukis-verity}.sh
  - igos-build/                  (Python package — imports via igos-build/_trace.py)
  - pkm/                         (Python package — imports via pkm/_trace.py)
  - installer/backend/           (Forge installer — imports via _trace.py shim;
                                  the legacy `installer/backend/trace.py` is now
                                  a thin re-export shim)

The module captures every subprocess invocation (argv + env + stdin + stdout +
stderr + returncode + duration), every file copy/write, every phase boundary,
and every per-package boundary. The requirement captured at the top of
the prior-art Forge module reads:

    "every input byte and corresponding output byte"

That byte-level capture property is preserved verbatim in this lift — the
subprocess wrapper (`traced_run`) captures both streams as full text and emits
both their bytes-length and the raw content into the JSONL stream. No
truncation is performed on subprocess stdout/stderr.

Durable sinks per build run:

  Build pipeline (under /mnt/intergenos/build/logs/trace/):
    1. build-orchestrator-<startts>-<runid>.jsonl  — master narration
    2. build-phase-<phase>-<startts>-<runid>.jsonl — per-phase events
    3. build-pkg-<pkg>-<startts>-<runid>.jsonl     — per-package events
    4. build-host-<script>-<startts>-<runid>.jsonl — per host-side helper

  Forge install (preserved backward-compat path):
    1. /tmp/forge-install-<startts>-<runid>.log
    2. <target>/var/log/forge-install-<startts>-<runid>.log

  Always-on: Python `logging` -> systemd journal (via the consumer's existing
  logging configuration; this module emits a `logger.info` for sink-open
  events + `logger.warning` for sink-write failures).

Enable via either env-var:

    IGOS_BUILD_DEBUG_VERBOSE=1     (preferred for build-pipeline use)
    FORGE_DEBUG_VERBOSE=1          (preserved for Forge installer use)

Either form opts in. Read once at module import — callers cannot toggle
verbose mid-run. This is a feature, not a bug: the env-var gate is part of
the operator's "I want a forensic trail for this attempt" up-front decision.

JSON-lines format: one event per line. Keys are stable. Binary stdin/stdout
gets utf-8 errors=replace decoded; we do not truncate.

Secret hygiene:
  - Named-arg values matching REDACT_KEYS are scrubbed in `step_enter` events.
  - The `env` dict passed to `traced_run` has its values scrubbed for any
    key matching REDACT_ENV_SUBSTRINGS (TOKEN/PASSWORD/KEY/SECRET/PASSPHRASE).
    The security-only-alignment constraint: build-host env may carry signing-key passphrases
    or repo tokens; those must not leak into durable JSONL files. Forge's
    original module redacted only named function args; the build-pipeline lift
    extends the redactor to subprocess `env_extra` to cover the build-side
    threat surface.
  - Stdin payloads passed to `traced_run(input="...")` are logged verbatim —
    caller's responsibility to pass via a side channel if secret. The build
    pipeline rarely passes secrets via stdin; the primary historical risk
    (signing-passphrase prompts) already uses a non-stdin path.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import inspect
import os
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state — guarded by _LOCK for thread-safety.
# ---------------------------------------------------------------------------

# Pre-v1.0 directive (operator, 2026-06-05): the forensic trace defaults ON so
# install/build failures are always diagnosable without a re-run ("how else are
# we going to troubleshoot these things properly?"). Opt OUT explicitly with
# IGOS_BUILD_DEBUG_VERBOSE=0 / FORGE_DEBUG_VERBOSE=0 (or false/no/off). In
# practice this only turns Forge's trace on — the build pipeline already passes
# --debug-verbose, so its env is already set.
# >>> FLIP THIS DEFAULT BACK TO OFF (opt-IN) AT THE v1.0 RELEASE CUT. <<<
_TRACE_OFF = ("0", "false", "no", "off")
_VERBOSE = not (
    os.environ.get("IGOS_BUILD_DEBUG_VERBOSE", "").strip().lower() in _TRACE_OFF
    or os.environ.get("FORGE_DEBUG_VERBOSE", "").strip().lower() in _TRACE_OFF
)
_LOCK = threading.Lock()
_SINKS: list = []                # list[TextIOWrapper] — open log file handles
_RUNID: Optional[str] = None
_START_TS: Optional[str] = None

# Argument names whose values should be redacted in trace output.
# Callers that pass secrets as named args (e.g., `password="..."`) get
# their values replaced with "<REDACTED>" before logging.
REDACT_KEYS = frozenset({
    "password", "root_password", "user_password",
    "passphrase", "token", "secret",
})

# Case-insensitive substrings that mark an environment-variable name as
# potentially secret-bearing. The `env_extra` field of subprocess_start
# events scrubs any matching variable's value before emission.
# Security-driven: build-host env may carry GPG/SBSIGN passphrases, repo
# tokens, or signing keys; durable JSONL must not surface them.
REDACT_ENV_SUBSTRINGS = ("TOKEN", "PASSWORD", "PASSPHRASE", "SECRET", "KEY",
                         "CRED", "AUTH")

# Default durable-sink root for build-pipeline traces.
# Honors IGOS_TRACE_ROOT env-var (matches the bash companion's
# scripts/lib/trace.sh `_TRACE_ROOT="${IGOS_TRACE_ROOT:-/mnt/intergenos/build/logs/trace}"`).
# Cross-side parity is load-bearing for the join-by-runid contract — both
# Python + bash need to write under the same root when the orchestrator
# (or operator) overrides the default.
_DEFAULT_BUILD_LOGS_TRACE = Path(
    os.environ.get("IGOS_TRACE_ROOT", "/mnt/intergenos/build/logs/trace")
)


# ---------------------------------------------------------------------------
# Internal helpers.
# ---------------------------------------------------------------------------

def is_verbose() -> bool:
    """True if IGOS_BUILD_DEBUG_VERBOSE or FORGE_DEBUG_VERBOSE was set at process start."""
    return _VERBOSE


def _iso_ts() -> str:
    """UTC ISO8601 with millisecond precision + Z suffix.

    Matches the bash companion's `date -u '+%Y-%m-%dT%H:%M:%S.%3NZ'` exactly,
    so cross-file `jq` joins by `ts` work for events emitted on either side.
    """
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _gen_runid() -> str:
    """Generate a 16-hex runid (matches bash `uuidgen | tr -d - | cut -c1-16`)."""
    return uuid.uuid4().hex[:16]


def _redact_kwargs(d: dict) -> dict:
    """Return a shallow copy of d with REDACT_KEYS values replaced."""
    return {k: ("<REDACTED>" if k in REDACT_KEYS else v) for k, v in d.items()}


def _redact_positional(params: Optional[list], args: tuple) -> list:
    """Redact positional call values whose declared parameter NAME is secret.

    A secret passed positionally must scrub exactly like one passed named —
    set_root_password(target, password) may not leak what password="..."
    would have redacted (the ge9b-04 dogfood install wrote the wizard-entered
    root and user passwords in plaintext into the world-readable target trace
    via exactly this bypass). Values are mapped to parameter names via the
    function's signature; a position with no resolvable name (*args, or an
    unreadable signature) is redacted unconditionally — over-redaction is
    the acceptable failure mode, a leaked credential is not.
    """
    out = []
    for i, a in enumerate(args):
        pname = None
        if params is not None and i < len(params):
            p = params[i]
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
                pname = p.name
        if pname is None or pname in REDACT_KEYS:
            out.append("<REDACTED>")
        else:
            out.append(str(a)[:200])
    return out


def _redact_env(env: Optional[dict]) -> Optional[dict]:
    """Redact secret-bearing env-var values in a subprocess env dict.

    Returns a NEW dict (does not mutate the caller's env). Case-insensitive
    substring match against REDACT_ENV_SUBSTRINGS; matched values are
    replaced with "<REDACTED>". Returns None if env is None.
    """
    if not env:
        return None
    out = {}
    for k, v in env.items():
        ku = k.upper()
        if any(sub in ku for sub in REDACT_ENV_SUBSTRINGS):
            out[k] = "<REDACTED>"
        else:
            out[k] = v
    return out


def _emit(event: dict) -> None:
    """Write one JSON-line event to all sinks. Thread-safe.

    Forge prior-art: on sink-write failure, logger.warning + continue (we keep
    the other sinks alive even if one breaks). The build pipeline inherits the
    same posture: verbose was a forensic-grade opt-in, but a single sink failure
    does not abort the build — the trail is best-effort once the build is
    underway. (Open-time hard-fail is a separate question handled at the
    consumer's `init_*` call site.)
    """
    if not _VERBOSE or not _SINKS:
        return
    event.setdefault("ts", _iso_ts())
    line = json.dumps(event, default=str, ensure_ascii=False) + "\n"
    with _LOCK:
        for s in _SINKS:
            try:
                s.write(line)
                s.flush()
            except Exception as exc:
                logger.warning("trace: sink write failed: %s", exc)


def _ensure_runid_and_ts(runid: Optional[str] = None) -> None:
    """Populate the module-level _RUNID / _START_TS if not already set.

    Honors externally provided values from the bash orchestrator via
    IGOS_TRACE_RUNID / IGOS_TRACE_START_TS env-vars so child Python
    processes (igos-build, pkm) share the parent's runid/startts and
    cross-file `jq` joins work.
    """
    global _RUNID, _START_TS
    if _RUNID and _START_TS:
        return
    _RUNID = (
        runid
        or os.environ.get("IGOS_TRACE_RUNID", "").strip()
        or _gen_runid()
    )
    _RUNID = _RUNID[:16] if _RUNID else "norunid"
    _START_TS = (
        os.environ.get("IGOS_TRACE_START_TS", "").strip()
        or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )


def _open_600(path: Path) -> Any:
    """Append-open a trace sink with owner-only permissions (0600).

    The trace carries env values and step arguments — secret-adjacent by
    nature. O_CREAT mode 0600 covers new files (umask can only tighten it);
    the fchmod tightens a pre-existing sink created by an older writer. A
    permissions failure aborts the open (caller's best-effort handling
    applies): no trace beats a group/world-readable one.
    """
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "a", encoding="utf-8")


def _open_sink(path: Path) -> Optional[Any]:
    """Open a JSONL sink in append mode + register it. Returns the handle or None."""
    explicit_trace_ctx = bool(
        os.environ.get("IGOS_TRACE_RUNID") or os.environ.get("IGOS_TRACE_ROOT")
    )
    # The default sink root (/mnt/intergenos/build/logs/trace) is a BUILD-VM
    # path. Off the build VM — i.e. a standalone `pkm` on an installed system —
    # writing there is meaningless: as a normal user it fails (handled below),
    # and as root it silently CREATES a phantom /mnt/intergenos/build tree and
    # litters the user's filesystem with one .jsonl per command. The OS quietly
    # scattering build artifacts on a user's box is exactly the hidden,
    # surprising behavior the PRIME DIRECTIVE rejects. So when there is no
    # explicit trace context (no IGOS_TRACE_RUNID from an orchestrated run, no
    # operator-chosen IGOS_TRACE_ROOT) and the sink targets the default build
    # root, skip it entirely — no mkdir, no file, no warning. Forge's own sinks
    # (/tmp + /var/log on the target) live elsewhere and are unaffected; the
    # build's `pkm import` sets IGOS_TRACE_RUNID and is unaffected.
    if not explicit_trace_ctx and str(path).startswith(str(_DEFAULT_BUILD_LOGS_TRACE)):
        logger.debug("trace: no trace context; skipping build-root sink %s", path)
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = _open_600(path)
        _SINKS.append(handle)
        logger.info("trace: opened sink at %s", path)
        return handle
    except Exception as exc:
        # Best-effort forensics: a failed sink open must NEVER surface as
        # user-facing noise. The default sink root is the build path
        # (/mnt/intergenos/build/logs/trace), unwritable by a normal user, so
        # on an installed system every standalone `pkm` command would
        # otherwise print "trace: could not open sink ... Permission denied"
        # to stderr — exactly the scary, opaque output the PRIME DIRECTIVE
        # rejects. Warn only inside an EXPLICIT trace context: an orchestrated
        # run sets IGOS_TRACE_RUNID, or an operator points the trace somewhere
        # writable via IGOS_TRACE_ROOT. (_VERBOSE can NOT gate this — it
        # defaults to True until the v1.0 opt-in flip, so it is always true on
        # an end-user box.) Otherwise degrade silently: the framework returns
        # None and trace events simply aren't written; build trace-coverage
        # gates, not a runtime warning, guard build-time coverage.
        if os.environ.get("IGOS_TRACE_RUNID") or os.environ.get("IGOS_TRACE_ROOT"):
            logger.warning("trace: could not open sink %s: %s", path, exc)
        else:
            logger.debug("trace: could not open sink %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Public surface — Forge backward-compat APIs (preserved verbatim).
# ---------------------------------------------------------------------------

def init_trace(runid: str) -> None:
    """Open the live-ISO /tmp sink (Forge installer convention).

    Preserved verbatim from `installer/backend/trace.py` so Forge install.py
    continues to behave bit-identically.

    Call attach_target_sink(target) AFTER target is mounted to add the
    durable target-side sink (which survives unmount + first reboot).

    Idempotent within a process: re-calling closes prior sinks first.
    Safe to call when verbose is off (becomes no-op).
    """
    global _SINKS, _RUNID, _START_TS

    close_trace()

    if not _VERBOSE:
        logger.info(
            "trace: IGOS_BUILD_DEBUG_VERBOSE / FORGE_DEBUG_VERBOSE not set; "
            "verbose forensic trace disabled"
        )
        return

    _RUNID = (runid[:16] if runid else "norunid")
    _START_TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fname = f"forge-install-{_START_TS}-{_RUNID}.log"

    # Live-ISO sink — always available
    _open_sink(Path("/tmp") / fname)

    _emit({"type": "trace_init", "runid": _RUNID,
           "sinks": [s.name for s in _SINKS],
           "pid": os.getpid(), "verbose": True})


def attach_target_sink(target: Path) -> None:
    """Open the durable target-side sink (Forge installer convention).

    Preserved verbatim from `installer/backend/trace.py`. Survives unmount +
    first reboot — the file lives at
    `<target>/var/log/forge-install-<startts>-<runid>.log` and becomes
    `/var/log/forge-install-...` on the installed system.

    Safe to call when verbose is off (no-op).
    """
    if not _VERBOSE or not _RUNID or not _START_TS:
        return
    fname = f"forge-install-{_START_TS}-{_RUNID}.log"
    try:
        target_log_dir = Path(target) / "var" / "log"
        target_log_dir.mkdir(parents=True, exist_ok=True)
        target_log = target_log_dir / fname
        sink = _open_600(target_log)
        _SINKS.append(sink)
        logger.info("trace: attached target sink at %s", target_log)
        _emit({"type": "target_sink_attached", "path": str(target_log)})
    except Exception as exc:
        logger.warning("trace: could not attach target sink: %s", exc)


def close_trace() -> None:
    """Flush + close all sinks. Idempotent."""
    global _SINKS
    for s in _SINKS:
        try:
            s.flush()
            s.close()
        except Exception:
            pass
    _SINKS = []


# ---------------------------------------------------------------------------
# Public surface — build-domain additions.
# ---------------------------------------------------------------------------

def init_build_trace(
    runid: Optional[str] = None,
    *,
    trace_root: Optional[Path] = None,
) -> None:
    """Open the master orchestrator sink for a build run.

    Path: <trace_root>/build-orchestrator-<startts>-<runid>.jsonl

    Where trace_root defaults to /mnt/intergenos/build/logs/trace/.

    If `runid` is omitted, falls back to IGOS_TRACE_RUNID env-var, then
    generates a fresh 16-hex value. Same precedence applies to the
    start-timestamp (IGOS_TRACE_START_TS).

    Idempotent within a process: re-calling closes prior sinks first.
    Safe to call when verbose is off (becomes no-op).

    Emits a `trace_init` event with sink path + runid + pid + verbose flag.
    """
    close_trace()

    if not _VERBOSE:
        logger.info(
            "trace: IGOS_BUILD_DEBUG_VERBOSE / FORGE_DEBUG_VERBOSE not set; "
            "verbose forensic trace disabled (init_build_trace skipped)"
        )
        return

    _ensure_runid_and_ts(runid)

    root = trace_root or _DEFAULT_BUILD_LOGS_TRACE
    fname = f"build-orchestrator-{_START_TS}-{_RUNID}.jsonl"
    _open_sink(root / fname)

    _emit({
        "type": "trace_init",
        "runid": _RUNID,
        "start_ts": _START_TS,
        "sinks": [s.name for s in _SINKS],
        "pid": os.getpid(),
        "verbose": True,
        "scope": "build_orchestrator",
    })


def init_phase_trace(
    phase: str,
    runid: Optional[str] = None,
    *,
    trace_root: Optional[Path] = None,
) -> None:
    """Open a per-phase sink in addition to whatever is already open.

    Path: <trace_root>/build-phase-<phase>-<startts>-<runid>.jsonl

    Called when a phase function (Python-side) wants its events siphoned to
    a per-phase JSONL file as well as the master orchestrator sink. The
    bash companion `lib/trace.sh:trace_init` does the same on its side.

    Safe to call when verbose is off (no-op). Does NOT close prior sinks —
    this is additive, so the orchestrator sink + the phase sink both receive
    events for the duration of the phase.
    """
    if not _VERBOSE:
        return
    _ensure_runid_and_ts(runid)
    root = trace_root or _DEFAULT_BUILD_LOGS_TRACE
    safe_phase = phase.replace("/", "_").replace(" ", "_")
    fname = f"build-phase-{safe_phase}-{_START_TS}-{_RUNID}.jsonl"
    _open_sink(root / fname)
    _emit({
        "type": "phase_sink_attached",
        "phase": phase,
        "path": str(root / fname),
        "runid": _RUNID,
    })


def init_package_trace(
    pkg_name: str,
    phase: Optional[str] = None,
    runid: Optional[str] = None,
    *,
    trace_root: Optional[Path] = None,
) -> None:
    """Open a per-package sink in addition to whatever is already open.

    Path: <trace_root>/build-pkg-<pkg>-<startts>-<runid>.jsonl

    Called from igos-build's `BuildExecutor.build_one()` so every package
    rebuild gets its own JSONL trail addressable by `<pkg>-<runid>`. The
    sink is additive — events also continue to flow to any orchestrator /
    phase sinks already open.

    Safe to call when verbose is off (no-op).
    """
    if not _VERBOSE:
        return
    _ensure_runid_and_ts(runid)
    root = trace_root or _DEFAULT_BUILD_LOGS_TRACE
    safe_pkg = pkg_name.replace("/", "_").replace(" ", "_")
    fname = f"build-pkg-{safe_pkg}-{_START_TS}-{_RUNID}.jsonl"
    _open_sink(root / fname)
    _emit({
        "type": "package_sink_attached",
        "pkg": pkg_name,
        "phase": phase,
        "path": str(root / fname),
        "runid": _RUNID,
    })


def init_host_trace(
    script_name: str,
    runid: Optional[str] = None,
    *,
    trace_root: Optional[Path] = None,
) -> None:
    """Open a per-host-script sink (for build-iso.sh, build-squashfs.sh, etc.).

    Path: <trace_root>/build-host-<script>-<startts>-<runid>.jsonl

    Safe to call when verbose is off (no-op).
    """
    if not _VERBOSE:
        return
    _ensure_runid_and_ts(runid)
    root = trace_root or _DEFAULT_BUILD_LOGS_TRACE
    safe = script_name.replace("/", "_").replace(" ", "_")
    fname = f"build-host-{safe}-{_START_TS}-{_RUNID}.jsonl"
    _open_sink(root / fname)
    _emit({
        "type": "host_sink_attached",
        "script": script_name,
        "path": str(root / fname),
        "runid": _RUNID,
    })


def get_runid() -> Optional[str]:
    """Accessor for the module-level _RUNID; useful for child-process env export."""
    return _RUNID


def get_start_ts() -> Optional[str]:
    """Accessor for the module-level _START_TS; useful for child-process env export."""
    return _START_TS


# ---------------------------------------------------------------------------
# Public surface — subprocess + file wrappers (byte-level capture).
# ---------------------------------------------------------------------------

def traced_run(
    cmd: Sequence[str],
    *,
    input: Optional[str] = None,
    env: Optional[dict] = None,
    cwd: Optional[str] = None,
    check: bool = False,
    timeout: Optional[float] = None,
    phase: Optional[str] = None,
    intent: Optional[str] = None,
    pkg: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """Wrap subprocess.run with full byte capture + JSON trace emit.

    Returns the CompletedProcess unmodified (same interface as subprocess.run).
    Always captures stdout + stderr; trace emit only when verbose is on.

    `phase`: PHASE_* string for grep-ability
    `intent`: free-text human description of what this call is supposed to do
    `pkg`:   package name (build-domain addition — pins each subprocess call
             to its owning package for cross-file `jq` joins)

    BYTE-LEVEL CAPTURE: stdin_bytes, stdout_bytes, stderr_bytes record the
    exact UTF-8 byte counts of the three streams. The raw content for each
    is emitted verbatim — no truncation. This is the load-bearing operator
    requirement preserved from Forge's prior art.
    """
    start = time.monotonic()
    if _VERBOSE:
        _emit({
            "type": "subprocess_start",
            "phase": phase,
            "intent": intent,
            "pkg": pkg,
            "cmd": list(cmd),
            "stdin_bytes": len(input.encode("utf-8")) if input else 0,
            "stdin": input if input else None,
            "cwd": cwd,
            "env_extra": _redact_env(
                {k: v for k, v in env.items() if k not in os.environ}
                if env else None
            ),
            "timeout": timeout,
        })

    try:
        result = subprocess.run(
            list(cmd),
            input=input,
            env=env,
            cwd=cwd,
            check=False,                # we surface rc explicitly
            timeout=timeout,
            capture_output=True,
            text=True,
            errors="replace",           # honor docstring promise (line 54-55)
        )
    except Exception as exc:
        _emit({
            "type": "subprocess_exception",
            "phase": phase,
            "pkg": pkg,
            "cmd": list(cmd),
            "exception": repr(exc),
            "duration_ms": int((time.monotonic() - start) * 1000),
        })
        raise

    duration_ms = int((time.monotonic() - start) * 1000)

    _emit({
        "type": "subprocess_end",
        "phase": phase,
        "intent": intent,
        "pkg": pkg,
        "cmd": list(cmd),
        "rc": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "stdout_bytes": len(result.stdout.encode("utf-8")) if result.stdout else 0,
        "stderr_bytes": len(result.stderr.encode("utf-8")) if result.stderr else 0,
        "duration_ms": duration_ms,
    })

    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, cmd, output=result.stdout, stderr=result.stderr
        )

    return result


def traced_run_chroot(
    target: Path,
    shell_command: str,
    *,
    phase: Optional[str] = None,
    intent: Optional[str] = None,
    input: Optional[str] = None,
    pkg: Optional[str] = None,
) -> tuple[int, str, str]:
    """Run a shell command inside a chroot of the target with full trace.

    Same return tuple shape as Forge's `hooks.run_chroot`: (rc, stdout, stderr).
    Use for package post_install hooks that genuinely need chroot context;
    prefer host-side `traced_run` with `--root` flags everywhere else.
    """
    cmd = ["chroot", str(target), "/bin/bash", "-c", shell_command]
    result = traced_run(
        cmd, input=input, phase=phase, pkg=pkg,
        intent=intent or f"chroot exec: {shell_command[:80]}",
    )
    return result.returncode, result.stdout, result.stderr


def traced_copy_file(
    src: Path, dst: Path,
    *,
    phase: Optional[str] = None,
    intent: Optional[str] = None,
    pkg: Optional[str] = None,
) -> None:
    """Host-side file copy with trace emit (src + dst paths + size + sha256 head)."""
    src = Path(src); dst = Path(dst)
    if _VERBOSE:
        _emit({
            "type": "file_copy_start",
            "phase": phase,
            "intent": intent,
            "pkg": pkg,
            "src": str(src),
            "dst": str(dst),
        })
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    except Exception as exc:
        _emit({"type": "file_copy_exception", "src": str(src), "dst": str(dst),
               "exception": repr(exc), "phase": phase, "pkg": pkg})
        raise

    if _VERBOSE:
        size = dst.stat().st_size
        sha = (hashlib.sha256(dst.read_bytes()).hexdigest()[:16]
               if size < 50_000_000 else "<too-large>")
        _emit({
            "type": "file_copy_end",
            "phase": phase,
            "pkg": pkg,
            "src": str(src),
            "dst": str(dst),
            "size_bytes": size,
            "sha256_head": sha,
        })


def traced_write_file(
    path: Path, content: str,
    *,
    mode: int = 0o644,
    phase: Optional[str] = None,
    intent: Optional[str] = None,
    pkg: Optional[str] = None,
) -> None:
    """Host-side file write with trace emit (path + bytes + sha256 head + content if small)."""
    path = Path(path)
    if _VERBOSE:
        _emit({
            "type": "file_write_start",
            "phase": phase,
            "intent": intent,
            "pkg": pkg,
            "path": str(path),
            "bytes": len(content.encode("utf-8")),
            "content": content if len(content) < 4096 else "<truncated>",
            "sha256_head": hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    os.chmod(path, mode)


# ---------------------------------------------------------------------------
# Public surface — decorator + ad-hoc event emitter.
# ---------------------------------------------------------------------------

def trace_install_step(name: Optional[str] = None):
    """Decorator: log function entry + exit + return value + duration.

    Use on phase-boundary functions. Args are emitted (with REDACT_KEYS scrubbed).

    Preserved verbatim from Forge's prior art for backward compatibility with
    existing Forge call sites (`installer/backend/users.py:289-291` etc.).
    """
    def deco(fn):
        step_name = name or fn.__name__
        # Bind the signature ONCE at decoration time so wrapper calls pay no
        # inspect cost; None => _redact_positional fails closed (all redacted).
        try:
            _sig_params = list(inspect.signature(fn).parameters.values())
        except (TypeError, ValueError):
            _sig_params = None
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            start = time.monotonic()
            if _VERBOSE:
                _emit({
                    "type": "step_enter",
                    "step": step_name,
                    "args_positional": _redact_positional(_sig_params, args),
                    "kwargs": _redact_kwargs({k: str(v)[:200] for k, v in kwargs.items()}),
                })
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                _emit({
                    "type": "step_exception",
                    "step": step_name,
                    "exception": repr(exc),
                    "duration_ms": int((time.monotonic() - start) * 1000),
                })
                raise
            if _VERBOSE:
                _emit({
                    "type": "step_exit",
                    "step": step_name,
                    "return": str(result)[:500] if result is not None else None,
                    "duration_ms": int((time.monotonic() - start) * 1000),
                })
            return result
        return wrapper
    return deco


# Alias for clarity in build-pipeline call sites.
trace_build_step = trace_install_step


def trace_event(event_type: str, **fields) -> None:
    """Emit an arbitrary structured event. For ad-hoc instrumentation.

    All build-domain event types (build_start, build_end, phase_enter,
    phase_exit, pkg_enter, pkg_exit, pkg_phase, chroot_mount, etc.) flow
    through this entry point. See `30-lift-plan.md` for the canonical
    event-type catalogue.
    """
    if not _VERBOSE:
        return
    _emit({"type": event_type, **fields})


# ---------------------------------------------------------------------------
# Public surface — structured-failure exception builders.
# ---------------------------------------------------------------------------

def install_failure(
    *,
    where: str,
    why: str,
    cmd: Optional[Sequence[str]] = None,
    rc: Optional[int] = None,
    stdout: Optional[str] = None,
    stderr: Optional[str] = None,
    extra: Optional[dict] = None,
) -> RuntimeError:
    """Build a RuntimeError with a clean, structured, scannable message.

    Caller raises the returned exception. The message names the exact
    failure site, explains the user-facing impact, and includes the
    subprocess context (cmd / rc / stdout / stderr) when applicable.
    Pattern is decided 2026-05-27: prefer raising-with-context
    over log.warning + continue for any failure that breaks the install.

    The structured fields are also emitted to the trace log so the
    durable forensic trail captures the failure even if the exception's
    traceback is swallowed by an outer handler.

    Args:
        where: file:line or function name pointing at the failure site
        why:   one-line explanation of what's now broken
        cmd:   the subprocess argv that failed (optional)
        rc:    the returncode (optional)
        stdout / stderr / extra: additional context (optional)
    """
    lines = [
        f"Install failure at {where}",
        f"  why: {why}",
    ]
    if cmd is not None:
        lines.append(f"  command: {' '.join(str(c) for c in cmd)}")
    if rc is not None:
        lines.append(f"  exit code: {rc}")
    if stderr:
        stderr_clean = stderr.rstrip()
        if "\n" in stderr_clean:
            lines.append("  stderr:")
            for ln in stderr_clean.splitlines():
                lines.append(f"    {ln}")
        else:
            lines.append(f"  stderr: {stderr_clean}")
    if stdout:
        stdout_clean = stdout.rstrip()
        if "\n" in stdout_clean:
            lines.append("  stdout:")
            for ln in stdout_clean.splitlines():
                lines.append(f"    {ln}")
        else:
            lines.append(f"  stdout: {stdout_clean}")
    if extra:
        for k, v in extra.items():
            lines.append(f"  {k}: {v}")
    msg = "\n".join(lines)
    trace_event(
        "install_failure",
        where=where, why=why,
        cmd=list(cmd) if cmd is not None else None,
        rc=rc, stdout=stdout, stderr=stderr, extra=extra,
    )
    return RuntimeError(msg)


def build_failure(
    *,
    where: str,
    why: str,
    cmd: Optional[Sequence[str]] = None,
    rc: Optional[int] = None,
    stdout: Optional[str] = None,
    stderr: Optional[str] = None,
    phase: Optional[str] = None,
    pkg: Optional[str] = None,
    extra: Optional[dict] = None,
) -> RuntimeError:
    """Build-pipeline equivalent of install_failure.

    Identical shape; emits a `build_failure` event (with `phase` + `pkg`
    pins) instead of `install_failure`. Use at every build-pipeline `raise`
    site that signals a fatal condition — the JSONL trail then captures
    every fatal even if the exception is swallowed by an outer handler.
    """
    lines = [
        f"Build failure at {where}",
        f"  why: {why}",
    ]
    if phase is not None:
        lines.append(f"  phase: {phase}")
    if pkg is not None:
        lines.append(f"  pkg: {pkg}")
    if cmd is not None:
        lines.append(f"  command: {' '.join(str(c) for c in cmd)}")
    if rc is not None:
        lines.append(f"  exit code: {rc}")
    if stderr:
        stderr_clean = stderr.rstrip()
        if "\n" in stderr_clean:
            lines.append("  stderr:")
            for ln in stderr_clean.splitlines():
                lines.append(f"    {ln}")
        else:
            lines.append(f"  stderr: {stderr_clean}")
    if stdout:
        stdout_clean = stdout.rstrip()
        if "\n" in stdout_clean:
            lines.append("  stdout:")
            for ln in stdout_clean.splitlines():
                lines.append(f"    {ln}")
        else:
            lines.append(f"  stdout: {stdout_clean}")
    if extra:
        for k, v in extra.items():
            lines.append(f"  {k}: {v}")
    msg = "\n".join(lines)
    trace_event(
        "build_failure",
        where=where, why=why,
        phase=phase, pkg=pkg,
        cmd=list(cmd) if cmd is not None else None,
        rc=rc, stdout=stdout, stderr=stderr, extra=extra,
    )
    return RuntimeError(msg)


# ---------------------------------------------------------------------------
# Re-export the canonical name list for `from igos_trace import *` consumers.
# ---------------------------------------------------------------------------

__all__ = [
    # State accessors
    "is_verbose", "get_runid", "get_start_ts",
    # Forge-compat sink openers
    "init_trace", "attach_target_sink", "close_trace",
    # Build-domain sink openers
    "init_build_trace", "init_phase_trace", "init_package_trace", "init_host_trace",
    # Subprocess + file wrappers
    "traced_run", "traced_run_chroot", "traced_copy_file", "traced_write_file",
    # Decorator + ad-hoc emitter
    "trace_install_step", "trace_build_step", "trace_event",
    # Structured-failure builders
    "install_failure", "build_failure",
    # Module-level redact policy (callers may extend per-package)
    "REDACT_KEYS", "REDACT_ENV_SUBSTRINGS",
]
