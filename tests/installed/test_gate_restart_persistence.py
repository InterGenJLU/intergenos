"""GATE 11 — restart and reboot persistence (section 9 line 10).

WHAT COMPOSITION PROPERTY THIS CATCHES. Two questions that a source-tree test cannot
ask: does what the user owns survive a restart, and does what the daemon computed at
start-up survive one? On R001.1 the answers were yes and no. Conversations and stored
facts persisted correctly. The documentation index did not: it was computed once in the
retrieval object's constructor, written nowhere, and never rebuilt — so every restart
re-paid a cost that had never once succeeded on that machine, and a restart was not a
recovery from a failed index.

WHAT RECOVERY LOOKS LIKE NOW, and what this gate measures since 2026-08-27. The shipped
index embeds within a start-up budget and keeps a resume path: the daemon continues
embedding between turns and on web-page turns until the corpus is complete, and logs
when it finishes. So the property is no longer "the constructor is not the only
builder" read from the source shape alone; it is measured twice — the resume path must
exist in the shipped module AND be wired by the daemon, and every start of the release
under test that began incomplete must reach complete without another restart, read from
this machine's journal. An on-disk cache of the computed index would make a restart
cheap; it is a development item (gating R001.3 notes), not a silent-failure class, and
this gate no longer demands it.

EXPECTED TO FAIL ON R001.1 AS SHIPPED.
"""

from __future__ import annotations

import inspect
import re
import subprocess
import time
from pathlib import Path

import pytest

UNIT = "intergen.service"

# The functions that build or continue building the documentation index. Any call to
# one of them outside the constructor is a rebuild path.
BUILDERS = ("_build_index", "_embed_chunks", "_embed_pass")
INCOMPLETE = "the rest are pending"          # a start whose index is not yet complete
RESUMED = "Wiki index finished embedding between turns"


