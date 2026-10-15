# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""One root launch of the record runner measures two tiers, in one record.

WHY THIS EXISTS. No record had ever passed scripts/check-release-validation.py,
and not because a gate found a defect. Launched as the user, the root password
gate cannot read /etc/shadow and skips, and the release gate refuses the skip.
Launched as root, thirteen user-tier gates read root's home, root's journal and
no session bus, and break (measured 2026-10-14 on an upgraded validation
machine). The runner had no notion of a tier: one pytest run as whoever launched
it. Neither launch could ever produce a record that measured both.

These tests hold the split: the root tier is a marker the tier registers; a root
launch runs the user tier as the account that invoked it and the root tier as
root, and writes ONE record naming who measured each gate; an unprivileged launch
still runs the user tier, says in one line that the root tier was NOT measured,
and leaves a record the release gate refuses.

WHAT IS NOT ASSERTED HERE. The runner only runs on a real installed machine, and
the identity change needs root. So the runner is driven in-process with its
subprocess calls recorded rather than executed: what is asserted is which runs it
asks for, as whom, with what environment, and what it writes. The privilege drop
itself is proven by the runner's real firing through sudo, shipped with the cut.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
TIER = REPO_ROOT / "tests" / "installed"
RUNNER = REPO_ROOT / "scripts" / "run-installed-gates.py"
CHECKER = REPO_ROOT / "scripts" / "check-release-validation.py"
ROOT_GATE_FILE = TIER / "test_gate_root_password_kept.py"
ROOT_GATE_NODEID = ("tests/installed/test_gate_root_password_kept.py::"
                    "TestRootPasswordKept::test_installed_root_has_a_real_hash")
GATE_ENV = "INTERGENOS_INSTALLED_GATES"

# Kept before any test patches subprocess.run: the hand-back copy and the
# release gate run for real.
_REAL_RUN = subprocess.run

# The account a root launch measures the user tier as, and root.
ACCOUNT = pwd.struct_passwd(
    ("gate-user", "x", 4242, 4243, "", "/home/gate-user", "/bin/sh"))
ACCOUNT_GROUPS = [27, 4243]
ROOT = pwd.struct_passwd(("root", "x", 0, 0, "", "/root", "/bin/sh"))
ACCOUNTS_BY_NAME = {"gate-user": ACCOUNT, "root": ROOT}
ACCOUNTS_BY_UID = {4242: ACCOUNT, 0: ROOT}

INSTALLED_INTERGEN = "/usr/lib/python3.14/site-packages/intergen/__init__.py"
INSTALLED_IDENTITY = {"intergen_version": "0.9.0", "intergen_release": 250,
                      "installed_manifest_sha256": "0" * 64,
                      "install_date": "2026-10-14T16:00:00"}
OS_RELEASE = {"ID": "intergenos", "VERSION_ID": "R001.2",
              "IMAGE_ID": "intergenos", "IMAGE_VERSION": "R001.2-03"}

