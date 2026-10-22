# SPDX-License-Identifier: GPL-3.0-or-later
"""A failure after removal names the changed package and its recovery point."""

import hashlib
import io
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pkm import cli, output, pretxn
from pkm import installer as installer_module
from pkm.hooks import HookResult
from pkm.installer import PackageInstaller, SIGNING_REFUSED_EXIT
from pkm.remover import PackageRemover
from tests.pkm.test_install_refuses_a_kind_change_at_another_packages_path import (
    _archive as _kind_archive)
from tests.pkm.test_local_archive_preflight import held_copy, local, invoke
from tests.pkm.test_upgrade_from_local_archive import FakeRepo, _make_archive


@pytest.fixture(autouse=True)
def process_reporter_streams(monkeypatch):
    # Put the process reporter's own stream attributes back after each case.
    # Patching its stream properties restores the stream that was current at
    # setup, and the shared invoke helper assigns them outright; either leaves
    # the reporter bound to a stream that is gone, and a later module's
    # redirect of stdout or stderr no longer reaches it.
    for name in ("_stream", "_err_stream"):
        monkeypatch.setattr(output._process_reporter, name,
                            getattr(output._process_reporter, name))


@pytest.mark.parametrize("failure", ["changed-archive", "unchanged-message"])
def test_late_refusal_names_removal_and_restore_id(local, failure):
    restore_id = "0000000042-abcdef123456"
    captured = SimpleNamespace(ran=True, handlers=["chronicle"], failures=[],
                               messages=[], restore_points=[restore_id])
    remove = PackageRemover.remove
    def removing(self, *args, **kwargs):
        result = remove(self, *args, **kwargs)
        held_copy().write_bytes(b"changed after removal")
        return result
    install = local.installer.__class__.install
    def installing(self, *args, **kwargs):
        if failure == "unchanged-message":
            return False, "Archive refused. Nothing was changed."
        return install(self, *args, **kwargs)
    with patch.object(PackageRemover, "remove", removing), \
            patch.object(local.installer.__class__, "install", installing):
        rc, said = invoke(local, capture=lambda *a, **k: captured)
    assert rc == 1, said
    assert local.db.get_installed("widget") is None
    assert "widget 1.0-3 was removed" in said
    assert restore_id in said
    assert "Nothing was changed" not in said


RESTORE_ID = "0000000042-abcdef123456"


def attempt(local, monkeypatch, *, verb="upgrade", rollback=False, names=None,
            repo=None, points=(RESTORE_ID,), archive=None):
    names = names or ["widget"]
    argv = [verb, *names]
    if repo is None:
        argv += ["--archive", str(archive or local.new), "--archive-trust", "loose"]
    if verb == "upgrade":
        argv.append("--yes")
    args = cli.build_parser().parse_args(argv)
    captured = SimpleNamespace(ran=True, handlers=["chronicle"], failures=[],
                               messages=[], restore_points=points)
    calls = []

    def capture(db, action, packages, **kwargs):
        calls.append((action, packages))
        return captured

    def saved(name, version, release):
        archive = local.new.with_name(f"{name}-{version}-{release}.igos.tar.gz")
        return archive, hashlib.sha256(archive.read_bytes()).hexdigest()

    monkeypatch.setattr(cli, "repo_manager", lambda: repo or FakeRepo())
    monkeypatch.setattr(cli, "_save_rollback_archive", saved if rollback else lambda *a: None)
    monkeypatch.setattr(cli, "refresh_available_updates_after_transaction", lambda *a: None)
    monkeypatch.setattr(cli, "_print_transaction_next_steps", lambda *a: None)
    monkeypatch.setattr(pretxn, "run_pre_transaction_hook", capture)
    monkeypatch.setattr(cli, "_INSTALL_ROOT", local.root)
    said = io.StringIO()
    level = output.process_level()
    output.set_process_level(output.NORMAL)
    monkeypatch.setattr(output._process_reporter, "stream", said)
    monkeypatch.setattr(output._process_reporter, "err_stream", said)
    try:
        with redirect_stdout(said), redirect_stderr(said):
            try:
                rc = getattr(cli, "cmd_" + verb)(local.db, args)
            except SystemExit as error:
                rc = error.code
    finally:
        output.set_process_level(level)
    assert calls == [(verb, names)]
    print(said.getvalue())
    return rc, " ".join(said.getvalue().split())


