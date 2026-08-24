"""One writer at a time for pkm's mutating operations — the waiting half.

WHAT ALREADY EXISTED, so this file is honest about what it adds. pkm has
serialized its mutating subcommands since H-023: `_pkm_mutation_lock` takes an
`fcntl.flock` on a lock file at dispatch and holds it around the handler for the
whole transaction. That is a kernel-held lock, not a check-then-act, and a crash
releases it by construction. Measured on a real install before any of this was
written: a second `pkm vacuum` launched while another process held the lock
returned rc 1 in under a second, naming the lock file.

WHAT WAS MISSING, and what these tests pin:

1. THE LOCK PATH WAS A CONSTANT. `PKM_LOCK_PATH = Path("/var/lock/pkm.lock")`
   with no override, while the database path has honoured IGOS_PKM_DB all along.
   A test that drove two real concurrent mutations therefore had to contend on
   the MACHINE-WIDE lock — a test that takes a real system lock on whatever
   machine runs it. The path now resolves from IGOS_PKM_LOCK when it is set, so
   a scratch prefix is genuinely a scratch prefix, and these tests can run two
   real processes without touching anything the machine uses.

2. A SECOND INVOCATION COULD ONLY FAIL, NEVER WAIT. Acquisition was
   LOCK_EX | LOCK_NB, so contention was always an immediate refusal. A person at
   a terminal who has just been told "another pkm operation is in progress"
   almost always wants to wait for it; a script or a unit almost never does. The
   decided shape: wait when stdin is a terminal, refuse immediately when it is
   not, and let --wait / --no-wait override either way, with --wait-timeout
   bounding the wait and the refusal text unchanged when it expires.

Every test here runs as an ordinary user against paths it creates itself. The
concurrency tests use real separate PROCESSES holding a real flock — a stub
cannot show that two processes serialize.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pkm import cli


REPO_ROOT = Path(__file__).resolve().parents[2]


def _holder_program(lock_path: Path, hold_seconds: float, ready_file: Path) -> str:
    """A real second process that takes the real lock and keeps it.

    It writes `ready_file` only AFTER the lock is held, so the test never races
    the holder's startup — a sleep here would make the test's own timing the
    thing under measurement.
    """
    return (
        "import fcntl, pathlib, sys, time\n"
        f"fd = open({str(lock_path)!r}, 'w')\n"
        "fcntl.flock(fd.fileno(), fcntl.LOCK_EX)\n"
        f"pathlib.Path({str(ready_file)!r}).write_text('held')\n"
        f"time.sleep({hold_seconds})\n"
        "fcntl.flock(fd.fileno(), fcntl.LOCK_UN)\n"
        "fd.close()\n"
    )


def _start_holder(lock_path: Path, hold_seconds: float, tmp_path: Path):
    ready = tmp_path / "holder-ready"
    proc = subprocess.Popen(
        [sys.executable, "-c", _holder_program(lock_path, hold_seconds, ready)],
        cwd=str(REPO_ROOT),
    )
    deadline = time.monotonic() + 10
    while not ready.exists():
        if time.monotonic() > deadline:
            proc.kill()
            pytest.fail("the holder process never reported holding the lock")
        time.sleep(0.02)
    return proc


# --------------------------------------------------------------------------
# 1. the lock path is resolvable, so a scratch prefix is really scratch
# --------------------------------------------------------------------------

def test_the_lock_path_defaults_to_the_system_path(monkeypatch):
    monkeypatch.delenv("IGOS_PKM_LOCK", raising=False)
    assert cli.resolve_lock_path() == Path("/var/lock/pkm.lock")


def test_the_lock_path_honours_the_environment(monkeypatch, tmp_path):
    scratch = tmp_path / "prefix" / "pkm.lock"
    monkeypatch.setenv("IGOS_PKM_LOCK", str(scratch))
    assert cli.resolve_lock_path() == scratch


def test_a_scratch_lock_is_used_instead_of_the_system_lock(monkeypatch, tmp_path):
    """The point of the override: nothing under /var is opened."""
    scratch = tmp_path / "pkm.lock"
    monkeypatch.setenv("IGOS_PKM_LOCK", str(scratch))
    with cli._pkm_mutation_lock("vacuum"):
        assert scratch.exists(), "the scratch lock file was not the one taken"


# --------------------------------------------------------------------------
# 2. without a terminal, contention still refuses at once — today's behaviour,
#    now pinned so it cannot drift into a hang inside a unit or a script
# --------------------------------------------------------------------------

def test_without_a_terminal_a_second_mutation_refuses_immediately(
        monkeypatch, tmp_path, capsys):
    lock = tmp_path / "pkm.lock"
    monkeypatch.setenv("IGOS_PKM_LOCK", str(lock))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    holder = _start_holder(lock, 30, tmp_path)
    try:
        started = time.monotonic()
        with pytest.raises(SystemExit) as exc:
            with cli._pkm_mutation_lock("vacuum"):
                pass
        elapsed = time.monotonic() - started
        assert exc.value.code == 1
        assert elapsed < 5, (
            f"a non-interactive invocation waited {elapsed:.1f}s; it must refuse "
            f"at once so a unit or a script never hangs")
        assert "another pkm operation is in progress" in capsys.readouterr().err
    finally:
        holder.kill()
        holder.wait()


# --------------------------------------------------------------------------
# 3. at a terminal, the default is to wait — and the wait ends when the holder
#    lets go, not on a timer
# --------------------------------------------------------------------------

def test_at_a_terminal_a_second_mutation_waits_for_the_holder(
        monkeypatch, tmp_path, capsys):
    lock = tmp_path / "pkm.lock"
    monkeypatch.setenv("IGOS_PKM_LOCK", str(lock))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    holder = _start_holder(lock, 3, tmp_path)
    try:
        started = time.monotonic()
        with cli._pkm_mutation_lock("vacuum"):
            waited = time.monotonic() - started
        assert waited >= 2, (
            f"the second invocation returned after {waited:.1f}s; it cannot have "
            f"waited for a holder that kept the lock for 3s")
        assert waited < 20, "the wait outlived the holder by too much"
        err = capsys.readouterr().err
        assert "waiting" in err.lower(), "a wait must say that it is waiting"
    finally:
        holder.kill()
        holder.wait()


def test_the_wait_names_the_process_that_holds_the_lock(
        monkeypatch, tmp_path, capsys):
    lock = tmp_path / "pkm.lock"
    monkeypatch.setenv("IGOS_PKM_LOCK", str(lock))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    holder = _start_holder(lock, 3, tmp_path)
    try:
        with cli._pkm_mutation_lock("vacuum"):
            pass
        err = capsys.readouterr().err
        assert str(holder.pid) in err, (
            "the waiting message must name the holder's pid, or the person "
            "waiting has nothing to look at")
    finally:
        holder.kill()
        holder.wait()


# --------------------------------------------------------------------------
# 4. the flags override the terminal in both directions
# --------------------------------------------------------------------------

def test_no_wait_refuses_even_at_a_terminal(monkeypatch, tmp_path, capsys):
    lock = tmp_path / "pkm.lock"
    monkeypatch.setenv("IGOS_PKM_LOCK", str(lock))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    holder = _start_holder(lock, 30, tmp_path)
    try:
        started = time.monotonic()
        with pytest.raises(SystemExit) as exc:
            with cli._pkm_mutation_lock("vacuum", wait=False):
                pass
        assert exc.value.code == 1
        assert time.monotonic() - started < 5
        assert "another pkm operation is in progress" in capsys.readouterr().err
    finally:
        holder.kill()
        holder.wait()


def test_wait_waits_even_without_a_terminal(monkeypatch, tmp_path):
    lock = tmp_path / "pkm.lock"
    monkeypatch.setenv("IGOS_PKM_LOCK", str(lock))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    holder = _start_holder(lock, 3, tmp_path)
    try:
        started = time.monotonic()
        with cli._pkm_mutation_lock("vacuum", wait=True):
            waited = time.monotonic() - started
        assert waited >= 2
    finally:
        holder.kill()
        holder.wait()


# --------------------------------------------------------------------------
# 5. a wait is bounded, and giving up says the same thing as refusing
# --------------------------------------------------------------------------

def test_a_wait_gives_up_at_the_timeout_with_the_refusal_text(
        monkeypatch, tmp_path, capsys):
    lock = tmp_path / "pkm.lock"
    monkeypatch.setenv("IGOS_PKM_LOCK", str(lock))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    holder = _start_holder(lock, 30, tmp_path)
    try:
        started = time.monotonic()
        with pytest.raises(SystemExit) as exc:
            with cli._pkm_mutation_lock("vacuum", wait=True, wait_timeout=2):
                pass
        elapsed = time.monotonic() - started
        assert exc.value.code == 1
        assert 1.5 <= elapsed < 15, (
            f"the wait ran {elapsed:.1f}s against a 2s timeout")
        assert "another pkm operation is in progress" in capsys.readouterr().err
    finally:
        holder.kill()
        holder.wait()


def test_the_default_wait_timeout_is_ten_minutes():
    assert cli.PKM_LOCK_WAIT_TIMEOUT_DEFAULT == 600


# --------------------------------------------------------------------------
# 6. the flags exist on the command line, in both spellings
# --------------------------------------------------------------------------

@pytest.mark.parametrize("argv,expected_wait,expected_timeout", [
    (["vacuum"], None, 600),
    (["--wait", "vacuum"], True, 600),
    (["--no-wait", "vacuum"], False, 600),
    (["--wait-timeout", "45", "vacuum"], None, 45),
])
def test_the_command_line_carries_the_waiting_options(argv, expected_wait,
                                                      expected_timeout):
    parser = cli.build_parser()
    args = parser.parse_args(argv)
    assert getattr(args, "wait", "MISSING") == expected_wait
    assert getattr(args, "wait_timeout", "MISSING") == expected_timeout


# --------------------------------------------------------------------------
# 7. the control: with nothing holding the lock, neither shape delays anything
# --------------------------------------------------------------------------

@pytest.mark.parametrize("wait", [None, True, False])
def test_an_uncontended_lock_is_taken_at_once(monkeypatch, tmp_path, wait):
    lock = tmp_path / "pkm.lock"
    monkeypatch.setenv("IGOS_PKM_LOCK", str(lock))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    started = time.monotonic()
    with cli._pkm_mutation_lock("vacuum", wait=wait):
        pass
    assert time.monotonic() - started < 2
