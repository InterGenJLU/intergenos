"""Tests for E1.B.6 generate-repodb.py — index generator + signer."""

import gzip
import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "generate-repodb.py"

# Load module by file path (scripts/ has no __init__.py)
spec = importlib.util.spec_from_file_location("generate_repodb", SCRIPT_PATH)
_grm = importlib.util.module_from_spec(spec)
sys.modules["generate_repodb"] = _grm
spec.loader.exec_module(_grm)

_load_release_keys = _grm._load_release_keys


def _write_valid_archive(path, name, version):
    """Write a minimal but VALID .igos.tar.gz carrying a .PKGINFO member.

    generate-repodb.py (via pkm.repo._read_package_meta, PKM-A13) fail-closes
    on a corrupt / non-tar archive — a present-but-unreadable archive must halt
    the index publish rather than be silently dropped. So index-generation
    tests must feed real gzip tarballs; dummy bytes would (correctly) abort.
    """
    import io
    import tarfile

    with tarfile.open(path, "w:gz") as tar:
        pkginfo = f"pkgname = {name}\npkgver = {version}\n".encode()
        info = tarfile.TarInfo(name=".PKGINFO")
        info.size = len(pkginfo)
        tar.addfile(info, io.BytesIO(pkginfo))


class TestLoadReleaseKeys:
    def test_config_file_exists(self):
        keys = _load_release_keys()
        assert keys
        assert "S1" in keys
        assert "S2" in keys
        assert "NK1" in keys
        assert "NK2" in keys

    def test_fingerprints_are_40_chars(self):
        keys = _load_release_keys()
        for name, fp in keys.items():
            assert len(fp) == 40, f"{name} fingerprint is {len(fp)} chars, expected 40"
            assert all(c in "0123456789ABCDEF" for c in fp), f"{name} fingerprint contains non-hex chars"

    def test_aliases_match_canonical(self):
        keys = _load_release_keys()
        assert keys["NK1"] == keys["S1"], "NK1 alias must match S1"
        assert keys["NK2"] == keys["S2"], "NK2 alias must match S2"

    def test_s1_matches_canonical(self):
        canonical = "D7AA641D81ACD690C5AD865E7276E14DD8886BFE"
        keys = _load_release_keys()
        assert keys["S1"] == canonical


