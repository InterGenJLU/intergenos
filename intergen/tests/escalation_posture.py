# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Taking the escalating posture back, for a cell that tests escalation itself.

Every test process runs with `INTERGEN_PRIVILEGED_ESCALATION=refuse`, set in
conftest.py before any import, so a privileged dispatch is declined at the
boundary and no cell can reach an authentication prompt on anybody's desktop.

A handful of cells exist precisely to check what that boundary does AFTERWARDS —
the argument vector it builds, the request file it writes and discards, the
diagnosis it reports when the user manager is unreachable. They need the
escalating branch, and taking the posture back in their own code is a visible,
reviewable act rather than a silent default.

It is deliberately not possible to use this helper on an unstubbed dispatch. The
one thing that must be true before the posture is lifted is that nothing can
actually start a process, so that is CHECKED here rather than left to each
caller's memory: `tool_registry.subprocess.run` must already be replaced by a
test double, or this refuses to lift anything. A real dispatch would ask
PolicyKit, and PolicyKit raises a password dialog on whatever desktop is
registered with it — which happened twice on 2026-09-16, once from a cell that
answered a gate with "allow" and once from a red run authoring the posture
itself.
"""

from __future__ import annotations

import contextlib
import os
from unittest import mock

from intergen import tool_registry
from intergen.tool_registry import ESCALATION_ALLOWED, PRIVILEGED_ESCALATION_ENV


@contextlib.contextmanager
def escalation_allowed_for_a_stubbed_dispatch():
    """Allow escalation for this block. The dispatch MUST already be stubbed."""
    run = tool_registry.subprocess.run
    if not isinstance(run, (mock.Mock, mock.MagicMock, mock.NonCallableMock)):
        raise AssertionError(
            "escalation_allowed_for_a_stubbed_dispatch() was entered while "
            "intergen.tool_registry.subprocess.run is the REAL function. "
            "Lifting the refusing posture now would let this cell start "
            "systemd-run and pkexec for real, and PolicyKit would raise an "
            "authentication dialog on the desktop of whoever is at the "
            "machine. Stub the dispatch first "
            "(mock.patch.object(tool_registry.subprocess, 'run', ...)) and "
            "enter this inside that patch.")
    with mock.patch.dict(os.environ,
                         {PRIVILEGED_ESCALATION_ENV: ESCALATION_ALLOWED}):
        yield
