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


# ---------------------------------------------------------------------------
# The signed index records every package's corresponding-source archive.
#
# SOURCES.md §4 already documents the verification a user runs:
#
#     gunzip -c InterGenOS.db | jq -r '.packages."<name>".source_sha256'
#     sha256sum <name>-<version>-<release>.igos.src.tar.gz
#
# and the README written into every source archive already claims "This
# archive's own SHA256 is recorded in the signed package index". Until this
# change no index entry carried any field about sources at all, so the
# documented command returned `null` and the claim was not true. These cases
# fix the index to the documented names and prove the documented command end
# to end, including the negative control that a tampered archive fails it.
# ---------------------------------------------------------------------------

_SRC_SUFFIX = ".igos.src.tar.gz"


def _write_binary_archive(path, name, version, release):
    """A valid .igos.tar.gz whose .PKGINFO carries name, version AND release.

    The release matters here and not in the older helpers above: the source
    archive's filename is <name>-<version>-<release>.igos.src.tar.gz, so an
    entry with no release cannot name its own source archive.
    """
    import io
    import tarfile

    pkginfo = (
        f"pkgname = {name}\n"
        f"pkgver = {version}\n"
        f"pkgrel = {release}\n"
    ).encode()
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo(name=".PKGINFO")
        info.size = len(pkginfo)
        tar.addfile(info, io.BytesIO(pkginfo))


def _write_source_archive(sources_dir, name, version, release, body=b"source bytes"):
    """A corresponding-source archive under the documented name. Returns its path."""
    path = Path(sources_dir) / f"{name}-{version}-{release}{_SRC_SUFFIX}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def _sha256_of(path):
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _fixture_publish_set(tmp_path, sources_dir_name="sources-archives"):
    """A publish set with source archives for SOME packages and not others.

    alpha and beta have one; gamma does not. That asymmetry is the point: the
    absence has to be visible in the generator's own summary rather than
    appearing as a silent null in an entry.
    """
    pkg_dir = tmp_path / "archives"
    pkg_dir.mkdir()
    src_dir = tmp_path / sources_dir_name
    src_dir.mkdir()

    _write_binary_archive(pkg_dir / "alpha-1.0-1.igos.tar.gz", "alpha", "1.0", 1)
    _write_binary_archive(pkg_dir / "beta-2.0-3.igos.tar.gz", "beta", "2.0", 3)
    _write_binary_archive(pkg_dir / "gamma-3.0-1.igos.tar.gz", "gamma", "3.0", 1)

    srcs = {
        "alpha": _write_source_archive(src_dir, "alpha", "1.0", 1, b"alpha source bytes"),
        "beta": _write_source_archive(src_dir, "beta", "2.0", 3, b"beta source bytes, longer"),
    }
    return pkg_dir, src_dir, srcs


def _run_generator(pkg_dir, output, sources_dir=None, extra=()):
    argv = [
        sys.executable, str(_PROJECT_ROOT / "scripts" / "generate-repodb.py"),
        "--no-sign", "-o", str(output),
    ]
    if sources_dir is not None:
        argv += ["--sources-dir", str(sources_dir)]
    argv += list(extra)
    argv.append(str(pkg_dir))
    return subprocess.run(argv, capture_output=True, text=True, cwd=str(_PROJECT_ROOT))


def _read_index(output):
    with gzip.open(output, "rt", encoding="utf-8") as f:
        return json.load(f)


