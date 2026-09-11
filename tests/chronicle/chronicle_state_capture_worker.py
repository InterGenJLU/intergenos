"""Spawned-process worker for the engine state-transaction tests.

This module deliberately carries a unique top-level name and is imported by the
test through its own directory on sys.path, never as ``tests.chronicle.…``: a
spawned child re-imports the target by module name, and during a whole-tree
test run a regular package also called ``tests`` (installer/tests, intergen/tests)
can appear on the import path ahead of this repository's namespace ``tests``
directory, which would leave the child unable to import the test module at all.
"""

import multiprocessing

from chronicle import engine as _engine
from chronicle import paths as _paths


def process_capture(local_root, source, barrier, results):
    engine = _engine.Engine(local_root=local_root, now_fn=lambda: 1_000_000)
    barrier.wait()
    try:
        version = engine.capture(
            _paths.LAYER_CONFIG_STATE,
            scope=[source],
            reason=f"process-{multiprocessing.current_process().name}",
        )["version_id"]
        results.put(("ok", version))
    except Exception as exc:
        results.put(("error", f"{type(exc).__name__}: {exc}"))
