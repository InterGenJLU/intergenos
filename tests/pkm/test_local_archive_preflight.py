# SPDX-License-Identifier: GPL-3.0-or-later
"""A refused local archive leaves the previous installation in place."""

import hashlib
import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pkm import cli, output, pretxn
from pkm import installer as installer_module
from pkm.database import PackageDB
from pkm.installer import PackageInstaller
from pkm.remover import PackageRemover
from tests.pkm.test_upgrade_from_local_archive import _make_archive, FakeRepo


@pytest.fixture
def local(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    db = PackageDB(tmp_path / "pkm.db", root=str(root))
    installer = PackageInstaller(db, root=str(root))
    old = _make_archive(tmp_path / "widget-1.0-3.igos.tar.gz", "widget", "1.0", 3,
                        {"usr/bin/widget": b"old\n"})
    ok, message = installer.install("widget", archive_path=old)
    assert ok, message
    new = _make_archive(tmp_path / "widget-1.0-4.igos.tar.gz", "widget", "1.0", 4,
                        {"usr/bin/widget": b"new\n"})
    before = db.get_installed("widget")
    yield SimpleNamespace(root=root, db=db, installer=installer, new=new, before=before)
    db.close()


def invoke(local, verb="upgrade", trust="loose", archive=None, capture=None, repo=None):
    args = cli.build_parser().parse_args(
        [verb, "widget", "--archive", str(archive or local.new),
         "--archive-trust", trust] + (["--yes"] if verb == "upgrade" else []))
    said = io.StringIO()
    level = output.process_level()
    old_root = cli._INSTALL_ROOT
    output.set_process_level(output.NORMAL)
    output._process_reporter.stream = said
    output._process_reporter.err_stream = said
    cli.set_install_root(local.root)
    try:
        with redirect_stdout(said), redirect_stderr(said), \
                patch.object(cli, "repo_manager", return_value=repo or FakeRepo()), \
                patch.object(cli, "_save_rollback_archive", return_value=None), \
                patch.object(cli, "refresh_available_updates_after_transaction"), \
                patch.object(cli, "_print_transaction_next_steps"), \
                patch.object(pretxn, "run_pre_transaction_hook", side_effect=capture or (lambda *a, **k: None)):
            try:
                rc = getattr(cli, "cmd_" + verb)(local.db, args)
            except SystemExit as error:
                rc = error.code
    finally:
        cli._INSTALL_ROOT = old_root
        output.set_process_level(level)
        output._process_reporter.stream = None
        output._process_reporter.err_stream = None
    return rc, " ".join(said.getvalue().split())


def held_copy(unheld=None):
    """The private copy pkm reads a named archive from once it has opened
    it (pkm.installer.hold_named_archive): a change to the bytes pkm checks
    and installs, after the first check, is made there. The file the person
    named is never read again, so changing it changes nothing. With no
    archive held (a repository archive from pkm's own cache), `unheld`."""
    held = list(installer_module._HELD.values())
    if not held:
        assert unheld is not None, "no archive is held"
        return unheld
    (held,) = held
    return held.path


def assert_preserved(local):
    assert local.db.get_installed("widget") == local.before
    assert (local.root / "usr/bin/widget").read_bytes() == b"old\n"
    assert not any(h["operation"] == "remove" for h in local.db.get_history("widget"))


@pytest.mark.parametrize("verb", ["upgrade", "install"])
def test_renamed_archive_preserves_installed_package(local, verb):
    renamed = local.new.with_name("widget-1.0-4-copy.igos.tar.gz")
    renamed.write_bytes(local.new.read_bytes())
    rc, said = invoke(local, verb, archive=renamed)
    assert rc == 1, said
    assert_preserved(local)


def test_matching_archive_keeps_restore_point_before_removal(local):
    events = []
    def capture(*args, **kwargs):
        assert_preserved(local)
        events.append("captured")
    remove = PackageRemover.remove
    def removing(self, *args, **kwargs):
        assert events == ["captured"]
        events.append("remove")
        return remove(self, *args, **kwargs)
    with patch.object(PackageRemover, "remove", removing):
        rc, said = invoke(local, capture=capture)
    assert rc == 0, said
    assert events == ["captured", "remove"]
    assert local.db.get_installed("widget")["release"] == 4


def test_changed_archive_is_rechecked_before_removal(local):
    def capture(*args, **kwargs):
        held_copy().write_bytes(b"changed after initial verification")
    rc, said = invoke(local, capture=capture)
    assert rc == 1, said
    assert_preserved(local)


@pytest.mark.parametrize("verb", ["upgrade", "install"])
def test_header_for_other_package_is_refused(local, verb):
    other = _make_archive(local.new.with_name("different-1.0-4.igos.tar.gz"),
                          "different", "1.0", 4, {"usr/bin/different": b"other\n"})
    if verb == "install":
        local.db.remove_installed("widget")
    rc, said = invoke(local, verb, archive=other)
    assert rc == 1, said
    assert not (local.root / "usr/bin/different").exists()
    if verb == "upgrade":
        assert_preserved(local)


@pytest.mark.parametrize("verb", ["upgrade", "install"])
def test_payload_refused_before_target_changes(local, verb):
    local.new = _make_archive(local.new, "widget", "1.0", 4,
                              {"../outside": b"invalid\n"})
    if verb == "install":
        local.db.remove_installed("widget")
    rc, said = invoke(local, verb)
    assert rc == 1, said
    if verb == "upgrade":
        assert_preserved(local)
    assert not (local.root.parent / "outside").exists()


@pytest.mark.parametrize("trust", ["strict", "repo-only"])
def test_contradicted_hash_preserves_package(local, trust):
    repo = FakeRepo({"widget": {"name": "widget", "version": "1.0", "release": 4,
                               "sha256": "0" * 64}})
    rc, said = invoke(local, trust=trust, repo=repo)
    assert rc == 1, said
    assert_preserved(local)


def test_every_archive_check_precedes_the_first_remover_call(local):
    import pkm.installer as installer_module
    events = []
    def recording(label, function):
        def call(*args, **kwargs):
            result = function(*args, **kwargs)
            events.append(label)
            return result
        return call
    def removing(*args, **kwargs):
        assert {"trust", "hash", "header", "filename", "payload"} <= set(events)
        events.append("remove")
        return False, "recording remover: no mutation requested"
    with patch.object(cli, "_explicit_archive_trust",
                      recording("trust", cli._explicit_archive_trust)), \
            patch.object(installer_module, "_sha256",
                         recording("hash", installer_module._sha256)), \
            patch.object(installer_module, "_read_package_meta",
                         recording("header", installer_module._read_package_meta)), \
            patch.object(installer_module, "_name_header_disagreement",
                         recording("filename", installer_module._name_header_disagreement)), \
            patch.object(installer_module, "_safe_extract_tar",
                         recording("payload", installer_module._safe_extract_tar)), \
            patch.object(PackageRemover, "remove", removing):
        rc, said = invoke(local)
    assert rc == 1, said
    assert events[-1] == "remove"
    assert_preserved(local)
