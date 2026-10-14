# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The source-build lane's recorder decides a rewrite by the bytes.

The build runs a recipe's post_install between `pkm hook-baseline` and
`pkm record-hook-changes`. A file of the package's own whose bytes are what the
baseline read stays content-checked whatever else the step did to it: a
`chmod`, `chgrp`, `touch`, an extended attribute or file capability, or a new
hard link each move ctime, and the recorder used to read a moved ctime over a
clean file as a rewrite and mark it hook-managed, existence-checked only, so
verify never compared that file's bytes again. Each case names the step, reads
the recorder's line for the file, and reads verify after the file's bytes
change.
"""

import hashlib
import os
import shutil
import subprocess
import tempfile

import pytest

from pkm.database import PackageDB
from pkm.installer import PackageInstaller

PATH = 'usr/bin/source-step'
BYTES = b'#!/bin/sh\necho built from source\n'
SETCAP = '/usr/sbin/setcap'
UNSHARE = '/usr/bin/unshare'


@pytest.fixture
def clean(tmp_path):
    """A registered package whose one file matches its recorded checksum."""
    root = tmp_path/'root'
    target = root/PATH
    target.parent.mkdir(parents=True)
    target.write_bytes(BYTES)
    target.chmod(0o755)
    db = PackageDB(tmp_path/'packages.db',root=str(root))
    installer = PackageInstaller(db,root=str(root))
    pkg = db.add_installed('source-step','1.0',install_method='source-build')
    db.add_files(pkg,[PATH],hashes={PATH:hashlib.sha256(BYTES).hexdigest()})
    yield root,db,installer,target
    db.close()


def named(messages):
    lines = [m for m in messages if f' {PATH}:' in m]
    assert len(lines) == 1, messages
    return lines[0]


def assert_kept(db,installer,target,baseline,change):
    changed,messages = installer.record_hook_changes('source-step',baseline)
    assert changed == [], messages
    assert db.get_generated_paths('source-step') == set()
    line = named(messages)
    assert change in line
    assert 'not the bytes' in line and 'the content stays checked' in line
    assert db.verify_package('source-step')['modified'] == []
    target.write_bytes(b'#!/bin/sh\necho not what was built\n')
    assert db.verify_package('source-step')['modified'] == [PATH]


def test_a_chmod_only_step_keeps_a_clean_file_content_checked(clean):
    root,db,installer,target = clean
    baseline = installer.hook_baseline('source-step')
    target.chmod(0o700)
    assert_kept(db,installer,target,baseline,'the mode (0755 -> 0700)')


def test_a_touch_only_step_keeps_a_clean_file_content_checked(clean):
    root,db,installer,target = clean
    baseline = installer.hook_baseline('source-step')
    os.utime(target,ns=(1767225600*10**9,1767225600*10**9))
    assert_kept(db,installer,target,baseline,'the modification time')


def test_a_chgrp_only_step_keeps_a_clean_file_content_checked(clean):
    root,db,installer,target = clean
    gid = next((g for g in sorted(os.getgroups()) if g != os.getegid()),None)
    if gid is None:
        pytest.skip('this process belongs to no second group to give a file')
    baseline = installer.hook_baseline('source-step')
    os.chown(target,-1,gid)
    assert_kept(db,installer,target,baseline,
                f'the owner ({os.geteuid()}:{os.getegid()} -> {os.geteuid()}:{gid})')


def test_an_extended_attribute_only_step_keeps_a_clean_file_content_checked(clean):
    root,db,installer,target = clean
    try:
        os.setxattr(target,'user.probe',b'1')
        os.removexattr(target,'user.probe')
    except OSError as e:
        pytest.skip(f'this filesystem keeps no user extended attribute: {e}')
    baseline = installer.hook_baseline('source-step')
    os.setxattr(target,'user.demo',b'1')
    assert_kept(db,installer,target,baseline,'the extended attributes (user.demo)')


def test_a_setcap_only_step_keeps_a_clean_file_content_checked(clean):
    root,db,installer,target = clean
    if not os.access(SETCAP,os.X_OK):
        pytest.skip(f'{SETCAP} is not on this host')
    probe_dir = tempfile.mkdtemp()
    try:
        probe = os.path.join(probe_dir,'probe')
        command,reasons = None,[]
        for prefix in ([SETCAP],[UNSHARE,'-r',SETCAP]):
            with open(probe,'wb') as f:
                f.write(b'probe\n')
            done = subprocess.run(prefix+['cap_net_raw+ep',probe],capture_output=True,text=True)
            if done.returncode == 0 and 'security.capability' in os.listxattr(probe):
                command = prefix
                break
            reasons.append(f"{' '.join(prefix)}: rc {done.returncode} {(done.stdout+done.stderr).strip()}")
            os.unlink(probe)
    finally:
        shutil.rmtree(probe_dir,ignore_errors=True)
    if command is None:
        pytest.skip('this host lets the test process set no file capability ('+'; '.join(reasons)+')')
    baseline = installer.hook_baseline('source-step')
    subprocess.run(command+['cap_net_raw+ep',str(target)],check=True)
    assert_kept(db,installer,target,baseline,
                'the extended attributes (security.capability)')


def test_a_new_hard_link_keeps_a_clean_file_content_checked(clean):
    root,db,installer,target = clean
    baseline = installer.hook_baseline('source-step')
    os.link(target,target.with_name('source-step-alias'))
    assert_kept(db,installer,target,baseline,'the link count (1 -> 2)')


def test_a_baseline_the_previous_form_wrote_keeps_a_clean_file_content_checked(clean):
    # sha256:ctime:owner, as a build that took its baseline before this
    # change wrote it: no metadata beyond ctime to name, and the bytes still
    # decide.
    root,db,installer,target = clean
    sha = hashlib.sha256(BYTES).hexdigest()
    baseline = {PATH:f'{sha}:{os.stat(target).st_ctime_ns}:{sha}'}
    target.chmod(0o700)
    assert_kept(db,installer,target,baseline,
                'the change time (this baseline records no other metadata)')


def test_a_step_that_rewrites_the_bytes_is_recorded_hook_managed(clean):
    root,db,installer,target = clean
    baseline = installer.hook_baseline('source-step')
    target.write_bytes(b'#!/bin/sh\necho regenerated by the step\n')
    target.chmod(0o700)
    changed,messages = installer.record_hook_changes('source-step',baseline)
    assert changed == [PATH]
    assert db.get_generated_paths('source-step') == {PATH}
    assert any('rewrote 1 of its own payload file(s)' in m for m in messages)
    assert PATH in db.verify_package('source-step')['generated']


def test_an_unchanged_file_is_not_named(clean):
    root,db,installer,target = clean
    baseline = installer.hook_baseline('source-step')
    assert installer.record_hook_changes('source-step',baseline) == ([],[])


def test_a_damaged_metadata_field_is_refused(clean):
    root,db,installer,target = clean
    baseline = installer.hook_baseline('source-step')
    token = baseline[PATH].rsplit(':',1)[0]+':'+'7b'
    with pytest.raises(ValueError):
        installer.record_hook_changes('source-step',{PATH:token})
    assert db.get_generated_paths('source-step') == set()
