"""A pkm read never reads a database a pkm write is changing underneath it.

WHAT ALREADY EXISTED, so this file is honest about what it adds. pkm has
serialized its MUTATING subcommands since H-023: `fcntl.flock(LOCK_EX)` taken
at dispatch and held around the handler, with a wait-or-refuse policy and a
refusal that names the holding process. tests/pkm/test_mutation_lock_waiting.py
pins that half and none of it changes here.

WHAT WAS MISSING. The gate at dispatch was entered ONLY for
PKM_MUTATING_COMMANDS. Every read command fell straight through it and then
opened the database with `file:...?immutable=1` — a promise to SQLite that the
file will not change while it is open, which nothing enforced. The two ways that
promise is broken were measured on an installed machine on 2026-09-20:

  * STALE — an immutable reader held its own view while a writer moved on. The
    reader reported 1 row where the truth was 5002, and `PRAGMA integrity_check`
    on the same file said `ok`. A read that answers confidently with the wrong
    number is worse than one that refuses.
  * MALFORMED — a page rewritten under the reader produced
    `sqlite3.DatabaseError: database disk image is malformed`, as a TRACEBACK,
    from a real `pkm verify --all --detail` at 512 of 875 packages while a
    `pkm reinstall` ran beside it. `integrity_check` said `ok` afterwards: the
    database was never malformed, the reader's view of it was. Telling a person
    their package database is corrupt when it is not is the worst answer of the
    three.

WHAT THESE TESTS PIN.
  1. A read command takes a SHARED lock on the same lock file, so readers run
     concurrently with each other and never beside a writer.
  2. Contention says the one thing pkm already says about contention, through
     the same wait-and-refuse path, so a reader and a writer do not describe the
     same situation differently.
  3. The lock file is PACKAGE-OWNED and world-readable, because /run/lock is
     root-owned and cleared at every boot, so an unprivileged reader cannot
     create it and must be able to open one that is already there.
  4. A reader that still cannot open the lock file says so in one line and
     proceeds unlocked. Silence there would be the same masked failure this cut
     exists to remove.
  5. `sqlite3.DatabaseError` on a read is reported in pkm's own voice with a
     non-zero exit and no traceback, and never with the word "malformed", which
     the machine's own integrity check contradicts.
  6. `pkm verify --detail` prints the PATH of every file it calls MODIFIED or
     MISSING. Today it prints those paths for one named package but only counts
     them for `--all`, so the run that checks the whole machine is the one that
     cannot say which file is wrong.

Every test runs as an ordinary user against paths it creates itself. The
concurrency tests use real separate processes holding a real flock; a stub
cannot show that two processes do or do not serialize.
"""

from __future__ import annotations

import fcntl
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pkm import cli


REPO_ROOT = Path(__file__).resolve().parents[2]
TMPFILES = REPO_ROOT / "packages" / "core" / "pkm" / "pkm.tmpfiles"


def _holder_program(lock_path: Path, mode: str, hold_seconds: float,
                    ready_file: Path) -> str:
    """A real second process holding a real flock in `mode` ('EX' or 'SH').

    It writes `ready_file` only AFTER the lock is held, so no test races the
    holder's startup.
    """
    return (
        "import fcntl, pathlib, time\n"
        f"fd = open({str(lock_path)!r}, 'r+')\n"
        f"fcntl.flock(fd.fileno(), fcntl.LOCK_{mode})\n"
        f"pathlib.Path({str(ready_file)!r}).write_text('held')\n"
        f"time.sleep({hold_seconds})\n"
        "fcntl.flock(fd.fileno(), fcntl.LOCK_UN)\n"
        "fd.close()\n"
    )


def _start_holder(lock_path: Path, mode: str, hold_seconds: float, tmp_path: Path,
                  tag: str = "holder"):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if not lock_path.exists():
        lock_path.write_text("")
    ready = tmp_path / f"{tag}-ready"
    proc = subprocess.Popen(
        [sys.executable, "-c",
         _holder_program(lock_path, mode, hold_seconds, ready)],
        cwd=str(REPO_ROOT),
    )
    deadline = time.monotonic() + 10
    while not ready.exists():
        if time.monotonic() > deadline:
            proc.kill()
            pytest.fail(f"the {tag} process never reported holding the lock")
        time.sleep(0.02)
    return proc


def _command_lock(*args, **kwargs):
    """The dispatch-time lock, under whichever name the tree gives it.

    The context manager was named for the only thing it used to do. Reading it
    by either name keeps this file honest about behaviour rather than about a
    rename: at the base it is `_pkm_mutation_lock` and enters for writers only.
    """
    fn = getattr(cli, "_pkm_command_lock", None) or cli._pkm_mutation_lock
    return fn(*args, **kwargs)


