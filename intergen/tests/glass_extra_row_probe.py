# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A pytest plugin that makes the turn record emit ONE EXTRA ROW when it opens.

WHAT THIS IS. An instrument for one question: which tests would break the next
time the writer learns to say something? It is not collected as a test and it
does nothing unless a run asks for it by name:

    python3 -m pytest -p intergen.tests.glass_extra_row_probe <files>

and, for the second variant, with EXTRA_ROW_OWN_TURN_ID set to any string.

WHAT IT IS NOT. It is not a defect report. The rows the writer emits TODAY — the
sequence-resumed row, the rotation marker, the synthesized terminal — are already
in every record, so a test that breaks on those is already failing. This probe
simulates the NEXT one: a row whose phase no existing filter names.

THE TWO VARIANTS, because they separate two different readings:
  EXTRA_ROW_OWN_TURN_ID unset — the extra row carries whatever turn is active
    when the writer opens its file. This also moves a test that asserts the
    complete row sequence of its OWN turn, which is a deliberate contract
    assertion and not the defect class.
  EXTRA_ROW_OWN_TURN_ID set — the extra row carries a turn id of its own, which
    is the convention the shipped sequence-resumed row already follows. A
    failure that survives this variant is a positional read and nothing else.
"""
from __future__ import annotations

import os

EXTRA_PHASE = "startup"
EXTRA_EVENT = "writer_learned_a_new_row"


def pytest_configure(config):
    import intergen.glass as glass

    if getattr(glass, "_extra_row_probe_installed", False):
        return
    own_turn_id = os.environ.get("EXTRA_ROW_OWN_TURN_ID") or None
    original_init = glass.GlassLogger.__init__

    def patched_init(self, *a, **kw):
        original_init(self, *a, **kw)
        try:
            self.emit(EXTRA_PHASE, EXTRA_EVENT,
                      turn_id=own_turn_id,
                      detail={"simulated": True,
                              "why": "one more row the writer emits on open"})
        except Exception:
            # The writer is best-effort and so is this probe: a run that cannot
            # place the extra row must still run, and its result then simply
            # says nothing about the extra row rather than failing for it.
            pass

    glass.GlassLogger.__init__ = patched_init
    glass._extra_row_probe_installed = True
