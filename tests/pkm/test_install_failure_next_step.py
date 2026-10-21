# SPDX-License-Identifier: GPL-3.0-or-later
"""A failed install prints one next step, chosen by the command that ran it.

install() names what failed and leaves the next step to its caller. The callers
that print its message with no outcome of their own (the dependency loop of
`pkm install`, a cached archive's install, a download helper's package and an
upgrade's new dependency) each print one sentence: the finish advice when the
package landed degraded, and the command the person ran when the database step
failed after the files were deployed.
"""

import hashlib
import io
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace

import pytest

from pkm import cli, output, pretxn
from pkm import installer as installer_module
from pkm.hooks import HookResult
from pkm.installer import PackageInstaller
from pkm.remover import PackageRemover
from tests.pkm.test_local_archive_preflight import local
from tests.pkm.test_upgrade_from_local_archive import FakeRepo, _make_archive

# Advice install() used to compose before its caller knew the outcome.
INSTALLER_ADVICE = ("Rollback recommended", "reinstall to retry", "Re-run",
                    "to recover")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entry(name, archive, release=4, depends=()):
    return {"name": name, "version": "1.0", "release": release,
            "sha256": _sha(archive), "depends": list(depends)}


def fail(monkeypatch, outcome):
    """Make the next install land degraded, or fail its database step."""
    if outcome == "degraded":
        monkeypatch.setattr(installer_module, "run_archive_lifecycle_hook",
                            lambda *a, **k: HookResult(["post-install"], [], []))
        return
    ratchet = installer_module.ratchet_baselines
    calls = []

    def failing(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("test database failure after deploy")
        return ratchet(*args, **kwargs)

    monkeypatch.setattr(installer_module, "ratchet_baselines", failing)


def run(local, monkeypatch, argv, repo, call=None):
    captured = SimpleNamespace(ran=False, handlers=[], failures=[], messages=[],
                               restore_points=())
    monkeypatch.setattr(cli, "repo_manager", lambda: repo)
    monkeypatch.setattr(cli, "_save_rollback_archive", lambda *a: None)
    monkeypatch.setattr(cli, "refresh_available_updates_after_transaction", lambda *a: None)
    monkeypatch.setattr(cli, "_print_transaction_next_steps", lambda *a: None)
    monkeypatch.setattr(pretxn, "run_pre_transaction_hook", lambda *a, **k: captured)
    monkeypatch.setattr(cli, "_INSTALL_ROOT", local.root)
    said = io.StringIO()
    level = output.process_level()
    output.set_process_level(output.NORMAL)
    monkeypatch.setattr(output._process_reporter, "stream", said)
    monkeypatch.setattr(output._process_reporter, "err_stream", said)
    try:
        with redirect_stdout(said), redirect_stderr(said):
            try:
                if call is not None:
                    rc = call()
                else:
                    args = cli.build_parser().parse_args(argv)
                    rc = getattr(cli, "cmd_" + argv[0])(local.db, args)
            except SystemExit as error:
                rc = error.code
    finally:
        output.set_process_level(level)
    print(said.getvalue())
    return rc, " ".join(said.getvalue().split())


def next_step_once(said, outcome, name, command):
    for phrase in INSTALLER_ADVICE:
        assert phrase not in said
    if outcome == "degraded":
        sentence = (f"{name} is installed, but its install did not finish "
                    f"(post-install). Retry it with: sudo pkm reinstall {name}")
        assert "Package marked DEGRADED" in said
    else:
        sentence = f"Retry it with: {command}"
        assert "FAILED at DB transaction after deploy" in said
    assert said.count(sentence) == 1, said
    assert said.count("Retry it with") == 1, said


@pytest.mark.parametrize("outcome", ["degraded", "database"])
def test_dependency_install_failure_names_the_requested_package(
        local, monkeypatch, outcome):
    # `pkm install app` resolves widget as app's dependency; widget's install
    # fails, and the next step is the command the person ran.
    assert PackageRemover(local.db).remove("widget", force=True)[0]
    app = _make_archive(local.new.with_name("app-1.0-1.igos.tar.gz"), "app", "1.0",
                        1, {"usr/bin/app": b"app\n"})
    repo = FakeRepo({"widget": _entry("widget", local.new),
                     "app": _entry("app", app, release=1, depends=["widget"])})
    repo.resolve_dependencies = lambda name, db: (True, ["widget", "app"])
    repo.download_package = lambda name, reporter=None: (
        True, str({"widget": local.new, "app": app}[name]))
    monkeypatch.setattr(PackageInstaller, "_find_archive", lambda self, name: None)
    fail(monkeypatch, outcome)
    rc, said = run(local, monkeypatch, ["install", "app", "--yes"], repo)
    assert rc == 1, said
    assert "installing widget:" in said
    assert local.db.get_installed("app") is None
    next_step_once(said, outcome, "widget", "sudo pkm install app")


def test_cached_archive_database_failure_names_the_command(local, monkeypatch):
    assert PackageRemover(local.db).remove("widget", force=True)[0]
    repo = FakeRepo({"widget": _entry("widget", local.new)})
    monkeypatch.setattr(PackageInstaller, "_find_archive",
                        lambda self, name: local.new)
    fail(monkeypatch, "database")
    rc, said = run(local, monkeypatch, ["install", "widget"], repo)
    assert rc == 1, said
    assert local.db.get_installed("widget") is None
    assert "matches the signed index" in said
    next_step_once(said, "database", "widget", "sudo pkm install widget")


@pytest.mark.parametrize("outcome", ["degraded", "database"])
def test_download_helper_package_failure_names_one_next_step(
        local, monkeypatch, outcome):
    assert PackageRemover(local.db).remove("widget", force=True)[0]
    repo = FakeRepo({"widget": _entry("widget", local.new)})
    repo.download_package = lambda name, reporter=None: (True, str(local.new))
    monkeypatch.setattr(PackageInstaller, "_find_archive", lambda self, name: None)
    monkeypatch.setattr(cli, "acceptance_record_exists", lambda name: True)
    fail(monkeypatch, outcome)

    def call():
        return cli._proprietary_install(
            local.db, local.installer, repo, output.Reporter(level=output.NORMAL),
            "widget", "a test license")

    rc, said = run(local, monkeypatch, None, repo, call=call)
    assert rc == "failed", said
    assert "installing widget:" in said
    next_step_once(said, outcome, "widget", "sudo pkm install widget")


@pytest.mark.parametrize("outcome", ["degraded", "database"])
def test_upgrade_new_dependency_failure_names_one_next_step(
        local, monkeypatch, outcome):
    # widget's new release depends on gamma, which is not installed; gamma's
    # install fails, and widget's upgrade is skipped at its old release.
    gamma = _make_archive(local.new.with_name("gamma-1.0-1.igos.tar.gz"), "gamma",
                          "1.0", 1, {"usr/bin/gamma": b"gamma\n"})
    repo = FakeRepo({"widget": _entry("widget", local.new, depends=["gamma"]),
                     "gamma": _entry("gamma", gamma, release=1)})
    repo.download_package = lambda name, reporter=None: (
        True, str({"widget": local.new, "gamma": gamma}[name]))
    fail(monkeypatch, outcome)
    rc, said = run(local, monkeypatch, ["upgrade", "widget", "--yes"], repo)
    assert rc == 1, said
    assert "installing new dep gamma:" in said
    assert "Skipping upgrade of widget" in said
    assert local.db.get_installed("widget")["release"] == 3
    next_step_once(said, outcome, "gamma", "sudo pkm upgrade widget")