# --------------------------------------------------------------------------
# 1. a read command takes a shared lock, and a writer excludes it
# --------------------------------------------------------------------------

def test_a_read_command_does_not_run_beside_a_writer(
        monkeypatch, tmp_path, capsys):
    """With a writer holding the lock, a non-interactive read must not proceed.

    At the base this test fails by RETURNING: the read never looks at the lock,
    so it sails past a held exclusive lock and goes on to read a database that
    is being rewritten — the stale-and-malformed window itself.
    """
    lock = tmp_path / "pkm.lock"
    monkeypatch.setenv("IGOS_PKM_LOCK", str(lock))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    holder = _start_holder(lock, "EX", 30, tmp_path)
    try:
        started = time.monotonic()
        with pytest.raises(SystemExit) as exc:
            with _command_lock("list"):
                pass
        elapsed = time.monotonic() - started
        assert exc.value.code == 1
        assert elapsed < 5, (
            f"a non-interactive read waited {elapsed:.1f}s; it must refuse at "
            f"once, exactly as a non-interactive write does")
        err = capsys.readouterr().err
        assert "another pkm operation is in progress" in err, (
            "a reader must describe contention in the same words a writer "
            "uses; two vocabularies for one situation is two bugs to read")
    finally:
        holder.kill()
        holder.wait()


def test_a_read_waits_for_a_writer_at_a_terminal(monkeypatch, tmp_path):
    """At a terminal the reader waits for the writer, as a writer would."""
    lock = tmp_path / "pkm.lock"
    monkeypatch.setenv("IGOS_PKM_LOCK", str(lock))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    holder = _start_holder(lock, "EX", 3, tmp_path)
    try:
        started = time.monotonic()
        with _command_lock("list"):
            waited = time.monotonic() - started
        assert waited >= 2, (
            f"the read returned after {waited:.1f}s; it cannot have waited for "
            f"a writer that held the lock for 3s")
        assert waited < 20
    finally:
        holder.kill()
        holder.wait()


def test_two_reads_run_at_the_same_time(monkeypatch, tmp_path):
    """A shared lock must not turn readers into a queue.

    A reader holds the lock for the whole of its handler, so if readers excluded
    each other, `pkm list` on a busy machine would serialize behind every other
    `pkm list`. With another reader holding LOCK_SH, this read proceeds at once.
    """
    lock = tmp_path / "pkm.lock"
    monkeypatch.setenv("IGOS_PKM_LOCK", str(lock))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    holder = _start_holder(lock, "SH", 30, tmp_path, tag="reader")
    try:
        started = time.monotonic()
        with _command_lock("list"):
            pass
        elapsed = time.monotonic() - started
        assert elapsed < 5, (
            f"a read waited {elapsed:.1f}s behind another READ; readers must "
            f"share the lock, not queue on it")
    finally:
        holder.kill()
        holder.wait()


