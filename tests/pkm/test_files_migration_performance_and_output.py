# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Guards for the one-time files-table uniqueness update: it must finish in a
usable time on a real-scale database, and it must never work in silence.

WHY THIS FILE EXISTS. The first version of that update asked, for every row,
"are you the newest row for your (package_id, path)?" — a correlated subquery
per row whose only usable index covered package_id alone, so each one rescanned
its package's whole row set. Measured against a copy of a real installed
system's database (881,959 rows across 1,006 packages, one package holding
118,775 of them) it had not finished after forty minutes at full CPU, and it
printed nothing for the whole of that time. Two defects, and this file guards
against both of them coming back:

  * the cost grew with the SQUARE of the largest package's row count, so the
    tests below build the shape that exposes that — many rows in ONE package —
    rather than only a wide, shallow database that would stay fast either way;
  * a long operation that prints nothing cannot be told from a hang, so the
    output assertions require the update to say what it is doing before it
    starts, while it runs, and when it finishes.

ON THE TIME BUDGETS. They are derived from measurement, not chosen to sit just
under an observed number. On the machine that developed this change, at 20,000
rows in one package the old mechanism took 83.66s and the new one 0.20s; at
200,000 rows across 200 packages, 422.41s against 2.39s. The budgets below sit
roughly 25-75x above what the new mechanism needs and several times below what
the old one needs, so a slower machine has room and a regression still has
nowhere to hide. A wall-clock budget is a blunt instrument on a shared build
machine, which is why the query-plan test is here as well: it fails on the old
mechanism without consulting the clock at all.
"""
import sqlite3
import time

from pkm import output
from pkm.database import PackageDB

# The reporter is imported inside the two tests that need it, not here. Keeping
# it out of the module import is what lets the other nine cases COLLECT against
# a tree that predates this change and fail on the defect itself — a module
# that cannot be imported at all fails at collection and proves only that the
# code is new, which is not the same claim.

# The legacy shape: a files table with no UNIQUE(package_id, path). `source`
# is present because the migration that adds it runs before this one — leaving
# it out here would test a path the real upgrade never takes.
LEGACY_SCHEMA = """
CREATE TABLE installed (id INTEGER PRIMARY KEY, name TEXT, version TEXT);
CREATE TABLE files (
    id INTEGER PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES installed(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    is_dir BOOLEAN DEFAULT 0,
    is_config BOOLEAN DEFAULT 0,
    checksum TEXT,
    is_generated INTEGER DEFAULT 0,
    source TEXT
);
CREATE INDEX idx_files_path ON files(path);
CREATE INDEX idx_files_package ON files(package_id);
"""


def build_legacy_db(path, rows, packages=1, duplicates=0):
    """Write a pre-migration database with `rows` file rows spread over
    `packages` packages, plus `duplicates` extra rows that duplicate an
    existing (package_id, path)."""
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    for pid in range(1, packages + 1):
        conn.execute(
            "INSERT INTO installed (id, name, version) VALUES (?, ?, ?)",
            (pid, f"pkg{pid}", "1.0"))
    conn.executemany(
        "INSERT INTO files (package_id, path, checksum) VALUES (?, ?, ?)",
        [((i % packages) + 1, f"usr/share/p{(i % packages) + 1}/file{i}",
          f"sum{i}") for i in range(rows)])
    if duplicates:
        conn.executemany(
            "INSERT INTO files (package_id, path, checksum) VALUES (?, ?, ?)",
            [((i % packages) + 1, f"usr/share/p{(i % packages) + 1}/file{i}",
              f"newer{i}") for i in range(duplicates)])
    conn.commit()
    conn.close()


def open_and_time(path):
    """Open the database through the real code path and return the seconds the
    open took. Every migration runs, in its true order."""
    started = time.monotonic()
    db = PackageDB(db_path=str(path))
    elapsed = time.monotonic() - started
    db.conn.close()
    return elapsed


# ----------------------------------------------------------------- timing

def test_large_single_package_database_migrates_within_budget(tmp_path):
    """The shape that actually bit: one package holding a great many files.

    This is the pathological case for a per-row correlated lookup, because
    every row rescans every other row of the same package. 20,000 rows took
    83.66s under the old mechanism and 0.20s under this one.
    """
    db_path = tmp_path / "single-package.db"
    build_legacy_db(db_path, rows=20_000, packages=1)

    elapsed = open_and_time(db_path)

    assert elapsed < 15.0, (
        f"the one-time files-table update took {elapsed:.1f}s for 20,000 rows "
        "in a single package. The budget is 15s and the measured cost of this "
        "mechanism is about 0.2s; a number in tens of seconds means the "
        "per-row correlated lookup is back."
    )


def test_hundreds_of_thousands_of_rows_migrate_within_budget(tmp_path):
    """Real-installation scale: 200,000 rows across 200 packages.

    Measured at 422.41s under the old mechanism and 2.39s under this one. A
    real system's database is this size — the specimen that exposed the defect
    held 881,959 rows — so a guard that only ever sees a toy database would not
    have caught it.
    """
    db_path = tmp_path / "large-scale.db"
    build_legacy_db(db_path, rows=200_000, packages=200)

    elapsed = open_and_time(db_path)

    assert elapsed < 60.0, (
        f"the one-time files-table update took {elapsed:.1f}s for 200,000 "
        "rows across 200 packages. The budget is 60s and the measured cost of "
        "this mechanism is about 2.4s."
    )


def test_the_rebuild_does_not_use_a_per_row_correlated_lookup(tmp_path,
                                                              monkeypatch):
    """The same defect, caught without a clock.

    A wall-clock budget can be knocked about by whatever else the machine is
    doing. This asks SQLite directly how it intends to run the statement that
    populates the rebuilt table, and refuses a plan containing a correlated
    scalar subquery — the construct whose cost grows with the square of a
    package's row count. It fails on the old mechanism, whose plan carries two.
    """
    captured = []
    real_connect = sqlite3.connect

    def spy_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(captured.append)
        return conn

    db_path = tmp_path / "planned.db"
    build_legacy_db(db_path, rows=500, packages=5, duplicates=50)
    monkeypatch.setattr(sqlite3, "connect", spy_connect)
    PackageDB(db_path=str(db_path)).conn.close()
    monkeypatch.undo()

    insert_idx = next(
        (i for i, s in enumerate(captured)
         if "insert into files_rebuild" in " ".join(s.split()).lower()), None)
    assert insert_idx is not None, (
        "no statement populating the rebuilt files table was executed — this "
        "test can no longer see the statement it exists to check.")

    # Replay everything the migration did up to that statement into a fresh
    # copy of the same legacy database, so the statement can be explained in
    # the state it actually runs in.
    replay_path = tmp_path / "replay.db"
    build_legacy_db(replay_path, rows=500, packages=5, duplicates=50)
    replay = real_connect(replay_path)
    for stmt in captured[:insert_idx]:
        try:
            replay.execute(stmt)
        except sqlite3.Error:
            pass          # pragmas and transaction control need not replay
    plan = "\n".join(
        str(row) for row in
        replay.execute("EXPLAIN QUERY PLAN " + captured[insert_idx]))
    replay.close()

    assert "CORRELATED" not in plan.upper(), (
        "the statement that rebuilds the files table plans a correlated "
        "scalar subquery, which is evaluated once per row and rescans the "
        "row's whole package. Plan:\n" + plan)


# ----------------------------------------------------------------- output

def run_migration_capturing_output(tmp_path, capsys, rows=400, packages=4,
                                   duplicates=40, level=output.NORMAL):
    """Run the update and return what the user saw, with runs of whitespace
    collapsed to single spaces.

    The collapse is not cosmetic. Everything here goes out through pkm's prose
    wrapper, which breaks lines at the terminal width, so a sentence the user
    reads as one phrase arrives with a newline somewhere in the middle of it —
    the exact position depending on how wide the terminal happened to be. An
    assertion against the raw text would be asserting the wrap column.
    """
    db_path = tmp_path / "reported.db"
    build_legacy_db(db_path, rows=rows, packages=packages,
                    duplicates=duplicates)
    previous = output.process_level()
    output.set_process_level(level)
    try:
        PackageDB(db_path=str(db_path)).conn.close()
    finally:
        output.set_process_level(previous)
    return " ".join(capsys.readouterr().out.split())


def test_the_update_announces_itself_before_it_starts(tmp_path, capsys):
    """The user is told what is about to happen, that it happens once, and how
    much there is to do — before the work, not after it."""
    printed = run_migration_capturing_output(tmp_path, capsys)

    assert "one-time update" in printed
    # 400 rows plus the 40 duplicate rows the helper adds: the count the user
    # is shown is what is actually in the table, not the unique-path count.
    assert "440 file-ownership records" in printed
    assert "runs once on this system" in printed
    assert "changes no installed file" in printed
    # The announcement precedes the outcome, which is the whole point of it.
    assert printed.index("one-time update") < printed.index("finished in")


def test_the_update_reports_each_step_and_its_outcome(tmp_path, capsys):
    printed = run_migration_capturing_output(tmp_path, capsys)

    assert "Checking" in printed and "records for duplicates" in printed
    assert "Rewriting" in printed
    assert "Rebuilding the lookup indexes" in printed
    assert "40 duplicated ownership records were collapsed" in printed


def test_a_database_with_no_duplicates_says_so_rather_than_nothing(
        tmp_path, capsys):
    printed = run_migration_capturing_output(tmp_path, capsys, duplicates=0)

    assert "no duplicated ownership records were found" in printed


def test_quiet_still_gets_the_opening_and_closing_lines(tmp_path, capsys):
    """-q asks for less chatter. It does not ask for a multi-minute pause that
    cannot be told from a freeze, so the two lines that bound the work survive
    at every level while the detail between them does not."""
    printed = run_migration_capturing_output(tmp_path, capsys,
                                             level=output.QUIET)

    assert "one-time update" in printed
    assert "finished in" in printed
    assert "Rewriting" not in printed


def test_the_update_prints_nothing_when_there_is_nothing_to_do(tmp_path,
                                                               capsys):
    """This runs on EVERY database open. A database that already carries the
    constraint must produce no output at all, or every pkm command would
    narrate a migration that is not happening."""
    db_path = tmp_path / "already-done.db"
    PackageDB(db_path=str(db_path)).conn.close()
    capsys.readouterr()

    PackageDB(db_path=str(db_path)).conn.close()

    assert capsys.readouterr().out == ""


def test_user_facing_output_needs_no_internal_knowledge_to_read(tmp_path,
                                                               capsys):
    """The output standard's bar: a user must be able to parse what they are
    shown without knowing how the package manager is built."""
    printed = run_migration_capturing_output(tmp_path, capsys).lower()

    for internal in ("package_id", "unique(", "sqlite", "files_rebuild",
                     "files_collapse", "_migrate", "rowid", "subquery"):
        assert internal not in printed, (
            f"user-facing output names {internal!r}, which is internal "
            "vocabulary a user has no way to interpret.")


def test_a_step_that_runs_long_keeps_reporting_while_it_runs(tmp_path, capsys,
                                                             monkeypatch):
    """The heartbeat is the part that turns a silent grind into a visibly
    working one, so it is proven to fire rather than assumed to.

    The thresholds are lowered instead of the work being made slow: what is
    under test is that the callback is wired to the statement that is running,
    not how long any particular database takes.

    The callback granularity has to come down with them. SQLite calls the
    handler every _PROGRESS_OPS virtual-machine instructions, and a database
    small enough for a fast test never executes that many — which is correct
    for real use and would make this test pass vacuously if only the clock
    thresholds were changed. Lowering all three is what makes a heartbeat
    reachable at this size.
    """
    from pkm.database import _OneTimeUpdateReport

    monkeypatch.setattr(_OneTimeUpdateReport, "HEARTBEAT_AFTER", 0.0)
    monkeypatch.setattr(_OneTimeUpdateReport, "HEARTBEAT_EVERY", 0.0)
    monkeypatch.setattr(_OneTimeUpdateReport, "_PROGRESS_OPS", 1_000)

    printed = run_migration_capturing_output(tmp_path, capsys, rows=2_000,
                                             packages=4, duplicates=200)

    assert "still working" in printed, (
        "no progress line was printed while a step was running — with the "
        "heartbeat thresholds at zero every step should report at least once, "
        "so the callback is not reaching the running statement.")


def test_the_heartbeat_cannot_abort_the_statement_it_reports_on(tmp_path,
                                                                capsys,
                                                                monkeypatch):
    """A SQLite progress callback that raises, or returns a true value, aborts
    the statement in progress. The reporter must therefore survive its own
    emitter failing — a broken console must not be able to break a database
    update.

    The failure is injected at the CONSOLE, not at the reporter's own method:
    the guard being tested lives inside that method, so replacing it would
    test a code path that does not exist in the shipped object.
    """
    from pkm.database import _OneTimeUpdateReport

    monkeypatch.setattr(_OneTimeUpdateReport, "HEARTBEAT_AFTER", 0.0)
    monkeypatch.setattr(_OneTimeUpdateReport, "HEARTBEAT_EVERY", 0.0)
    monkeypatch.setattr(_OneTimeUpdateReport, "_PROGRESS_OPS", 1_000)

    def explode(_text):
        raise RuntimeError("the console is broken")

    monkeypatch.setattr(output, "emit_info", explode)
    monkeypatch.setattr(output, "emit_done", explode)

    db_path = tmp_path / "hostile-console.db"
    build_legacy_db(db_path, rows=2_000, packages=4, duplicates=200)
    db = PackageDB(db_path=str(db_path))

    rows = db.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    sql = db.conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='files'"
    ).fetchone()[0]
    db.conn.close()

    assert rows == 2_000, "the update lost rows when the console misbehaved"
    assert "unique(package_id, path)" in " ".join(sql.split()).lower()