class TestGenerateRepodbSmoke:
    def test_help_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "scripts/generate-repodb.py", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_no_sign_generates_index(self, tmp_path):
        """Smoke test: generate index from valid mock archives (no signing)."""
        # Valid .igos.tar.gz archives — generate-repodb fail-closes on corrupt
        # input (PKM-A13), so the happy-path index test must feed real tarballs.
        _write_valid_archive(tmp_path / "testpkg-1.0.igos.tar.gz", "testpkg", "1.0-1")
        _write_valid_archive(tmp_path / "testpkg2-2.0.igos.tar.gz", "testpkg2", "2.0-1")

        output = tmp_path / "InterGenOS.db"
        result = subprocess.run(
            [
                sys.executable, "scripts/generate-repodb.py",
                "--no-sign", "-o", str(output), str(tmp_path),
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert output.exists()
        assert output.stat().st_size > 0


class TestIndexRoundtrip:
    def test_pkm_parser_roundtrip(self, tmp_path):
        """Index produced by generate-repodb.py must roundtrip through pkm.repo.RepoIndex."""
        # Create a valid .igos.tar.gz with .PKGINFO inside
        import tarfile as tarfile_mod
        archive = tmp_path / "testpkg-1.0.igos.tar.gz"
        with tarfile_mod.open(archive, "w:gz") as tar:
            # .PKGINFO with metadata
            pkginfo = (
                "pkgname = testpkg\n"
                "pkgver = 1.0-1\n"
                "pkgdesc = Test package for roundtrip\n"
                "depend = glibc\n"
                "license = MIT\n"
                "tier = core\n"
                "builddate = 2026-05-12T00:00:00Z\n"
                "size = 1000\n"
            )
            import io
            info = tarfile_mod.TarInfo(name=".PKGINFO")
            info.size = len(pkginfo)
            tar.addfile(info, io.BytesIO(pkginfo.encode()))

        output = tmp_path / "InterGenOS.db"
        result = subprocess.run(
            [
                sys.executable, str(_PROJECT_ROOT / "scripts" / "generate-repodb.py"),
                "--no-sign", "-o", str(output), str(tmp_path),
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

        # Parse with pkm.repo
        sys.path.insert(0, str(_PROJECT_ROOT))
        from pkm.repo import RepoIndex

        with gzip.open(output, "rt", encoding="utf-8") as f:
            data = json.load(f)

        index = RepoIndex("test", "https://example.com/x86_64", data)
        assert index.version == 1
        assert index.arch == "x86_64"
        assert index.package_count == 1
        assert "testpkg" in index.packages

        pkg = index.packages["testpkg"]
        assert "sha256" in pkg
        assert "filename" in pkg
        assert pkg["filename"] == "testpkg-1.0.igos.tar.gz"

    def test_missing_packages_key_does_not_crash(self, tmp_path):
        """RepoIndex handles empty or missing packages gracefully."""
        index_data = {"version": 1, "generated": "2026-01-01T00:00:00Z", "arch": "x86_64", "package_count": 0}
        from pkm.repo import RepoIndex
        index = RepoIndex("test", "https://example.com/x86_64", index_data)
        assert index.package_count == 0

    def test_required_top_level_fields(self, tmp_path):
        import tarfile as tarfile_mod, io
        archive = tmp_path / "dummy-1.0.igos.tar.gz"
        with tarfile_mod.open(archive, "w:gz") as tar:
            pkginfo = "pkgname = dummy\npkgver = 1.0-1\n"
            info = tarfile_mod.TarInfo(name=".PKGINFO")
            info.size = len(pkginfo)
            tar.addfile(info, io.BytesIO(pkginfo.encode()))

        output = tmp_path / "InterGenOS.db"
        subprocess.run(
            [sys.executable, str(_PROJECT_ROOT / "scripts" / "generate-repodb.py"),
             "--no-sign", "-o", str(output), str(tmp_path)],
            capture_output=True,
        )

        with gzip.open(output, "rt", encoding="utf-8") as f:
            data = json.load(f)

        assert "version" in data
        assert "generated" in data
        assert "arch" in data
        assert "package_count" in data
        assert "packages" in data
        assert data["arch"] == "x86_64"
        assert data["package_count"] >= 0


_GPG_DAEMONS = ("gpg-agent", "scdaemon")


def _agents_holding(home) -> list[str]:
    """Return `pid name` for every gpg helper daemon still holding `home`.

    Read from /proc rather than from `ps`, for two reasons that both produced a
    wrong answer while this was being written:

      * a free-text process scan (`pgrep -f <pattern>`) matches the SCANNING
        process itself, because the pattern is in its own argv — so it counts
        processes that do not exist and can never report an honest zero;
      * `ps -o args=` truncates to the terminal width, and with no terminal it
        falls back to a fixed width. A temporary directory path is long enough
        to be cut off, so the homedir this function matches on simply was not
        in the text it searched, and it reported zero daemons while one was
        running.

    /proc/<pid>/cmdline is the argument vector itself: no formatting layer, no
    width, and nothing to match the reader.
    """
    held = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
            if not argv or not argv[0]:
                continue
            name = os.path.basename(argv[0].decode("utf-8", "replace"))
            if name not in _GPG_DAEMONS:
                continue
            if str(home) in b" ".join(argv).decode("utf-8", "replace"):
                held.append(f"{entry.name} {name}")
        except (OSError, ValueError):
            continue          # the process exited while we were reading it
    return held


def _stop_agents(home) -> subprocess.CompletedProcess:
    """ASK the gpg helper daemons for a throwaway keyring to stop.

    `gpg --quick-gen-key` against a private GNUPGHOME starts a gpg-agent. The
    agent does not exit with the process that asked for the key: it stays
    resident holding the temporary homedir, and the only thing that eventually
    removed it was pytest deleting the directory out from under it several runs
    later. Measured on this tree: one gpg-agent per invocation, pinned to its
    own tmp directory. An earlier version of this docstring also claimed one
    scdaemon per invocation; re-measured on gpg 2.5.17, no scdaemon appeared in
    25 rounds. `scdaemon` stays in _GPG_DAEMONS anyway — the kill is asked for
    by name through gpgconf, and a gpg build that does start one must still be
    seen by the scan rather than silently missed.

    `gpgconf --homedir <h> --kill all` is scoped to that homedir alone, so the
    invoking user's real agent — which may be holding an unlocked signing key —
    is never touched.

    THIS ASKS; IT DOES NOT WAIT. `gpgconf --kill` returns once the agent has
    acknowledged the shutdown request, not once the process is gone, so the
    daemon is routinely still in the process table when this function returns.
    Callers that need the homedir to be actually clear must use `_reap_agents`,
    which waits. The completed process is RETURNED rather than discarded so a
    kill that failed outright can be named in a failure report instead of
    looking identical to a kill that worked.
    """
    return subprocess.run(
        ["gpgconf", "--homedir", str(home), "--kill", "all"],
        capture_output=True, text=True,
    )


_REAP_TIMEOUT_S = 10.0
_REAP_POLL_S = 0.01


def _reap_agents(home, timeout: float = _REAP_TIMEOUT_S,
                 poll: float = _REAP_POLL_S) -> dict:
    """Stop the gpg helper daemons for `home` and WAIT until they are really gone.

    THE RACE THIS CLOSES, measured rather than argued. `gpgconf --kill all` is
    not synchronous with the daemons' exit: it returns as soon as the agent has
    acknowledged the request, and the process leaves /proc some time afterwards.
    A scan of /proc is too coarse an instrument to see that window — it costs
    milliseconds, so its first sample usually lands after the window has already
    closed, and it found nothing in 20 out of 20 attempts. Measuring the agent's
    own pid with a single stat instead, on an IDLE machine: the daemon was still
    alive at the instant the kill returned in 21 of 30 rounds, and took up to
    10.3 ms to disappear.

    So a checker that asks "is anything still holding this homedir?" immediately
    after the kill is sampling inside a window where the honest answer is "yes,
    for a few more milliseconds". It then reports a transient state as a leak.
    That is the whole mechanism: the daemons were never failing to exit, the
    question was being asked too early.

    Waiting is the structural fix rather than a longer guess, because it removes
    the assumption instead of padding it — the check now ends when the process
    table actually says the homedir is clear. The wait is BOUNDED because a
    daemon that genuinely never exits is a real finding, and a test that hangs
    reports it to nobody.

    Returns ONE record of what happened, so a caller can assert on it and report
    the same values it asserted on.
    """
    started = time.monotonic()
    killed = _stop_agents(home)
    holders = _agents_holding(home)
    polls = 1
    while holders and (time.monotonic() - started) < timeout:
        time.sleep(poll)
        holders = _agents_holding(home)
        polls += 1
    return {
        "holders": holders,
        "kill_returncode": killed.returncode,
        "kill_stderr": (killed.stderr or "").strip(),
        "waited_s": round(time.monotonic() - started, 4),
        "polls": polls,
        "timed_out": bool(holders),
    }


def _reap_failure_message(home, reap: dict) -> str:
    """Render a failure report FROM the record its assertion was evaluated on."""
    message = (
        f"gpg helper daemons still hold {home} after `gpgconf --kill all` and a "
        f"bounded {reap['waited_s']}s wait ({reap['polls']} checks): "
        f"{reap['holders']}. The kill itself exited {reap['kill_returncode']}"
    )
    if reap["kill_stderr"]:
        message += f" with stderr {reap['kill_stderr']!r}"
    return message + "."


def _assert_agents_reaped(home, timeout: float = _REAP_TIMEOUT_S) -> dict:
    """Reap the daemons holding `home` and assert the reap actually worked.

    WHY THE VALUE IS CAPTURED FIRST. The assertion this replaces read

        assert not _agents_holding(home), f"...{_agents_holding(home)}"

    which derives its evidence TWICE — once to decide whether to fail, and again,
    later, to describe the failure. Those are two separate readings of a process
    table that is changing underneath both of them, so the text a reader is shown
    is not the observation that failed. In the specific case that made this worth
    fixing, the condition sees a daemon, the daemon exits during the microseconds
    that follow, and the message then reports an empty list: a failure whose own
    stated evidence says nothing was wrong. Anyone debugging that reads a
    contradiction and has no way to tell which half to believe.

    Deriving once and reporting the captured value is therefore not a formatting
    preference. It is what makes the failure report admissible as evidence of the
    failure it is attached to.
    """
    reap = _reap_agents(home, timeout=timeout)
    assert not reap["holders"], _reap_failure_message(home, reap)
    return reap


@pytest.fixture
def private_gnupg_home(tmp_path):
    """A throwaway GNUPGHOME that reaps its own daemons.

    0700 because gpg refuses a homedir with looser permissions, and the refusal
    is quiet enough to read as a signing failure.

    Teardown runs whether the test passes or fails — a leak that only happens on
    the failure path is the one nobody sees — and then ASSERTS the reap worked.
    A cleanup step that is never checked is indistinguishable from no cleanup.

    The homedir is per-test already: `tmp_path` is a fresh directory for every
    test function, so two tests can never share a keyring or each other's agent.
    What was missing was the other half of the isolation — the daemon LIFETIME.
    The old teardown asked the daemons to stop and then immediately asserted they
    were gone, which is a question asked inside the window where the answer is
    legitimately "not yet" (see `_reap_agents`). `_assert_agents_reaped` waits for
    the process table to agree before it judges, and reports the same reading it
    judged on.
    """
    home = tmp_path / "gnupg"
    home.mkdir(mode=0o700)
    try:
        yield home
    finally:
        _assert_agents_reaped(home)


class TestGpgAgentTeardown:
    """Prove the teardown reaps a daemon that is really there.

    Without this, `private_gnupg_home`'s assertion could pass forever on a
    machine where no agent ever starts, and the fixture would certify a zero it
    had never been shown to detect. This test starts an agent, proves it is
    running, reaps it, and proves it is gone — a positive control on both sides.
    """

    def test_stop_agents_reaps_a_running_agent(self, tmp_path):
        if subprocess.run(["gpgconf", "--version"], capture_output=True).returncode != 0:
            pytest.skip("gpgconf not available")

        home = tmp_path / "gnupg-control"
        home.mkdir(mode=0o700)

        # `--launch` starts the agent and returns once it is up. The first
        # version of this control generated a key instead and asserted
        # immediately, which raced: gpg had returned but its agent had not yet
        # appeared in the process table, so the control failed while the very
        # daemon it was looking for was starting. Launching directly removes
        # the race rather than sleeping past it.
        launched = subprocess.run(
            ["gpgconf", "--homedir", str(home), "--launch", "gpg-agent"],
            capture_output=True, text=True,
        )
        if launched.returncode != 0:
            pytest.skip(f"gpg-agent would not launch: {launched.stderr.strip()}")

        # POSITIVE CONTROL: something must actually be holding the homedir, or
        # the reap below proves nothing.
        assert _agents_holding(home), (
            "no gpg helper daemon is holding the throwaway keyring after "
            "--launch — this control cannot demonstrate that the teardown "
            "reaps anything"
        )

        # The reap now WAITS for the daemons to leave the process table, and the
        # record it returns is both what the assertion tested and what a failure
        # would report. Asserting on the record afterwards keeps this a control
        # over the real path rather than over a convenient shortcut: if the kill
        # itself had failed, `kill_returncode` would say so even in a run where
        # no daemon happened to be left behind.
        reap = _assert_agents_reaped(home)
        assert reap["kill_returncode"] == 0, (
            f"`gpgconf --kill all` exited {reap['kill_returncode']} "
            f"({reap['kill_stderr']!r}) — the homedir came up clear, but not "
            f"demonstrably because the kill worked"
        )


class TestGpgSignRoundtrip:
    def test_sign_then_verify(self, private_gnupg_home, tmp_path):
        """If GPG is available, sign the index and verify the signature.

        Every gpg call here runs against a PRIVATE keyring in tmp_path, never
        the invoking user's. The comment below used to claim "no external
        keyring pollution" while doing the opposite: with no GNUPGHOME set,
        --quick-gen-key created a key in the real user's keyring, and on a
        release machine that keyring holds the project signing key. Measured on
        a development workstation 2026-08-03: this test could not complete there,
        because the user's key database was held by a lock, and the failure it
        produced looked like a code fault rather than what it was.

        The isolation is also what makes the test honest — it now proves the
        sign/verify round trip against a key it created itself, rather than
        against whatever happens to be in the operator's keyring.
        """
        # Check if GPG is available (skip test gracefully if not)
        gpg_check = subprocess.run(["gpg", "--version"], capture_output=True)
        if gpg_check.returncode != 0:
            pytest.skip("GPG not available")

        # A keyring of this test's own, supplied by the private_gnupg_home
        # fixture, which also stops the gpg-agent and scdaemon this test starts.
        # Creating the homedir inline (as this test used to) left both daemons
        # resident after the test returned.
        gnupg_home = private_gnupg_home
        env = {**os.environ, "GNUPGHOME": str(gnupg_home)}

        key_id = "test-key-for-repodb-tests"
        subprocess.run(
            ["gpg", "--batch", "--passphrase", "", "--quick-gen-key", key_id],
            capture_output=True, env=env,
        )
        assert key_id in subprocess.run(
            ["gpg", "--list-keys", key_id], capture_output=True, text=True, env=env
        ).stdout

        # Create a VALID archive + index (dummy bytes would fail-closed per PKM-A13).
        _write_valid_archive(tmp_path / "testpkg-1.0.igos.tar.gz", "testpkg", "1.0-1")
        output = tmp_path / "InterGenOS.db"
        gen = subprocess.run(
            [sys.executable, "scripts/generate-repodb.py", "--no-sign", "-o", str(output), str(tmp_path)],
            capture_output=True, text=True,
        )
        # Surface a generate failure HERE (loud) instead of as a confusing
        # downstream "gpg: can't open InterGenOS.db" — the bug this test had.
        assert gen.returncode == 0, gen.stderr
        assert output.exists(), "index generation produced no InterGenOS.db to sign"

        # Sign using the throwaway key
        sig_path = Path(str(output) + ".sig")
        result = subprocess.run(
            ["gpg", "--detach-sign", "--armor", "--local-user", key_id,
             "--output", str(sig_path), str(output)],
            capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, result.stderr
        assert sig_path.exists()

        # Verify signature
        result = subprocess.run(
            ["gpg", "--verify", str(sig_path), str(output)],
            capture_output=True, text=True, env=env,
        )
        assert "Good signature" in result.stderr or result.returncode == 0, result.stderr

        # No key cleanup step is needed or wanted: the keyring lives in
        # tmp_path and goes away with it. The old cleanup ran a delete against
        # the REAL keyring, which is a destructive command aimed at the wrong
        # place — if the key ever collided with a real one, that deletion is
        # what would have found it.


# The new teardown helpers are reached through the module object rather than by
# name, so that this block can be appended to the PARENT commit's copy of this
# file and still be COLLECTED there: a missing module-level name would abort
# collection for the whole file and take the other cells with it, which reports
# nothing about any individual case. Reached this way, each case fails at the
# parent commit on its own merits, with `AttributeError` as the proof that the
# behaviour is absent rather than merely wrong.
_SELF = sys.modules[__name__]


class TestTeardownEvidenceIsTheMeasurementThatFailed:
    """A failure report must describe the observation that actually failed."""

    def test_failure_message_names_the_holders_the_condition_saw(self, tmp_path, monkeypatch):
        """The reported evidence is the FIRST reading, not a later one.

        The detector here never agrees with itself: every call returns a
        different, non-empty answer. That is the seeded form of what the process
        table does on its own during a teardown. With the wait budget set to zero
        the reap gets exactly one reading, so the message has only one honest
        thing it can say — and if the message is rendered from a fresh call
        instead of from the captured record, it names a holder the condition
        never saw.
        """
        readings = []

        def disagreeing_detector(home):
            readings.append(home)
            return [f"{100 + len(readings)} gpg-agent"]

        monkeypatch.setattr(_SELF, "_agents_holding", disagreeing_detector)
        monkeypatch.setattr(
            _SELF, "_stop_agents",
            lambda home: subprocess.CompletedProcess(["gpgconf"], 0, "", ""),
        )

        with pytest.raises(AssertionError) as raised:
            _SELF._assert_agents_reaped(tmp_path, timeout=0)

        message = str(raised.value)
        assert "101 gpg-agent" in message, (
            "the failure report does not name the holder the condition was "
            f"evaluated against; it says: {message}"
        )
        assert "102 gpg-agent" not in message, (
            "the failure report names a holder from a LATER reading than the one "
            f"that failed; it says: {message}"
        )

    def test_a_failing_check_never_reports_an_empty_holder_list(self, tmp_path, monkeypatch):
        """The specific contradiction: fails, then reports that nothing is wrong.

        The detector sees a daemon once and then sees none — the real sequence
        when an agent exits during the microseconds after the check. A report
        rendered from a second reading says `[]` while the assertion it is
        attached to failed because the list was not empty.
        """
        calls = {"n": 0}

        def vanishing_detector(home):
            calls["n"] += 1
            return ["4242 gpg-agent"] if calls["n"] == 1 else []

        monkeypatch.setattr(_SELF, "_agents_holding", vanishing_detector)
        monkeypatch.setattr(
            _SELF, "_stop_agents",
            lambda home: subprocess.CompletedProcess(["gpgconf"], 0, "", ""),
        )

        with pytest.raises(AssertionError) as raised:
            _SELF._assert_agents_reaped(tmp_path, timeout=0)

        message = str(raised.value)
        assert "4242 gpg-agent" in message, message
        assert "[]" not in message, (
            "a failing check reported an EMPTY holder list — its own stated "
            f"evidence contradicts the condition that failed: {message}"
        )

    def test_reap_waits_for_a_daemon_that_exits_a_moment_late(self, tmp_path, monkeypatch):
        """A daemon that leaves a few checks later is reaped, not reported.

        This is the measured behaviour of the real thing: the kill returns, the
        process is still there, and it goes shortly afterwards.
        """
        calls = {"n": 0}

        def slow_to_exit(home):
            calls["n"] += 1
            return ["777 gpg-agent"] if calls["n"] <= 3 else []

        monkeypatch.setattr(_SELF, "_agents_holding", slow_to_exit)
        monkeypatch.setattr(
            _SELF, "_stop_agents",
            lambda home: subprocess.CompletedProcess(["gpgconf"], 0, "", ""),
        )

        reap = _SELF._reap_agents(tmp_path, timeout=5.0, poll=0.001)

        assert reap["holders"] == [], reap
        assert reap["timed_out"] is False, reap
        assert reap["polls"] >= 4, (
            f"the reap did not re-check after the first reading (polls={reap['polls']}) "
            "— it cannot have waited for anything"
        )

    def test_reap_is_bounded_when_the_daemons_never_leave(self, tmp_path, monkeypatch):
        """A daemon that never exits is reported, not waited on forever."""
        monkeypatch.setattr(_SELF, "_agents_holding", lambda home: ["999 gpg-agent"])
        monkeypatch.setattr(
            _SELF, "_stop_agents",
            lambda home: subprocess.CompletedProcess(["gpgconf"], 0, "", ""),
        )

        reap = _SELF._reap_agents(tmp_path, timeout=0.05, poll=0.001)

        assert reap["timed_out"] is True, reap
        assert reap["holders"] == ["999 gpg-agent"], reap
        assert reap["waited_s"] < 5.0, (
            f"the bounded wait ran {reap['waited_s']}s against a 0.05s budget"
        )

    def test_reap_record_carries_a_kill_that_failed(self, tmp_path, monkeypatch):
        """A kill that failed outright must not look like a kill that worked."""
        monkeypatch.setattr(_SELF, "_agents_holding", lambda home: ["555 gpg-agent"])
        monkeypatch.setattr(
            _SELF, "_stop_agents",
            lambda home: subprocess.CompletedProcess(
                ["gpgconf"], 2, "", "gpgconf: cannot reach the agent\n"),
        )

        with pytest.raises(AssertionError) as raised:
            _SELF._assert_agents_reaped(tmp_path, timeout=0)

        message = str(raised.value)
        assert "exited 2" in message, message
        assert "cannot reach the agent" in message, message

    def test_reap_reaps_a_real_running_agent(self, tmp_path):
        """The same path, against real daemons rather than a seeded detector.

        A seeded detector proves the accounting. It cannot prove that the reap
        works on the thing it exists for, because nothing in it ever started a
        process. This case launches a real gpg-agent, proves it is really there,
        reaps it through the real path, and reads the record back.
        """
        if subprocess.run(["gpgconf", "--version"], capture_output=True).returncode != 0:
            pytest.skip("gpgconf not available")

        home = tmp_path / "gnupg-real"
        home.mkdir(mode=0o700)

        launched = subprocess.run(
            ["gpgconf", "--homedir", str(home), "--launch", "gpg-agent"],
            capture_output=True, text=True,
        )
        if launched.returncode != 0:
            pytest.skip(f"gpg-agent would not launch: {launched.stderr.strip()}")

        assert _agents_holding(home), (
            "no gpg helper daemon is holding the throwaway keyring after "
            "--launch, so reaping it would demonstrate nothing"
        )

        reap = _SELF._assert_agents_reaped(home)

        assert reap["kill_returncode"] == 0, reap
        assert reap["holders"] == [], reap
        assert reap["timed_out"] is False, reap


class TestTheDefectClassIsReal:
    """Control: the shape that was replaced really can contradict itself.

    This case is deliberately base-passing and depends on nothing added by this
    change. It exists so the rest of the file is not the only argument that the
    two-call shape is a defect — it builds that shape from scratch, drives it
    with a detector that changes between the two calls, and shows the resulting
    report describing a state other than the one that failed.
    """

    def test_deriving_the_evidence_twice_can_report_the_wrong_state(self):
        calls = {"n": 0}

        def vanishing_detector():
            calls["n"] += 1
            return ["1234 gpg-agent"] if calls["n"] == 1 else []

        with pytest.raises(AssertionError) as raised:
            # The shape under criticism, written out in full: the condition and
            # the message each call the detector.
            assert not vanishing_detector(), f"still held: {vanishing_detector()}"

        assert calls["n"] == 2, "the shape should have called the detector twice"
        assert "still held: []" in str(raised.value), (
            "this control cannot demonstrate the defect: the second reading did "
            f"not differ from the first ({raised.value})"
        )
