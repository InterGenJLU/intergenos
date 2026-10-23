# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Capture and estimate walks exclude both stores by directory identity."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from chronicle import config, engine, held_open, paths, sentinel, userdata


@pytest.fixture
def layout(tmp_path):
    def make(inside=True):
        source = tmp_path / "source"
        source.mkdir()
        (source / "document").write_bytes(b"data")
        parent = source if inside else tmp_path
        cfg = config.Config()
        cfg.user_data_paths = [str(source)]
        local = parent / "local-store"
        built = engine.Engine(local_root=local, config=cfg,
                              now_fn=lambda: 1_000_000.0)
        mount = parent / "target-mount"
        mount.mkdir()
        built.target_adopt(str(mount), target_class="directory")
        target = built.target_root()
        (local / "local-marker").write_bytes(b"L" * 1024)
        (target / "target-marker").write_bytes(b"T" * 2048)
        return SimpleNamespace(engine=built, source=source, local=local,
                               target=target)
    return make


def assert_no_store_entries(built, layer, version, excluded):
    entries = built.get_manifest(layer, version)["entries"]
    for entry in entries:
        path = Path(entry["path"])
        assert not any(path == store or path.is_relative_to(store)
                       for store in excluded), entry
    return entries


def test_user_data_capture_skips_both_stores_inside_its_source(layout):
    case = layout()
    result = case.engine.capture(paths.LAYER_USER_DATA)
    entries = assert_no_store_entries(case.engine, paths.LAYER_USER_DATA,
                                     result["version_id"], [case.local, case.target])
    assert any(entry["path"] == str(case.source / "document") for entry in entries)
    assert case.engine.verify(paths.LAYER_USER_DATA, result["version_id"])["ok"]


def test_change_estimate_ignores_bytes_in_both_stores(layout):
    case = layout()
    assert sentinel.estimate_userdata_change(case.engine) == 4


def test_home_size_estimate_ignores_bytes_in_both_stores(layout):
    case = layout()
    assert case.engine._estimate_home_size() == 4


def test_config_scope_skips_both_stores_inside_its_source(layout):
    case = layout()
    result = case.engine.capture(paths.LAYER_CONFIG_STATE, scope=[str(case.source)])
    entries = assert_no_store_entries(case.engine, paths.LAYER_CONFIG_STATE,
                                     result["version_id"], [case.local, case.target])
    assert any(entry["path"] == str(case.source / "document") for entry in entries)
    assert case.engine.verify(paths.LAYER_CONFIG_STATE, result["version_id"])["ok"]


def test_home_size_applies_configured_exclude_patterns(layout):
    case = layout(inside=False)
    cache = case.source / ".cache"
    cache.mkdir()
    (cache / "ignored").write_bytes(b"cache bytes")
    case.engine.config.exclude_globs.append("*/skip-me")
    (case.source / "skip-me").write_bytes(b"excluded file")
    assert case.engine._estimate_home_size() == 4


@pytest.mark.parametrize("walker", ["user-data", "config-state", "change", "size"])
def test_a_second_directory_name_is_checked_from_its_held_identity(
        layout, monkeypatch, walker):
    case = layout(inside=False)
    alias = case.source / "another-name"
    alias.mkdir()
    (alias / "target-marker").write_bytes(b"a visible directory entry")
    original = held_open.held_directory
    observed = []

    def hold(directory, create=False):
        if Path(directory) == alias:
            # An actual descriptor on the store supplies the second name's
            # identity; no path-prefix comparison can identify this object.
            fd = original(str(case.target))
            info = os.fstat(fd)
            observed.append((info.st_dev, info.st_ino))
            return fd
        return original(directory, create=create)

    monkeypatch.setattr(held_open, "held_directory", hold)
    if walker == "change":
        assert sentinel.estimate_userdata_change(case.engine) == 4
    elif walker == "size":
        assert case.engine._estimate_home_size() == 4
    else:
        scope = [str(case.source)] if walker == "config-state" else None
        result = case.engine.capture(walker, scope=scope)
        assert_no_store_entries(case.engine, walker, result["version_id"], [alias])
    assert observed
    info = os.stat(case.target)
    assert set(observed) == {(info.st_dev, info.st_ino)}


def test_control_source_bytes_are_kept_when_stores_are_beside_it(layout):
    case = layout(inside=False)
    assert sentinel.estimate_userdata_change(case.engine) == 4
    assert case.engine._estimate_home_size() == 4
    for layer in (paths.LAYER_USER_DATA, paths.LAYER_CONFIG_STATE):
        scope = [str(case.source)] if layer == paths.LAYER_CONFIG_STATE else None
        result = case.engine.capture(layer, scope=scope)
        entries = case.engine.get_manifest(layer, result["version_id"])["entries"]
        files = [entry for entry in entries if entry["type"] == "file"]
        assert [entry["path"] for entry in files] == [str(case.source / "document")]
