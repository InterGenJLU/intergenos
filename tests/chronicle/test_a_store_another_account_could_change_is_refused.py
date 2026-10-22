# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A store another account could change is refused, at adoption and at use.

A store holds the manifests a restore writes from as root, wherever they say,
and the files it writes. Target adoption took any mountpoint it was named and
made the store there; every capture and restore then used the store by path.
An administrator adopting a directory another account can write, or one under
such a directory, or one reached through a link, handed that account the
store. Each case stands the other account in by the mode bits (this test runs
unprivileged and cannot own a directory as another uid): a directory its group
or others can write, a parent others can write without the sticky bit, a link
on the way, a skeleton directory that is a link.
"""

import os

import pytest

from chronicle import engine, paths


@pytest.fixture
def scratch(tmp_path):
    os.umask(0o022)
    eng = engine.Engine(local_root=str(tmp_path / "local"), now_fn=lambda: 0.0)
    source = tmp_path / "etc-ish" / "conf"
    source.parent.mkdir()
    source.write_bytes(b"settings\n")
    return tmp_path, eng, source


def test_control_a_directory_no_other_account_can_change_is_adopted(scratch):
    tmp, eng, source = scratch
    target = tmp / "target"
    target.mkdir()
    adopted = eng.target_adopt(str(target), target_class="directory")
    assert adopted["adopted"]
    assert eng.target_root() == target / paths.TARGET_DIR_NAME
    eng.capture(paths.LAYER_CONFIG_STATE, scope=[str(source)], reason="ok")
    assert "target_refused" not in eng.status()


@pytest.mark.parametrize("mode", [0o777, 0o775, 0o1777])
def test_a_mountpoint_others_can_write_is_not_adopted(scratch, mode):
    tmp, eng, _source = scratch
    target = tmp / "target"
    target.mkdir()
    os.chmod(target, mode)
    with pytest.raises(engine.EngineError, match="cannot hold a backup target"):
        eng.target_adopt(str(target), target_class="directory")
    assert eng.status()["target"] is None
    assert not (target / paths.TARGET_DIR_NAME).exists()


def test_a_mountpoint_under_a_directory_others_can_write_is_not_adopted(
        scratch):
    tmp, eng, _source = scratch
    parent = tmp / "shared"
    parent.mkdir()
    (parent / "target").mkdir()
    os.chmod(parent, 0o777)
    with pytest.raises(engine.EngineError, match="cannot hold a backup target"):
        eng.target_adopt(str(parent / "target"), target_class="whole-volume")
    assert eng.status()["target"] is None


def test_a_mountpoint_reached_through_a_link_is_not_adopted(scratch):
    tmp, eng, _source = scratch
    (tmp / "real").mkdir()
    (tmp / "link").symlink_to(tmp / "real")
    with pytest.raises(engine.EngineError, match="cannot hold a backup target"):
        eng.target_adopt(str(tmp / "link"), target_class="directory")
    assert not (tmp / "real" / paths.TARGET_DIR_NAME).exists()


def test_a_store_whose_skeleton_is_a_link_is_not_adopted(scratch):
    tmp, eng, _source = scratch
    target = tmp / "target"
    (target / paths.TARGET_DIR_NAME).mkdir(parents=True)
    elsewhere = tmp / "elsewhere"
    elsewhere.mkdir()
    (target / paths.TARGET_DIR_NAME / "cas").symlink_to(elsewhere)
    with pytest.raises(engine.EngineError, match="cannot hold a backup target"):
        eng.target_adopt(str(target), target_class="directory")


def test_an_adopted_store_that_others_can_now_write_is_refused_at_use(scratch):
    tmp, eng, source = scratch
    target = tmp / "target"
    target.mkdir()
    eng.target_adopt(str(target), target_class="directory")
    os.chmod(target / paths.TARGET_DIR_NAME, 0o777)
    with pytest.raises(engine.EngineError, match="is refused"):
        eng.target_root()
    captured = eng.capture(paths.LAYER_CONFIG_STATE, scope=[str(source)],
                           reason="mirror")
    assert captured["mirrored"] is False
    assert "is refused" in captured["target_refused"]
    assert eng.verify(paths.LAYER_CONFIG_STATE, captured["version_id"])["ok"]
    with pytest.raises(engine.EngineError, match="is refused"):
        eng.list_versions(paths.LAYER_USER_DATA)
    status = eng.status()
    assert status["target_present"] is False
    assert "is refused" in status["target_refused"]
    assert "writable by its group or by other accounts" in \
        status["target_refused"]


def test_a_local_store_others_can_write_is_refused(tmp_path):
    local = tmp_path / "local"
    local.mkdir()
    os.chmod(local, 0o777)
    with pytest.raises(engine.EngineError, match="local store .* is refused"):
        engine.Engine(local_root=str(local), now_fn=lambda: 0.0)
