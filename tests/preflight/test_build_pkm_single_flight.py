"""The build's premise — one pkm at a time — is asserted, not assumed.

pkm's mutation lock has one escape: where the lock directory cannot be created it
runs the mutation with NO lock held, on the stated assumption that a build chroot
runs one pkm at a time. scripts/lib/pkm-single-flight.sh is the check that turns
that assumption into a measured property, at the two places the build invokes
pkm (scripts/pkg-functions.sh's pkg_install, and scripts/chroot-config-ch9.sh).

These tests drive the real shell function in real concurrent PROCESSES against a
stand-in `pkm` on PATH. The stand-in is deliberate: what is under test is the
guard, and invoking the real package manager from a test would be both slow and a
mutation of this machine.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "lib" / "pkm-single-flight.sh"


def _fake_pkm(tmp_path: Path, sleep_seconds: float) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    fake = bindir / "pkm"
    fake.write_text(
        "#!/bin/bash\n"
        f"echo \"stand-in pkm: $*\"\n"
        f"sleep {sleep_seconds}\n"
    )
    fake.chmod(0o755)
    return bindir


def _run(tmp_path: Path, bindir: Path, logs: Path, background: bool = False):
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["IGOS_LOGS"] = str(logs)
    cmd = ["bash", "-c", f'. "{LIB}"; pkg_run_pkm_single_flight import']
    if background:
        # its own session, so the test can kill the shell AND the pkm child it
        # spawned. Killing only the shell leaves the child holding the inherited
        # file descriptor, and the lock stays held by a process the test thinks
        # it stopped — a false negative that looks like a wedged build.
        return subprocess.Popen(cmd, env=env, cwd=str(tmp_path),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, start_new_session=True)
    return subprocess.run(cmd, env=env, cwd=str(tmp_path), capture_output=True,
                          text=True)


def test_the_library_exists_and_is_syntactically_valid():
    assert LIB.is_file(), f"{LIB} is missing; the build has no single-flight check"
    rc = subprocess.run(["bash", "-n", str(LIB)], capture_output=True, text=True)
    assert rc.returncode == 0, rc.stderr


def test_one_caller_alone_runs_and_does_not_halt(tmp_path):
    """The control. A guard that refused everything would also 'pass' the
    concurrency test below while breaking every build."""
    logs = tmp_path / "logs"; logs.mkdir()
    r = _run(tmp_path, _fake_pkm(tmp_path, 0), logs)
    assert r.returncode == 0, f"a single caller was refused: {r.stdout}{r.stderr}"
    assert "stand-in pkm" in (r.stdout + r.stderr), "pkm was never invoked"
    assert "HALT" not in (r.stdout + r.stderr)


def test_a_second_concurrent_caller_halts_and_says_why(tmp_path):
    logs = tmp_path / "logs"; logs.mkdir()
    bindir = _fake_pkm(tmp_path, 4)
    first = _run(tmp_path, bindir, logs, background=True)
    try:
        # wait until the first caller is demonstrably inside pkm, so the second
        # one is a real overlap rather than a race with process startup
        deadline = time.monotonic() + 15
        lock = logs / ".pkm-single-flight.lock"
        while not lock.exists():
            if time.monotonic() > deadline:
                pytest.fail("the first caller never took the build lock")
            time.sleep(0.05)
        time.sleep(0.5)
        second = _run(tmp_path, bindir, logs)
        out = second.stdout + second.stderr
        assert second.returncode != 0, (
            "a second concurrent pkm invocation was allowed; the build's premise "
            f"that one runs at a time is not asserted. Output: {out}")
        assert "HALT" in out, f"it refused without saying it halted: {out}"
        assert "one at a time" in out or "one pkm at a time" in out, (
            f"the halt does not state the premise it is defending: {out}")
        assert str(lock) in out, f"the halt does not name the lock: {out}"
    finally:
        try:
            os.killpg(os.getpgid(first.pid), 9)
        except (ProcessLookupError, PermissionError):
            first.kill()
        first.wait()


def test_the_lock_is_released_when_the_holder_dies(tmp_path):
    """A crash must not wedge the build. flock gives this by construction, and
    the test exists because 'by construction' is exactly the kind of claim that
    should be measured once."""
    logs = tmp_path / "logs"; logs.mkdir()
    bindir = _fake_pkm(tmp_path, 30)
    first = _run(tmp_path, bindir, logs, background=True)
    lock = logs / ".pkm-single-flight.lock"
    deadline = time.monotonic() + 15
    while not lock.exists():
        if time.monotonic() > deadline:
            os.killpg(os.getpgid(first.pid), 9); first.wait()
            pytest.fail("the first caller never took the build lock")
        time.sleep(0.05)
    time.sleep(0.5)
    os.killpg(os.getpgid(first.pid), 9)
    first.wait()
    # give the kernel a moment to reap the group and drop the descriptors
    time.sleep(1.0)
    quick = _fake_pkm(tmp_path, 0)
    r = _run(tmp_path, quick, logs)
    assert r.returncode == 0, (
        "the lock survived its holder's death and the build would stay wedged: "
        f"{r.stdout}{r.stderr}")


def test_both_build_call_sites_use_the_guard():
    """The population check. A guard on one of the two places the build invokes
    pkm leaves the other one exactly as it was, while reading as covered."""
    pkg_functions = (REPO_ROOT / "scripts" / "pkg-functions.sh").read_text()
    ch9 = (REPO_ROOT / "scripts" / "chroot-config-ch9.sh").read_text()
    assert "pkg_run_pkm_single_flight import" in pkg_functions, (
        "pkg_install no longer routes its pkm invocation through the guard")
    assert "pkg_run_pkm_single_flight import" in ch9, (
        "the config phase's pkm invocation does not go through the guard")
    # and neither may still carry a bare invocation that bypasses it
    for name, text in (("pkg-functions.sh", pkg_functions),
                       ("chroot-config-ch9.sh", ch9)):
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "pkg_run_pkm_single_flight" in stripped:
                continue
            if stripped.startswith("pkm import"):
                pytest.fail(f"{name} still invokes `pkm import` outside the guard: "
                            f"{stripped!r}")