# The gates the stand-in pytest reports, as pytest's report names them.
_KEPT = "tests.installed.test_gate_root_password_kept.TestRootPasswordKept"
ROOT_TIER_GATES = [(_KEPT, "test_installed_root_has_a_real_hash")]
USER_TIER_GATES = [
    (_KEPT, "test_control_reader_sees_a_locked_field"),
    ("tests.installed.test_gate_state_permissions",
     "test_shipped_code_creates_its_state_private_under_the_real_umask"),
    ("tests.installed.test_gate_glass_trace_integrity",
     "test_no_row_of_the_record_is_unreadable"),
]
ROOT_GATE_ID = f"{_KEPT}::test_installed_root_has_a_real_hash"


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_installed_gates", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _crypt_chars(seed: bytes, n: int) -> str:
    """``n`` characters of the crypt alphabet, fixed by ``seed``: a stand-in
    salt or hash that reads like a real one and is the same on every run."""
    alphabet = ("./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                "abcdefghijklmnopqrstuvwxyz")
    out, counter = "", 0
    while len(out) < n:
        block = hashlib.sha512(seed + counter.to_bytes(4, "big")).digest()
        out += "".join(alphabet[b % 64] for b in block)
        counter += 1
    return out[:n]


# A field that is locked but still carries its hash, as passwd -l leaves one.
SALT = _crypt_chars(b"root-gate-control-salt", 16)
HASH = _crypt_chars(b"root-gate-control-hash", 86)
LOCKED_OVER_HASH = f"!$6${SALT}${HASH}"

# The stand-in machine for the root gate: the gate file itself, unchanged, with
# a conftest that registers the marker, stands in for the installed-system
# fixture and points the gate at a crafted shadow file.
_STAND_IN_CONFTEST = '''\
from pathlib import Path

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "root_tier: measures only as root")


@pytest.fixture(scope="session")
def require_installed_intergenos():
    """The stand-in machine: the crafted shadow file is all there is."""


@pytest.fixture(autouse=True)
def crafted_shadow(request, monkeypatch):
    if hasattr(request.module, "SHADOW"):
        monkeypatch.setattr(request.module, "SHADOW", Path({shadow!r}))
'''


def _selection(argv: list[str]) -> "str | None":
    """The marker expression a pytest argv selects, or None for the whole tier."""
    rest = argv[argv.index("pytest") + 1:]
    return rest[rest.index("-m") + 1] if "-m" in rest else None


def _outcome(root_tier: bool, uid: int) -> tuple[str, str]:
    """What a gate reports when measured by ``uid``: a root-tier gate measures
    only as root and skips elsewhere; a user-tier gate breaks as root, reading
    root's home, journal and no session bus (measured 2026-10-14)."""
    if root_tier:
        if uid == 0:
            return "passed", ""
        return "skipped", ("NOT VERIFIED: /etc/shadow is readable by root only; "
                           "run this gate as root (the root tier)")
    if uid == 0:
        return "error", "as root it read root's home and found no session bus"
    return "passed", ""


def _junit(cases: list[tuple[str, str, str, str]]) -> str:
    body = []
    for classname, name, outcome, message in cases:
        inner = {"passed": "",
                 "skipped": f'<skipped type="pytest.skip" message="{message}" />',
                 "failed": f'<failure message="{message}" />',
                 "error": f'<error message="{message}" />'}[outcome]
        body.append(f'<testcase classname="{classname}" name="{name}">'
                    f'{inner}</testcase>')
    return ('<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest '
            'tests"><testsuite name="pytest">' + "".join(body)
            + "</testsuite></testsuites>")


class _Launch:
    """One in-process launch of the record runner on a stand-in installed
    machine: the account database, the identity calls and every subprocess are
    replaced; the runner's own logic, its staging directory, its seal and the
    hand-back copy are real."""

    def __init__(self, euid: int, empty_report: "str | None" = None,
                 real_pytest: bool = False) -> None:
        self.euid = euid
        self.empty_report = empty_report
        self.real_pytest = real_pytest
        self.calls: list[tuple[list[str], dict]] = []
        self.chowns: list[tuple] = []

    def run(self, argv: list[str], **kw) -> subprocess.CompletedProcess:
        argv = list(argv)
        self.calls.append((argv, dict(kw)))
        real = {k: v for k, v in kw.items()
                if k not in ("user", "group", "extra_groups")}
        if argv[1:3] == ["-I", "-c"]:
            # The hand-back copy runs for real, as this test's own account.
            return _REAL_RUN(argv, **real)
        if "-c" in argv and "import intergen" in argv[argv.index("-c") + 1]:
            return subprocess.CompletedProcess(argv, 0, INSTALLED_INTERGEN, "")
        if self.real_pytest:
            # pytest itself, over whatever tier the launch names.
            return _REAL_RUN(argv, **real)
        if "--collect-only" in argv:
            ids = [f"{c}::{n}" for c, n in USER_TIER_GATES + ROOT_TIER_GATES]
            return subprocess.CompletedProcess(argv, 0, "\n".join(ids) + "\n", "")
        junit = Path(next(a for a in argv if a.startswith("--junitxml="))
                     .split("=", 1)[1])
        selection = _selection(argv)
        uid = kw.get("user", self.euid)
        cases = []
        for root_tier, group in ((False, USER_TIER_GATES), (True, ROOT_TIER_GATES)):
            if selection is None or (selection == "root_tier") == root_tier:
                for classname, name in group:
                    cases.append((classname, name, *_outcome(root_tier, uid)))
        tier = "root" if selection == "root_tier" else "user"
        junit.write_text("" if self.empty_report == tier else _junit(cases),
                         encoding="utf-8")
        red = any(c[2] in ("failed", "error") for c in cases)
        return subprocess.CompletedProcess(argv, 1 if red else 0,
                                           f"{len(cases)} gates as uid {uid}\n", "")

    def chown(self, path, uid, gid, *args, **kw) -> None:
        self.chowns.append(("chown", str(path), uid, gid))

    def fchown(self, fd, uid, gid) -> None:
        self.chowns.append(("fchown", uid, gid))


class _RunnerCase(unittest.TestCase):
    """A scratch directory outside any checkout, and a launch helper."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="runner-tiers-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.out = self.tmp / "records" / "run-1"
        self.runner = _load_runner()

    def _launch(self, *, euid: int, sudo_user: "str | None" = None,
                extra: tuple = (), empty_report: "str | None" = None,
                environ: "dict | None" = None, tier: Path = TIER,
                real_pytest: bool = False):
        launch = _Launch(euid, empty_report, real_pytest)
        runner = self.runner
        output = io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.work)
        try:
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.dict(os.environ, environ or {}))
                os.environ.pop("SUDO_USER", None)
                if sudo_user is not None:
                    os.environ["SUDO_USER"] = sudo_user
                for name, value in (
                        ("geteuid", lambda: euid),
                        ("getegid", lambda: 0 if euid == 0 else ACCOUNT.pw_gid),
                        ("getgroups", lambda: [0] if euid == 0 else ACCOUNT_GROUPS),
                        ("getgrouplist", lambda n, g: list(ACCOUNT_GROUPS)),
                        ("chown", launch.chown), ("fchown", launch.fchown)):
                    stack.enter_context(mock.patch.object(runner.os, name, value))
                stack.enter_context(mock.patch.object(
                    pwd, "getpwnam", lambda n: ACCOUNTS_BY_NAME[n]))
                stack.enter_context(mock.patch.object(
                    pwd, "getpwuid", lambda u: ACCOUNTS_BY_UID[u]))
                stack.enter_context(mock.patch.object(
                    runner.subprocess, "run", launch.run))
                stack.enter_context(mock.patch.object(
                    runner, "_installed_identity", lambda: dict(INSTALLED_IDENTITY)))
                stack.enter_context(mock.patch.object(
                    runner, "_os_release", lambda: dict(OS_RELEASE)))
                stack.enter_context(contextlib.redirect_stdout(output))
                stack.enter_context(contextlib.redirect_stderr(output))
                try:
                    code = runner.main(["--output", str(self.out), "--tier",
                                        str(tier), "--shape", "upgrade", *extra])
                except SystemExit as e:
                    code = e.code
        finally:
            os.chdir(cwd)
        return code, output.getvalue(), launch

    def _record(self) -> dict:
        return json.loads((self.out / "record.json").read_text(encoding="utf-8"))

    def _pytest_runs(self, launch: _Launch) -> list[tuple[list[str], dict]]:
        """The pytest runs the launch asked for. A failure message built from
        these names the argv only, never the keyword arguments: they carry the
        whole environment of the process running this test."""
        return [(a, k) for a, k in launch.calls
                if any(x.startswith("--junitxml=") for x in a)]

    def _check(self, record_dir: Path) -> subprocess.CompletedProcess:
        """The release gate on a runner-made record. The record is unsigned, so
        UNSIGNED_TEST=1 downgrades only that; every other rule applies."""
        return _REAL_RUN(
            [sys.executable, str(CHECKER), "--record", str(record_dir),
             "--candidate-release", str(INSTALLED_IDENTITY["intergen_release"]),
             "--candidate-content-hash", ""],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "UNSIGNED_TEST": "1"})


def _pytest(*args: str) -> subprocess.CompletedProcess:
    """pytest over the tier from the repository root, as an ordinary run: the
    tier is collected (its gates skip unless asked for), nothing is executed."""
    env = {k: v for k, v in os.environ.items() if k != GATE_ENV}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        capture_output=True, text=True, cwd=str(REPO_ROOT), env=env, timeout=300)


class TheRootTierIsAMarkerTheTierRegisters(unittest.TestCase):
    """The runner selects the root tier by marker, so the marker has to exist,
    be registered under that exact name, and sit on the gate that needs root."""

    def test_the_root_password_gate_is_selected_by_the_root_tier_marker(self) -> None:
        r = _pytest("--collect-only", "-m", "root_tier", str(ROOT_GATE_FILE))
        selected = [line for line in r.stdout.splitlines() if "::" in line]
        self.assertEqual(
            selected, [ROOT_GATE_NODEID],
            "selecting the root tier must select exactly the gate that reads "
            "/etc/shadow; anything else means a root launch measures it as the "
            "user (where it skips) or measures a user-tier gate as root:\n"
            + r.stdout + r.stderr)

    def test_the_marker_is_registered_by_the_tier(self) -> None:
        """Registered, not merely used: an unregistered marker is a warning, and a
        misspelling of it would quietly put a root gate in the user tier."""
        r = _pytest("--markers", str(TIER))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("@pytest.mark.root_tier:", r.stdout,
                      "tests/installed/conftest.py does not register root_tier")

    def test_the_root_password_gates_control_stays_in_the_user_tier(self) -> None:
        """Only the gate that needs root is in the root tier. The reader's own
        control needs nothing and must keep measuring as the user."""
        r = _pytest("--collect-only", "-m", "not root_tier", str(ROOT_GATE_FILE))
        selected = [line for line in r.stdout.splitlines() if "::" in line]
        self.assertEqual(
            selected,
            ["tests/installed/test_gate_root_password_kept.py::"
             "TestRootPasswordKept::test_control_reader_sees_a_locked_field"],
            r.stdout + r.stderr)


class ARootLaunchMeasuresEachTierAsItsOwnIdentity(_RunnerCase):
    """Launched through sudo: the user tier as the account sudo came from, with
    that account's own environment; the root tier as root; one record."""

    def setUp(self) -> None:
        super().setUp()
        self.code, self.output, self.launch = self._launch(
            euid=0, sudo_user="gate-user",
            environ={"HOME": "/root", "USER": "root", "LOGNAME": "root",
                     "MAIL": "/var/mail/root", "SUDO_COMMAND": "/usr/bin/python3",
                     "SUDO_UID": "4242", "SUDO_GID": "4243"})

    def test_the_launch_writes_a_record(self) -> None:
        self.assertEqual(self.code, 0, self.output)
        self.assertTrue((self.out / "record.json").is_file(), self.output)

    def test_the_user_tier_runs_as_the_account_sudo_was_invoked_from(self) -> None:
        runs = self._pytest_runs(self.launch)
        user = [(a, k) for a, k in runs if _selection(a) == "not root_tier"]
        self.assertEqual(len(user), 1, [a for a, _ in runs])
        argv, kw = user[0]
        self.assertEqual((kw.get("user"), kw.get("group"), kw.get("extra_groups")),
                         (4242, 4243, ACCOUNT_GROUPS),
                         "the user tier did not change to the account's uid, "
                         "primary group and supplementary groups")
        self.assertIn("-p", argv)
        self.assertEqual(argv[argv.index("-p") + 1], "no:cacheprovider")

    def test_the_user_tier_carries_the_accounts_environment_and_none_of_sudos(self) -> None:
        (argv, kw), = [(a, k) for a, k in self._pytest_runs(self.launch)
                       if _selection(a) == "not root_tier"]
        env = kw["env"]
        expected = {"HOME": "/home/gate-user", "USER": "gate-user",
                    "LOGNAME": "gate-user", "XDG_RUNTIME_DIR": "/run/user/4242",
                    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/4242/bus",
                    GATE_ENV: "1", "IGOS_RECORD_SHAPE": "upgrade"}
        for key, value in expected.items():
            self.assertEqual(env.get(key), value, key)
        self.assertFalse("MAIL" in env, "root's MAIL reached the user tier")
        self.assertEqual([k for k in env if k.startswith("SUDO_")], [])

    def test_the_root_tier_runs_as_root(self) -> None:
        runs = self._pytest_runs(self.launch)
        root = [(a, k) for a, k in runs if _selection(a) == "root_tier"]
        self.assertEqual(len(root), 1, [a for a, _ in runs])
        self.assertFalse("user" in root[0][1],
                         "the root tier changed identity; it must measure as root")
        self.assertEqual(root[0][1]["env"].get(GATE_ENV), "1")

    def test_the_record_names_who_measured_each_gate(self) -> None:
        record = self._record()
        self.assertEqual(record["record_version"], 2)
        by_id = {g["id"]: g for g in record["gates"]}
        root = by_id[ROOT_GATE_ID]
        self.assertEqual((root["tier"], root["measured_as"], root["outcome"]),
                         ("root", {"user": "root", "uid": 0}, "passed"))
        users = [g for g in record["gates"] if g["id"] != ROOT_GATE_ID]
        self.assertEqual(len(users), len(USER_TIER_GATES))
        for gate in users:
            self.assertEqual(
                (gate["tier"], gate["measured_as"], gate["outcome"]),
                ("user", {"user": "gate-user", "uid": 4242}, "passed"), gate)

    def test_the_invocation_carries_both_runs(self) -> None:
        inv = self._record()["invocation"]
        self.assertEqual(inv["launched_by"],
                         {"user": "root", "uid": 0, "sudo_user": "gate-user"})
        self.assertEqual([r["tier"] for r in inv["runs"]], ["user", "root"])
        user, root = inv["runs"]
        self.assertEqual(user["measured_as"],
                         {"user": "gate-user", "uid": 4242, "gid": 4243,
                          "groups": ACCOUNT_GROUPS})
        self.assertEqual(root["measured_as"]["uid"], 0)
        self.assertEqual(_selection(user["argv"]), "not root_tier")
        self.assertEqual(_selection(root["argv"]), "root_tier")
        self.assertEqual(user["env"]["HOME"], "/home/gate-user")
        self.assertEqual((user["capture"], user["junit"]),
                         ("pytest-output.txt", "junit.xml"))
        self.assertEqual((root["capture"], root["junit"]),
                         ("pytest-output-root-tier.txt", "junit-root-tier.xml"))
        for run in inv["runs"]:
            self.assertEqual(run["returncode"], 0)

    def test_the_seal_covers_both_tiers_files(self) -> None:
        sealed = sorted(line.split("  ", 1)[1] for line in
                        (self.out / "SHA256SUMS").read_text().splitlines())
        self.assertEqual(sealed, ["junit-root-tier.xml", "junit.xml",
                                  "pytest-output-root-tier.txt",
                                  "pytest-output.txt", "record.json"])

    def test_the_account_writes_only_its_own_report_into_staging(self) -> None:
        """Root's staging directory stays root's: the user tier gets the one
        report file it writes, made in advance and given to the account."""
        self.assertIn(("fchown", 4242, 4243), self.launch.chowns)

    def test_the_record_is_handed_back_by_the_account_itself(self) -> None:
        """Root never writes into the account's directory: the account copies
        its record out of root's staging, and staging is then removed."""
        places = [(a, k) for a, k in self.launch.calls if a[1:3] == ["-I", "-c"]]
        self.assertEqual(len(places), 1, [a for a, _ in self.launch.calls])
        argv, kw = places[0]
        self.assertEqual((kw.get("user"), kw.get("group")), (4242, 4243))
        self.assertEqual(argv[5], str(self.out.absolute()))
        staging = Path(argv[4])
        self.assertFalse(staging.exists(), "staging was left behind")
        given = sorted(Path(c[1]).name for c in self.launch.chowns
                       if c[0] == "chown" and c[2:] == (4242, 4243))
        self.assertEqual(given, sorted(p.name for p in self.out.iterdir()))

    def test_the_launch_says_once_who_measured_each_tier(self) -> None:
        lines = [line for line in self.output.splitlines()
                 if "the user tier ran as gate-user (uid 4242), the root tier "
                    "as root (uid 0)" in line]
        self.assertEqual(len(lines), 1, self.output)
        self.assertNotIn("ROOT TIER NOT MEASURED", self.output)

    def test_the_release_gate_accepts_the_record(self) -> None:
        """The control for the refusal below: this record differs from the
        unprivileged one only in who measured the root tier."""
        r = self._check(self.out)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_the_release_gate_names_who_measured_the_gates(self) -> None:
        r = self._check(self.out)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("measured as gate-user (uid 4242): 3 gates; "
                      "root (uid 0): 1 gate.", r.stdout)


class AnUnprivilegedLaunchSaysTheRootTierWasNotMeasured(_RunnerCase):
    """Launched without root: both tiers measure as the invoking account, the
    root tier skips, one line says so, and the release gate refuses."""

    def setUp(self) -> None:
        super().setUp()
        self.code, self.output, self.launch = self._launch(euid=4242)

    def test_both_tiers_run_as_the_invoking_account(self) -> None:
        runs = self._pytest_runs(self.launch)
        self.assertEqual(sorted(_selection(a) for a, _ in runs),
                         ["not root_tier", "root_tier"])
        for argv, kw in runs:
            self.assertFalse("user" in kw, argv)

    def test_the_root_gate_is_recorded_as_skipped_by_the_account(self) -> None:
        self.assertEqual(self.code, 0, self.output)
        root = {g["id"]: g for g in self._record()["gates"]}[ROOT_GATE_ID]
        self.assertEqual((root["tier"], root["measured_as"], root["outcome"]),
                         ("root", {"user": "gate-user", "uid": 4242}, "skipped"))

    def test_one_line_says_the_root_tier_was_not_measured(self) -> None:
        lines = [line for line in self.output.splitlines()
                 if "ROOT TIER NOT MEASURED" in line]
        self.assertEqual(len(lines), 1, self.output)
        self.assertIn("gate-user (uid 4242)", lines[0])

    def test_the_release_gate_refuses_the_record(self) -> None:
        r = self._check(self.out)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("skipped without being declared", r.stderr)
        self.assertIn(ROOT_GATE_ID, r.stderr)


class TheRunnerRefusesALaunchItCannotAttribute(_RunnerCase):
    """No record is written when the runner cannot say who measured what."""

    def _refused(self, needle: str, **launch) -> None:
        code, output, _ = self._launch(**launch)
        self.assertEqual(code, 1, output)
        self.assertIn("REFUSED", output)
        self.assertIn(needle, output)
        self.assertFalse(self.out.exists(), "a refused launch left a record")

    def test_a_root_launch_with_no_account_named(self) -> None:
        self._refused("no account to measure the user tier as", euid=0)

    def test_a_root_launch_naming_root(self) -> None:
        self._refused("is root (uid 0)", euid=0, extra=("--user", "root"))

    def test_a_root_launch_naming_an_unknown_account(self) -> None:
        self._refused("not in the password database", euid=0,
                      extra=("--user", "nobody-here"))

    def test_an_unprivileged_launch_asking_for_another_account(self) -> None:
        self._refused("names another account", euid=4242,
                      extra=("--user", "root"))

    def test_a_root_launch_whose_root_tier_wrote_an_empty_report(self) -> None:
        self._refused("no readable report for the root tier", euid=0,
                      sudo_user="gate-user", empty_report="root")


class TheRootGateReportsTheFieldsClassNeverItsBytes(_RunnerCase):
    """The root gate runs as root on every validation machine, and its failure
    message lands in the capture, the report and the sealed record. A field that
    is locked but still carries its hash fails the gate: the record must say
    what the field is and carry no fragment of the hash or its salt.

    Proven end to end: the real gate file, unchanged, run by real pytest under
    the runner, against a crafted shadow file; every file of the record read."""

    def setUp(self) -> None:
        super().setUp()
        tier = self.tmp / "tier"
        tier.mkdir()
        shutil.copyfile(ROOT_GATE_FILE, tier / ROOT_GATE_FILE.name)
        shadow = self.tmp / "shadow"
        shadow.write_text(f"root:{LOCKED_OVER_HASH}:20000:0:99999:7:::\n",
                          encoding="utf-8")
        (tier / "conftest.py").write_text(
            _STAND_IN_CONFTEST.format(shadow=str(shadow)), encoding="utf-8")
        self.code, self.output, _ = self._launch(euid=4242, tier=tier,
                                                 real_pytest=True)

    def _root_gate(self) -> dict:
        gates = [g for g in self._record()["gates"]
                 if g["id"].endswith("::test_installed_root_has_a_real_hash")]
        self.assertEqual(len(gates), 1, self.output)
        return gates[0]

    def test_the_gate_read_the_crafted_field_and_failed(self) -> None:
        """The control: a record with no hash in it proves nothing unless the
        gate read the field that carries one."""
        self.assertEqual(self.code, 0, self.output)
        gate = self._root_gate()
        self.assertEqual((gate["tier"], gate["outcome"]), ("root", "failed"), gate)

    def test_the_failure_names_the_fields_class(self) -> None:
        self.assertIn("root's password field is locked ('!') over a $6$ hash of "
                      "106 characters", self._root_gate()["reason"])

    def test_no_fragment_of_the_hash_or_its_salt_reaches_the_record(self) -> None:
        files = sorted(self.out.iterdir())
        self.assertEqual([p.name for p in files],
                         ["SHA256SUMS", "junit-root-tier.xml", "junit.xml",
                          "pytest-output-root-tier.txt", "pytest-output.txt",
                          "record.json"], self.output)
        carriers = []
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace")
            for label, secret in (("salt", SALT), ("hash", HASH)):
                if any(secret[i:i + 8] in text for i in range(len(secret) - 7)):
                    carriers.append(f"{path.name} ({label})")
        self.assertEqual(carriers, [],
                         "files of the record carrying eight or more characters "
                         "of the crafted field's salt or hash")


class TheRootGatesMessageIsAClass(unittest.TestCase):
    """Each kind of field the gate refuses, and the words it uses for it."""

    def setUp(self) -> None:
        spec = importlib.util.spec_from_file_location("root_gate", ROOT_GATE_FILE)
        self.gate = importlib.util.module_from_spec(spec)
        # The tier's own conftest registers root_tier; imported from here, outside
        # the tier, the gate's marker is unknown to this session. That one warning
        # is expected for this import and is filtered for it alone.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message=r"Unknown pytest\.mark\.root_tier")
            spec.loader.exec_module(self.gate)

    def test_each_refused_field_is_named_by_its_class(self) -> None:
        yescrypt = f"$y$j9T${SALT}${HASH}"
        cases = {
            "": "empty",
            "!": "the locked marker '!'",
            "*": "the locked marker '*'",
            "!!": "the locked marker '!!'",
            LOCKED_OVER_HASH: "locked ('!') over a $6$ hash of 106 characters",
            f"!!{yescrypt}": (f"locked ('!!') over a $y$ hash of "
                              f"{len(yescrypt)} characters"),
            f"*{SALT}": ("locked ('*') over a value of 16 characters that is not "
                         "a hash of a known crypt scheme"),
            f"!${SALT}${HASH}": (f"locked ('!') over a value of "
                                 f"{len(SALT) + len(HASH) + 2} characters that is "
                                 f"not a hash of a known crypt scheme"),
            f"{SALT}{HASH}": ("a value of 102 characters that is not a hash of a "
                              "known crypt scheme"),
        }
        for field, expected in cases.items():
            with self.subTest(expected=expected):
                said = self.gate._field_class(field)
                self.assertEqual(said, expected)
                for secret in (SALT, HASH):
                    self.assertFalse(secret[:8] in said)


if __name__ == "__main__":
    unittest.main()