def test_the_lock_a_read_takes_is_shared_not_exclusive(monkeypatch, tmp_path):
    """Read it from the kernel, not from the code: while the read holds its
    lock, a second process must still be able to take LOCK_SH and must NOT be
    able to take LOCK_EX."""
    lock = tmp_path / "pkm.lock"
    lock.write_text("")
    monkeypatch.setenv("IGOS_PKM_LOCK", str(lock))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    with _command_lock("list"):
        probe = open(str(lock), "r+")
        try:
            fcntl.flock(probe.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
        except BlockingIOError:
            pytest.fail("another reader could not share the lock this read holds")
        with pytest.raises(BlockingIOError):
            fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        probe.close()


def test_a_write_still_takes_an_exclusive_lock(monkeypatch, tmp_path):
    """The writer half is unchanged: nothing may share a writer's lock."""
    lock = tmp_path / "pkm.lock"
    monkeypatch.setenv("IGOS_PKM_LOCK", str(lock))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    with _command_lock("vacuum"):
        probe = open(str(lock), "r+")
        with pytest.raises(BlockingIOError):
            fcntl.flock(probe.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        probe.close()


# --------------------------------------------------------------------------
# 2. a reader does not need to CREATE the lock file, and says so when it
#    cannot open one
# --------------------------------------------------------------------------

def test_a_read_does_not_create_or_truncate_the_lock_file(
        monkeypatch, tmp_path):
    """A reader opens the lock file; it does not make it and does not empty it.

    /run/lock is root-owned, so an unprivileged reader cannot create anything
    there. A reader that opened the file for writing would fail on every
    installed machine for exactly the users this lock is meant to protect.
    """
    lock = tmp_path / "pkm.lock"
    lock.write_text("sentinel")
    monkeypatch.setenv("IGOS_PKM_LOCK", str(lock))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    with _command_lock("list"):
        pass
    assert lock.read_text() == "sentinel", (
        "the read truncated the lock file, so it opened it for writing")


def test_a_reader_on_a_real_system_with_no_lock_file_says_so_and_continues(
        monkeypatch, tmp_path, capsys):
    """Unlocked is an acceptable outcome for a reader. Silently unlocked is not.

    The lock DIRECTORY exists and the file does not: a machine that has pkm
    installed but has not rebooted since, so tmpfiles has not created the file
    yet. That read runs unserialized and has to say so.
    """
    lockdir = tmp_path / "var" / "lock"
    lockdir.mkdir(parents=True)
    lock = lockdir / "pkm.lock"
    monkeypatch.setenv("IGOS_PKM_LOCK", str(lock))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    entered = False
    with _command_lock("list"):
        entered = True
    assert entered, "a reader with no lock file must still run"
    err = capsys.readouterr().err
    assert "lock" in err.lower(), (
        "a read that ran without the lock said nothing about it; that is the "
        "masked-failure shape this cut removes")
    assert not lock.exists(), "the reader created the lock file after all"


def test_a_reader_against_a_root_with_no_lock_directory_is_quiet(
        monkeypatch, tmp_path, capsys):
    """An install root has no /var/lock and no second pkm to race.

    The writer path has treated a missing lock directory as lock-free by
    construction since the chroot-install fix. The reader does the same, and
    says nothing, because a warning that fires on every ordinary `--root` read
    is how people learn to skip warnings.
    """
    lock = tmp_path / "scratch-root" / "var" / "lock" / "pkm.lock"
    monkeypatch.setenv("IGOS_PKM_LOCK", str(lock))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    entered = False
    with _command_lock("list"):
        entered = True
    assert entered
    assert capsys.readouterr().err == "", (
        "a read against a scratch install root warned about a lock file that "
        "root was never going to have")


def test_the_lock_file_ships_with_the_package(monkeypatch):
    """The lock file is created at boot by the package, world-readable.

    /run/lock is root-owned 0755 and cleared at every boot, so nothing an
    unprivileged reader does can bring the file into existence. It has to be
    shipped, by the same tmpfiles route this package already uses for its cache
    directories.
    """
    assert TMPFILES.exists(), f"{TMPFILES} is missing"
    text = TMPFILES.read_text()
    rows = [ln.split() for ln in text.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]
    lock_rows = [r for r in rows if len(r) >= 4 and r[1] == "/var/lock/pkm.lock"]
    assert lock_rows, (
        "packages/core/pkm/pkm.tmpfiles does not create /var/lock/pkm.lock, so "
        "on an installed machine an unprivileged reader has no lock file to "
        "open and every read runs unlocked")
    row = lock_rows[0]
    assert row[0] == "f", (
        f"the lock file is declared as type {row[0]!r}; it is a file, so it is "
        f"declared with f")
    assert row[2] == "0644", (
        f"the lock file ships mode {row[2]}; a non-root reader must be able to "
        f"open it, so it is 0644")
    assert row[3] == "root", f"the lock file ships owned by {row[3]}, not root"


# --------------------------------------------------------------------------
# 3. a read that meets a concurrent write says so in pkm's words
# --------------------------------------------------------------------------

def test_a_database_error_on_a_read_is_reported_in_pkms_own_words(capsys):
    """Never a traceback, never "malformed", always a non-zero exit.

    The word matters. `integrity_check` on the same file says `ok`, so telling
    a person their package database is malformed sends them to repair something
    that is not broken. What actually happened is that another pkm operation
    changed the file while this one was reading it.
    """
    report = getattr(cli, "report_concurrent_read_failure", None)
    assert report is not None, (
        "pkm has no place that turns a read-time sqlite3.DatabaseError into an "
        "explanation, so it reaches the user as a traceback")
    err = sqlite3.DatabaseError("database disk image is malformed")
    with pytest.raises(SystemExit) as exc:
        report("verify", err)
    assert exc.value.code != 0, "a failed read must not exit 0"
    out = capsys.readouterr()
    text = out.err + out.out
    assert "malformed" not in text.lower(), (
        "the report repeats SQLite's word for it; the machine's own integrity "
        "check contradicts that word")
    assert "Traceback" not in text
    assert "another pkm operation" in text, (
        "the report must name the actual cause — a concurrent pkm operation")


def test_the_read_path_catches_a_database_error(monkeypatch, capsys):
    """The dispatch really routes a read-time DatabaseError into that report."""
    assert hasattr(cli, "report_concurrent_read_failure")
    handled = {}

    def _fake_report(command, err):
        handled["command"] = command
        raise SystemExit(1)

    monkeypatch.setattr(cli, "report_concurrent_read_failure", _fake_report)

    def _boom(db, args):
        raise sqlite3.DatabaseError("database disk image is malformed")

    guard = getattr(cli, "run_read_handler", None)
    assert guard is not None, (
        "there is no single place a read handler runs, so the DatabaseError "
        "guard cannot be applied once for every read command")
    with pytest.raises(SystemExit):
        guard("list", _boom, None, None)
    assert handled.get("command") == "list"


# --------------------------------------------------------------------------
# 4. verify --detail names the files it calls wrong
# --------------------------------------------------------------------------

def test_verify_all_detail_prints_the_path_of_every_modified_and_missing_file(
        capsys):
    """A whole-machine verify that cannot say WHICH file is wrong is a number,
    not a report. The per-package run has printed these paths all along; the
    --all run printed only counts."""
    printer = getattr(cli, "_print_file_problem_detail", None)
    assert printer is not None, (
        "there is no printer for the missing/modified paths under --detail, so "
        "`pkm verify --all --detail` reports counts and no paths")
    printer({"missing": ["usr/bin/one", "etc/two.conf"],
             "modified": ["usr/lib/three.so"]})
    out = capsys.readouterr().out
    for path in ("/usr/bin/one", "/etc/two.conf", "/usr/lib/three.so"):
        assert path in out, f"{path} was not named under --detail"


def test_verify_detail_does_not_silently_truncate_a_long_list(capsys):
    """Twenty paths and a stop is a curated instrument. Under --detail the run
    either names them all or says how many it did not name."""
    printer = getattr(cli, "_print_file_problem_detail", None)
    assert printer is not None
    many = [f"usr/share/f{i:03d}" for i in range(50)]
    printer({"missing": many, "modified": []})
    out = capsys.readouterr().out
    named = sum(1 for p in many if f"/{p}" in out)
    assert named == len(many) or "more" in out, (
        f"--detail named {named} of {len(many)} paths and said nothing about "
        f"the rest")


# --------------------------------------------------------------------------
# 5. the lock is taken BEFORE the database is opened
# --------------------------------------------------------------------------

def test_the_database_is_opened_inside_the_lock_not_before_it():
    """A lock acquired after the read has begun protects nothing.

    A read-only open is not a cheap handle: it runs `PRAGMA table_info` to
    learn which columns this database actually has, and that read meets the
    same rewritten pages every other read does. Measured on 2026-09-20 against
    a scratch root with a writer looping beside forty reads: with the open
    ahead of the lock, one read in forty still died with a traceback inside
    the open itself while the handler was fully protected.

    Read from the source rather than from a stub, because what is being pinned
    is an ORDER in one function and any stub of it would be a stub of the
    answer: in `main`, the line that enters the lock must come before the line
    that opens the database.
    """
    import inspect

    src = inspect.getsource(cli.main)
    lock_at = src.find("_pkm_command_lock(args.command")
    assert lock_at != -1, "main no longer enters the lock by that name"
    assert "PackageDB(db_path" in src, "main no longer opens the database here"

    # The open lives in a closure, so DEFINITION order says nothing. What is
    # pinned is where the closure is CALLED: every call must be after the line
    # that enters the lock.
    calls = [i for i in range(len(src))
             if src.startswith("_open_database()", i)
             and src[max(0, i - 4):i] != "def "]
    assert calls, "main never calls the database opener"
    early = [i for i in calls if i < lock_at]
    assert not early, (
        f"main opens the package database at offset(s) {early} — before it "
        f"enters the lock at offset {lock_at} — so the open itself runs "
        f"unprotected against a concurrent write")


def test_the_read_guard_covers_the_open_as_well_as_the_handler():
    """The concurrent-write guard wraps the open too, not only the handler."""
    assert hasattr(cli, "_run_with_database"), (
        "there is no single place that owns opening, running and closing for "
        "a guarded read, so the open cannot be inside the guard")
    src = __import__("inspect").getsource(cli.main)
    guarded = src[src.find("run_read_handler("):]
    assert "_run_with_database" in guarded[:400], (
        "the read path does not route its database open through the guard")
