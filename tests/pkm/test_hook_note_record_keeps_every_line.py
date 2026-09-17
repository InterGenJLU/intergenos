# SPDX-License-Identifier: GPL-3.0-or-later
"""Folding a repeat changes the display and never the record.

WHY THIS FILE EXISTS.

NOTE output that repeats is now shown once and counted rather than printed
again (test_hook_note_lines_are_collapsed.py). That is only safe while the
thing being folded is a VIEW. If the fold reached the record, this change would
be the same mechanism as the one it was written to undo: an install that called
every hook OK while discarding eight fontconfig diagnostics that named a real
defect. The rule is collapse, never drop — so what a hook said must still be
recoverable in full, line for line, for every operation that said it, including
the ones whose display was folded.

The record is the forensic install trace. Every canonical hook runs through the
tracer, which writes a `subprocess_end` event carrying that run's exit code and
its complete stderr. This file proves that the events are still there and still
complete after the display folded them, by running the real reporter with the
real tracer in a child process and then reading the trace file back.

WHAT THESE TESTS PIN.

1. Two package operations in which the same hook says the same thing produce
   ONE displayed NOTE line and TWO complete stderr records in the trace.
2. The folded operation's record carries the full text, not a marker and not a
   truncation.
3. A multi-line block that folds in the display is still recorded line for line
   for each operation that produced it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

CHILD = r'''
import json, os, re, sys
sys.path.insert(0, %(repo)r)
from pkm import hooks, _trace
from pathlib import Path

_trace.init_build_trace("notefold", trace_root=Path(os.environ["TRACE_DIR"]))

script = os.environ["HOOK_SCRIPT"]
hook = hooks.CanonicalHook(
    id="icons",
    description="gtk icon cache",
    pattern=re.compile(r"^usr/share/testhook/"),
    cmd_fn=lambda root, matched: ["/bin/sh", "-c", script],
    critical=False,
)
fold = hooks.NoteFold()
shown = {}
for package in ("first-package", "second-package"):
    result = hooks.run_canonical_hooks(
        "/tmp", ["usr/share/testhook/marker"], package, "1.0", "install",
        hooks=[hook], note_fold=fold,
    )
    shown[package] = [m for m in result.messages if "] NOTE " in m]
_trace.close_trace()
print(json.dumps(shown))
'''


def _run_child(tmp_path, hook_script):
    """Run two operations through the real reporter and the real tracer."""
    trace_dir = tmp_path / "trace"
    trace_dir.mkdir()
    env = dict(os.environ)
    env["TRACE_DIR"] = str(trace_dir)
    env["HOOK_SCRIPT"] = hook_script
    env["IGOS_BUILD_DEBUG_VERBOSE"] = "1"
    env["IGOS_TRACE_ROOT"] = str(trace_dir)
    proc = subprocess.run(
        [sys.executable, "-c", CHILD % {"repo": str(REPO_ROOT)}],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, (
        f"the child that produces the trace failed: {proc.stderr}"
    )
    shown = json.loads(proc.stdout)
    traces = sorted(trace_dir.glob("*.jsonl"))
    assert traces, (
        f"no trace file was written, so the record cannot be read back: "
        f"{list(trace_dir.iterdir())}"
    )
    events = []
    for path in traces:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    events.append(json.loads(line))
    hook_runs = [
        e for e in events
        if e.get("type") == "subprocess_end"
        and e.get("phase") == "pkm_canonical_hook"
    ]
    return shown, hook_runs


def test_the_folded_operation_is_still_recorded_in_full(tmp_path):
    shown, hook_runs = _run_child(
        tmp_path, "echo 'cache written' >&2; exit 0",
    )
    assert len(shown["first-package"]) == 1, (
        f"the first operation did not show the line: {shown!r}"
    )
    assert shown["second-package"] == [], (
        f"the repeat was displayed instead of folded: {shown!r}"
    )
    assert len(hook_runs) == 2, (
        "the record lost a hook run when its display was folded: "
        f"{hook_runs!r}"
    )
    for event in hook_runs:
        assert "cache written" in (event.get("stderr") or ""), (
            f"a recorded hook run lost the text it produced: {event!r}"
        )


def test_a_folded_multi_line_block_is_recorded_line_for_line(tmp_path):
    shown, hook_runs = _run_child(
        tmp_path,
        "printf 'advisory one\\nadvisory two\\nadvisory three\\n' >&2; exit 0",
    )
    assert len(shown["first-package"]) == 3
    assert shown["second-package"] == []
    assert len(hook_runs) == 2
    for event in hook_runs:
        recorded = [
            line for line in (event.get("stderr") or "").splitlines()
            if line.strip()
        ]
        assert recorded == ["advisory one", "advisory two", "advisory three"], (
            f"the record kept only part of a folded block: {recorded!r}"
        )


def test_the_record_is_not_a_marker_or_a_truncation(tmp_path):
    long_line = "x" * 400
    shown, hook_runs = _run_child(
        tmp_path, f"echo '{long_line}' >&2; exit 0",
    )
    assert shown["second-package"] == []
    for event in hook_runs:
        assert (event.get("stderr") or "").strip() == long_line, (
            "the record shortened what the hook said: "
            f"{len((event.get('stderr') or '').strip())} characters kept of "
            f"{len(long_line)}"
        )
