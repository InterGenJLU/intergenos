# SPDX-License-Identifier: GPL-3.0-or-later
"""A failure after removal names the changed package and its recovery point."""

import hashlib
import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pkm import cli, output, pretxn
from pkm import installer as installer_module
from pkm.installer import PackageInstaller, SIGNING_REFUSED_EXIT
from pkm.remover import PackageRemover
from tests.pkm.test_local_archive_preflight import local, invoke
from tests.pkm.test_upgrade_from_local_archive import FakeRepo, _make_archive

@pytest.mark.parametrize("failure", ["changed-archive", "unchanged-message"])
def test_late_refusal_names_removal_and_restore_id(local, failure):
    restore_id = "0000000042-abcdef123456"
    captured = SimpleNamespace(ran=True, handlers=["chronicle"], failures=[],
                               messages=[], restore_points=[restore_id])
    remove = PackageRemover.remove
    def removing(self, *args, **kwargs):
        result = remove(self, *args, **kwargs)
        local.new.write_bytes(b"changed after removal")
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
            repo=None, points=(RESTORE_ID,)):
    names = names or ["widget"]
    argv = [verb, *names]
    if repo is None:
        argv += ["--archive", str(local.new), "--archive-trust", "loose"]
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
            local.new.write_bytes(b"changed after removal")
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
