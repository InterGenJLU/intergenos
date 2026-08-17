# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Loader shim — points pkm at the shared scripts/lib/igos_trace.py.

The canonical implementation of the JSON-line forensic-trace framework lives at
`scripts/lib/igos_trace.py` and is shared by:

  - scripts/build-intergenos.sh + chroot-build-*.sh (via lib/trace.sh — bash)
  - igos-build/         (via igos-build/_trace.py loader shim)
  - pkm/                (this shim — package manager)
  - installer/backend/  (via installer/backend/_trace.py loader shim)

pkm has the most interesting dual-context property of any consumer: it runs
INSIDE the build pipeline (via `pkg-functions.sh:pkg_install` -> `pkm import`
for every package) AND on the installed system (live ISO installer + post-
install package operations). Both contexts go through this shim.

The dual-path fallback below handles both:
  1. During build (and host clone work): scripts/lib/igos_trace.py is at the
     relative location reachable from this file.
  2. On the installed system (and the live ISO): scripts/lib/ is not packaged,
     so we fall back to the canonical install location /usr/lib/intergenos/.
     The pkm package itself is shipped to /usr/lib/python<X>/site-packages/pkm/
     so the relative walk would land outside the source tree on those hosts —
     the fallback is load-bearing for pkm-on-installed-system contexts.

Either path yields the same module surface. pkm call sites that want
forensic-trace coverage do:

    from . import _trace
    _trace.traced_run(...)
    _trace.trace_event("pkm_invoke", subcommand=..., argv=..., cwd=...)

The shared module is cached under `sys.modules["_igos_trace_shared"]` so every
consumer (igos-build, pkm, installer.backend) sees one module instance with
one set of open sinks and one threading.Lock. This is load-bearing — module
state must be singular across consumers for cross-file `jq` joins to work.

RUNID inheritance: when pkm runs as a subprocess of the build orchestrator
(or igos-build), the IGOS_TRACE_RUNID + IGOS_TRACE_START_TS env-vars are
already set in the inherited environment. The shared module's
`_ensure_runid_and_ts` reads those at first emit, so pkm-emitted events
land in the SAME `<startts>-<runid>` family as the orchestrator's events,
making cross-file `jq` joins trivial.

When pkm runs standalone (operator-typed `pkm install ...` on the installed
system), no IGOS_TRACE_RUNID is set, so the module generates a fresh runid
for the pkm invocation and the trail is stand-alone.
"""

from __future__ import annotations

import importlib.util as _u
import sys as _s
from pathlib import Path as _P

_CANDIDATES = (
    # Build / source-tree path — relative to this file:
    #   pkm/_trace.py -> ../scripts/lib/igos_trace.py
    _P(__file__).resolve().parent.parent / "scripts" / "lib" / "igos_trace.py",
    # Installed-system path (per the dossier 30-lift-plan.md section 6):
    _P("/usr/lib/intergenos/igos_trace.py"),
    # Absolute build-VM path:
    _P("/mnt/intergenos/scripts/lib/igos_trace.py"),
)

_path: _P
for _candidate in _CANDIDATES:
    if _candidate.exists():
        _path = _candidate
        break
else:
    raise ImportError(
        "pkm._trace: cannot locate igos_trace.py at any of the "
        f"expected paths: {[str(p) for p in _CANDIDATES]}. "
        "On the installed system, the build pipeline ships igos_trace.py "
        "to /usr/lib/intergenos/. If this import fails on an installed "
        "system, the packaging step missed the trace module."
    )

# Load the shared module once, cache under sys.modules so all consumers
# share one instance with one sink list and one threading.Lock.
_MODULE_KEY = "_igos_trace_shared"
if _MODULE_KEY in _s.modules:
    _mod = _s.modules[_MODULE_KEY]
else:
    _spec = _u.spec_from_file_location(_MODULE_KEY, str(_path))
    if _spec is None or _spec.loader is None:
        raise ImportError(
            f"pkm._trace: importlib could not build a spec for {_path}"
        )
    _mod = _u.module_from_spec(_spec)
    _s.modules[_MODULE_KEY] = _mod
    _spec.loader.exec_module(_mod)

# Re-export everything the shared module exposes via __all__.
for _name in getattr(_mod, "__all__", []):
    globals()[_name] = getattr(_mod, _name)

# Convenience: expose the loaded module object too.
module = _mod


# ---------------------------------------------------------------------------
# PKM-A17: fail-soft trace emission with a ONE-TIME 'degraded' warning.
# ---------------------------------------------------------------------------
# pkm call sites historically wrapped every trace_event in
# `try: ... except Exception: pass`, so a sink / lock / serialization fault
# silently dropped forensic events — INCLUDING the hook_fire/hook_done events
# that bracket privileged subprocesses — with ZERO signal. These helpers keep
# the original fail-soft posture (a trace fault must never break a package
# operation) but surface a SINGLE 'forensic trace degraded' warning the first
# time an emit raises, so a darkened audit trail is visible instead of
# invisible. 'Trace UNAVAILABLE' (this shim not importable) is handled by each
# caller's `_TRACE_AVAILABLE` guard and is a normal, un-warned condition — these
# only run when trace IS available and an emit RAISES.

_trace_degraded_warned = False


def note_trace_degraded(exc):
    """Emit a one-time 'forensic trace degraded' WARN; suppress the rest."""
    global _trace_degraded_warned
    if _trace_degraded_warned:
        return
    _trace_degraded_warned = True
    try:
        _s.stderr.write(
            "pkm: WARNING — forensic trace degraded; subsequent audit events "
            f"may be missing from the trace log ({type(exc).__name__}: {exc}). "
            "Further trace failures this run are suppressed.\n"
        )
    except Exception:
        # Even the warning channel failed — nothing safe left to do.
        pass


# Shadow the re-exported `trace_event` with a fail-soft wrapper. Every pkm
# call site uses attribute access (`_trace.trace_event(...)`) — never a direct
# `from ._trace import trace_event` — so rebinding the name here routes ALL of
# them through the one-time degraded-warning path with ZERO call-site edits.
# The call sites' own `try: ... except Exception: pass` wrappers become dead
# (the wrapper never raises) but harmless; they are the PKM-A17 silent-swallow
# this replaces. `traced_run` is intentionally NOT shadowed — its return value
# is load-bearing and its failures must surface to callers. This shadow lives
# only in pkm._trace's namespace; the shared module + other consumers'
# (build pipeline / igos-build / installer.backend) trace_event are untouched.
_raw_trace_event = getattr(_mod, "trace_event", None)


def trace_event(*args, **kwargs):
    """Fail-soft trace_event: emit, or warn ONCE on failure (PKM-A17).

    Never raises into the caller and never silently swallows every event —
    the first emit failure surfaces a 'forensic trace degraded' WARN.
    """
    if _raw_trace_event is None:
        return
    try:
        _raw_trace_event(*args, **kwargs)
    except Exception as exc:
        note_trace_degraded(exc)
