# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""What a source-build hook did to a package's own files, as its recorder reads it.

REVERSED DELIBERATELY: an identical-content write used to be read, from its
moved ctime, as a rewrite, and the file became hook-managed (existence-checked
only). The same ctime moves on a chmod, a chown, a touch, an extended attribute
or a new hard link, so a post_install that only ran `chmod` on its own file
turned that file's byte check off for good. The recorder now decides by the
bytes: a file whose bytes did not change keeps its content check and is named,
with what changed.
"""

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from pkm import cli
from pkm.database import PackageDB
from pkm.installer import PackageInstaller


@pytest.fixture
def state(tmp_path):
    root = tmp_path/'root'
    root.mkdir()
    db = PackageDB(tmp_path/'packages.db',root=str(root))
    installer = PackageInstaller(db,root=str(root))
    path = 'usr/share/cache package/index'
    target = root/path
    target.parent.mkdir(parents=True)
    target.write_bytes(b'initial index\n')
    pkg = db.add_installed('cache-package','1.0',install_method='source-build')
    db.add_files(pkg,[path])
    yield root,db,installer,path,target
    db.close()


def rewrite_different(target):
    target.write_bytes(b'a different index\n')


def rewrite_same(target):
    before = target.stat()
    replacement = target.with_name('replacement')
    replacement.write_bytes(target.read_bytes())
    os.replace(replacement,target)
    os.utime(target,ns=(before.st_atime_ns,before.st_mtime_ns))
    assert target.stat().st_ctime_ns != before.st_ctime_ns


def test_an_identical_rewrite_keeps_the_content_check(state):
    # REVERSED DELIBERATELY: this case required the identical-content write to
    # be recorded hook-managed. Its bytes are the baseline's, so the file keeps
    # its content check, the transcript names it, and a later change to its
    # bytes is reported by verify; a file that is gone is missing.
    root,db,installer,path,target = state
    baseline = installer.hook_baseline('cache-package')
    rewrite_same(target)
    changed,messages = installer.record_hook_changes('cache-package',baseline)
    assert changed == []
    assert db.get_generated_paths('cache-package') == set()
    named = [m for m in messages if f' {path}:' in m]
    assert len(named) == 1
    assert 'the inode (the same bytes in a new file)' in named[0]
    assert 'not the bytes' in named[0]
    target.write_bytes(b'later generated index\n')
    assert db.verify_package('cache-package')['modified'] == [path]
    target.unlink()
    assert db.verify_package('cache-package')['missing'] == [path]


def test_no_hook_write_does_not_reclassify_preexisting_damage(state):
    root,db,installer,path,target = state
    target.write_bytes(b'damage before the hook\n')
    baseline = installer.hook_baseline('cache-package')
    assert installer.record_hook_changes('cache-package',baseline) == ([],[])
    assert db.verify_package('cache-package')['modified'] == [path]


def test_only_the_observed_owner_is_reclassified(state):
    # The write changes the bytes: an identical-content one reclassifies
    # nothing now, and this case is about whose row a rewrite reaches.
    root,db,installer,path,target = state
    other = db.add_installed('other-owner','1.0')
    db.add_files(other,[path])
    baseline = installer.hook_baseline('cache-package')
    rewrite_different(target)
    changed,_ = installer.record_hook_changes('cache-package',baseline)
    assert changed == [path]
    assert db.get_generated_paths('cache-package') == {path}
    assert db.get_generated_paths('other-owner') == set()
    target.write_bytes(b'later content\n')
    assert db.verify_package('other-owner')['modified'] == [path]


def test_legacy_hash_only_baseline_does_not_invent_write_evidence(state):
    root,db,installer,path,target = state
    baseline = {path:hashlib.sha256(target.read_bytes()).hexdigest()}
    rewrite_same(target)
    assert installer.record_hook_changes('cache-package',baseline) == ([],[])
    target.write_bytes(b'changed bytes\n')
    assert installer.record_hook_changes('cache-package',baseline)[0] == [path]


def test_cli_round_trip_preserves_write_state_and_spaces(state,tmp_path,monkeypatch,capsys):
    # REVERSED DELIBERATELY: the identical-content write this case made used
    # to come back hook-managed. The round trip through the baseline file
    # still carries the state, and a path with spaces, whole: the chmod is
    # named with the mode it read before the hook.
    root,db,installer,path,target = state
    target.chmod(0o644)
    monkeypatch.setattr(cli,'package_installer',lambda _db:installer)
    baseline_file = tmp_path/'baseline'
    assert cli.cmd_hook_baseline(db,SimpleNamespace(package='cache-package',out=str(baseline_file))) == 0
    target.chmod(0o600)
    assert cli.cmd_record_hook_changes(db,SimpleNamespace(package='cache-package',baseline=str(baseline_file))) == 0
    assert db.get_generated_paths('cache-package') == set()
    output = ' '.join(capsys.readouterr().out.split())
    assert f'{path}: its hook changed the mode (0644 -> 0600), not the bytes' in output


def test_malformed_state_fails_without_reclassification(state,tmp_path,monkeypatch):
    root,db,installer,path,target = state
    monkeypatch.setattr(cli,'package_installer',lambda _db:installer)
    baseline_file = tmp_path/'invalid-baseline'
    baseline_file.write_text(f'{"0"*64}:invalid  {path}\n')
    assert cli.cmd_record_hook_changes(db,SimpleNamespace(package='cache-package',baseline=str(baseline_file))) == 1
    assert db.get_generated_paths('cache-package') == set()


def test_metadata_change_does_not_classify_preexisting_damage(state):
    root,db,installer,path,target = state
    target.write_bytes(b'damage before the hook\n')
    baseline = installer.hook_baseline('cache-package')
    target.chmod(0o600)
    changed,messages = installer.record_hook_changes('cache-package',baseline)
    assert changed == []
    assert messages
    assert db.get_generated_paths('cache-package') == set()
    assert db.verify_package('cache-package')['modified'] == [path]


def test_missing_checksum_does_not_authorize_identical_content_change(state):
    root,db,installer,path,target = state
    db.conn.execute('UPDATE files SET checksum = NULL WHERE path = ?', (path,))
    db.conn.commit()
    baseline = installer.hook_baseline('cache-package')
    rewrite_same(target)
    changed,messages = installer.record_hook_changes('cache-package',baseline)
    assert changed == []
    assert messages
    assert db.get_generated_paths('cache-package') == set()
    assert db.verify_package('cache-package')['unverifiable'] == [path]


def test_other_owner_checksum_cannot_authorize_metadata_change(state):
    root,db,installer,path,target = state
    target.write_bytes(b'other owner content\n')
    other = db.add_installed('other-owner','1.0')
    db.add_files(other,[path])
    baseline = installer.hook_baseline('cache-package')
    target.chmod(0o600)
    changed,messages = installer.record_hook_changes('cache-package',baseline)
    assert changed == []
    assert messages
    assert db.get_generated_paths('cache-package') == set()
    assert db.get_generated_paths('other-owner') == set()


def test_hook_time_checksum_refresh_cannot_repair_the_starting_evidence(state):
    root,db,installer,path,target = state
    target.write_bytes(b'damage before the hook\n')
    baseline = installer.hook_baseline('cache-package')
    db.reconcile_checksums_from_live(paths=[path])
    target.chmod(0o600)
    changed,messages = installer.record_hook_changes('cache-package',baseline)
    assert changed == []
    assert messages
    assert db.get_generated_paths('cache-package') == set()
    target.write_bytes(b'later different bytes\n')
    assert db.verify_package('cache-package')['modified'] == [path]


def test_cli_reports_observation_that_keeps_its_content_check(state,tmp_path,monkeypatch,capsys):
    root,db,installer,path,target = state
    monkeypatch.setattr(cli,'package_installer',lambda _db:installer)
    target.write_bytes(b'damage before the hook\n')
    baseline_file = tmp_path/'baseline'
    assert cli.cmd_hook_baseline(db,SimpleNamespace(package='cache-package',out=str(baseline_file))) == 0
    target.chmod(0o600)
    assert cli.cmd_record_hook_changes(db,SimpleNamespace(package='cache-package',baseline=str(baseline_file))) == 0
    output = ' '.join(capsys.readouterr().out.split())
    assert f'{path}: its hook changed the mode' in output
    assert 'the content stays checked' in output
    assert 'no own payload files newly classified' in output
    assert db.verify_package('cache-package')['modified'] == [path]
