"""GATE 11 — restart and reboot persistence (section 9 line 10).

WHAT COMPOSITION PROPERTY THIS CATCHES. Two questions that a source-tree test cannot
ask: does what the user owns survive a restart, and does what the daemon computed at
start-up survive one? On the released system the answers are yes and no. Conversations
and stored facts persist correctly. The documentation index does not: it is computed
once in the retrieval object's constructor, written nowhere, and never rebuilt — so
every restart re-pays a cost that has never once succeeded on this machine, and a
restart is not a recovery from a failed index.

That second half is why "restart persistence" is a gate and not a formality. When the
start-up index fails there is no path back: no watchdog, no refresh, no cache to fall
back on. The comparable intent corpus DOES have a refresh path, which is the evidence
that the missing one here is an omission rather than a design.

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
                    and inner.func.attr == "_build_index"):
                builder_calls.append((node.name, inner.lineno))

    outside_constructor = [c for c in builder_calls if c[0] != "__init__"]

    from intergen.semantic import SemanticMatcher
    comparable = hasattr(SemanticMatcher, "refresh_pending_intents")

    assert outside_constructor, (
        "\nThe documentation index can only ever be built once, in the constructor.\n"
        f"  every call site of the index builder: {builder_calls}\n"
        f"  the comparable intent corpus has a refresh path: {comparable}\n"
        "There is no watchdog, no refresh and no cache. If the embedding server is not "
        "ready when the daemon starts — which is the ordinary case at boot — the "
        "documentation is unreachable by meaning for the whole life of that daemon, and "
        "nothing that happens afterwards can change it."
    )


def test_the_computed_index_is_written_somewhere_a_restart_can_read(installed_intergen_dir):
    """A cache would make a restart cheap and make a failed build survivable.

    THE WRITE HAS TO BE IN THE CODE THAT BUILDS THE INDEX. This used to accept any
    of a list of markers appearing ANYWHERE in the module, so an unrelated
    ``json.dump`` — a log line, a debug dump, a settings write — would have reported
    the index as cached while nothing about the index was written at all. That is a
    check that can go green on the defect it exists to catch, so the region is now
    located by parsing: the module's index-building functions, and the calls inside
    them. Measured 2026-08-24: no marker appears anywhere in either the tree module
    or the installed one, so this gate's VERDICT does not change here — only its
    ability to be fooled later.
    """
    import ast

    source = (installed_intergen_dir / "wiki_retrieval.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    MARKERS = ("save", "savez", "dump", "write_bytes", "write_text", "to_file")
    BUILDERS = ("_build_index", "_embed_chunks")

    builder_bodies = [node for node in ast.walk(tree)
                      if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and node.name in BUILDERS]
    if not builder_bodies:
        pytest.fail(
            "None of the index-building functions "
            f"{BUILDERS} exist in the shipped wiki_retrieval.py, so this gate cannot "
            "say where a cache would be written. The shape has moved and the gate "
            "must move with it — this is reported rather than passed over."
        )

    writes = []
    for body in builder_bodies:
        for node in ast.walk(body):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in MARKERS:
                writes.append((body.name, node.lineno, ast.unparse(node)[:100]))
    persists = bool(writes)

    assert persists, (
        "\nThe computed documentation index is never written to disk.\n"
        "Every start re-embeds the whole corpus from scratch against a one-slot server "
        "under a thirty second deadline. A cache keyed on the documentation's own "
        "verified hashes would make a restart cheap and would let a machine that "
        "succeeded once keep working; there is none."
    )