def change_archive_after_removal(local, monkeypatch):
    remove = PackageRemover.remove

    def removing(self, name, **kwargs):
        result = remove(self, name, **kwargs)
        if name == "widget":
            assert result[0], result
            held_copy(local.new).write_bytes(b"changed after removal")
        return result

    monkeypatch.setattr(PackageRemover, "remove", removing)


def test_restored_upgrade_has_no_transaction_restore_advice(local, monkeypatch):
    change_archive_after_removal(local, monkeypatch)
    rc, said = attempt(local, monkeypatch, rollback=True)
    assert rc == 1, said
    assert local.db.get_installed("widget")["release"] == 3
    assert (local.root / "usr/bin/widget").read_bytes() == b"old\n"
    assert "Rollback succeeded" in said
    assert "widget 1.0-3 was removed and restored" in said
    assert RESTORE_ID not in said
    assert "Recover from" not in said
    assert "No restore-point id" not in said


@pytest.mark.parametrize("verb", ["upgrade", "install"])
def test_degraded_install_has_only_finish_advice(local, monkeypatch, verb):
    if verb == "install":
        assert PackageRemover(local.db).remove("widget", force=True)[0]
    monkeypatch.setattr(PackageInstaller, "_run_post_install_hook",
                        lambda *a: SIGNING_REFUSED_EXIT)
    rc, said = attempt(local, monkeypatch, verb=verb, rollback=True)
    assert rc == 1, said
    row = local.db.get_installed("widget")
    assert row["release"] == 4 and row["degraded"]
    assert (local.root / "usr/bin/widget").read_bytes() == b"new\n"
    assert "sudo pkm reinstall widget" in said
    assert RESTORE_ID not in said
    assert "Recover from" not in said
    assert "was removed before" not in said


@pytest.mark.parametrize("points", [(RESTORE_ID,), ()], ids=["with-id", "no-id"])
def test_absent_upgrade_keeps_transaction_recovery(local, monkeypatch, points):
    change_archive_after_removal(local, monkeypatch)
    rc, said = attempt(local, monkeypatch, points=points)
    assert rc == 1, said
    assert local.db.get_installed("widget") is None
    assert not (local.root / "usr/bin/widget").exists()
    assert "widget 1.0-3 was removed before this install attempt" in said
    assert "Nothing was changed" not in said
    if points:
        assert f"Recover from Chronicle restore point {RESTORE_ID}." in said
    else:
        assert "No restore-point id was returned" in said
        assert RESTORE_ID not in said


@pytest.mark.parametrize("points", [(RESTORE_ID,), ()], ids=["with-id", "no-id"])
def test_archive_install_failure_after_write_names_restore_point(local, monkeypatch, points):
    assert PackageRemover(local.db).remove("widget", force=True)[0]
    extract = installer_module._safe_extract_tar
    deployed = []

    def extracting(archive, destination, **kwargs):
        result = extract(archive, destination, **kwargs)
        if Path(destination) == local.root:
            assert result[0], result
            assert (local.root / "usr/bin/widget").read_bytes() == b"new\n"
            deployed.append(True)
            return False, "test refusal after target write"
        return result

    monkeypatch.setattr(installer_module, "_safe_extract_tar", extracting)
    rc, said = attempt(local, monkeypatch, verb="install", points=points)
    assert rc == 1 and deployed == [True], said
    assert local.db.get_installed("widget") is None
    assert (local.root / "usr/bin/widget").read_bytes() == b"new\n"
    assert "test refusal after target write" in said
    if points:
        assert f"Recover from Chronicle restore point {RESTORE_ID}." in said
    else:
        assert "No restore-point id was returned" in said
        assert RESTORE_ID not in said
        assert "previous build" not in said
    assert "was removed" not in said
    assert "Nothing was changed" not in said


def test_existing_archive_install_has_no_recovery_advice(local, monkeypatch):
    rc, said = attempt(local, monkeypatch, verb="install")
    assert rc == 1, said
    assert local.db.get_installed("widget") == local.before
    assert (local.root / "usr/bin/widget").read_bytes() == b"old\n"
    assert "already installed" in said
    assert RESTORE_ID not in said
    assert "Recover from" not in said


