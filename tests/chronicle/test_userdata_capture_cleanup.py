# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Failures after walking a source cannot accumulate unpublished version trees."""

import pytest

from chronicle import manifest, paths, userdata


@pytest.fixture
def store(tmp_path):
    source = tmp_path / "home"
    source.mkdir()
    (source / "note").write_bytes(b"saved bytes\n")
    target = tmp_path / "target"
    first = userdata.capture([str(source)], target, None, 1, 100, "first")
    return source, target, first


@pytest.mark.parametrize("stage", ["walk", "manifest", "final-path", "rename", "publish"])
def test_failure_cleans_the_unpublished_tree_and_preserves_previous(store, monkeypatch, stage):
    source, target, first = store
    modules = {
        "walk": (userdata, "_capture_file_or_link"),
        "manifest": (manifest, "build_manifest"),
        "final-path": (userdata, "userdata_tree"),
        "rename": (userdata.os, "rename"),
        "publish": (manifest, "commit_manifest"),
    }

    def fail(*args, **kwargs):
        raise RuntimeError("injected " + stage)

    with monkeypatch.context() as patch:
        patch.setattr(*modules[stage], fail)
        with pytest.raises(RuntimeError, match="injected " + stage):
            userdata.capture([str(source)], target, None, 2, 200, "second")
    assert sorted(p.name for p in (target / "userdata").iterdir()) == [first]
    previous = manifest.find_version(target, paths.LAYER_USER_DATA, first)
    assert previous is not None
    entry = next(e for e in previous["entries"] if e["type"] == "file")
    assert userdata.read_file(target, first, entry).read_bytes() == b"saved bytes\n"
    assert len(manifest.list_versions_complete(target, paths.LAYER_USER_DATA)) == 1


@pytest.mark.parametrize("stage", ["manifest", "publish"])
def test_cleanup_error_reports_both_failures(store, monkeypatch, stage):
    source, target, _first = store

    def fail(*args, **kwargs):
        raise ValueError("capture failure")

    def refuse_cleanup(*args, **kwargs):
        raise OSError("cleanup failure")

    monkeypatch.setattr(manifest, "build_manifest" if stage == "manifest" else "commit_manifest", fail)
    monkeypatch.setattr(userdata.shutil, "rmtree", refuse_cleanup)
    with pytest.raises(RuntimeError, match="capture failure.*staging cleanup failed: cleanup failure") as caught:
        userdata.capture([str(source)], target, None, 2, 200, "second")
    assert isinstance(caught.value.__cause__, ValueError)


def test_colliding_tree_survives_cleanup_of_the_new_staging_tree(store):
    source, target, first = store
    with pytest.raises(manifest.ManifestCollision):
        userdata.capture([str(source)], target, None, 1, 100, "first")
    assert sorted(p.name for p in (target / "userdata").iterdir()) == [first]
