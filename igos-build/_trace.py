# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Loader shim — points igos-build at the shared scripts/lib/igos_trace.py.

The canonical implementation of the JSON-line forensic-trace framework lives at
`scripts/lib/igos_trace.py` and is shared by:

  - scripts/build-intergenos.sh + chroot-build-*.sh (via lib/trace.sh — bash)
  - igos-build/         (this shim — Python build driver)
  - pkm/                (via pkm/_trace.py loader shim)
  - installer/backend/  (via installer/backend/_trace.py loader shim)

This shim is a "loader" because `igos-build/` is shipped both as part of the
source tree AND as the in-place build driver invoked by the orchestrator. The
shared `igos_trace.py` lives in `scripts/lib/` in the source tree, and gets
copied to `/usr/lib/intergenos/igos_trace.py` on the installed system.

The dual-path fallback below handles both contexts:
  1. During build (and when working on a host clone of the repo):
     scripts/lib/igos_trace.py is at the relative location.
  2. On the installed system: scripts/lib/ is not packaged, so we fall back
     to the canonical install location /usr/lib/intergenos/.

Either path yields the same module surface. Existing igos-build call sites
that want forensic-trace coverage do:

    from . import _trace
    _trace.traced_run(...)
    _trace.trace_event(...)

The shared module is cached under `sys.modules["_igos_trace_shared"]` so every
consumer (igos-build, pkm, installer.backend) sees one module instance with
one set of open sinks and one threading.Lock. This is load-bearing — module
state must be singular across consumers for cross-file `jq` joins to work.
"""

from __future__ import annotations

import importlib.util as _u
import sys as _s
from pathlib import Path as _P

_CANDIDATES = (
    # Build / source-tree path — relative to this file:
    #   igos-build/_trace.py -> ../scripts/lib/igos_trace.py
    _P(__file__).resolve().parent.parent / "scripts" / "lib" / "igos_trace.py",
    # Installed-system path (per the dossier 30-lift-plan.md section 6):
    _P("/usr/lib/intergenos/igos_trace.py"),
    # Absolute build-VM path (for the case where the source tree is on a
    # virtiofs share and __file__ resolution doesn't match the relative walk):
    _P("/mnt/intergenos/scripts/lib/igos_trace.py"),
)

_path: _P
for _candidate in _CANDIDATES:
    if _candidate.exists():
        _path = _candidate
        break
else:
    raise ImportError(
        "igos-build._trace: cannot locate igos_trace.py at any of the "
        f"expected paths: {[str(p) for p in _CANDIDATES]}. "
        "This is a packaging error — the build system expects "
        "scripts/lib/igos_trace.py in the source tree."
    )

# Load the shared module once, cache it in sys.modules under a stable name so
# repeated imports across igos-build modules pick up the same object (the
# threading.Lock + sink list in igos_trace are module-level state — every
# consumer must share one copy).
_MODULE_KEY = "_igos_trace_shared"
if _MODULE_KEY in _s.modules:
    _mod = _s.modules[_MODULE_KEY]
else:
    _spec = _u.spec_from_file_location(_MODULE_KEY, str(_path))
    if _spec is None or _spec.loader is None:
        raise ImportError(
            f"igos-build._trace: importlib could not build a spec for {_path}"
        )
    _mod = _u.module_from_spec(_spec)
    _s.modules[_MODULE_KEY] = _mod
    _spec.loader.exec_module(_mod)

# Re-export everything the shared module exposes via __all__.
for _name in getattr(_mod, "__all__", []):
    globals()[_name] = getattr(_mod, _name)

# Convenience: expose the loaded module object too.
module = _mod