def test_refusal_before_any_write_prints_the_installers_message_alone(
        local, monkeypatch, tmp_path):
    # Another package owns a symbolic link where this archive ships a regular
    # file: install() refuses before it writes anything and says so.
    linked = _kind_archive(tmp_path, "alpha", links={"usr/bin/typer": "alpha-tool"})
    assert local.installer.install("alpha", archive_path=linked)[0]
    made = Path(_kind_archive(tmp_path, "beta",
                              files={"usr/bin/typer": b"#!/bin/sh\necho beta\n"}))
    beta = made.rename(made.with_name("beta-1.0-1.igos.tar.gz"))
    rc, said = attempt(local, monkeypatch, verb="install", names=["beta"],
                       archive=beta)
    assert rc == 1, said
    assert local.db.get_installed("beta") is None
    assert os.readlink(local.root / "usr/bin/typer") == "alpha-tool"
    assert "would put a different kind of file" in said
    assert "Nothing was changed." in said
    assert "Recovery if this attempt changed files" not in said
    assert RESTORE_ID not in said
    assert "No restore-point id" not in said


def test_later_rollback_does_not_advise_undoing_an_earlier_upgrade(local, monkeypatch):
    first_old = _make_archive(local.new.with_name("alpha-1.0-3.igos.tar.gz"),
                              "alpha", "1.0", 3, {"usr/bin/alpha": b"old\n"})
    assert local.installer.install("alpha", archive_path=first_old)[0]
    first_new = _make_archive(local.new.with_name("alpha-1.0-4.igos.tar.gz"),
                              "alpha", "1.0", 4, {"usr/bin/alpha": b"new\n"})
    archives = {"alpha": first_new, "widget": local.new}
    repo = FakeRepo({name: {"name": name, "version": "1.0", "release": 4,
                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                     for name, path in archives.items()})
    repo.download_package = lambda name: (True, archives[name])
    change_archive_after_removal(local, monkeypatch)
    rc, said = attempt(local, monkeypatch, rollback=True,
                       names=["alpha", "widget"], repo=repo)
    assert rc == 1, said
    assert local.db.get_installed("alpha")["release"] == 4
    assert (local.root / "usr/bin/alpha").read_bytes() == b"new\n"
    assert local.db.get_installed("widget")["release"] == 3
    assert (local.root / "usr/bin/widget").read_bytes() == b"old\n"
    assert "Rollback succeeded" in said
    assert RESTORE_ID not in said
    assert "Recover from" not in said


# Advice install() used to compose before its caller knew the outcome.
INSTALLER_ADVICE = ("Rollback recommended", "reinstall to retry", "Re-run",
                    "to recover")


def critical_hook_fails(monkeypatch):
    monkeypatch.setattr(installer_module, "run_archive_lifecycle_hook",
                        lambda *a, **k: HookResult(["widget post-install"], [], []))


def signing_refused(monkeypatch):
    monkeypatch.setattr(PackageInstaller, "_run_post_install_hook",
                        lambda *a: SIGNING_REFUSED_EXIT)


def database_fails_once(monkeypatch):
    """The first install's database step fails after its files are deployed."""
    ratchet = installer_module.ratchet_baselines
    calls = []

    def failing(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("test database failure after deploy")
        return ratchet(*args, **kwargs)

    monkeypatch.setattr(installer_module, "ratchet_baselines", failing)
    return calls


@pytest.mark.parametrize("verb,failure", [
    ("upgrade", "critical"), ("install", "critical"),
    ("upgrade", "signing"), ("install", "signing"),
    ("upgrade", "signing-and-critical")])
def test_degraded_install_has_one_recovery_sentence(local, monkeypatch, verb, failure):
    if verb == "install":
        assert PackageRemover(local.db).remove("widget", force=True)[0]
    if "critical" in failure:
        critical_hook_fails(monkeypatch)
    if "signing" in failure:
        signing_refused(monkeypatch)
    rc, said = attempt(local, monkeypatch, verb=verb, rollback=True)
    assert rc == 1, said
    row = local.db.get_installed("widget")
    assert row["release"] == 4 and row["degraded"]
    for phrase in INSTALLER_ADVICE:
        assert phrase not in said
    finish = ("Finish it with the signing key passphrase in hand"
              if "signing" in failure else "Retry it with: sudo pkm reinstall widget")
    assert said.count(finish) == 1, said
    assert said.count("sudo pkm reinstall widget") == 1, said
    assert said.count("do not reboot for it yet") == (failure == "signing")
    assert RESTORE_ID not in said


def test_database_failure_after_a_restored_rollback_names_no_install(local, monkeypatch):
    calls = database_fails_once(monkeypatch)
    rc, said = attempt(local, monkeypatch, rollback=True)
    assert rc == 1 and len(calls) == 2, said
    assert local.db.get_installed("widget")["release"] == 3
    assert (local.root / "usr/bin/widget").read_bytes() == b"old\n"
    assert "FAILED at DB transaction after deploy" in said
    assert "widget 1.0-3 was removed and restored from the rollback cache" in said
    for phrase in INSTALLER_ADVICE:
        assert phrase not in said
    assert "pkm install widget" not in said
    assert RESTORE_ID not in said


def test_archive_install_database_failure_has_one_recovery_sentence(local, monkeypatch):
    assert PackageRemover(local.db).remove("widget", force=True)[0]
    calls = database_fails_once(monkeypatch)
    rc, said = attempt(local, monkeypatch, verb="install")
    assert rc == 1 and len(calls) == 1, said
    assert local.db.get_installed("widget") is None
    assert "FAILED at DB transaction after deploy" in said
    for phrase in INSTALLER_ADVICE:
        assert phrase not in said
    assert said.count("Recover from Chronicle restore point") == 1, said
    assert ("Recovery if this attempt changed files: Recover from Chronicle "
            f"restore point {RESTORE_ID}.") in said


@pytest.mark.parametrize("points", [(RESTORE_ID,), ()], ids=["with-id", "no-id"])
def test_absent_upgrade_names_what_its_restore_point_would_revert(
        local, monkeypatch, points):
    # alpha upgrades before widget and zeta after it (alphabetical within a
    # rank); zeta's new release brings gamma as a new dependency. widget's
    # install fails after its removal with no rollback copy, so it is absent.
    # The restore point covers every package the transaction named and pkm.db,
    # so the sentence that names it, composed once the transaction has ended,
    # names alpha's and zeta's upgrades and gamma's record too.
    archives = {"widget": local.new}
    for name in ("alpha", "zeta"):
        old = _make_archive(local.new.with_name(f"{name}-1.0-3.igos.tar.gz"),
                            name, "1.0", 3, {f"usr/bin/{name}": b"old\n"})
        assert local.installer.install(name, archive_path=old)[0]
        archives[name] = _make_archive(
            local.new.with_name(f"{name}-1.0-4.igos.tar.gz"), name, "1.0", 4,
            {f"usr/bin/{name}": b"new\n"})
    archives["gamma"] = _make_archive(local.new.with_name("gamma-1.0-1.igos.tar.gz"),
                                      "gamma", "1.0", 1, {"usr/bin/gamma": b"gamma\n"})
    entries = {name: {"name": name, "version": "1.0", "release": 4,
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
               for name, path in archives.items()}
    entries["gamma"]["release"] = 1
    entries["zeta"]["depends"] = ["gamma"]
    repo = FakeRepo(entries)
    repo.download_package = lambda name, reporter=None: (True, archives[name])
    change_archive_after_removal(local, monkeypatch)
    rc, said = attempt(local, monkeypatch, names=["alpha", "widget", "zeta"],
                       repo=repo, points=points)
    assert rc == 1, said
    assert local.db.get_installed("widget") is None
    assert local.db.get_installed("alpha")["release"] == 4
    assert local.db.get_installed("zeta")["release"] == 4
    assert local.db.get_installed("gamma")["release"] == 1
    assert "widget 1.0-3 was removed before this install attempt" in said
    # The point holds none of gamma's files (a package not installed before the
    # transaction contributes none to its footprint), so a restore leaves them.
    reverts = ("returns alpha from 1.0-4 to 1.0-3 and zeta from 1.0-4 to 1.0-3 "
               "(upgraded by this transaction) and removes the record of gamma "
               "(installed by this transaction), whose files stay")
    if points:
        recovery = (f"Recover from Chronicle restore point {RESTORE_ID}, which "
                    f"also {reverts}.")
    else:
        # What restoring the capture would also undo is said of the restore,
        # not of the capture.
        recovery = ("Inspect `chronicle list restore-point` for a matching capture "
                    f"(restoring it also {reverts}), or reinstall a verified "
                    "archive of the previous build.")
        assert RESTORE_ID not in said
    assert said.count(recovery) == 1, said
    assert said.count("Recover from") + said.count("No restore-point id") == 1, said
    # Composed after the transaction ended: after zeta's upgrade, not at widget's.
    assert said.index("Upgraded zeta to 1.0-4") < said.index(recovery)


def test_rejected_rollback_copy_leaves_one_recovery_sentence(local, monkeypatch):
    # The copy saved for the rollback carries another release than its name
    # says, so the gate before the restore refuses it and widget stays absent.
    old = local.new.with_name("widget-1.0-3.igos.tar.gz")
    old.write_bytes(local.new.read_bytes())
    change_archive_after_removal(local, monkeypatch)
    rc, said = attempt(local, monkeypatch, rollback=True)
    assert rc == 1, said
    assert local.db.get_installed("widget") is None
    assert "NOT restoring widget from the rollback cache" in said
    assert "--archive=" not in said
    assert said.count("Recover from") == 1, said
    assert ("widget is not installed. Recover from Chronicle restore point "
            f"{RESTORE_ID}.") in said


def test_failed_rollback_install_leaves_one_recovery_sentence(local, monkeypatch):
    # The rollback copy keeps its name and header but not the bytes it was
    # saved with, so its install refuses and widget stays absent.
    old = local.new.with_name("widget-1.0-3.igos.tar.gz")
    remove = PackageRemover.remove

    def removing(self, name, **kwargs):
        result = remove(self, name, **kwargs)
        assert result[0], result
        held_copy().write_bytes(b"changed after removal")
        _make_archive(old, "widget", "1.0", 3, {"usr/bin/widget": b"other\n"})
        return result

    monkeypatch.setattr(PackageRemover, "remove", removing)
    rc, said = attempt(local, monkeypatch, rollback=True)
    assert rc == 1, said
    assert local.db.get_installed("widget") is None
    assert "CRITICAL: rollback of widget also failed" in said
    assert "Manual recovery" not in said
    assert "--archive=" not in said
    assert said.count("Recover from") == 1, said
    assert ("widget is not installed. Recover from Chronicle restore point "
            f"{RESTORE_ID}.") in said


@pytest.mark.parametrize("failure,points", [
    ("critical", (RESTORE_ID,)), ("critical", ()),
    ("signing", (RESTORE_ID,)), ("signing", ())],
    ids=["critical-with-id", "critical-no-id", "signing-with-id", "signing-no-id"])
def test_rollback_that_landed_degraded_names_the_command_that_finishes_it(
        local, monkeypatch, failure, points):
    # widget's new build refuses after the old build's removal; the rollback
    # install of 1.0-3 then LANDS and a critical post-install hook fails, or
    # the signing hook refuses. The old build's row is back, marked degraded:
    # widget is not absent, so the one next step is the command that finishes
    # the old build, never the transaction's restore point.
    change_archive_after_removal(local, monkeypatch)
    if failure == "critical":
        critical_hook_fails(monkeypatch)
        reason = "widget post-install"
        finish = "Retry it with: sudo pkm reinstall widget"
    else:
        signing_refused(monkeypatch)
        reason = "widget post-install (refused to sign)"
        finish = ("Finish it with the signing key passphrase in hand (the one set "
                  "at install, not the MOK enrollment password): sudo pkm "
                  "reinstall widget")
    rc, said = attempt(local, monkeypatch, rollback=True, points=points)
    assert rc == 1, said
    row = local.db.get_installed("widget")
    assert row["release"] == 3 and row["degraded"] == reason, row
    assert (local.root / "usr/bin/widget").read_bytes() == b"old\n"
    # The row the rollback install wrote is the one _unfinished_install reads,
    # as it reads a forward install's.
    assert cli._unfinished_install(local.db, "widget", False) == reason
    assert "restoring widget 1.0 from rollback cache" in said
    assert "rollback of widget also failed" not in said
    assert "widget is not installed" not in said
    assert "Recover from" not in said and "No restore-point id" not in said
    assert "chronicle list restore-point" not in said
    for phrase in INSTALLER_ADVICE:
        assert phrase not in said
    assert said.count(finish) == 1, said
    assert said.count("sudo pkm reinstall widget") == 1, said
    assert (f"widget is back at 1.0-3 from the rollback cache, but its install "
            f"did not finish ({reason}). {finish}") in said
    assert "1 of 1 package(s) did not upgrade: widget" in said