class TestSourceLookupBoundary:
    @pytest.mark.parametrize("component", ["name", "version", "release"])
    @pytest.mark.parametrize("value", ["../outside/alpha", "nested/alpha", "ABSOLUTE",
                                       "nested\\alpha", "..", ""])
    def test_identity_components_are_refused(self, tmp_path, component, value):
        from pkm.repo import _source_archive_for
        sources = tmp_path / "sources"
        sources.mkdir()
        identity = {"name": "alpha", "version": "1.0", "release": 1}
        identity[component] = str(tmp_path / "outside/alpha") if value == "ABSOLUTE" else value
        _write_source_archive(sources, identity["name"], identity["version"], identity["release"])
        with pytest.raises(ValueError, match=component + " component") as raised:
            _source_archive_for(sources, identity["name"], identity)
        assert repr(identity["name"]) in str(raised.value)

    def _boundary_set(self, tmp_path, symlink=False):
        binaries = tmp_path / "archives"
        sources = tmp_path / "sources"
        packages = tmp_path / "packages"
        binaries.mkdir()
        sources.mkdir()
        packages.mkdir()
        name = "alpha" if symlink else "../outside/alpha"
        _write_binary_archive(binaries / "malformed-1.igos.tar.gz", name, "1.0", 1)
        outside = _write_source_archive(tmp_path / "outside", "alpha", "1.0", 1,
                                        b"outside the declared sources directory\n")
        if symlink:
            (sources / outside.name).symlink_to(outside)
        return binaries, sources, packages, name, outside

    @pytest.mark.parametrize("symlink", [False, True])
    def test_generator_refuses_escape_and_summary_names_package(self, tmp_path, symlink):
        binaries, sources, _, name, outside = self._boundary_set(tmp_path, symlink)
        output = tmp_path / "InterGenOS.db"
        result = _run_generator(binaries, output, sources)
        assert result.returncode == 0, result.stderr
        entry = _read_index(output)["packages"][name]
        assert "source_filename" not in entry and "source_sha256" not in entry
        assert "REFUSED" in result.stderr
        assert ("resolved source path" if symlink else "name component") in result.stderr
        assert name in result.stderr.split("no corresponding-source archive")[-1]
        assert _sha256_of(outside) not in json.dumps(entry)

    @pytest.mark.parametrize("symlink", [False, True])
    def test_correspondence_gate_cannot_accept_an_outside_source(self, tmp_path, symlink):
        binaries, sources, packages, name, _ = self._boundary_set(tmp_path, symlink)
        # A source-less recipe must not turn an unsafe candidate into a pass.
        recipe = packages / "core" / "fixture"
        recipe.mkdir(parents=True)
        (recipe / "package.yml").write_text(f"name: {name}\nsource: []\n")
        result = subprocess.run(
            [sys.executable, str(_PROJECT_ROOT / "scripts/check-source-correspondence.py"),
             "--archive-dir", str(binaries), "--sources-archive-dir", str(sources),
             "--packages-root", str(packages)], capture_output=True, text=True,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert name in result.stderr
        assert ("resolved source path" if symlink else "name component") in result.stderr

    def test_in_root_symlink_keeps_the_published_name(self, tmp_path):
        binaries = tmp_path / "archives"
        sources = tmp_path / "sources"
        binaries.mkdir()
        sources.mkdir()
        _write_binary_archive(binaries / "alpha.igos.tar.gz", "alpha", "1.0", 1)
        payload = sources / "stored-source.tar.gz"
        payload.write_bytes(b"source bytes inside the declared root")
        alias = sources / "alpha-1.0-1.igos.src.tar.gz"
        alias.symlink_to(payload)
        output = tmp_path / "InterGenOS.db"
        result = _run_generator(binaries, output, sources)
        assert result.returncode == 0, result.stderr
        entry = _read_index(output)["packages"]["alpha"]
        assert entry["source_filename"] == alias.name
        assert entry["source_sha256"] == _sha256_of(payload)

    def test_lookup_returns_the_confined_target_before_hashing(self, tmp_path, monkeypatch):
        from pkm import repo
        binaries = tmp_path / "archives"
        sources = tmp_path / "sources"
        binaries.mkdir()
        sources.mkdir()
        _write_binary_archive(binaries / "alpha.igos.tar.gz", "alpha", "1.0", 1)
        inside = sources / "stored-source.tar.gz"
        inside.write_bytes(b"inside bytes")
        outside = tmp_path / "outside-source.tar.gz"
        outside.write_bytes(b"outside bytes")
        alias = sources / "alpha-1.0-1.igos.src.tar.gz"
        alias.symlink_to(inside)
        lookup = repo._source_archive_for

        def replace_alias_after_lookup(*args):
            target = lookup(*args)
            alias.unlink()
            alias.symlink_to(outside)
            return target

        monkeypatch.setattr(repo, "_source_archive_for", replace_alias_after_lookup)
        output = repo.generate_index(binaries, sources_dir=sources)
        entry = _read_index(output)["packages"]["alpha"]
        assert entry["source_sha256"] == _sha256_of(inside)
        assert entry["source_filename"] == alias.name

    @pytest.mark.parametrize("consumer", ["generator", "gate", "twin"])
    def test_replaced_validated_target_is_refused(self, tmp_path, monkeypatch, consumer):
        import importlib.util
        from pkm import repo

        binaries = tmp_path / "archives"
        sources = tmp_path / "sources"
        packages = tmp_path / "packages"
        for directory in (binaries, sources, packages):
            directory.mkdir()
        _write_binary_archive(binaries / "alpha.igos.tar.gz", "alpha", "1.0", 1)
        target_name = "alpha-pass1" if consumer == "twin" else "alpha"
        target = _write_source_archive(sources, target_name, "1.0", 1, b"inside")
        outside = tmp_path / "outside-source.tar.gz"
        outside.write_bytes(b"outside")
        if consumer == "twin":
            import io
            import tarfile
            payload_hash = _sha256_of(outside)
            with tarfile.open(outside, "w:gz") as archive:
                member = tarfile.TarInfo("alpha-pass1-1.0-1/alpha.tar.gz")
                member.size = len(b"outside")
                archive.addfile(member, io.BytesIO(b"outside"))
            recipe = packages / "toolchain/alpha-pass1"
            recipe.mkdir(parents=True)
            (recipe / "package.yml").write_text(
                "name: alpha-pass1\nversion: '1.0'\nrelease: 1\nsource:\n"
                "  - url: https://example.invalid/alpha.tar.gz\n"
                f"    sha256: {payload_hash}\n")
        spec = importlib.util.spec_from_file_location(
            "correspondence_swap_control", _PROJECT_ROOT / "scripts/check-source-correspondence.py")
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
        owner = repo if consumer == "generator" else gate
        original = owner._source_archive_for
        swapped = []

        def replace_target_after_lookup(*args):
            result = original(*args)
            if args[1] == target_name:
                target.unlink()
                target.symlink_to(outside)
                swapped.append(True)
            return result

        monkeypatch.setattr(owner, "_source_archive_for", replace_target_after_lookup)
        if consumer == "generator":
            output = repo.generate_index(binaries, sources_dir=sources)
            entry = _read_index(output)["packages"]["alpha"]
            assert "source_sha256" not in entry
            assert "source_filename" not in entry
        else:
            monkeypatch.setattr(sys, "argv", [str(gate.__file__),
                "--archive-dir", str(binaries), "--sources-archive-dir", str(sources),
                "--packages-root", str(packages)])
            assert gate.main() == 1
        assert swapped == [True]

    def test_hashing_uses_the_opened_file_after_its_name_changes(self, tmp_path, monkeypatch):
        import os
        from pkm import repo

        binaries = tmp_path / "archives"
        sources = tmp_path / "sources"
        binaries.mkdir()
        sources.mkdir()
        _write_binary_archive(binaries / "alpha.igos.tar.gz", "alpha", "1.0", 1)
        target = _write_source_archive(sources, "alpha", "1.0", 1, b"inside bytes")
        expected = _sha256_of(target)
        outside = tmp_path / "outside"
        outside.write_bytes(b"outside bytes")
        retained = sources / "retained"
        real_readlink = os.readlink
        changed = []

        def rename_after_descriptor_validation(path, *args, **kwargs):
            value = real_readlink(path, *args, **kwargs)
            if str(path).startswith("/proc/self/fd/") and not changed:
                changed.append(True)
                target.rename(retained)
                target.symlink_to(outside)
                # The descriptor remains attached to the in-root retained file.
                return real_readlink(path, *args, **kwargs)
            return value

        monkeypatch.setattr(os, "readlink", rename_after_descriptor_validation)
        output = repo.generate_index(binaries, sources_dir=sources)
        assert changed == [True]
        entry = _read_index(output)["packages"]["alpha"]
        assert entry["source_sha256"] == expected
        assert entry["source_filename"] == target.name

    def test_descriptor_inspection_failure_refuses_and_closes(self, tmp_path, monkeypatch, capsys):
        import os
        from pkm import repo

        binaries = tmp_path / "archives"
        sources = tmp_path / "sources"
        binaries.mkdir()
        sources.mkdir()
        _write_binary_archive(binaries / "alpha.igos.tar.gz", "alpha", "1.0", 1)
        _write_source_archive(sources, "alpha", "1.0", 1)
        real_readlink = os.readlink
        inspected = []

        def refuse_descriptor(path, *args, **kwargs):
            if str(path).startswith("/proc/self/fd/"):
                inspected.append(int(str(path).rsplit("/", 1)[1]))
                raise PermissionError("descriptor inspection denied")
            return real_readlink(path, *args, **kwargs)

        monkeypatch.setattr(os, "readlink", refuse_descriptor)
        output = repo.generate_index(binaries, sources_dir=sources)
        assert inspected
        assert "source_sha256" not in _read_index(output)["packages"]["alpha"]
        assert "descriptor inspection denied" in capsys.readouterr().err
        for fd in inspected:
            with pytest.raises(OSError):
                os.fstat(fd)

    def test_toolchain_twin_cannot_escape_through_a_symlink(self, tmp_path):
        import hashlib
        import io
        import tarfile

        binaries = tmp_path / "archives"
        sources = tmp_path / "sources"
        recipe = tmp_path / "packages/toolchain/alpha-pass1"
        binaries.mkdir()
        sources.mkdir()
        recipe.mkdir(parents=True)
        _write_binary_archive(binaries / "alpha.igos.tar.gz", "alpha", "1.0", 1)
        payload = b"upstream fixture source"
        (recipe / "package.yml").write_text(
            "name: alpha-pass1\nversion: '1.0'\nrelease: 1\nsource:\n"
            "  - url: https://example.invalid/alpha.tar.gz\n"
            f"    sha256: {hashlib.sha256(payload).hexdigest()}\n")
        outside = tmp_path / "alpha-pass1-1.0-1.igos.src.tar.gz"
        with tarfile.open(outside, "w:gz") as archive:
            info = tarfile.TarInfo("alpha-pass1-1.0-1/alpha.tar.gz")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        (sources / outside.name).symlink_to(outside)
        result = subprocess.run(
            [sys.executable, str(_PROJECT_ROOT / "scripts/check-source-correspondence.py"),
             "--archive-dir", str(binaries), "--sources-archive-dir", str(sources),
             "--packages-root", str(tmp_path / "packages")], capture_output=True, text=True,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "alpha-pass1" in result.stderr and "resolved source path" in result.stderr


class TestTheIndexRecordsTheCorrespondingSourceArchive:
    """RED at the base, GREEN at the tip: the two documented fields."""

    def test_a_package_with_a_source_archive_carries_both_fields(self, tmp_path):
        pkg_dir, src_dir, srcs = _fixture_publish_set(tmp_path)
        output = tmp_path / "InterGenOS.db"
        result = _run_generator(pkg_dir, output, src_dir)
        assert result.returncode == 0, result.stderr

        packages = _read_index(output)["packages"]
        for name, archive in srcs.items():
            entry = packages[name]
            assert entry["source_filename"] == archive.name, (
                f"{name}: index names {entry.get('source_filename')!r}, "
                f"archive is {archive.name!r}"
            )
            assert entry["source_sha256"] == _sha256_of(archive), (
                f"{name}: index records {entry.get('source_sha256')!r}, the "
                f"archive's own bytes hash to {_sha256_of(archive)!r}"
            )

    def test_the_hash_comes_from_the_archive_bytes_and_not_from_a_sidecar(self, tmp_path):
        """A .sha256 sidecar carrying a WRONG value must not be believed."""
        pkg_dir, src_dir, srcs = _fixture_publish_set(tmp_path)
        lie = "0" * 64
        (src_dir / f"{srcs['alpha'].name}.sha256").write_text(
            f"{lie}  {srcs['alpha'].name}\n"
        )
        output = tmp_path / "InterGenOS.db"
        assert _run_generator(pkg_dir, output, src_dir).returncode == 0

        recorded = _read_index(output)["packages"]["alpha"]["source_sha256"]
        assert recorded != lie, "the generator read a manifest instead of the bytes"
        assert recorded == _sha256_of(srcs["alpha"])

    def test_a_package_with_no_source_archive_carries_neither_field(self, tmp_path):
        """Absent, not null: a reader must not see an empty field at all."""
        pkg_dir, src_dir, _ = _fixture_publish_set(tmp_path)
        output = tmp_path / "InterGenOS.db"
        assert _run_generator(pkg_dir, output, src_dir).returncode == 0

        entry = _read_index(output)["packages"]["gamma"]
        assert "source_filename" not in entry, f"gamma carries {entry.get('source_filename')!r}"
        assert "source_sha256" not in entry, f"gamma carries {entry.get('source_sha256')!r}"

    def test_the_summary_counts_and_names_the_packages_with_no_source_archive(self, tmp_path):
        pkg_dir, src_dir, _ = _fixture_publish_set(tmp_path)
        output = tmp_path / "InterGenOS.db"
        result = _run_generator(pkg_dir, output, src_dir)
        assert result.returncode == 0, result.stderr

        summary = result.stderr
        assert "gamma" in summary, f"the absence is not named in the summary:\n{summary}"
        assert "2" in summary and "3" in summary, (
            f"the summary does not carry the two counts:\n{summary}"
        )
        for carried in ("alpha", "beta"):
            assert carried not in summary.split("no corresponding-source archive")[-1], (
                f"{carried} has an archive and must not be listed as missing one:\n{summary}"
            )

    def test_a_run_given_no_sources_directory_says_so_and_adds_no_fields(self, tmp_path):
        """The loud zero: never a silent absence of the whole feature."""
        pkg_dir, _src_dir, _ = _fixture_publish_set(tmp_path)
        output = tmp_path / "InterGenOS.db"
        missing = tmp_path / "there-is-no-such-directory"
        result = _run_generator(pkg_dir, output, missing)
        assert result.returncode == 0, result.stderr

        assert str(missing) in result.stderr, (
            f"the path it looked at is not named:\n{result.stderr}"
        )
        for entry in _read_index(output)["packages"].values():
            assert "source_filename" not in entry
            assert "source_sha256" not in entry

    def test_the_default_sources_directory_is_beside_the_index(self, tmp_path):
        """With no flag, the PUBLISHED layout's <package_dir>/sources is read."""
        pkg_dir = tmp_path / "archives"
        pkg_dir.mkdir()
        _write_binary_archive(pkg_dir / "delta-4.0-2.igos.tar.gz", "delta", "4.0", 2)
        archive = _write_source_archive(pkg_dir / "sources", "delta", "4.0", 2, b"delta src")

        output = tmp_path / "InterGenOS.db"
        assert _run_generator(pkg_dir, output).returncode == 0

        entry = _read_index(output)["packages"]["delta"]
        assert entry["source_filename"] == archive.name
        assert entry["source_sha256"] == _sha256_of(archive)

    def test_the_envelope_fields_are_unchanged(self, tmp_path):
        """The two fields are ADDITIVE: nothing in the envelope moves."""
        pkg_dir, src_dir, _ = _fixture_publish_set(tmp_path)
        output = tmp_path / "InterGenOS.db"
        assert _run_generator(pkg_dir, output, src_dir).returncode == 0

        data = _read_index(output)
        assert data["version"] == 1
        assert data["min_pkm_version"] == "0.1.0"
        assert data["signature_format"] == "gpg-detached-armored"
        assert data["arch"] == "x86_64"
        assert data["package_count"] == 3


class TestTheDocumentedVerificationReturnsARealValue:
    """SOURCES.md §4's command, run end to end, with both controls."""

    @staticmethod
    def _documented_lookup(output, name):
        """Exactly the pipeline SOURCES.md writes, as a shell pipeline."""
        return subprocess.run(
            ["bash", "-c",
             f"gunzip -c {output} | jq -r '.packages.\"{name}\".source_sha256'"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    @staticmethod
    def _documented_sha256sum(archive):
        return subprocess.run(
            ["sha256sum", str(archive)], capture_output=True, text=True, check=True,
        ).stdout.split()[0]

    def test_the_documented_command_returns_the_archives_own_hash(self, tmp_path):
        pkg_dir, src_dir, srcs = _fixture_publish_set(tmp_path)
        output = tmp_path / "InterGenOS.db"
        assert _run_generator(pkg_dir, output, src_dir).returncode == 0

        from_index = self._documented_lookup(output, "alpha")
        from_disk = self._documented_sha256sum(srcs["alpha"])
        assert from_index == from_disk, (
            f"the documented verification does not match: index {from_index!r} "
            f"against disk {from_disk!r}"
        )
        assert from_index != "null", "the documented command still returns null"

    def test_a_tampered_source_archive_fails_the_documented_verification(self, tmp_path):
        """The negative control. Without it a match proves nothing."""
        pkg_dir, src_dir, srcs = _fixture_publish_set(tmp_path)
        output = tmp_path / "InterGenOS.db"
        assert _run_generator(pkg_dir, output, src_dir).returncode == 0

        from_index = self._documented_lookup(output, "alpha")
        archive = srcs["alpha"]
        with open(archive, "ab") as fh:          # one appended byte, after indexing
            fh.write(b"\x00")
        from_disk = self._documented_sha256sum(archive)
        assert from_index != from_disk, (
            "a tampered archive still verified against the index, so the "
            "documented verification detects nothing"
        )

    def test_a_package_with_no_archive_still_reads_null_and_that_is_honest(self, tmp_path):
        """gamma has no archive: the documented command says null, by design."""
        pkg_dir, src_dir, _ = _fixture_publish_set(tmp_path)
        output = tmp_path / "InterGenOS.db"
        assert _run_generator(pkg_dir, output, src_dir).returncode == 0
        assert self._documented_lookup(output, "gamma") == "null"


class TestThePackageManagerStillLoadsTheIndex:
    """The additive control: pkm's own loader, unchanged, reads the new index."""

    def test_repoindex_loads_the_new_index_and_reports_its_packages(self, tmp_path):
        pkg_dir, src_dir, srcs = _fixture_publish_set(tmp_path)
        output = tmp_path / "InterGenOS.db"
        assert _run_generator(pkg_dir, output, src_dir).returncode == 0

        sys.path.insert(0, str(_PROJECT_ROOT))
        from pkm.repo import RepoIndex

        index = RepoIndex("test", "https://example.com/x86_64", _read_index(output))
        assert index.version == 1
        assert index.arch == "x86_64"
        assert index.package_count == 3
        assert set(index.packages) == {"alpha", "beta", "gamma"}
        assert index.packages["alpha"]["source_sha256"] == _sha256_of(srcs["alpha"])
        assert index.packages["alpha"]["sha256"]            # the binary hash still there
        assert index.packages["alpha"]["filename"].endswith(".igos.tar.gz")