def _journal_lines() -> list[str]:
    proc = subprocess.run(
        ["journalctl", "--user", "-u", UNIT, "--no-pager", "-o", "short-iso"],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        pytest.fail("Could not read this machine's service journal; restart behaviour "
                    f"cannot be characterised.\nstderr:\n{proc.stderr}")
    return proc.stdout.splitlines()


def test_the_users_own_data_survives_a_restart(real_home):
    """The control: what the user owns must persist. Expected to pass on a used account.

    Kept as a real assertion rather than dropped, because the tests below only mean
    something if persistence works at all on this machine.

    It reads the OLDEST record in the stored conversation database and compares it with
    the moment the currently running daemon started. A record older than the running
    daemon is a record that survived at least one restart. An earlier draft compared
    file modification times and failed: the daemon writes to that file at start-up, so
    its modification time says nothing about when its contents were created.
    """
    import sqlite3

    store = real_home / ".local" / "share" / "intergen" / "memory.db"
    if not store.exists():
        pytest.skip(
            "NOT VERIFIED: this account has no conversation store yet, so persistence "
            "across a restart was not exercised. This is a skip, not a pass.")

    proc = subprocess.run(
        ["systemctl", "--user", "show", UNIT, "-pExecMainStartTimestamp"],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        pytest.fail("Could not read the daemon's start time from the service manager.")
    stamp = proc.stdout.strip().split("=", 1)[-1].strip()
    if not stamp:
        pytest.skip("NOT VERIFIED: the daemon is not running, so nothing was shown to "
                    "have survived its restart. This is a skip, not a pass.")

    started = subprocess.run(["date", "-d", stamp, "+%s"],
                             capture_output=True, text=True, timeout=60)
    if started.returncode != 0:
        pytest.fail(f"Could not interpret the daemon start time {stamp!r}.")
    daemon_started_at = int(started.stdout.strip())

    conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    try:
        oldest = None
        for table, column in (("facts", "created_at"), ("sessions", "created_at")):
            try:
                row = conn.execute(
                    f"SELECT MIN({column}) FROM {table}").fetchone()
            except sqlite3.Error:
                continue
            if row and row[0] is not None:
                value = float(row[0])
                oldest = value if oldest is None else min(oldest, value)
    finally:
        conn.close()

    if oldest is None:
        pytest.skip(
            "NOT VERIFIED: the conversation store exists but holds no dated record, so "
            "nothing was shown to have survived a restart. This is a skip, not a pass.")

    assert oldest < daemon_started_at, (
        "\nNothing in the conversation store predates the running daemon, so this run "
        "has not shown that stored conversations survive a restart.\n"
        f"  oldest stored record : epoch {oldest:.0f}\n"
        f"  daemon started at    : epoch {daemon_started_at}"
    )


def test_a_restart_recovers_a_documentation_index_that_failed_to_build():
    """A start that fails must be recoverable by the next one, or by anything at all."""
    lines = _journal_lines()
    starts = [ln for ln in lines if "wiki-retrieval: indexed" in ln]
    fallbacks = [ln for ln in lines if "keyword fallback only" in ln]

    if not starts:
        pytest.fail("This machine's journal records no daemon start that indexed the "
                    "documentation; this gate measured nothing.")

    assert len(fallbacks) < len(starts), (
        f"\nEvery recorded daemon start on this machine degraded to keyword matching:\n"
        f"  starts that indexed the documentation : {len(starts)}\n"
        f"  starts that then fell back to keywords: {len(fallbacks)}\n"
        "Restarting is not a recovery. The same request is made the same way against "
        "the same one-slot server under the same deadline, so a restart reproduces the "
        "failure rather than clearing it."
    )


def test_the_documentation_index_has_a_rebuild_path(installed_intergen_dir):
    """Something other than the constructor must be able to build the index.

    The region of the file that counts as "the constructor" is found by parsing the
    module, not by searching for the first ``def __init__`` in the text — the first one
    in this file belongs to a different class, so a text search put the region in the
    wrong place and this test passed on the defect it exists to catch.
    """
    import ast

    source = (installed_intergen_dir / "wiki_retrieval.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    builder_calls = []          # (enclosing function name, line)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr in BUILDERS):
                builder_calls.append((node.name, inner.lineno, inner.func.attr))

    outside_constructor = [c for c in builder_calls if c[0] != "__init__"]

    # The path must also be reachable: the daemon has to call it. A resume method
    # nothing invokes is the omission wearing a new name.
    daemon = (installed_intergen_dir / "dbus_daemon.py").read_text(encoding="utf-8")
    wired = "resume_embedding" in daemon

    from intergen.semantic import SemanticMatcher
    comparable = hasattr(SemanticMatcher, "refresh_pending_intents")

    assert outside_constructor and wired, (
        "\nThe documentation index can only ever be built once, in the constructor.\n"
        f"  the daemon wires the resume path: {wired}\n"
        f"  every call site of the index builder: {builder_calls}\n"
        f"  the comparable intent corpus has a refresh path: {comparable}\n"
        "There is no watchdog, no refresh and no cache. If the embedding server is not "
        "ready when the daemon starts — which is the ordinary case at boot — the "
        "documentation is unreachable by meaning for the whole life of that daemon, and "
        "nothing that happens afterwards can change it."
    )


def test_an_incomplete_index_reaches_complete_without_a_restart():
    """Every start of the release under test that began incomplete must finish.

    Read from this machine's journal, bounded to the release under test by its
    install date (the trace-integrity gate's bound, loaded from its file — this
    tier keeps the checkout off sys.path, so a sibling cannot be imported by name).
    A start that embedded the whole corpus within its budget has nothing to
    recover and is counted as measured. A start that was still pending and never
    logged completion is the defect: the machine would answer by keyword for the
    life of that daemon, and only a restart — the thing this gate says must not be
    the recovery — would change it. The running daemon is given a short grace
    period, since its resume passes run between turns.
    """
    import importlib.util
    from datetime import datetime

    src = Path(__file__).with_name("test_gate_glass_trace_integrity.py")
    spec = importlib.util.spec_from_file_location("_trace_integrity_gate", src)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    since = datetime.fromtimestamp(module.installed_release_install_date())

    ts = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")
    pid_re = re.compile(r"intergen\[(\d+)\]")
    by_pid: dict[str, list[str]] = {}
    for ln in _journal_lines():
        m = ts.match(ln)
        if not m or datetime.fromisoformat(m.group(1)) < since:
            continue
        p = pid_re.search(ln)
        if p:
            by_pid.setdefault(p.group(1), []).append(ln)

    starts = {pid: block for pid, block in by_pid.items()
              if any("wiki-retrieval: indexed" in ln for ln in block)}
    if not starts:
        pytest.fail(f"This machine's journal records no daemon start since the release "
                    f"under test was installed ({since}); this gate measured nothing.")

    current = subprocess.run(
        ["systemctl", "--user", "show", "-p", "MainPID", "--value", UNIT],
        capture_output=True, text=True).stdout.strip()
    if current in starts and any(INCOMPLETE in ln for ln in starts[current]) \
            and not any(RESUMED in ln for ln in starts[current]):
        time.sleep(30)                      # one more resume pass for the live daemon
        starts[current] = by_pid[current] = [
            ln for ln in _journal_lines() if pid_re.search(ln)
            and pid_re.search(ln).group(1) == current]

    report = ["", f"DAEMON STARTS SINCE {since}: {len(starts)}", ""]
    stuck = []
    for pid, block in starts.items():
        incomplete = any(INCOMPLETE in ln for ln in block)
        resumed = any(RESUMED in ln for ln in block)
        state = ("complete at start-up" if not incomplete
                 else "incomplete, then finished between turns" if resumed
                 else "INCOMPLETE AND NEVER FINISHED")
        report.append(f"  pid {pid:>8}  {state}")
        if incomplete and not resumed:
            stuck.append(pid)
    report.append("")
    report.append("A start that never finishes embedding answers by keyword for the life "
                  "of that daemon; a restart is not a recovery.")
    assert not stuck, "\n".join(report)
