# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A restore writes only through the directory it walked to, never a link.

The restore runs as root with the capabilities to replace and re-own any file
under /etc, /usr, /var and /home, and the entries it writes name paths inside
directories their accounts control. Each case lets the account change its own
tree the way it can — a directory it owns made a link to another directory, a
name exchanged for a link while the restore works, a FIFO put in the store's
place — and asserts that the directory standing for /etc is untouched and that
nothing was opened that the restore did not hold first. The engine's restore
step is called as restore_apply calls it.
"""

import os
import stat
import sys
import threading
import time

import pytest

from chronicle import cas, configstate, engine, manifest, paths, userdata

CHOSEN = b"ACCOUNT-CHOSEN BYTES (stand-in for a line wanted in /etc/passwd)\n"
ORIGINAL = b"THE SYSTEM FILE AS IT WAS (stand-in for /etc/passwd)\n"

_HOOK = {"on": None}


def _audit(event, args):
    action = _HOOK["on"]
    if action is not None and args and isinstance(args[0], str):
        action(event, args[0])


sys.addaudithook(_audit)


@pytest.fixture
def tree(tmp_path):
    os.umask(0o022)
    src = tmp_path / "src" / "home"
    acct = src / "a"
    d = acct / "d"
    d.mkdir(parents=True)
    (d / "passwd").write_bytes(CHOSEN)
    os.chmod(d / "passwd", 0o644)
    (d / "sub").mkdir()
    os.symlink("passwd", d / "link")
    etcish = tmp_path / "etcish"
    etcish.mkdir()
    (etcish / "passwd").write_bytes(ORIGINAL)
    os.chmod(etcish / "passwd", 0o600)
    target = tmp_path / "target"
    target.mkdir()
    version = userdata.capture([str(src)], str(target), None, 1, 0, "restore")
    found = manifest.find_version(target, paths.LAYER_USER_DATA, version)
    entries = {e["path"]: e for e in found["entries"]}
    yield src, acct, d, etcish, target, version, entries
    _HOOK["on"] = None


def _restore(layer, version, entry, root, store=None):
    return engine.Engine._restore_one(None, layer, version, entry, root, store,
                                      "replace-confirm")


def _link_parent(acct, d, etcish):
    os.rename(d, acct / "d.was")
    os.symlink(etcish, d)


def _untouched(etcish):
    names = sorted(p.name for p in etcish.iterdir())
    mode = stat.S_IMODE(os.lstat(etcish / "passwd").st_mode)
    return names, (etcish / "passwd").read_bytes(), mode


def test_control_a_restore_of_the_accounts_own_tree_lands(tree):
    _src, _acct, d, _etcish, target, version, entries = tree
    (d / "passwd").write_bytes(b"changed since\n")
    _restore(paths.LAYER_USER_DATA, version, entries[str(d / "passwd")], target)
    assert (d / "passwd").read_bytes() == CHOSEN
    assert stat.S_IMODE(os.lstat(d / "passwd").st_mode) == 0o644


@pytest.mark.parametrize("name", ["passwd", "sub", "link"])
def test_a_parent_made_a_link_turns_no_write_toward_another_directory(
        tree, name):
    _src, acct, d, etcish, target, version, entries = tree
    before = _untouched(etcish)
    _link_parent(acct, d, etcish)
    with pytest.raises((OSError, engine.EngineError)):
        _restore(paths.LAYER_USER_DATA, version, entries[str(d / name)],
                 target)
    assert _untouched(etcish) == before


def test_a_config_state_restore_through_a_linked_parent_writes_nothing(tree):
    _src, acct, d, etcish, _target, _version, _entries = tree
    local = d.parent.parent.parent / "local"
    local.mkdir()
    store = cas.ContentStore(local)
    version = configstate.capture([str(d / "passwd")], local, store, 1, 0,
                                  "scope")
    found = manifest.find_version(local, paths.LAYER_CONFIG_STATE, version)
    entry = next(e for e in found["entries"] if e["path"] == str(d / "passwd"))
    before = _untouched(etcish)
    _link_parent(acct, d, etcish)
    with pytest.raises((OSError, engine.EngineError)):
        _restore(paths.LAYER_CONFIG_STATE, version, entry, local, store)
    assert _untouched(etcish) == before


def test_a_directory_exchanged_for_a_link_gets_no_mode_through_it(tree):
    _src, _acct, d, etcish, target, version, entries = tree
    entry = dict(entries[str(d / "sub")])
    entry["mode"] = 0o755
    (d / "sub").rmdir()
    victim = etcish / "passwd"
    exchanged = []

    def exchange(event, path):
        # Once the restore has made d/sub, the account exchanges it for a
        # link to the victim before its mode is set.
        if exchanged or not (d / "sub").is_dir():
            return
        if (event == "os.chmod" and path == str(d / "sub")) or \
                (event == "open" and path == "sub"):
            os.rmdir(d / "sub")
            os.symlink(victim, d / "sub")
            exchanged.append(event)

    _HOOK["on"] = exchange
    try:
        try:
            _restore(paths.LAYER_USER_DATA, version, entry, target)
        except (OSError, engine.EngineError):
            pass
    finally:
        _HOOK["on"] = None
    assert exchanged, "the exchange never fired"
    assert stat.S_IMODE(os.lstat(victim).st_mode) == 0o600


def test_a_fifo_in_the_stores_place_is_never_opened(tree):
    _src, _acct, d, _etcish, target, version, entries = tree
    stored = userdata.read_file(target, version, entries[str(d / "passwd")])
    os.unlink(stored)
    os.mkfifo(stored, 0o600)
    released = []

    def writer():
        fd = os.open(stored, os.O_WRONLY)
        released.append(True)
        os.close(fd)

    peer = threading.Thread(target=writer, daemon=True)
    peer.start()
    time.sleep(0.3)
    try:
        with pytest.raises(engine.EngineError, match="not a regular file"):
            _restore(paths.LAYER_USER_DATA, version,
                     entries[str(d / "passwd")], target)
    finally:
        peer.join(1.0)
        was_released = bool(released)
        if not was_released:
            mine = os.open(stored, os.O_RDWR | os.O_NONBLOCK)
            peer.join(2.0)
            os.close(mine)
    assert not was_released, "the restore opened the FIFO in the store"
