# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Legacy installer alias for the shared `igos_trace` module.

`installer/backend/trace.py` predates the build-pipeline debug-logger lift.
The canonical implementation now lives at `scripts/lib/igos_trace.py` (see
the 2026-05-28 build-pipeline debug-logging lift plan, section 6, for the
rationale).

This shim preserves every existing Forge call site:

    from . import trace          # still imports this module
    trace.traced_run(...)        # still works — re-exported below
    trace.install_failure(...)   # still works
    trace.FORGE_DEBUG_VERBOSE    # still gates verbosity via env-var (in addition
                                 # to IGOS_BUILD_DEBUG_VERBOSE)

There is NO duplication of logic here — every public name resolves to the
shared `_trace` shim (which loads `scripts/lib/igos_trace.py` once and caches
under sys.modules["_igos_trace_shared"]). Verbatim Forge behavior is preserved
because the underlying module IS Forge's prior-art code lifted unchanged, with
only additive build-domain APIs (init_build_trace, build_failure, etc.).

Existing Forge install + smoke tests run against this shim with no behavior
change. The `FORGE_DEBUG_VERBOSE=1` env-var continues to enable verbose mode
exactly as it did before the lift.
"""

from __future__ import annotations

# Star-import every public name from the loader shim — this is the simplest
# stable surface for the Forge call sites (which use attribute access on the
# `trace` module: `trace.traced_run`, `trace.install_failure`, etc.).
from ._trace import *  # noqa: F401,F403

# Explicit re-exports for static analyzers (mypy, pyright, ruff F401 rules):
# this list mirrors igos_trace.__all__.
from ._trace import (  # noqa: F401
    is_verbose,
    get_runid,
    get_start_ts,
    init_trace,
    attach_target_sink,
    close_trace,
    init_build_trace,
    init_phase_trace,
    init_package_trace,
    init_host_trace,
    traced_run,
    traced_run_chroot,
    traced_copy_file,
    traced_write_file,
    trace_install_step,
    trace_build_step,
    trace_event,
    install_failure,
    build_failure,
    REDACT_KEYS,
    REDACT_ENV_SUBSTRINGS,
)
